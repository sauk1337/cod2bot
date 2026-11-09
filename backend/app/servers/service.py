"""Servers module: favourites, live queries, monitoring, master browser, commands."""
from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from ..core import irc, log
from ..core.announcer import PRIO_HIGH
from ..core.commands import MODERATOR, USER, Reply, command, page_arg
from ..core.db import utcnow
from . import query as q

L = log.get("servers")
NAME_RE = re.compile(r"^[a-z0-9_\-.]{1,24}$", re.IGNORECASE)


class ServerService:
    def __init__(self, app):
        self.app = app
        self.db = app.db
        cfg = app.cfg
        self.timeout = cfg.getfloat("servers", "timeout", 2.0)
        self.cache_secs = cfg.getint("servers", "query_cache", 20)
        self.down_after = cfg.getint("servers", "down_after", 3)
        self.master = cfg.get("servers", "master", "cod2master.activision.com:20710")
        self.protocols = [int(p) for p in cfg.getlist("servers", "master_protocols", "118,117,115") if p.isdigit()]
        self._cache: dict[tuple[str, int], tuple[float, q.Status]] = {}
        self._lock = threading.Lock()
        self.browser: list[q.Status] = []
        self.browser_at = 0.0
        self.browser_total = 0

    # -- queries ---------------------------------------------------------------
    def status(self, host: str, port: int, fresh: bool = False) -> q.Status:
        key = (host, port)
        with self._lock:
            hit = self._cache.get(key)
        if hit and not fresh and time.time() - hit[0] < self.cache_secs:
            return hit[1]
        st = q.query(host, port, self.timeout)
        with self._lock:
            self._cache[key] = (time.time(), st)
            if len(self._cache) > 500:
                for k in sorted(self._cache, key=lambda k: self._cache[k][0])[:250]:
                    self._cache.pop(k, None)
        return st

    def get(self, name: str):
        return self.db.one("SELECT * FROM servers WHERE name = ?", (name,))

    def add(self, name: str, host: str, port: int, who: str):
        self.db.x("INSERT INTO servers(name, host, port, added_by, created_at) VALUES (?,?,?,?,?)",
                  (name, host, port, who, utcnow()))

    def save_status(self, srv, st: q.Status) -> None:
        if st.online:
            self.db.x("UPDATE servers SET last_check=?, last_online=?, online=1, fail_count=0, hostname=?, map=?, "
                      "gametype=?, version=?, players=?, maxplayers=?, ping=? WHERE id=?",
                      (utcnow(), utcnow(), irc.clean(st.hostname, 80), st.map, st.gametype, st.version,
                       len(st.players), st.maxplayers, st.ping, srv["id"]))
        else:
            self.db.x("UPDATE servers SET last_check=?, fail_count=fail_count+1, online=CASE WHEN fail_count+1 >= ? "
                      "THEN 0 ELSE online END WHERE id=?", (utcnow(), self.down_after, srv["id"]))
        self.db.x("INSERT INTO server_history(server_id, ts, online, players, map, ping) VALUES (?,?,?,?,?,?)",
                  (srv["id"], utcnow(), 1 if st.online else 0, len(st.players) if st.online else None,
                   st.map or None, st.ping if st.online else None))

    # -- monitoring ------------------------------------------------------------
    def monitor(self) -> None:
        rows = self.db.q("SELECT * FROM servers WHERE monitor = 1")
        if not rows:
            return
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda r: (r, self.status(r["host"], r["port"], fresh=True)), rows))
        ann = self.app.services["announcer"]
        for srv, st in results:
            self.save_status(srv, st)
            row = self.get(srv["name"])
            if not st.online and row["fail_count"] >= self.down_after and not row["down_announced"]:
                self.db.x("UPDATE servers SET down_announced = 1 WHERE id = ?", (srv["id"],))
                ann.push("servers_down", irc.line("SERVER", irc.bold(srv["name"]), irc.bad("OFFLINE"),
                                                  irc.muted(f"{srv['host']}:{srv['port']}"),
                                                  self.app.t("servers.down_since", self.app.lang,
                                                             n=row["fail_count"])), PRIO_HIGH,
                         f"down:{srv['id']}:{int(time.time() // 3600)}")
            elif st.online and srv["down_announced"]:
                self.db.x("UPDATE servers SET down_announced = 0 WHERE id = ?", (srv["id"],))
                ann.push("servers_down", self.format_status(srv["name"], st, short=True), PRIO_HIGH,
                         f"up:{srv['id']}:{int(time.time() // 60)}")

    def scan_master(self) -> None:
        servers = q.master_list(self.master, self.protocols)
        self.browser_total = len(servers)
        uniq = {(h, p) for h, p, _ in servers}
        with ThreadPoolExecutor(max_workers=32) as pool:
            results = list(pool.map(lambda a: q.query(a[0], a[1], 1.5, retries=0), uniq))
        self.browser = [s for s in results if s.online]
        self.browser_at = time.time()
        L.info("master scan: %d listed, %d answered", len(uniq), len(self.browser))

    # -- formatting --------------------------------------------------------------
    def format_status(self, name: str, st: q.Status, short: bool = False, state: bool = True,
                      addr: bool = False) -> str:
        address = irc.muted(f"{st.host}:{st.port}")
        if not st.online:
            return irc.line("SERVER", irc.bold(name or f"{st.host}:{st.port}"), irc.bad("OFFLINE"), address)
        gt = st.gametype
        mode = irc.muted(gt.upper()) if gt else ""
        players = f"{irc.bold(str(len(st.players)))}/{st.maxplayers} {irc.bar(len(st.players), st.maxplayers, 8)}"
        flags = []
        if st.password:
            flags.append(irc.warn("🔒pw") if not self.app.cfg.getbool("irc", "ascii_only") else irc.warn("pw"))
        if st.punkbuster:
            flags.append(irc.muted("PB"))
        parts = [irc.bold(name) if name else "", irc.good("ONLINE") if state else "",
                 irc.cod_colours(re.sub(r"^((?:\^\d)*)\s+", r"\1", irc.clean(st.hostname, 60))) + irc.R,
                 f"{irc.label('Map', irc.accent(st.map))} {mode}", players]
        if not short:
            parts += [irc.muted(f"{st.ping}ms"), irc.muted("v" + st.version) if st.version else "",
                      " ".join(flags), address]
        elif addr:
            parts.append(address)
        return irc.line("SERVER", *parts)

    def format_players(self, name: str, st: q.Status, limit: int = 12) -> str:
        if not st.online:
            return irc.line("SERVER", irc.bold(name), irc.bad("OFFLINE"))
        if not st.players:
            return irc.line("SERVER", irc.bold(name), self.app.t("servers.empty", self.app.lang))
        shown = [f"{irc.cod_colours(irc.clean(p.name, 32))}{irc.R} {irc.muted(str(p.score))}"
                 for p in st.players[:limit]]
        extra = f" {irc.muted('+' + str(len(st.players) - limit))}" if len(st.players) > limit else ""
        return irc.line("SERVER", f"{irc.bold(name)} {irc.muted(f'{len(st.players)}/{st.maxplayers}')}",
                        ", ".join(shown) + extra)


# =============================================================================
# IRC commands
# =============================================================================

def setup(app, scheduler):
    svc = ServerService(app)
    app.services["servers"] = svc
    scheduler.add("servers.monitor", svc.monitor, app.cfg.getint("servers", "check_interval", 300), delay=20,
                  enabled=lambda: app.module_enabled("servers"))
    scheduler.add("servers.browser", svc.scan_master, max(300, app.cfg.getint("servers", "master_cache", 600)),
                  delay=30, enabled=lambda: app.module_enabled("servers") and
                  app.cfg.getbool("servers", "browser", True))
    reg = app.registry

    def resolve(ctx, target: str, reply: Reply):
        """Name of a saved server, or a public ip:port."""
        srv = svc.get(target)
        if srv:
            return srv["name"], srv["host"], srv["port"], srv
        addr = q.parse_address(target)
        if addr and q.is_public(addr[0]):
            return "", addr[0], addr[1], None
        reply.tell(irc.error(app.t("servers.unknown", ctx.lang, name=irc.bold(target[:30]), p=ctx.prefix)))
        return None

    @command(reg, "servers", "servers", USER, category="servers", usage="servers [page|top|online]")
    def servers_cmd(app_, ctx):
        reply = Reply()
        sub = ctx.arg(0).lower()
        if sub in ("top", "popular", "browser"):
            return top(ctx, reply)
        rows = app.db.q("SELECT * FROM servers WHERE favorite = 1 ORDER BY online DESC, players DESC, name")
        if sub == "online":
            rows = [r for r in rows if r["online"]]
        if not rows:
            return reply.say(irc.line("SERVERS", app.t("servers.none", ctx.lang, p=ctx.prefix)))
        size = app.cfg.getint("irc", "max_channel_lines", 4)
        items, page, pages = irc.paginate(rows, page_arg(sub) or 1, size * 3)
        parts = []
        for r in items:
            if r["online"]:
                count = f"{r['players'] or 0}/{r['maxplayers'] or 0}"
                parts.append(f"{irc.good('●')} {irc.bold(r['name'])} {irc.accent(r['map'] or '?')} "
                             f"{irc.muted(count)}")
            else:
                parts.append(f"{irc.bad('●')} {irc.muted(r['name'])}")
        footer = irc.muted(f"[{page}/{pages}]") if pages > 1 else ""
        reply.say(irc.line("SERVERS", "  ".join(parts) + (" " + footer if footer else "")))
        return reply

    def top(ctx, reply):
        if not svc.browser:
            return reply.say(irc.line("SERVERS", app.t("servers.browser_wait", ctx.lang)))
        best = sorted((s for s in svc.browser if s.players), key=lambda s: -len(s.players))[:5]
        age = irc.muted(irc.ago(time.time() - svc.browser_at, ctx.lang))
        total_players = sum(len(s.players) for s in svc.browser)
        reply.say(irc.line("SERVERS", app.t("servers.browser_summary", ctx.lang, servers=irc.bold(len(svc.browser)),
                                            players=irc.bold(total_players)), age))
        for s in best:
            reply.say(svc.format_status("", s, short=True, state=False, addr=True))
        return reply

    @command(reg, "server", "servers", USER, aliases=("srv",), category="servers",
             usage="server <name|ip:port> | players|map|status <name> | add <name> <ip:port> | remove <name> | find <text>")
    def server_cmd(app_, ctx):
        reply = Reply()
        sub = ctx.arg(0).lower()
        if not sub:
            return reply.tell(irc.error(f"{ctx.prefix}server <name|ip:port> {irc.muted('· ' + ctx.prefix + 'help servers')}"))
        if sub == "add":
            if ctx.level < MODERATOR:
                return reply.tell(irc.error(app.t("perm.denied", ctx.lang)))
            name, addr_s = ctx.arg(1), ctx.arg(2)
            addr = q.parse_address(addr_s) if addr_s else None
            if not NAME_RE.match(name or "") or not addr:
                return reply.tell(irc.error(f"{ctx.prefix}server add <name> <ip:port>"))
            if not q.is_public(addr[0]):
                return reply.tell(irc.error(app.t("servers.not_public", ctx.lang)))
            if svc.get(name) or app.db.one("SELECT 1 FROM servers WHERE host=? AND port=?", addr):
                return reply.tell(irc.error(app.t("servers.exists", ctx.lang)))
            svc.add(name, addr[0], addr[1], ctx.nick)
            st = svc.status(addr[0], addr[1], fresh=True)
            svc.save_status(svc.get(name), st)
            app.log_admin(ctx, "server.add", f"{name} {addr[0]}:{addr[1]}", "online" if st.online else "offline")
            reply.say(irc.line("ADMIN", app.t("servers.added", ctx.lang, name=irc.bold(name))))
            return reply.say(svc.format_status(name, st))
        if sub in ("remove", "del", "delete"):
            if ctx.level < MODERATOR:
                return reply.tell(irc.error(app.t("perm.denied", ctx.lang)))
            srv = svc.get(ctx.arg(1))
            if not srv:
                return reply.tell(irc.error(app.t("servers.unknown", ctx.lang, name=ctx.arg(1)[:30], p=ctx.prefix)))
            app.db.x("DELETE FROM servers WHERE id = ?", (srv["id"],))
            app.log_admin(ctx, "server.remove", srv["name"], "ok")
            return reply.say(irc.line("ADMIN", app.t("servers.removed", ctx.lang, name=irc.bold(srv["name"]))))
        if sub in ("find", "search"):
            text = ctx.rest(1).strip().lower()
            if len(text) < 3:
                return reply.tell(irc.error(app.t("news.search_short", ctx.lang)))
            if not svc.browser:
                return reply.say(irc.line("SERVERS", app.t("servers.browser_wait", ctx.lang)))
            found = [s for s in svc.browser if text in irc.strip_cod(s.hostname).lower()]
            found.sort(key=lambda s: -len(s.players))
            if not found:
                return reply.say(irc.line("SERVERS", app.t("servers.find_none", ctx.lang, q=irc.bold(text[:30]))))
            for s in found[:3]:
                reply.say(svc.format_status("", s, short=True, state=False, addr=True))
            if len(found) > 3:
                reply.say(irc.line("SERVERS", irc.muted(app.t("servers.more", ctx.lang, n=len(found) - 3))))
            return reply
        if sub in ("players", "map", "status", "info"):
            target = ctx.arg(1)
            if not target:
                return reply.tell(irc.error(f"{ctx.prefix}server {sub} <name>"))
        else:
            target, sub = ctx.arg(0), "info"
        res = resolve(ctx, target, reply)
        if not res:
            return reply
        name, host, port, srv = res
        st = svc.status(host, port)
        if srv:
            svc.save_status(srv, st)
        label = name or f"{host}:{port}"
        if sub == "players":
            return reply.say(svc.format_players(label, st))
        if sub == "map":
            if not st.online:
                return reply.say(irc.line("SERVER", irc.bold(label), irc.bad("OFFLINE")))
            kb = app.db.one("SELECT title FROM maps WHERE name = ?", (st.map,))
            gt = q.GAMETYPES.get(st.gametype, st.gametype.upper())
            return reply.say(irc.line("SERVER", irc.bold(label), irc.label("Map", irc.accent(st.map)) +
                                      (f" {irc.muted('(' + kb['title'] + ')')}" if kb else ""),
                                      irc.label("Mode", gt)))
        if sub == "status":
            if srv:
                hist = app.db.q("SELECT online FROM server_history WHERE server_id = ? AND ts > datetime('now','-1 day')",
                                (srv["id"],))
                uptime = (100 * sum(h["online"] for h in hist) // len(hist)) if hist else None
                extra = irc.label(app.t("servers.uptime24", ctx.lang), f"{uptime}%") if uptime is not None else ""
                return reply.say(svc.format_status(label, st, short=True) + (irc.sep() + extra if extra else ""))
            return reply.say(svc.format_status(label, st, short=True))
        return reply.say(svc.format_status(name, st) if name else svc.format_status("", st))

    @command(reg, "find", "servers", USER, aliases=("whereis",), category="servers", usage="find <player>")
    def find_cmd(app_, ctx):
        reply = Reply()
        text = ctx.rest(0).strip().lower()
        if len(text) < 3:
            return reply.tell(irc.error(app.t("news.search_short", ctx.lang)))
        if not svc.browser:
            return reply.say(irc.line("PLAYER", app.t("servers.browser_wait", ctx.lang)))
        hits = []
        for s in svc.browser:
            for p in s.players:
                if text in irc.strip_cod(p.name).lower():
                    hits.append((p, s))
        if not hits:
            return reply.say(irc.line("PLAYER", app.t("servers.player_none", ctx.lang, q=irc.bold(text[:30])),
                                      irc.muted(irc.ago(time.time() - svc.browser_at, ctx.lang))))
        for p, s in hits[:3]:
            reply.say(irc.line("PLAYER", irc.cod_colours(irc.clean(p.name, 32)) + irc.R,
                               irc.cod_colours(irc.clean(s.hostname, 50)) + irc.R,
                               irc.label("Map", irc.accent(s.map)), irc.muted(f"{s.host}:{s.port}")))
        return reply

    return svc
