"""News module: sources, polling, storage, classification, announcements, commands."""
from __future__ import annotations

import json
import re
import time
from datetime import timezone

from ..core import irc, log
from ..core.announcer import PRIO_NORMAL
from ..core.commands import ADMIN, MODERATOR, USER, Reply, command, page_arg, valid_name, valid_url
from ..core.db import utcnow
from ..core.http import FetchError
from . import parsers

L = log.get("news")

DEFAULT_CATEGORIES = {
    "results": "takes the crown,conquers,champion,champions,winner,wins,won,victorious,grand final,mvp,recap",
    "tournaments": "tournament,cup,nightcup,mixcup,league,season,playoff,qualifier,registration,signups,bracket,challenge",
    "lan": "lan,offline event,arena",
    "ladders": "ladder,elo,ranking,rankings,standings",
    "interviews": "interview,talks,q&a",
    "tools": "release,patch,update,version,zpam,cod2x,anticheat,anti-cheat,launcher,fix",
    "mods": "mod,modding,gametype,addon,promod",
    "maps": "map,mp_,remake,mappack",
    "servers": "server,hosting,dedicated",
    "community": "community,meet-up,meetup,clan,video,youtube",
}


class NewsService:
    def __init__(self, app):
        self.app = app
        self.db = app.db
        self.seed_categories()
        self.sync_sources()

    # -- setup -----------------------------------------------------------
    def seed_categories(self) -> None:
        for name, kw in DEFAULT_CATEGORIES.items():
            self.db.x("INSERT OR IGNORE INTO news_categories(name, keywords) VALUES (?, ?)", (name, kw))

    def sync_sources(self) -> None:
        """Insert/update the sources of config/news_sources.ini (config wins over
        the database for config sources; IRC-added sources are kept)."""
        cfg = self.app.cfg.sources
        default_interval = self.app.cfg.getint("news", "default_interval", 1800)
        names = []
        for name in cfg.sections():
            s = cfg[name]
            options = {k: v for k, v in s.items()
                       if k not in ("type", "label", "url", "interval", "announce", "category", "colour", "enabled")}
            values = (
                s.get("label", name), s.get("type", "feed"), s.get("url", ""),
                1 if s.getboolean("enabled", True) else 0,
                1 if s.getboolean("announce", True) else 0,
                max(300, s.getint("interval", default_interval)),
                s.get("category", "general"), s.getint("colour", 7), json.dumps(options),
            )
            if not values[2]:
                L.warning("source %s has no url, skipped", name)
                continue
            names.append(name.lower())
            exists = self.db.one("SELECT id FROM news_sources WHERE name = ?", (name,))
            if exists:
                self.db.x("UPDATE news_sources SET label=?, type=?, url=?, enabled=?, announce=?, interval=?, "
                          "category=?, colour=?, options=?, origin='config' WHERE id=?", (*values, exists["id"]))
            else:
                self.db.x("INSERT INTO news_sources(label, type, url, enabled, announce, interval, category, colour, "
                          "options, name, origin) VALUES (?,?,?,?,?,?,?,?,?,?, 'config')", (*values, name))
        # config sources removed from the ini file are disabled (history kept)
        for row in self.db.q("SELECT id, name FROM news_sources WHERE origin = 'config'"):
            if row["name"].lower() not in names:
                self.db.x("UPDATE news_sources SET enabled = 0 WHERE id = ?", (row["id"],))

    # -- classification ----------------------------------------------------
    def classify(self, item: parsers.NewsItem, default: str) -> str:
        text = f" {item.title} {' '.join(item.tags)} ".lower()
        for row in self.db.q("SELECT name, keywords FROM news_categories ORDER BY id"):
            for kw in row["keywords"].split(","):
                kw = kw.strip().lower()
                if kw and re.search(r"(?<![a-z0-9])" + re.escape(kw), text):
                    return row["name"]
        return default

    # -- polling -------------------------------------------------------------
    def poll_due(self) -> None:
        now = time.time()
        for src in self.db.q("SELECT * FROM news_sources WHERE enabled = 1 AND next_fetch <= ? ORDER BY next_fetch",
                             (now,)):
            self.fetch_source(src)

    def fetch_source(self, src) -> tuple[int, int]:
        """Fetch one source. Returns (new items, updated items)."""
        opts = json.loads(src["options"] or "{}")
        try:
            items = self._download(src, opts)
        except (FetchError, parsers.ParseError) as e:
            errors = src["error_count"] + 1
            # back off: interval * 2^errors, max 12h
            delay = min(12 * 3600, src["interval"] * (2 ** min(errors, 5)))
            if getattr(e, "status", 0) == 429:
                delay = max(delay, 3600)
            self.db.x("UPDATE news_sources SET last_fetch=?, last_error=?, error_count=?, next_fetch=? WHERE id=?",
                      (utcnow(), str(e)[:300], errors, time.time() + delay, src["id"]))
            L.warning("source %s failed (%d): %s - next try in %ds", src["name"], errors, e, delay)
            return 0, 0
        if items is None:  # 304 not modified
            self.db.x("UPDATE news_sources SET last_fetch=?, last_ok=?, error_count=0, last_error=NULL, next_fetch=? "
                      "WHERE id=?", (utcnow(), utcnow(), time.time() + src["interval"], src["id"]))
            return 0, 0
        new, updated = self.store(src, items)
        self.db.x("UPDATE news_sources SET last_fetch=?, last_ok=?, error_count=0, last_error=NULL, next_fetch=?, "
                  "initialized=1 WHERE id=?", (utcnow(), utcnow(), time.time() + src["interval"], src["id"]))
        L.info("source %s: %d items, %d new, %d updated", src["name"], len(items), new, updated)
        return new, updated

    def _download(self, src, opts) -> list | None:
        http = self.app.http
        stype = src["type"]
        if stype == "fpschallenge_api":
            cats = [c.strip() for c in opts.get("categories", "").split(",") if c.strip()]
            template = opts.get("link_template", "https://fpschallenge.eu/news/{slug}")
            pages = 1 if src["initialized"] else max(1, min(10, int(opts.get("initial_pages", 3))))
            tz = self.app.services["tournaments"].provider_tz if "tournaments" in self.app.services else timezone.utc
            items = []
            for page in range(1, pages + 1):
                sep = "&" if "?" in src["url"] else "?"
                data = http.get_json(f"{src['url']}{sep}page={page}" if page > 1 else src["url"])
                items.extend(parsers.parse_fpschallenge(data, template, cats, tz))
                if page >= int(data.get("lastPage") or 1):
                    break
            return items
        accept = ("application/vnd.github+json" if stype == "github_releases" else
                  "application/rss+xml, application/atom+xml, application/xml, text/xml, text/html;q=0.5")
        resp = http.get(src["url"], etag=src["etag"], last_modified=src["last_modified"], accept=accept)
        if resp.not_modified:
            return None
        self.db.x("UPDATE news_sources SET etag=?, last_modified=? WHERE id=?",
                  (resp.headers.get("etag"), resp.headers.get("last-modified"), src["id"]))
        if stype == "github_releases":
            try:
                return parsers.parse_github_releases(resp.json())
            except ValueError as e:
                raise parsers.ParseError(f"invalid JSON: {e}") from e
        if stype == "html":
            pattern = opts.get("item_pattern", "")
            if not pattern:
                raise parsers.ParseError("html source without item_pattern")
            return parsers.parse_html(resp.text, pattern, opts.get("base_url", src["url"]))
        return parsers.parse_feed(resp.body, src["url"])

    def store(self, src, items: list) -> tuple[int, int]:
        new = updated = 0
        initialized = bool(src["initialized"])
        max_age = self.app.cfg.getint("news", "announce_max_age_hours", 48) * 3600
        per_run = self.app.cfg.getint("news", "announce_per_run", 2)
        to_announce = []
        for it in items:
            row = self.db.one("SELECT id, content_hash, title FROM news WHERE source_id = ? AND guid = ?",
                              (src["id"], it.guid))
            if row is None:
                # also dedupe by URL across sources (same article in two feeds)
                if self.db.one("SELECT 1 FROM news WHERE url = ?", (it.url,)):
                    continue
                cat = self.classify(it, src["category"])
                age = self.app.seconds_since(it.published) if it.published else 0
                fresh = initialized and src["announce"] and (age is None or age <= max_age)
                cur = self.db.x(
                    "INSERT INTO news(source_id, guid, url, title, description, image, category, published_at, "
                    "fetched_at, content_hash, announced) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (src["id"], it.guid, it.url, irc.clean(it.title, 300), irc.clean(it.description, 600), it.image,
                     cat, it.published or utcnow(), utcnow(), it.content_hash, 0 if fresh else 1))
                new += 1
                if fresh:
                    to_announce.append(cur.lastrowid)
            elif row["content_hash"] != it.content_hash:
                self.db.x("UPDATE news SET title=?, description=?, image=COALESCE(?, image), content_hash=?, "
                          "updated_at=?, changes=changes+1 WHERE id=?",
                          (irc.clean(it.title, 300), irc.clean(it.description, 600), it.image, it.content_hash,
                           utcnow(), row["id"]))
                updated += 1
        # oldest first, limited per run; the rest is kept silent
        for i, news_id in enumerate(reversed(to_announce)):
            if i < per_run:
                self.announce(news_id)
            else:
                self.db.x("UPDATE news SET announced = 1 WHERE id = ?", (news_id,))
        return new, updated

    def announce(self, news_id: int, force: bool = False) -> int:
        row = self.db.one("SELECT n.*, s.label, s.colour FROM news n JOIN news_sources s ON s.id = n.source_id "
                          "WHERE n.id = ?", (news_id,))
        if not row:
            return 0
        self.db.x("UPDATE news SET announced = 1 WHERE id = ?", (news_id,))
        return self.app.services["announcer"].push("news", self.format_item(row, announce=True), PRIO_NORMAL,
                                                   f"news:{news_id}", force=force)

    # -- formatting ------------------------------------------------------------
    def format_item(self, row, announce: bool = False, lang: str | None = None, show_id: bool = False) -> str:
        lang = lang or self.app.lang
        max_bytes = self.app.cfg.getint("irc", "max_line_bytes", 400)
        source = irc.bold(irc.coloured(row["label"], row["colour"]))
        cat = irc.muted(row["category"])
        when = ""
        if not announce:
            secs = self.app.seconds_since(row["published_at"])
            when = irc.muted(irc.ago(secs, lang)) if secs is not None else ""
        ident = irc.muted(f"#{row['id']}") if show_id else ""
        link = irc.url(row["url"])
        head = f"{irc.badge('NEWS')} {ident + ' ' if ident else ''}{source}{irc.arrow()}"
        tail = irc.sep().join(p for p in (cat, when) if p)
        fixed = len(f"{head}{irc.sep()}{tail}{irc.sep()}{link}".encode("utf-8"))
        title = irc.truncate(irc.bold(row["title"]), max(40, max_bytes - fixed))
        return f"{head}{title}{irc.sep()}{tail}{irc.sep()}{link}"

    # -- queries -----------------------------------------------------------------
    def latest(self, limit: int = 5, offset: int = 0, source: str | None = None, category: str | None = None):
        sql = ("SELECT n.*, s.label, s.colour, s.name AS source_name FROM news n "
               "JOIN news_sources s ON s.id = n.source_id WHERE 1=1")
        params: list = []
        if source:
            sql += " AND s.name = ?"
            params.append(source)
        if category:
            sql += " AND n.category = ?"
            params.append(category)
        sql += " ORDER BY n.published_at DESC, n.id DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        return self.db.q(sql, params)

    def search(self, text: str, limit: int = 5):
        like = "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        return self.db.q("SELECT n.*, s.label, s.colour FROM news n JOIN news_sources s ON s.id = n.source_id "
                         "WHERE n.title LIKE ? ESCAPE '\\' OR n.description LIKE ? ESCAPE '\\' "
                         "ORDER BY n.published_at DESC LIMIT ?", (like, like, limit))

    def add_source(self, url: str, name: str | None, ctx) -> str:
        """Add a source from IRC: detects RSS/Atom (or FPSChallenge-style JSON) by fetching it."""
        resp = self.app.http.get(url, accept="application/rss+xml, application/atom+xml, application/xml, */*")
        kind = parsers.detect_feed(resp.body)
        if not kind:
            raise parsers.ParseError("no RSS/Atom feed found at that URL (HTML sites must be added in "
                                     "config/news_sources.ini with an item_pattern)")
        items = parsers.parse_feed(resp.body, url)
        if not items:
            raise parsers.ParseError("the feed has no items")
        if not name:
            host = re.sub(r"^www\.", "", url.split("/")[2])
            name = re.sub(r"[^a-z0-9\-]", "-", host.split(".")[0].lower())[:20]
        if self.db.one("SELECT 1 FROM news_sources WHERE name = ?", (name,)):
            raise ValueError(f"a source named '{name}' already exists")
        cur = self.db.x("INSERT INTO news_sources(name, label, type, url, interval, category, origin) "
                        "VALUES (?, ?, ?, ?, ?, 'general', ?)",
                        (name, name, kind, url, self.app.cfg.getint("news", "default_interval", 1800),
                         f"irc:{ctx.nick}"))
        src = self.db.one("SELECT * FROM news_sources WHERE id = ?", (cur.lastrowid,))
        self.store(src, items)  # initial import: not announced
        self.db.x("UPDATE news_sources SET initialized = 1, last_ok = ?, next_fetch = ? WHERE id = ?",
                  (utcnow(), time.time() + src["interval"], src["id"]))
        return f"{name} ({kind}, {len(items)} items)"


# =============================================================================
# IRC commands
# =============================================================================

def setup(app, scheduler):
    svc = NewsService(app)
    app.services["news"] = svc
    scheduler.add("news.poll", svc.poll_due, 60, delay=10, enabled=lambda: app.module_enabled("news"))
    reg = app.registry

    def listing(ctx, rows, reply: Reply, title: str, page: int = 1, more_cmd: str = "") -> Reply:
        if not rows:
            return reply.say(irc.line("NEWS", app.t("news.none", ctx.lang)))
        for r in rows:
            reply.say(svc.format_item(r, lang=ctx.lang, show_id=ctx.level >= MODERATOR))
        return reply

    @command(reg, "news", "news", USER, category="news", usage="news [latest|N|source|search <text>|sources|categories]")
    def news_cmd(app_, ctx):
        reply = Reply()
        size = app.cfg.getint("irc", "page_size", 5)
        sub = ctx.arg(0).lower()
        maxc = app.cfg.getint("irc", "max_channel_lines", 4)
        if sub in ("", "list"):
            return listing(ctx, svc.latest(min(3, maxc)), reply, "latest")
        if sub in ("latest", "last"):
            rows = svc.latest(1)
            if not rows:
                return reply.say(irc.line("NEWS", app.t("news.none", ctx.lang)))
            r = rows[0]
            reply.say(svc.format_item(r, lang=ctx.lang))
            if r["description"]:
                reply.say(irc.line("NEWS", irc.truncate(irc.muted(r["description"]),
                                                        app.cfg.getint("irc", "max_line_bytes", 400) - 30)))
            return reply
        n = page_arg(sub)
        if n:
            n = min(n, 10)
            rows = svc.latest(n)
            if n > maxc:  # long listings go by notice
                for r in rows:
                    reply.tell(svc.format_item(r, lang=ctx.lang))
                return reply
            return listing(ctx, rows, reply, "latest")
        if sub in ("search", "find"):
            text = ctx.rest(1).strip()
            if len(text) < 3:
                return reply.tell(irc.error(app.t("news.search_short", ctx.lang)))
            rows = svc.search(text[:60], maxc)
            if not rows:
                return reply.say(irc.line("NEWS", app.t("news.search_none", ctx.lang, q=irc.bold(text[:60]))))
            return listing(ctx, rows, reply, "search")
        if sub in ("sources",):
            rows = app.db.q("SELECT * FROM news_sources ORDER BY enabled DESC, name")
            parts = []
            for s in rows:
                state = irc.good("●") if s["enabled"] and not s["error_count"] else (
                    irc.warn("●") if s["enabled"] else irc.muted("○"))
                parts.append(f"{state} {irc.coloured(s['name'], s['colour'])}")
            reply.say(irc.line("NEWS", irc.label(app.t("news.sources", ctx.lang), " ".join(parts))))
            if ctx.level >= MODERATOR:
                for s in rows:
                    if s["last_error"]:
                        reply.tell(irc.line("NEWS", f"{irc.bold(s['name'])}: {irc.bad('error')} "
                                                    f"{irc.clean(s['last_error'], 160)}"))
            return reply
        if sub in ("categories",):
            rows = app.db.q("SELECT n.category, COUNT(*) c FROM news n GROUP BY n.category ORDER BY c DESC")
            text = " ".join(f"{irc.accent(r['category'])}{irc.muted('(' + str(r['c']) + ')')}" for r in rows)
            return reply.say(irc.line("NEWS", irc.label(app.t("news.categories", ctx.lang), text or "-")))
        if sub in ("category", "cat"):
            cat = ctx.arg(1).lower()
            return listing(ctx, svc.latest(maxc, category=cat), reply, "category")
        if sub == "help":
            return reply.tell(*app.services["help"].topic("news", ctx))
        # admin subcommands
        if sub in ("addsource", "removesource", "check", "publish", "enable", "disable"):
            return news_admin(ctx, sub, reply)
        # !news <source> [page]
        src = app.db.one("SELECT * FROM news_sources WHERE name = ?", (sub,))
        if src:
            page = page_arg(ctx.arg(1)) or 1
            rows = svc.latest(min(size, maxc), (page - 1) * min(size, maxc), source=src["name"])
            return listing(ctx, rows, reply, "source")
        return reply.tell(irc.error(app.t("news.unknown", ctx.lang, arg=irc.bold(sub[:30]), p=ctx.prefix)))

    def news_admin(ctx, sub, reply):
        need = ADMIN if sub in ("addsource", "removesource", "enable", "disable") else MODERATOR
        if ctx.level < need:
            return reply.tell(irc.error(app.t("perm.denied", ctx.lang)))
        if sub == "addsource":
            url = ctx.arg(1)
            name = ctx.arg(2).lower() or None
            if not valid_url(url) or (name and not valid_name(name, 20)):
                return reply.tell(irc.error(f"{ctx.prefix}news addsource <url> [name]"))
            try:
                result = svc.add_source(url, name, ctx)
            except (FetchError, parsers.ParseError, ValueError) as e:
                app.log_admin(ctx, "news.addsource", url, f"failed: {e}")
                return reply.tell(irc.error(irc.clean(str(e), 200)))
            app.log_admin(ctx, "news.addsource", url, result)
            return reply.say(irc.line("ADMIN", app.t("news.added", ctx.lang, name=irc.bold(result))))
        if sub in ("removesource", "disable", "enable"):
            name = ctx.arg(1).lower()
            src = app.db.one("SELECT * FROM news_sources WHERE name = ?", (name,))
            if not src:
                return reply.tell(irc.error(app.t("news.nosource", ctx.lang, name=name[:30])))
            if sub == "removesource" and src["origin"] != "config":
                app.db.x("DELETE FROM news_sources WHERE id = ?", (src["id"],))
                msg = app.t("news.removed", ctx.lang, name=irc.bold(name))
            else:
                # config sources can only be disabled (re-enabled by !news enable or the ini file)
                app.db.x("UPDATE news_sources SET enabled = ? WHERE id = ?", (1 if sub == "enable" else 0, src["id"]))
                msg = app.t("news.enabled" if sub == "enable" else "news.disabled", ctx.lang, name=irc.bold(name))
            app.log_admin(ctx, f"news.{sub}", name, "ok")
            return reply.say(irc.line("ADMIN", msg))
        if sub == "check":
            name = ctx.arg(1).lower()
            rows = app.db.q("SELECT * FROM news_sources WHERE enabled = 1" + (" AND name = ?" if name else ""),
                            (name,) if name else ())
            if not rows:
                return reply.tell(irc.error(app.t("news.nosource", ctx.lang, name=name[:30])))
            total_new = total_upd = 0
            for src in rows:
                n, u = svc.fetch_source(src)
                total_new += n
                total_upd += u
            app.log_admin(ctx, "news.check", name or "all", f"{total_new} new")
            return reply.say(irc.line("ADMIN", app.t("news.checked", ctx.lang, n=len(rows), new=irc.bold(total_new),
                                                     upd=total_upd)))
        if sub == "publish":
            target = ctx.arg(1).lstrip("#")
            if target.isdigit():
                row = app.db.one("SELECT id FROM news WHERE id = ?", (int(target),))
            else:
                row = app.db.one("SELECT id FROM news ORDER BY published_at DESC LIMIT 1")
            if not row:
                return reply.tell(irc.error(app.t("news.none", ctx.lang)))
            app.db.x("DELETE FROM announcements WHERE dedupe_key = ?", (f"news:{row['id']}",))
            queued = svc.announce(row["id"], force=True)
            app.log_admin(ctx, "news.publish", str(row["id"]), f"{queued} channels")
            return reply.tell(irc.line("ADMIN", app.t("news.published", ctx.lang, id=row["id"], n=queued)))
        return reply

    return svc
