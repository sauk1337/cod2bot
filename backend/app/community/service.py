"""Community module: clans, players and wars (clan matches)."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from ..core import irc, log
from ..core.announcer import PRIO_HIGH, PRIO_NORMAL
from ..core.commands import MODERATOR, REGISTERED, USER, Reply, command, page_arg, valid_url
from ..core.db import utcnow

L = log.get("community")
TAG_RE = re.compile(r"^[A-Za-z0-9_\-\[\]|.!$*+=^~]{1,12}$")
COUNTRY_RE = re.compile(r"^[A-Za-z]{2}$")
FORMAT_RE = re.compile(r"^([1-9])v([1-9])$", re.I)
CLAN_FIELDS = {"name": 60, "website": 120, "irc": 40, "country": 2, "description": 200, "contact": 60}
PLAYER_FIELDS = {"country": 2, "role": 30, "description": 200}


def identity(ctx) -> str:
    """Stable identity: Q account if the user is +x, else the Eggdrop handle."""
    if ctx.account:
        return "q:" + ctx.account
    if ctx.handle and ctx.handle != "*":
        return "h:" + ctx.handle.lower()
    return ""


class CommunityService:
    def __init__(self, app):
        self.app = app
        self.db = app.db

    # -- clans ---------------------------------------------------------------
    def clan(self, tag: str):
        return self.db.one("SELECT * FROM clans WHERE tag = ?", (tag,))

    def can_edit_clan(self, ctx, clan) -> bool:
        return ctx.level >= MODERATOR or (identity(ctx) and identity(ctx) == clan["owner_account"])

    def war_record(self, tag: str) -> tuple[int, int, int]:
        rows = self.db.q("SELECT * FROM wars WHERE status = 'played' AND (clan1 = ? OR clan2 = ?)", (tag, tag))
        w = sum(1 for r in rows if r["winner"] and r["winner"].lower() == tag.lower())
        d = sum(1 for r in rows if r["winner"] == "draw")
        return w, len(rows) - w - d, d

    def clan_line(self, c, lang) -> str:
        members = self.db.scalar("SELECT COUNT(*) FROM players WHERE clan_id = ?", (c["id"],), 0)
        w, l, d = self.war_record(c["tag"])
        parts = [f"{irc.bold(irc.accent('[' + c['tag'] + ']'))} {irc.bold(c['name'])}",
                 (c["country"] or "").upper(),
                 irc.label(self.app.t("clan.members", lang), members),
                 irc.label("Wars", f"{irc.good(str(w))}-{irc.bad(str(l))}-{d}") if w + l + d else "",
                 irc.info(c["irc"]) if c["irc"] else "",
                 irc.url(c["website"]) if c["website"] else ""]
        return irc.line("CLAN", *parts)

    # -- players -------------------------------------------------------------
    def player(self, nick: str):
        return self.db.one("SELECT p.*, c.tag AS clan_tag FROM players p LEFT JOIN clans c ON c.id = p.clan_id "
                           "WHERE p.nick = ?", (nick,))

    # -- wars ------------------------------------------------------------------
    def format_war(self, w, lang: str | None = None) -> str:
        lang = lang or self.app.lang
        start = self.app.parse_utc(w["scheduled_at"])
        when = self.app.fmt_dt(w["scheduled_at"]) if start else "?"
        rel = ""
        if start:
            delta = (start - datetime.now(timezone.utc)).total_seconds()
            if delta > 0:
                rel = irc.warn(self.app.t("war.in", lang, t=irc.duration(delta)))
            elif delta > -3 * 3600:
                rel = irc.bad(self.app.t("war.live", lang))
        vs = f"{irc.bold(w['clan1'])} {irc.muted('vs')} {irc.bold(w['clan2'])}"
        return irc.line("WAR", f"{irc.muted('#' + str(w['id']))} {vs}", irc.accent(w["format"]), when, rel,
                        irc.label("Maps", w["maps"]) if w["maps"] else "",
                        irc.label("Server", w["server"]) if w["server"] else "")

    def format_result(self, w, lang: str | None = None) -> str:
        lang = lang or self.app.lang
        s1, s2 = w["score1"], w["score2"]
        c1 = irc.good(w["clan1"]) if s1 > s2 else (irc.bad(w["clan1"]) if s1 < s2 else irc.bold(w["clan1"]))
        c2 = irc.good(w["clan2"]) if s2 > s1 else (irc.bad(w["clan2"]) if s2 < s1 else irc.bold(w["clan2"]))
        winner = self.app.t("war.draw", lang) if w["winner"] == "draw" else \
            self.app.t("war.winner", lang, w=irc.good(w["winner"]))
        return irc.line("WAR", f"{irc.muted('#' + str(w['id']))} {c1} {irc.bold(f'{s1}-{s2}')} {c2}",
                        irc.accent(w["format"]), winner, irc.muted(self.app.fmt_date(w["scheduled_at"])))

    def reminders(self) -> None:
        mins = sorted({int(m) for m in self.app.cfg.getlist("announcer", "war_reminders", "60,15") if m.isdigit()},
                      reverse=True)
        ann = self.app.services["announcer"]
        now = datetime.now(timezone.utc)
        for w in self.db.q("SELECT * FROM wars WHERE status = 'scheduled' AND scheduled_at BETWEEN datetime('now') "
                           "AND datetime('now', ?)", (f"+{max(mins or [0]) + 1} minutes",)):
            start = self.app.parse_utc(w["scheduled_at"])
            left = (start - now).total_seconds() / 60
            done = set(filter(None, w["reminded"].split(",")))
            due = [m for m in mins if left <= m and str(m) not in done]
            if not due:
                continue
            done.update(str(m) for m in mins if m >= min(due))
            self.db.x("UPDATE wars SET reminded = ? WHERE id = ?", (",".join(sorted(done)), w["id"]))
            chans = set(ann.channels)
            if w["channel"]:
                chans.add(w["channel"].lower())
            ann.push("wars", self.format_war(w), PRIO_HIGH, f"war:{w['id']}:{min(due)}", channels=sorted(chans))

    def parse_war_args(self, tokens: list[str]) -> dict:
        """<clan1> <clan2> <date> [time] [format] [maps,...] [server=<name>]"""
        out = {"format": "5v5", "maps": "", "server": None}
        if len(tokens) < 3:
            raise ValueError("args")
        out["clan1"], out["clan2"] = tokens[0], tokens[1]
        rest = tokens[2:]
        time_s = rest[1] if len(rest) > 1 and re.match(r"^\d{1,2}([:h]\d{2})?h?$", rest[1], re.I) else ""
        when = self.app.parse_user_datetime(rest[0], time_s)
        if not when:
            raise ValueError("date")
        out["scheduled_at"] = when.strftime("%Y-%m-%d %H:%M:%S")
        for tok in rest[2 if time_s else 1:]:
            if FORMAT_RE.match(tok):
                out["format"] = tok.lower()
            elif tok.lower().startswith("server="):
                out["server"] = tok.split("=", 1)[1][:30]
            else:
                maps = [m for m in tok.split(",") if m]
                names = []
                for m in maps:
                    row = self.app.services["cod2"].find_map(m[:30]) if "cod2" in self.app.services else None
                    names.append(row["title"] if row else m[:20])
                out["maps"] = ", ".join(([out["maps"]] if out["maps"] else []) + names)[:120]
        for key in ("clan1", "clan2"):
            if not TAG_RE.match(out[key]):
                raise ValueError("tag")
        if out["clan1"].lower() == out["clan2"].lower():
            raise ValueError("same")
        return out


# =============================================================================
# IRC commands
# =============================================================================

def setup(app, scheduler):
    svc = CommunityService(app)
    app.services["community"] = svc
    app.services["wars"] = svc
    scheduler.add("wars.reminders", svc.reminders, 60, delay=15, enabled=lambda: app.module_enabled("wars"))
    reg = app.registry
    maxc = lambda: app.cfg.getint("irc", "max_channel_lines", 4)

    def need_identity(ctx, reply) -> bool:
        if not identity(ctx) and ctx.level < REGISTERED:
            reply.tell(irc.error(app.t("community.need_auth", ctx.lang)))
            return False
        return True

    # -- clans -------------------------------------------------------------------
    @command(reg, "clans", "clans", USER, category="community", usage="clans [page]")
    def clans_cmd(app_, ctx):
        rows = app.db.q("SELECT tag, country FROM clans ORDER BY tag COLLATE NOCASE")
        if not rows:
            return Reply().say(irc.line("CLAN", app.t("clan.none", ctx.lang, p=ctx.prefix)))
        items, page, pages = irc.paginate(rows, page_arg(ctx.arg(0)) or 1, 30)
        text = " ".join(irc.accent(r["tag"]) + (irc.muted(f"({r['country'].upper()})") if r["country"] else "")
                        for r in items)
        footer = irc.muted(f"[{page}/{pages}]") if pages > 1 else ""
        return Reply().say(irc.line("CLAN", irc.label(f"{len(rows)} clans", text), footer,
                                    irc.muted(f"{ctx.prefix}clan <tag>")))

    @command(reg, "clan", "clans", USER, category="community",
             usage="clan <tag> | register <tag> <name> | set <tag> <field> <value> | add/kick <tag> <nick> | del <tag>")
    def clan_cmd(app_, ctx):
        reply = Reply()
        sub = ctx.arg(0).lower()
        if not sub:
            return reply.tell(irc.error(f"{ctx.prefix}clan <tag> {irc.muted('· ' + ctx.prefix + 'help community')}"))
        if sub == "register":
            if not need_identity(ctx, reply):
                return reply
            tag, name = ctx.arg(1), irc.clean(ctx.rest(2), 60)
            if not TAG_RE.match(tag) or len(name) < 2:
                return reply.tell(irc.error(f"{ctx.prefix}clan register <tag> <clan name>"))
            if svc.clan(tag):
                return reply.tell(irc.error(app.t("clan.exists", ctx.lang, tag=tag)))
            owned = app.db.scalar("SELECT COUNT(*) FROM clans WHERE owner_account = ?", (identity(ctx),), 0)
            if owned >= 3 and ctx.level < MODERATOR:
                return reply.tell(irc.error(app.t("clan.limit", ctx.lang)))
            now = utcnow()
            app.db.x("INSERT INTO clans(tag, name, owner_account, created_by, created_at, updated_at) "
                     "VALUES (?,?,?,?,?,?)", (tag, name, identity(ctx) or f"h:{ctx.handle}", ctx.nick, now, now))
            app.log_admin(ctx, "clan.register", f"{tag} {name}", "ok")
            reply.say(irc.line("CLAN", app.t("clan.registered", ctx.lang, tag=irc.bold(tag), name=name)))
            return reply.tell(irc.line("CLAN", irc.muted(app.t("clan.set_hint", ctx.lang, p=ctx.prefix, tag=tag))))
        if sub in ("set", "add", "kick", "del", "delete"):
            clan = svc.clan(ctx.arg(1))
            if not clan:
                return reply.tell(irc.error(app.t("clan.not_found", ctx.lang, tag=ctx.arg(1)[:12], p=ctx.prefix)))
            if not svc.can_edit_clan(ctx, clan):
                return reply.tell(irc.error(app.t("clan.not_owner", ctx.lang)))
            if sub == "set":
                field, value = ctx.arg(2).lower(), irc.clean(ctx.rest(3), 200)
                if field not in CLAN_FIELDS:
                    return reply.tell(irc.error(f"{ctx.prefix}clan set <tag> <{'|'.join(CLAN_FIELDS)}> <value>"))
                if field == "website" and value and not valid_url(value):
                    return reply.tell(irc.error("website: http(s)://..."))
                if field == "country" and value and not COUNTRY_RE.match(value):
                    return reply.tell(irc.error("country: 2 letters (es, de, pl...)"))
                if field == "irc" and value and not value.startswith("#"):
                    value = "#" + value
                value = value[:CLAN_FIELDS[field]].lower() if field == "country" else value[:CLAN_FIELDS[field]]
                app.db.x(f"UPDATE clans SET {field} = ?, updated_at = ? WHERE id = ?",  # field is whitelisted
                         (value or None, utcnow(), clan["id"]))
                app.log_admin(ctx, "clan.set", f"{clan['tag']} {field}={value}", "ok")
                return reply.say(svc.clan_line(svc.clan(clan["tag"]), ctx.lang))
            if sub in ("add", "kick"):
                p = svc.player(ctx.arg(2))
                if not p:
                    return reply.tell(irc.error(app.t("player.not_found", ctx.lang, nick=ctx.arg(2)[:30], p=ctx.prefix)))
                app.db.x("UPDATE players SET clan_id = ?, updated_at = ? WHERE id = ?",
                         (clan["id"] if sub == "add" else None, utcnow(), p["id"]))
                app.log_admin(ctx, f"clan.{sub}", f"{clan['tag']} {p['nick']}", "ok")
                return reply.tell(irc.line("CLAN", app.t("common.saved", ctx.lang)))
            app.db.x("DELETE FROM clans WHERE id = ?", (clan["id"],))
            app.log_admin(ctx, "clan.del", clan["tag"], "ok")
            return reply.tell(irc.line("ADMIN", app.t("common.deleted", ctx.lang)))
        clan = svc.clan(ctx.arg(0)) or app.db.one("SELECT * FROM clans WHERE name LIKE ? LIMIT 1",
                                                  (f"%{ctx.rest(0)[:40]}%",))
        if not clan:
            return reply.say(irc.line("CLAN", app.t("clan.not_found", ctx.lang, tag=irc.bold(ctx.arg(0)[:12]),
                                                    p=ctx.prefix)))
        reply.say(svc.clan_line(clan, ctx.lang))
        extra = []
        if clan["description"]:
            extra.append(irc.muted(clan["description"]))
        members = [r["nick"] for r in app.db.q("SELECT nick FROM players WHERE clan_id = ? ORDER BY nick", (clan["id"],))]
        if members:
            extra.append(irc.label(app.t("clan.members", ctx.lang), ", ".join(members)))
        if clan["contact"]:
            extra.append(irc.label(app.t("clan.contact", ctx.lang), clan["contact"]))
        if extra:
            reply.say(irc.line("CLAN", *extra))
        return reply

    # -- players -------------------------------------------------------------------
    @command(reg, "player", "players", USER, aliases=("profile",), category="community",
             usage="player <nick> | register [country] | set <country|role|description> <value> | del <nick>")
    def player_cmd(app_, ctx):
        reply = Reply()
        sub = ctx.arg(0).lower()
        if sub == "register":
            if not need_identity(ctx, reply):
                return reply
            if svc.player(ctx.nick):
                return reply.tell(irc.error(app.t("player.exists", ctx.lang, p=ctx.prefix)))
            if identity(ctx).startswith("q:") and app.db.one("SELECT 1 FROM players WHERE account = ?", (ctx.account,)):
                return reply.tell(irc.error(app.t("player.account_used", ctx.lang)))
            country = ctx.arg(1).lower() if COUNTRY_RE.match(ctx.arg(1) or "") else None
            now = utcnow()
            app.db.x("INSERT INTO players(nick, account, country, created_at, updated_at) VALUES (?,?,?,?,?)",
                     (ctx.nick[:30], ctx.account or None, country, now, now))
            return reply.say(irc.line("PLAYER", app.t("player.registered", ctx.lang, nick=irc.bold(ctx.nick))),
                             ).tell(irc.line("PLAYER", irc.muted(app.t("player.set_hint", ctx.lang, p=ctx.prefix))))
        if sub == "set":
            p = svc.player(ctx.nick)
            owner = p and (ctx.level >= MODERATOR or (p["account"] and p["account"] == ctx.account) or
                           (not p["account"] and ctx.level >= REGISTERED))
            if not p or not owner:
                return reply.tell(irc.error(app.t("player.not_yours", ctx.lang, p=ctx.prefix)))
            field, value = ctx.arg(1).lower(), irc.clean(ctx.rest(2), 200)
            if field not in PLAYER_FIELDS:
                return reply.tell(irc.error(f"{ctx.prefix}player set <{'|'.join(PLAYER_FIELDS)}> <value>"))
            if field == "country" and value and not COUNTRY_RE.match(value):
                return reply.tell(irc.error("country: 2 letters (es, de, pl...)"))
            app.db.x(f"UPDATE players SET {field} = ?, updated_at = ? WHERE id = ?",  # whitelisted
                     ((value.lower() if field == "country" else value[:PLAYER_FIELDS[field]]) or None, utcnow(), p["id"]))
            return reply.tell(irc.line("PLAYER", app.t("common.saved", ctx.lang)))
        if sub in ("del", "delete"):
            if ctx.level < MODERATOR:
                return reply.tell(irc.error(app.t("perm.denied", ctx.lang)))
            if not app.db.x("DELETE FROM players WHERE nick = ?", (ctx.arg(1),)).rowcount:
                return reply.tell(irc.error(app.t("player.not_found", ctx.lang, nick=ctx.arg(1)[:30], p=ctx.prefix)))
            app.log_admin(ctx, "player.del", ctx.arg(1), "ok")
            return reply.tell(irc.line("ADMIN", app.t("common.deleted", ctx.lang)))
        nick = ctx.arg(0) or ctx.nick
        p = svc.player(nick)
        if not p:
            return reply.say(irc.line("PLAYER", app.t("player.not_found", ctx.lang, nick=irc.bold(nick[:30]),
                                                      p=ctx.prefix)))
        parts = [irc.bold(p["nick"]) + (f" {irc.accent('[' + p['clan_tag'] + ']')}" if p["clan_tag"] else ""),
                 (p["country"] or "").upper(), irc.info(p["role"]) if p["role"] else "",
                 irc.good("✔ Q") if p["account"] else "",
                 irc.muted(app.t("player.since", ctx.lang, d=app.fmt_date(p["created_at"])))]
        if p["description"]:
            parts.append(irc.muted(p["description"]))
        # is the player on a public server right now? (master browser cache)
        srv = app.services.get("servers")
        if srv and srv.browser:
            needle = p["nick"].lower()
            for s in srv.browser:
                if any(needle == irc.strip_cod(pl.name).lower() for pl in s.players):
                    parts.append(irc.good(app.t("player.playing", ctx.lang)) + " " +
                                 irc.cod_colours(irc.clean(s.hostname, 40)) + irc.R)
                    break
        return reply.say(irc.line("PLAYER", *parts))

    # -- wars ----------------------------------------------------------------------
    @command(reg, "war", "wars", USER, aliases=("wars",), category="community",
             usage="war create <clan1> <clan2> <dd/mm> <hh:mm> [5v5] [map1,map2] [server=name] | list | upcoming | "
                   "result <id> <s1>-<s2> | cancel <id> | history <clan> | info <id> | <clan1> <clan2>")
    def war_cmd(app_, ctx):
        reply = Reply()
        sub = ctx.arg(0).lower()
        if sub in ("create", "new", "add"):
            if not need_identity(ctx, reply):
                return reply
            try:
                w = svc.parse_war_args(ctx.args[1:])
            except ValueError as e:
                msg = {"date": app.t("war.bad_date", ctx.lang), "same": app.t("war.same", ctx.lang)}.get(str(e), "")
                return reply.tell(irc.error(msg or f"{ctx.prefix}war create <clan1> <clan2> <dd/mm> <hh:mm> "
                                                   f"[5v5] [toujane,burgundy] [server=name]"))
            if app.parse_utc(w["scheduled_at"]) < datetime.now(timezone.utc) - timedelta(minutes=5):
                return reply.tell(irc.error(app.t("war.past", ctx.lang)))
            pending = app.db.scalar("SELECT COUNT(*) FROM wars WHERE created_by = ? AND status = 'scheduled'",
                                    (ctx.nick,), 0)
            if pending >= 5 and ctx.level < MODERATOR:
                return reply.tell(irc.error(app.t("war.limit", ctx.lang)))
            now = utcnow()
            cur = app.db.x("INSERT INTO wars(clan1, clan2, format, scheduled_at, server, maps, channel, created_by, "
                           "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                           (w["clan1"], w["clan2"], w["format"], w["scheduled_at"], w["server"], w["maps"],
                            ctx.chan if not ctx.private else None, identity(ctx) or ctx.nick, now, now))
            war = app.db.one("SELECT * FROM wars WHERE id = ?", (cur.lastrowid,))
            app.log_admin(ctx, "war.create", ctx.rest(1), f"#{war['id']}")
            app.services["announcer"].push("wars", svc.format_war(war), PRIO_NORMAL, f"war:{war['id']}:new",
                                           channels=[c for c in app.services["announcer"].channels
                                                     if c.lower() != ctx.chan.lower()])
            return reply.say(svc.format_war(war, ctx.lang))
        if sub in ("list", "upcoming", "next", ""):
            only_upcoming = sub != "list"
            rows = app.db.q("SELECT * FROM wars WHERE status = 'scheduled' AND scheduled_at >= datetime('now', '-3 hours') "
                            "ORDER BY scheduled_at LIMIT ?", (maxc(),))
            for w in rows:
                reply.say(svc.format_war(w, ctx.lang))
            if not only_upcoming:
                for w in app.db.q("SELECT * FROM wars WHERE status = 'played' ORDER BY updated_at DESC LIMIT ?",
                                  (max(1, maxc() - len(rows)),)):
                    reply.say(svc.format_result(w, ctx.lang))
            if not reply.chan:
                reply.say(irc.line("WAR", app.t("war.none", ctx.lang, p=ctx.prefix)))
            return reply
        if sub in ("result", "cancel", "info", "del"):
            wid = ctx.arg(1).lstrip("#")
            war = app.db.one("SELECT * FROM wars WHERE id = ?", (int(wid),)) if wid.isdigit() else None
            if not war:
                return reply.tell(irc.error(app.t("war.not_found", ctx.lang)))
            if sub == "info":
                return reply.say(svc.format_result(war, ctx.lang) if war["status"] == "played" else svc.format_war(war, ctx.lang))
            is_owner = war["created_by"] in (identity(ctx), ctx.nick) and (identity(ctx) or ctx.level >= REGISTERED)
            if not (is_owner or ctx.level >= MODERATOR):
                return reply.tell(irc.error(app.t("war.not_owner", ctx.lang)))
            if sub == "result":
                m = re.match(r"^(\d{1,3})\s*[-:]\s*(\d{1,3})$", ctx.rest(2).strip())
                if not m:
                    return reply.tell(irc.error(f"{ctx.prefix}war result <#id> <score1>-<score2>"))
                s1, s2 = int(m.group(1)), int(m.group(2))
                winner = war["clan1"] if s1 > s2 else (war["clan2"] if s2 > s1 else "draw")
                app.db.x("UPDATE wars SET status='played', score1=?, score2=?, winner=?, updated_at=? WHERE id=?",
                         (s1, s2, winner, utcnow(), war["id"]))
                app.log_admin(ctx, "war.result", f"#{war['id']} {s1}-{s2}", winner)
                war = app.db.one("SELECT * FROM wars WHERE id = ?", (war["id"],))
                app.services["announcer"].push("wars", svc.format_result(war), PRIO_NORMAL, f"war:{war['id']}:result",
                                               channels=[c for c in app.services["announcer"].channels
                                                         if c.lower() != ctx.chan.lower()])
                return reply.say(svc.format_result(war, ctx.lang))
            app.db.x("UPDATE wars SET status='cancelled', updated_at=? WHERE id=?", (utcnow(), war["id"]))
            app.log_admin(ctx, "war.cancel", f"#{war['id']}", "ok")
            return reply.say(irc.line("WAR", irc.muted(f"#{war['id']}"), irc.bad(app.t("war.cancelled", ctx.lang))))
        if sub in ("history",):
            tag = ctx.arg(1)
            if not tag:
                return reply.tell(irc.error(f"{ctx.prefix}war history <clan>"))
            rows = app.db.q("SELECT * FROM wars WHERE status = 'played' AND (clan1 = ? OR clan2 = ?) "
                            "ORDER BY scheduled_at DESC LIMIT ?", (tag, tag, maxc()))
            if not rows:
                return reply.say(irc.line("WAR", app.t("war.no_history", ctx.lang, tag=irc.bold(tag[:12]))))
            w, l, d = svc.war_record(tag)
            reply.say(irc.line("WAR", irc.bold(tag), f"{irc.good(str(w))}W {irc.bad(str(l))}L {d}D"))
            for r in rows[: maxc() - 1]:
                reply.say(svc.format_result(r, ctx.lang))
            return reply
        # !war <clan1> <clan2> : head to head
        if len(ctx.args) == 2:
            a, b = ctx.args
            rows = app.db.q("SELECT * FROM wars WHERE status = 'played' AND ((clan1 = ? AND clan2 = ?) OR "
                            "(clan1 = ? AND clan2 = ?)) ORDER BY scheduled_at DESC", (a, b, b, a))
            wa = sum(1 for r in rows if r["winner"].lower() == a.lower())
            wb = sum(1 for r in rows if r["winner"].lower() == b.lower())
            reply.say(irc.line("WAR", f"{irc.bold(a)} {irc.good(str(wa))} - {irc.good(str(wb))} {irc.bold(b)}",
                               irc.muted(app.t("war.h2h", ctx.lang, n=len(rows)))))
            for r in rows[:2]:
                reply.say(svc.format_result(r, ctx.lang))
            return reply
        return reply.tell(irc.error(f"{ctx.prefix}war create|list|upcoming|result|history|info  "
                                    f"{irc.muted(ctx.prefix + 'help community')}"))

    return svc
