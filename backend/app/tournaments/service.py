"""Competitive module: tournaments, teams, ladders, results, matches.

Data source: FPSChallenge public JSON API (verified 2026-11-06):
  GET /api/v1/tournament?page=N        current tournaments (all games)
  GET /api/v1/tournament/{slug}        details (registration/check-in dates, map pool)
  GET /api/v1/tournament/{slug}/teams  registered teams and members
  GET /api/v1/teams/{id}               team profile + ladder rank/ELO/W-L per league
There is no public endpoint for match lists or full ladder standings: standings
are built from the teams seen in CoD2 tournaments (partial, clearly labelled),
and matches/results can be entered manually.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from ..core import irc, log
from ..core.announcer import PRIO_HIGH, PRIO_NORMAL
from ..core.commands import MODERATOR, USER, Reply, command, page_arg, valid_url
from ..core.db import utcnow
from ..core.http import FetchError

L = log.get("tournaments")
API = "https://fpschallenge.eu/api/v1"
SITE = "https://fpschallenge.eu"
STATE_LABEL = {
    "ANNOUNCE": ("announced", irc.CYAN), "REGISTRATION": ("registration open", irc.LIME),
    "CHECKIN": ("check-in", irc.YELLOW), "WAITING_FOR_START": ("starting", irc.YELLOW),
    "ONGOING": ("live", irc.ORANGE), "FINISHED": ("finished", irc.GREY), "CANCELLED": ("cancelled", irc.RED),
    "MANUAL": ("scheduled", irc.CYAN),
}
WINNER_RES = [
    re.compile(r"^(?P<team>.+?) takes the crown at (?P<t>.+?)!?$", re.I),
    re.compile(r"^(?P<team>.+?) conquers (?P<t>.+?)!?$", re.I),
    re.compile(r"^(?P<team>.+?) wins (?P<t>.+?)!?$", re.I),
]
RUNNER_UP_RE = re.compile(r"(?:against|besting|defeating|overcoming|taking down) (?P<team>.+?)(?: in|,| to)", re.I)


def _zone(name):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return timezone(timedelta(hours=1))


class TournamentService:
    def __init__(self, app):
        self.app = app
        self.db = app.db
        self.games = [g.lower() for g in app.cfg.getlist("tournaments", "games", "Call of Duty 2", sep=",")]
        self.provider_tz = _zone(app.cfg.get("tournaments", "provider_timezone", "Europe/Berlin"))

    # -- helpers ---------------------------------------------------------------
    def to_utc(self, value) -> str | None:
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=self.provider_tz)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    def wanted(self, game: str) -> bool:
        return not self.games or (game or "").lower() in self.games

    # -- refresh -----------------------------------------------------------------
    def refresh(self) -> None:
        http = self.app.http
        seen = []
        page, last = 1, 1
        while page <= min(last, 5):
            data = http.get_json(f"{API}/tournament" + (f"?page={page}" if page > 1 else ""))
            if not isinstance(data, dict) or not isinstance(data.get("data"), list):
                raise FetchError("unexpected tournaments API format")
            last = int((data.get("meta") or {}).get("lastPage") or 1)
            for t in data["data"]:
                if isinstance(t, dict) and self.wanted(t.get("gameName", "")):
                    seen.append(self.upsert(t))
            page += 1
        first_run = self.db.cfg_get("tournaments.initialized") != "1"
        # tournaments that left the list: ask for details (finished?)
        for row in self.db.q("SELECT * FROM tournaments WHERE provider='fpschallenge' AND state NOT IN "
                             "('FINISHED','CANCELLED') AND last_seen < datetime('now','-1 hour')"):
            self.fetch_detail(row["slug"])
        # details + teams for active tournaments (cached)
        for tid in seen:
            row = self.db.one("SELECT * FROM tournaments WHERE id = ?", (tid,))
            age = self.app.seconds_since(row["detail_at"]) if row["detail_at"] else None
            if age is None or age > 6 * 3600:
                self.fetch_detail(row["slug"])
                self.fetch_teams(row)
        self.winners_from_news()
        self.announce_changes(silent=first_run)
        if first_run:
            self.db.cfg_set("tournaments.initialized", "1")

    def upsert(self, t: dict) -> int:
        ext = str(t.get("id"))
        values = dict(
            slug=t.get("slug"), name=irc.clean(t.get("name"), 120), game=t.get("gameName", ""),
            state=str(t.get("state") or "ANNOUNCE").upper(), mode=t.get("mode"), start_at=self.to_utc(t.get("start")),
            teams_count=t.get("teamsCount"), max_teams=t.get("maxTeams"), player_base=t.get("playerBaseCount"),
            url=f"{SITE}/tournament/{t.get('slug')}", now=utcnow(),
        )
        row = self.db.one("SELECT id FROM tournaments WHERE provider='fpschallenge' AND ext_id=?", (ext,))
        if row:
            self.db.x("UPDATE tournaments SET slug=:slug, name=:name, game=:game, state=:state, mode=:mode, "
                      "start_at=:start_at, teams_count=:teams_count, max_teams=:max_teams, player_base=:player_base, "
                      "url=:url, updated_at=:now, last_seen=:now WHERE id=:id", {**values, "id": row["id"]})
            return row["id"]
        cur = self.db.x("INSERT INTO tournaments(provider, ext_id, slug, name, game, state, mode, start_at, teams_count, "
                        "max_teams, player_base, url, first_seen, updated_at, last_seen) VALUES ('fpschallenge', "
                        ":ext, :slug, :name, :game, :state, :mode, :start_at, :teams_count, :max_teams, :player_base, "
                        ":url, :now, :now, :now)", {**values, "ext": ext})
        return cur.lastrowid

    def fetch_detail(self, slug: str) -> None:
        if not slug:
            return
        try:
            d = self.app.http.get_json(f"{API}/tournament/{slug}")
        except FetchError as e:
            L.warning("detail %s: %s", slug, e)
            if e.status == 404:
                self.db.x("UPDATE tournaments SET state='FINISHED', updated_at=? WHERE slug=? AND provider='fpschallenge'",
                          (utcnow(), slug))
            return
        if not isinstance(d, dict) or (d.get("slug") and d["slug"] != slug):
            L.warning("detail %s: unexpected answer (slug %s), ignored", slug, d.get("slug") if isinstance(d, dict) else "?")
            return
        state = "FINISHED" if d.get("isFinished") else str(d.get("state") or "ANNOUNCE").upper()
        maps = ", ".join(m.get("name", "") for m in d.get("mapPool") or [] if isinstance(m, dict))
        self.db.x("UPDATE tournaments SET state=?, reg_start=?, reg_end=?, checkin_start=?, checkin_end=?, "
                  "teams_count=COALESCE(?, teams_count), max_teams=COALESCE(?, max_teams), map_pool=?, "
                  "start_at=COALESCE(?, start_at), detail_at=?, updated_at=? WHERE provider='fpschallenge' AND slug=?",
                  (state, self.to_utc(d.get("registrationStart")), self.to_utc(d.get("registrationEnd")),
                   self.to_utc(d.get("checkinStart")), self.to_utc(d.get("checkinEnd")), d.get("teamsCount"),
                   d.get("maxTeams"), maps, self.to_utc(d.get("start")), utcnow(), utcnow(), slug))

    def fetch_teams(self, trow) -> None:
        try:
            teams = self.app.http.get_json(f"{API}/tournament/{trow['slug']}/teams")
        except FetchError as e:
            L.warning("teams %s: %s", trow["slug"], e)
            return
        if not isinstance(teams, list):
            return
        refresh_h = self.app.cfg.getint("tournaments", "team_refresh_hours", 24)
        fetch_profiles = self.app.cfg.getbool("tournaments", "fetch_teams", True)
        for t in teams:
            if not isinstance(t, dict) or not t.get("id"):
                continue
            members = ", ".join(m.get("username", "") for m in t.get("members") or [] if isinstance(m, dict))
            country = (t.get("flag") or "").rsplit("/", 1)[-1].replace(".png", "")[:3]
            team_id = self.upsert_team(str(t["id"]), t.get("slug"), t.get("name", "?"), None, country, members)
            self.db.x("INSERT OR IGNORE INTO tournament_teams(tournament_id, team_id) VALUES (?, ?)",
                      (trow["id"], team_id))
            row = self.db.one("SELECT updated_at, matches_count FROM teams WHERE id = ?", (team_id,))
            age = self.app.seconds_since(row["updated_at"])
            if fetch_profiles and (row["matches_count"] is None or (age or 0) > refresh_h * 3600):
                self.fetch_team(str(t["id"]))

    def upsert_team(self, ext, slug, name, tag, country, members) -> int:
        row = self.db.one("SELECT id FROM teams WHERE provider='fpschallenge' AND ext_id=?", (ext,))
        if row:
            self.db.x("UPDATE teams SET slug=?, name=?, tag=COALESCE(?, tag), country=COALESCE(?, country), "
                      "members=CASE WHEN ? != '' THEN ? ELSE members END WHERE id=?",
                      (slug, irc.clean(name, 60), tag, country or None, members, members, row["id"]))
            return row["id"]
        cur = self.db.x("INSERT INTO teams(provider, ext_id, slug, name, tag, country, members, updated_at) "
                        "VALUES ('fpschallenge',?,?,?,?,?,?, '2000-01-01 00:00:00')",
                        (ext, slug, irc.clean(name, 60), tag, country or None, members))
        return cur.lastrowid

    def fetch_team(self, ext: str):
        try:
            d = (self.app.http.get_json(f"{API}/teams/{ext}") or {}).get("data")
        except FetchError as e:
            L.warning("team %s: %s", ext, e)
            return None
        if not isinstance(d, dict):
            return None
        team_id = self.upsert_team(ext, d.get("slug"), d.get("name", "?"), d.get("tag"), d.get("country"), "")
        self.db.x("UPDATE teams SET members_count=?, matches_count=?, updated_at=? WHERE id=?",
                  (d.get("membersCount"), d.get("matchesCount"), utcnow(), team_id))
        for lg in d.get("leagues") or []:
            game = ((lg.get("game") or {}).get("slug") or "").lower()
            if game and game != "cod2":
                continue
            region = (lg.get("region") or {}).get("slug", "")
            self.db.x("INSERT INTO ladder_entries(provider, league_id, league_name, region, team_id, rank, elo, wins, "
                      "losses, draws, updated_at) VALUES ('fpschallenge',?,?,?,?,?,?,?,?,?,?) "
                      "ON CONFLICT(provider, league_id, team_id) DO UPDATE SET league_name=excluded.league_name, "
                      "region=excluded.region, rank=excluded.rank, elo=excluded.elo, wins=excluded.wins, "
                      "losses=excluded.losses, draws=excluded.draws, updated_at=excluded.updated_at",
                      (str(lg.get("id")), lg.get("name", "?"), region, team_id, lg.get("rank"), lg.get("elo"),
                       lg.get("wins"), lg.get("losses"), lg.get("draws"), utcnow()))
        return team_id

    def winners_from_news(self) -> None:
        """FPSChallenge publishes '<team> takes the crown at <tournament>!' news."""
        for n in self.db.q("SELECT n.guid, n.url, n.title, n.description, n.published_at FROM news n "
                           "JOIN news_sources s ON s.id = n.source_id WHERE s.type = 'fpschallenge_api' "
                           "AND n.category = 'results' AND n.fetched_at > datetime('now', '-60 days')"):
            for rx in WINNER_RES:
                m = rx.match(n["title"])
                if not m:
                    continue
                tname = m.group("t").strip().rstrip("!")
                runner = RUNNER_UP_RE.search(n["description"] or "")
                winner = m.group("team").strip()
                if runner:
                    winner = f"{winner} (vs {runner.group('team').strip()})"
                cur = self.db.x("UPDATE tournaments SET winner = ?, state = CASE WHEN state IN ('ONGOING', "
                                "'WAITING_FOR_START') THEN 'FINISHED' ELSE state END "
                                "WHERE winner IS NULL AND (name = ? COLLATE NOCASE OR name LIKE ?)",
                                (winner, tname, f"%] {tname}"))
                known = self.db.one("SELECT 1 FROM tournaments WHERE name = ? COLLATE NOCASE OR name LIKE ?",
                                    (tname, f"%] {tname}"))
                if not cur.rowcount and not known:
                    # finished tournament no longer listed by the API: keep it from the official results news
                    now = utcnow()
                    self.db.x("INSERT OR IGNORE INTO tournaments(provider, ext_id, slug, name, state, start_at, url, "
                              "winner, notes, first_seen, updated_at, last_seen, announced_state) VALUES "
                              "('fpschallenge-news', ?, ?, ?, 'FINISHED', ?, ?, ?, 'from results news', ?, ?, ?, "
                              "'FINISHED')", (n["guid"], n["guid"], irc.clean(tname, 120), n["published_at"],
                                              n["url"], winner, n["published_at"] or now, now, now))
                break

    # -- announcements -------------------------------------------------------------
    def announce_changes(self, silent: bool = False) -> None:
        ann = self.app.services["announcer"]
        soon = self.app.cfg.getint("tournaments", "announce_start_minutes", 60)
        reg_on = self.app.cfg.getbool("tournaments", "announce_registration", True)
        for t in self.db.q("SELECT * FROM tournaments WHERE state NOT IN ('FINISHED','CANCELLED')"):
            if t["state"] != t["announced_state"]:
                self.db.x("UPDATE tournaments SET announced_state = ? WHERE id = ?", (t["state"], t["id"]))
                if not silent and (t["state"] in ("ANNOUNCE", "REGISTRATION") and reg_on or t["state"] == "ONGOING"):
                    ann.push("tournaments", self.format_line(t), PRIO_NORMAL, f"tourney:{t['id']}:{t['state']}")
            start = self.app.parse_utc(t["start_at"])
            if (start and not t["reminded"] and t["state"] not in ("ONGOING",)
                    and timedelta(0) < start - datetime.now(timezone.utc) <= timedelta(minutes=soon)):
                self.db.x("UPDATE tournaments SET reminded = 1 WHERE id = ?", (t["id"],))
                if not silent:
                    mins = int((start - datetime.now(timezone.utc)).total_seconds() // 60)
                    ann.push("tournaments", irc.line("TOURNEY", irc.bold(t["name"]),
                                                     irc.warn(self.app.t("tourney.starts_in", self.app.lang, m=mins)),
                                                     irc.url(t["url"]) if t["url"] else ""),
                             PRIO_HIGH, f"tourney:{t['id']}:soon")

    # -- formatting -------------------------------------------------------------------
    def state_badge(self, state: str, lang: str) -> str:
        label, colour = STATE_LABEL.get(state, (state.lower(), irc.GREY))
        return irc.coloured(irc.bold(self.app.t(f"tstate.{state}", lang) if state in STATE_LABEL else label), colour)

    def format_line(self, t, lang: str | None = None, with_url: bool = True) -> str:
        lang = lang or self.app.lang
        teams = ""
        if t["max_teams"]:
            teams = f"{irc.bold(str(t['teams_count'] or 0))}/{t['max_teams']} {self.app.t('tourney.teams', lang)}"
        fmt = f"{t['player_base']}v{t['player_base']}" if t["player_base"] else ""
        start = irc.label(self.app.t("tourney.start", lang), self.app.fmt_dt(t["start_at"])) if t["start_at"] else ""
        winner = irc.label("🏆", irc.good(t["winner"])) if t["winner"] else ""
        if self.app.cfg.getbool("irc", "ascii_only") and winner:
            winner = irc.label("Winner", irc.good(t["winner"]))
        return irc.line("TOURNEY", irc.bold(t["name"]), self.state_badge(t["state"], lang), winner or teams,
                        irc.muted(fmt), start, irc.url(t["url"]) if with_url and t["url"] else "")

    def find(self, text: str):
        if text.isdigit():
            row = self.db.one("SELECT * FROM tournaments WHERE id = ?", (int(text),))
            if row:
                return row
        like = f"%{text}%"
        return self.db.one("SELECT * FROM tournaments WHERE name LIKE ? OR slug LIKE ? "
                           "ORDER BY state IN ('FINISHED','CANCELLED'), start_at DESC LIMIT 1", (like, like))

    def find_team(self, text: str):
        return self.db.one("SELECT * FROM teams WHERE name = ? COLLATE NOCASE OR tag = ? COLLATE NOCASE "
                           "OR slug = ? ORDER BY matches_count DESC LIMIT 1", (text, text, text.lower())) or \
            self.db.one("SELECT * FROM teams WHERE name LIKE ? ORDER BY matches_count DESC LIMIT 1", (f"%{text}%",))


# =============================================================================
# IRC commands
# =============================================================================

def setup(app, scheduler):
    svc = TournamentService(app)
    app.services["tournaments"] = svc
    scheduler.add("tournaments.refresh", svc.refresh, app.cfg.getint("tournaments", "interval", 900), delay=40,
                  enabled=lambda: app.module_enabled("tournaments"))
    reg = app.registry
    maxc = lambda: app.cfg.getint("irc", "max_channel_lines", 4)

    def stale_note(ctx) -> str:
        last = app.db.scalar("SELECT MAX(last_seen) FROM tournaments WHERE provider = 'fpschallenge'")
        age = app.seconds_since(last) if last else None
        if age is None:
            return irc.warn(app.t("tourney.no_data", ctx.lang))
        if age > 3 * 3600:
            return irc.warn(app.t("common.outdated", ctx.lang, ago=irc.ago(age, ctx.lang)))
        return ""

    @command(reg, "tournaments", "tournaments", USER, aliases=("cups",), category="competitive",
             usage="tournaments [upcoming|live|finished|page]")
    def tournaments_cmd(app_, ctx):
        reply = Reply()
        sub = ctx.arg(0).lower()
        where = "state NOT IN ('FINISHED','CANCELLED')"
        order = "start_at"
        if sub in ("finished", "past"):
            where, order = "state = 'FINISHED'", "start_at DESC"
        elif sub in ("live", "ongoing"):
            where = "state = 'ONGOING'"
        elif sub in ("upcoming", "next"):
            where = "state NOT IN ('FINISHED','CANCELLED','ONGOING')"
        rows = app.db.q(f"SELECT * FROM tournaments WHERE {where} ORDER BY {order}")
        note = stale_note(ctx)
        if not rows:
            return reply.say(irc.line("TOURNEY", app.t("tourney.none", ctx.lang), note))
        items, page, pages = irc.paginate(rows, page_arg(sub) or page_arg(ctx.arg(1)) or 1, maxc())
        for t in items:
            reply.say(svc.format_line(t, ctx.lang, with_url=False) + irc.sep() + irc.muted(f"#{t['id']}"))
        if pages > 1 or note:
            reply.tell(irc.line("TOURNEY", irc.muted(f"[{page}/{pages}] {ctx.prefix}tournaments {page + 1 if page < pages else 1}"
                                                     f" · {ctx.prefix}tournament <name|#id>"), note))
        return reply

    @command(reg, "tournament", "tournaments", USER, aliases=("cup",), category="competitive",
             usage="tournament <name|id> | add <dd/mm> <hh:mm> <name> [url] | winner <id> <team> | del <id>")
    def tournament_cmd(app_, ctx):
        reply = Reply()
        sub = ctx.arg(0).lower()
        if not sub:
            return reply.tell(irc.error(f"{ctx.prefix}tournament <name|#id>"))
        if sub in ("add", "winner", "del", "delete"):
            if ctx.level < MODERATOR:
                return reply.tell(irc.error(app.t("perm.denied", ctx.lang)))
            if sub == "add":
                when = app.parse_user_datetime(ctx.arg(1), ctx.arg(2))
                rest = ctx.args[3:]
                url = rest[-1] if rest and valid_url(rest[-1]) else None
                name = irc.clean(" ".join(rest[:-1] if url else rest), 100)
                if not when or len(name) < 3:
                    return reply.tell(irc.error(f"{ctx.prefix}tournament add <dd/mm> <hh:mm> <name> [url]"))
                now = utcnow()
                cur = app.db.x("INSERT INTO tournaments(provider, ext_id, name, state, start_at, url, first_seen, "
                               "updated_at, last_seen, created_by, announced_state) VALUES ('manual', ?, ?, 'MANUAL', "
                               "?, ?, ?, ?, ?, ?, 'MANUAL')",
                               (f"m{int(datetime.now().timestamp())}", name, when.strftime("%Y-%m-%d %H:%M:%S"),
                                url, now, now, now, ctx.nick))
                app.log_admin(ctx, "tournament.add", name, f"#{cur.lastrowid}")
                return reply.say(svc.format_line(app.db.one("SELECT * FROM tournaments WHERE id = ?",
                                                             (cur.lastrowid,)), ctx.lang))
            t = svc.find(ctx.arg(1).lstrip("#"))
            if not t:
                return reply.tell(irc.error(app.t("tourney.not_found", ctx.lang, q=ctx.arg(1)[:40])))
            if sub == "winner":
                team = irc.clean(ctx.rest(2), 60)
                if not team:
                    return reply.tell(irc.error(f"{ctx.prefix}tournament winner <#id> <team>"))
                app.db.x("UPDATE tournaments SET winner = ?, state = 'FINISHED', updated_at = ? WHERE id = ?",
                         (team, utcnow(), t["id"]))
                app.log_admin(ctx, "tournament.winner", f"#{t['id']} {team}", "ok")
                return reply.say(irc.line("RESULT", f"{irc.good(team)} {app.t('tourney.wins', ctx.lang)} "
                                                    f"{irc.bold(t['name'])}"))
            if t["provider"] != "manual":
                return reply.tell(irc.error(app.t("tourney.not_manual", ctx.lang)))
            app.db.x("DELETE FROM tournaments WHERE id = ?", (t["id"],))
            app.log_admin(ctx, "tournament.del", f"#{t['id']} {t['name']}", "ok")
            return reply.tell(irc.line("ADMIN", app.t("common.deleted", ctx.lang)))
        t = svc.find(ctx.rest(0).lstrip("#")[:60])
        if not t:
            return reply.say(irc.line("TOURNEY", app.t("tourney.not_found", ctx.lang, q=irc.bold(ctx.rest(0)[:40]))))
        reply.say(svc.format_line(t, ctx.lang))
        details = []
        if t["reg_end"] and t["state"] in ("ANNOUNCE", "REGISTRATION"):
            details.append(irc.label(app.t("tourney.reg_end", ctx.lang), app.fmt_dt(t["reg_end"])))
        if t["checkin_start"] and t["state"] not in ("ONGOING", "FINISHED"):
            details.append(irc.label("Check-in", f"{app.fmt_dt(t['checkin_start'])}"))
        if t["mode"]:
            details.append(irc.label(app.t("tourney.entry", ctx.lang),
                                     irc.good("FREE") if t["mode"] == "FREE" else irc.warn(t["mode"])))
        if t["map_pool"]:
            details.append(irc.label("Maps", irc.accent(t["map_pool"])))
        if details:
            reply.say(irc.line("TOURNEY", *details))
        return reply

    @command(reg, "teams", "tournaments", USER, category="competitive",
             usage="teams [tournament]")
    def teams_cmd(app_, ctx):
        reply = Reply()
        if ctx.args:
            t = svc.find(ctx.rest(0).lstrip("#")[:60])
        else:
            t = app.db.one("SELECT * FROM tournaments WHERE state NOT IN ('FINISHED','CANCELLED') AND provider = "
                           "'fpschallenge' ORDER BY state = 'ONGOING' DESC, teams_count DESC LIMIT 1")
        if not t:
            return reply.say(irc.line("TOURNEY", app.t("tourney.none", ctx.lang)))
        rows = app.db.q("SELECT tm.* FROM teams tm JOIN tournament_teams tt ON tt.team_id = tm.id "
                        "WHERE tt.tournament_id = ? ORDER BY tm.name COLLATE NOCASE", (t["id"],))
        if not rows:
            return reply.say(irc.line("TOURNEY", irc.bold(t["name"]), app.t("tourney.no_teams", ctx.lang)))
        names = ", ".join(r["name"] + (irc.muted(f" {r['country']}") if r["country"] else "") for r in rows)
        return reply.say(irc.truncate(irc.line("TOURNEY", f"{irc.bold(t['name'])} {irc.muted(f'({len(rows)})')}",
                                               names), app.cfg.getint("irc", "max_line_bytes", 400)))

    @command(reg, "team", "tournaments", USER, category="competitive", usage="team <name|tag>")
    def team_cmd(app_, ctx):
        reply = Reply()
        if not ctx.args:
            return reply.tell(irc.error(f"{ctx.prefix}team <name|tag>"))
        tm = svc.find_team(ctx.rest(0)[:60])
        if not tm:
            # maybe a community clan
            clan = app.db.one("SELECT tag FROM clans WHERE tag = ? OR name LIKE ?", (ctx.rest(0), f"%{ctx.rest(0)}%"))
            hint = app.t("tourney.try_clan", ctx.lang, p=ctx.prefix, tag=clan["tag"]) if clan else ""
            return reply.say(irc.line("TOURNEY", app.t("tourney.team_none", ctx.lang, q=irc.bold(ctx.rest(0)[:40])),
                                      hint))
        if tm["provider"] == "fpschallenge" and (tm["matches_count"] is None or
                                                 (app.seconds_since(tm["updated_at"]) or 0) > 12 * 3600):
            svc.fetch_team(tm["ext_id"])
            tm = app.db.one("SELECT * FROM teams WHERE id = ?", (tm["id"],))
        tag = irc.muted(f"[{tm['tag']}]") if tm["tag"] else ""
        parts = [f"{irc.bold(tm['name'])} {tag}".strip(), (tm["country"] or "").upper()]
        if tm["members_count"] is not None:
            parts.append(irc.label(app.t("tourney.members", ctx.lang), tm["members_count"]))
        if tm["matches_count"] is not None:
            parts.append(irc.label(app.t("tourney.matches", ctx.lang), tm["matches_count"]))
        for lg in app.db.q("SELECT * FROM ladder_entries WHERE team_id = ? ORDER BY elo DESC", (tm["id"],)):
            parts.append(f"{irc.accent(lg['league_name'])} {irc.muted((lg['region'] or '').upper())} "
                         f"#{irc.bold(lg['rank'])} ELO {irc.bold(lg['elo'])} "
                         f"{irc.good(str(lg['wins']))}-{irc.bad(str(lg['losses']))}")
        reply.say(irc.line("TOURNEY", *parts))
        if tm["members"]:
            reply.say(irc.truncate(irc.line("TOURNEY", irc.label(app.t("tourney.roster", ctx.lang),
                                                                 irc.muted(tm["members"]))),
                                   app.cfg.getint("irc", "max_line_bytes", 400)))
        return reply

    @command(reg, "ladders", "tournaments", USER, aliases=("ladder",), category="competitive")
    def ladders_cmd(app_, ctx):
        rows = app.db.q("SELECT league_id, league_name, region, COUNT(*) n, MAX(updated_at) upd FROM ladder_entries "
                        "GROUP BY league_id ORDER BY n DESC")
        if not rows:
            return Reply().say(irc.line("LADDER", app.t("ladder.none", ctx.lang)))
        parts = [f"{irc.accent(r['league_name'])} {irc.muted((r['region'] or '').upper())} "
                 f"{irc.muted('(' + str(r['n']) + ' ' + app.t('tourney.teams', ctx.lang) + ')')}" for r in rows]
        return Reply().say(irc.line("LADDER", *parts, irc.muted(f"{ctx.prefix}standings [league]")))

    @command(reg, "standings", "tournaments", USER, aliases=("ranking",), category="competitive",
             usage="standings [league] [page]")
    def standings_cmd(app_, ctx):
        reply = Reply()
        page = 1
        args = list(ctx.args)
        if args and page_arg(args[-1]):
            page = page_arg(args.pop())
        league = " ".join(args)
        if league:
            lg = app.db.one("SELECT league_id, league_name, region FROM ladder_entries WHERE league_name LIKE ? "
                            "OR league_id = ? LIMIT 1", (f"%{league}%", league))
        else:
            lg = app.db.one("SELECT league_id, league_name, region FROM ladder_entries GROUP BY league_id "
                            "ORDER BY COUNT(*) DESC LIMIT 1")
        if not lg:
            return reply.say(irc.line("LADDER", app.t("ladder.none", ctx.lang)))
        rows = app.db.q("SELECT le.*, t.name FROM ladder_entries le JOIN teams t ON t.id = le.team_id "
                        "WHERE le.league_id = ? ORDER BY le.rank IS NULL, le.rank, le.elo DESC", (lg["league_id"],))
        items, page, pages = irc.paginate(rows, page, 10)
        medal = {1: irc.YELLOW, 2: irc.SILVER, 3: irc.ORANGE}
        parts = []
        for r in items:
            pos = irc.coloured(irc.bold(f"#{r['rank']}"), medal.get(r["rank"], irc.GREY))
            parts.append(f"{pos} {r['name']} {irc.muted(str(r['elo']))}")
        upd = max(r["updated_at"] for r in rows)
        reply.say(irc.line("LADDER", f"{irc.bold(lg['league_name'])} {irc.muted((lg['region'] or '').upper())}",
                           "  ".join(parts)))
        reply.tell(irc.line("LADDER", irc.warn(app.t("ladder.partial", ctx.lang, n=len(rows))),
                            irc.muted(app.t("common.updated", ctx.lang, ago=irc.ago(app.seconds_since(upd) or 0,
                                                                                     ctx.lang))),
                            irc.muted(f"[{page}/{pages}]")))
        return reply

    @command(reg, "results", "tournaments", USER, category="competitive",
             usage="results | add <team1> <s1>-<s2> <team2> [map] | del <id>")
    def results_cmd(app_, ctx):
        reply = Reply()
        sub = ctx.arg(0).lower()
        if sub in ("add", "del", "delete"):
            if ctx.level < MODERATOR:
                return reply.tell(irc.error(app.t("perm.denied", ctx.lang)))
            if sub == "add":
                m = re.match(r"^(\S+)\s+(\d{1,3})\s*[-:]\s*(\d{1,3})\s+(\S+)(?:\s+(\S+))?$", ctx.rest(1))
                if not m:
                    return reply.tell(irc.error(f"{ctx.prefix}results add <team1> <s1>-<s2> <team2> [map]"))
                cur = app.db.x("INSERT INTO matches(team1, score1, score2, team2, map, played_at, source, created_by, "
                               "created_at) VALUES (?,?,?,?,?,?, 'manual', ?, ?)",
                               (m.group(1)[:30], int(m.group(2)), int(m.group(3)), m.group(4)[:30],
                                (m.group(5) or "")[:30] or None, utcnow(), ctx.nick, utcnow()))
                app.log_admin(ctx, "results.add", ctx.rest(1), f"#{cur.lastrowid}")
                return reply.tell(irc.line("ADMIN", app.t("common.saved", ctx.lang) + f" #{cur.lastrowid}"))
            mid = ctx.arg(1).lstrip("#")
            if not mid.isdigit() or not app.db.x("DELETE FROM matches WHERE id = ?", (int(mid),)).rowcount:
                return reply.tell(irc.error(f"{ctx.prefix}results del <#id>"))
            app.log_admin(ctx, "results.del", mid, "ok")
            return reply.tell(irc.line("ADMIN", app.t("common.deleted", ctx.lang)))
        lines = []
        for t in app.db.q("SELECT * FROM tournaments WHERE winner IS NOT NULL ORDER BY updated_at DESC LIMIT 3"):
            lines.append(irc.line("RESULT", f"{irc.good(t['winner'])} {app.t('tourney.wins', ctx.lang)} "
                                            f"{irc.bold(t['name'])}", irc.muted(app.fmt_date(t["start_at"]))))
        for m in app.db.q("SELECT * FROM matches WHERE score1 IS NOT NULL ORDER BY played_at DESC LIMIT 2"):
            lines.append(match_line(m, ctx))
        for w in app.db.q("SELECT * FROM wars WHERE status = 'played' ORDER BY updated_at DESC LIMIT 2"):
            lines.append(app.services["wars"].format_result(w, ctx.lang))
        if not lines:
            return reply.say(irc.line("RESULT", app.t("results.none", ctx.lang)))
        return reply.say(*lines[:maxc()])

    def match_line(m, ctx):
        s1, s2 = m["score1"], m["score2"]
        t1 = irc.good(m["team1"]) if s1 > s2 else (irc.bad(m["team1"]) if s1 < s2 else irc.bold(m["team1"]))
        t2 = irc.good(m["team2"]) if s2 > s1 else (irc.bad(m["team2"]) if s2 < s1 else irc.bold(m["team2"]))
        return irc.line("RESULT", f"{t1} {irc.bold(f'{s1}-{s2}')} {t2}", irc.accent(m["map"]) if m["map"] else "",
                        irc.muted(app.fmt_date(m["played_at"])), irc.muted(f"#{m['id']}"))

    @command(reg, "matches", "tournaments", USER, category="competitive")
    def matches_cmd(app_, ctx):
        reply = Reply()
        wars = app.db.q("SELECT * FROM wars WHERE status = 'scheduled' AND scheduled_at >= datetime('now','-3 hours') "
                        "ORDER BY scheduled_at LIMIT ?", (maxc(),))
        for w in wars:
            reply.say(app.services["wars"].format_war(w, ctx.lang))
        live = app.db.q("SELECT * FROM tournaments WHERE state = 'ONGOING' ORDER BY start_at")
        for t in live[: max(0, maxc() - len(wars))]:
            reply.say(svc.format_line(t, ctx.lang))
        if not reply.chan:
            reply.say(irc.line("WAR", app.t("matches.none", ctx.lang, p=ctx.prefix)))
        reply.tell(irc.line("INFO", irc.muted(app.t("matches.note", ctx.lang))))
        return reply

    return svc
