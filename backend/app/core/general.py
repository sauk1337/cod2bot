"""General commands, help system and administration commands."""
from __future__ import annotations

import platform
import sqlite3
import time
from datetime import datetime, timezone

from . import irc, log
from .announcer import PRIO_URGENT
from .commands import ADMIN, LEVEL_NAMES, MODERATOR, OWNER, USER, Reply, command

L = log.get("admin")

CATEGORY_ORDER = ["general", "news", "competitive", "servers", "community", "cod2", "fun", "admin"]
CATEGORY_ALIASES = {"tournaments": "competitive", "server": "servers", "clans": "community", "wars": "community",
                    "players": "community", "utils": "cod2", "info": "cod2"}


class Help:
    def __init__(self, app):
        self.app = app

    def visible(self, ctx):
        return {cat: [c for c in cmds if c.level <= ctx.level and self.app.module_enabled(c.module)]
                for cat, cmds in self.app.registry.by_category().items()}

    def overview(self, ctx) -> list[str]:
        cats = self.visible(ctx)
        parts = []
        for cat in CATEGORY_ORDER:
            if cats.get(cat):
                parts.append(f"{irc.bold(irc.accent(cat))} {irc.muted(str(len(cats[cat])))}")
        return [irc.line("HELP", irc.label(self.app.t("help.categories", ctx.lang), "  ".join(parts))),
                irc.line("HELP", irc.muted(self.app.t("help.usage", ctx.lang, p=ctx.prefix)))]

    def topic(self, name: str, ctx) -> list[str]:
        name = CATEGORY_ALIASES.get(name.lower(), name.lower())
        cats = self.visible(ctx)
        if name in cats and cats[name]:
            cmds = " ".join(irc.bold(ctx.prefix + c.name) for c in sorted(cats[name], key=lambda c: c.name))
            return [irc.line("HELP", irc.label(irc.accent(name), cmds)),
                    irc.line("HELP", irc.muted(self.app.t("help.cmd_usage", ctx.lang, p=ctx.prefix)))]
        cmd = self.app.registry.get(name.lstrip(ctx.prefix))
        if cmd and cmd.level <= ctx.level:
            desc = self.app.t(cmd.help_key, ctx.lang, p=ctx.prefix)
            usage = irc.muted(f"{ctx.prefix}{cmd.usage or cmd.name}")
            lines = [irc.line("HELP", irc.bold(ctx.prefix + cmd.name), desc)]
            lines.append(irc.line("HELP", irc.label(self.app.t("help.syntax", ctx.lang), usage)))
            if cmd.aliases:
                lines[-1] += irc.sep() + irc.label("alias", " ".join(ctx.prefix + a for a in cmd.aliases))
            if cmd.level > USER:
                lines[-1] += irc.sep() + irc.warn(LEVEL_NAMES[cmd.level])
            return lines
        return [irc.error(self.app.t("help.unknown", ctx.lang, p=ctx.prefix))]


def setup(app, scheduler):
    reg = app.registry
    helper = Help(app)
    app.services["help"] = helper

    @command(reg, "help", "general", USER, aliases=("commands-list",), category="general", usage="help [category|command]")
    def help_cmd(app_, ctx):
        lines = helper.topic(ctx.arg(0), ctx) if ctx.args else helper.overview(ctx)
        return Reply().tell(*lines)  # help always by notice

    @command(reg, "cod2", "general", USER, category="general", usage="cod2 [command ...]")
    def cod2_cmd(app_, ctx):
        # "!cod2 <command>" works for every command (useful when another script uses the same name)
        if ctx.args:
            return {"redirect": ctx.args[0].lower(), "args": ctx.args[1:]}
        news = app.db.scalar("SELECT COUNT(*) FROM news", (), 0)
        tours = app.db.scalar("SELECT COUNT(*) FROM tournaments WHERE state NOT IN ('FINISHED','CANCELLED')", (), 0)
        wars = app.db.scalar("SELECT COUNT(*) FROM wars WHERE status='scheduled' AND scheduled_at > datetime('now')", (), 0)
        srv = app.services.get("servers")
        online = len(srv.browser) if srv else 0
        players = sum(len(s.players) for s in srv.browser) if srv else 0
        return Reply().say(irc.line(
            "INFO", irc.bold(app.t("about.tagline", ctx.lang)),
            irc.label(app.t("about.news", ctx.lang), irc.bold(news)),
            irc.label(app.t("about.tournaments", ctx.lang), irc.bold(tours)),
            irc.label("Wars", irc.bold(wars)),
            irc.label(app.t("about.online", ctx.lang), f"{irc.bold(online)} {app.t('about.servers', ctx.lang)} / "
                                                       f"{irc.bold(players)} {app.t('about.players', ctx.lang)}") if online else "",
            irc.muted(f"{ctx.prefix}help")))

    @command(reg, "about", "general", USER, category="general")
    def about_cmd(app_, ctx):
        return Reply().say(irc.line("INFO", f"{irc.bold(app.cfg.get('general', 'bot_name', 'COD2BOT'))} "
                                            f"{irc.muted('v' + app.version)}", app.t("about.text", ctx.lang),
                                    irc.muted("Eggdrop + Python · FPSChallenge API · CoD2 master server")))

    @command(reg, "version", "general", USER, aliases=("ver",), category="general")
    def version_cmd(app_, ctx):
        return Reply().say(irc.line("INFO", f"{irc.bold('COD2BOT')} v{app.version}",
                                    irc.muted(f"Python {platform.python_version()} · SQLite {sqlite3.sqlite_version} "
                                              f"· schema {app.db.schema_version()}")))

    @command(reg, "uptime", "general", USER, category="general")
    def uptime_cmd(app_, ctx):
        return Reply().say(irc.line("INFO", irc.label(app.t("general.backend_uptime", ctx.lang),
                                                      irc.bold(irc.duration(time.time() - app.started)))))

    @command(reg, "ping", "general", USER, category="general")
    def ping_cmd(app_, ctx):
        return Reply().say(irc.line("INFO", irc.good("pong"), irc.muted(ctx.nick)))

    @command(reg, "time", "general", USER, category="general")
    def time_cmd(app_, ctx):
        now = datetime.now(app.tz)
        return Reply().say(irc.line("INFO", irc.bold(now.strftime("%H:%M:%S")) + " " + app.tz_label,
                                    irc.muted(datetime.now(timezone.utc).strftime("%H:%M UTC"))))

    @command(reg, "date", "general", USER, category="general")
    def date_cmd(app_, ctx):
        now = datetime.now(app.tz)
        return Reply().say(irc.line("INFO", irc.bold(now.strftime("%A %d/%m/%Y")),
                                    irc.muted(f"week {now.isocalendar()[1]}")))

    @command(reg, "stats", "general", USER, category="general")
    def stats_cmd(app_, ctx):
        db = app.db
        parts = [
            irc.label(app.t("about.news", ctx.lang), irc.bold(db.scalar("SELECT COUNT(*) FROM news", (), 0))),
            irc.label(app.t("stats.sources", ctx.lang), db.scalar("SELECT COUNT(*) FROM news_sources WHERE enabled=1", (), 0)),
            irc.label(app.t("about.tournaments", ctx.lang), db.scalar("SELECT COUNT(*) FROM tournaments", (), 0)),
            irc.label(app.t("stats.teams", ctx.lang), db.scalar("SELECT COUNT(*) FROM teams", (), 0)),
            irc.label("Clans", db.scalar("SELECT COUNT(*) FROM clans", (), 0)),
            irc.label(app.t("about.players", ctx.lang), db.scalar("SELECT COUNT(*) FROM players", (), 0)),
            irc.label("Wars", db.scalar("SELECT COUNT(*) FROM wars", (), 0)),
            irc.label(app.t("about.servers", ctx.lang), db.scalar("SELECT COUNT(*) FROM servers", (), 0)),
        ]
        top = db.q("SELECT command, count FROM command_stats ORDER BY count DESC LIMIT 3")
        if top:
            parts.append(irc.label("Top", " ".join(f"{ctx.prefix}{r['command']}{irc.muted('(' + str(r['count']) + ')')}"
                                                    for r in top)))
        reply = Reply().say(irc.line("INFO", *parts))
        if ctx.level >= MODERATOR:
            sched = app.services["scheduler"]
            failing = [j.name for j in sched.jobs.values() if j.failures]
            reply.tell(irc.line("ADMIN", irc.label("HTTP", f"{app.http.requests} req / {app.http.errors} err"),
                                irc.label(app.t("stats.queue", ctx.lang), app.services["announcer"].pending()),
                                irc.label("Jobs", irc.bad(", ".join(failing)) if failing else irc.good("ok"))))
        return reply

    @command(reg, "modules", "general", USER, category="general")
    def modules_cmd(app_, ctx):
        from .app import MODULES
        parts = [(irc.good("●") if app.module_enabled(m) else irc.bad("○")) + " " + m for m in MODULES]
        return Reply().say(irc.line("INFO", irc.label(app.t("general.modules", ctx.lang), " ".join(parts))))

    # -- administration ------------------------------------------------------------
    @command(reg, "admin", "general", MODERATOR, category="admin")
    def admin_cmd(app_, ctx):
        cmds = [c for c in reg.primary.values() if c.category == "admin" and c.level <= ctx.level]
        names = " ".join(irc.bold(ctx.prefix + c.name) for c in sorted(cmds, key=lambda c: c.name))
        extra = (f"{ctx.prefix}news addsource|removesource|enable|disable|check|publish · {ctx.prefix}server add|remove · "
                 f"{ctx.prefix}tournament add|winner|del · {ctx.prefix}results add|del · {ctx.prefix}tips/quote/fact add")
        return Reply().tell(irc.line("ADMIN", irc.label(LEVEL_NAMES[ctx.level], names)),
                            irc.line("ADMIN", irc.muted(extra)))

    @command(reg, "reload", "general", ADMIN, category="admin", admin_log=True)
    def reload_cmd(app_, ctx):
        app.cfg.reload()
        irc.configure(app.cfg)
        app.services["news"].sync_sources()
        app.services["cod2"].seed()
        return Reply().say(irc.line("ADMIN", irc.good(app.t("admin.reloaded", ctx.lang))))

    @command(reg, "module", "general", ADMIN, category="admin", usage="module enable|disable <name>", admin_log=True)
    def module_cmd(app_, ctx):
        from .app import MODULES
        action, name = ctx.arg(0).lower(), ctx.arg(1).lower()
        if action not in ("enable", "disable", "on", "off") or name not in MODULES:
            return Reply().tell(irc.error(f"{ctx.prefix}module enable|disable <{'|'.join(MODULES)}>"))
        on = action in ("enable", "on")
        app.set_module(name, on)
        return Reply().say(irc.line("ADMIN", f"{irc.bold(name)}: " + (irc.good("ON") if on else irc.bad("OFF"))))

    @command(reg, "broadcast", "general", ADMIN, category="admin", usage="broadcast <message>", admin_log=True)
    def broadcast_cmd(app_, ctx):
        text = irc.clean(ctx.rest(0), 350)
        if len(text) < 3:
            return Reply().tell(irc.error(f"{ctx.prefix}broadcast <message>"))
        n = app.services["announcer"].push("broadcast", irc.line("ADMIN", irc.bold(text), irc.muted("— " + ctx.nick)),
                                           PRIO_URGENT, f"bc:{time.time()}", force=True)
        return Reply().tell(irc.line("ADMIN", app.t("admin.broadcast", ctx.lang, n=n)))

    @command(reg, "backup", "general", ADMIN, category="admin", admin_log=True)
    def backup_cmd(app_, ctx):
        path = app.db.backup(app.cfg.path("backup", "dir", "backups"), app.cfg.getint("backup", "keep", 14))
        return Reply().tell(irc.line("ADMIN", irc.good("backup ok"), irc.muted(path.name)))

    @command(reg, "maintenance", "general", OWNER, category="admin", usage="maintenance on|off", admin_log=True)
    def maintenance_cmd(app_, ctx):
        arg = ctx.arg(0).lower()
        if arg not in ("on", "off"):
            state = irc.warn("ON") if app.maintenance else irc.good("OFF")
            return Reply().tell(irc.line("ADMIN", irc.label(app.t("admin.maintenance", ctx.lang), state)))
        app.maintenance = arg == "on"
        app.db.cfg_set("maintenance", "1" if app.maintenance else "0")
        return Reply().say(irc.line("ADMIN", irc.label(app.t("admin.maintenance", ctx.lang),
                                                       irc.warn("ON") if app.maintenance else irc.good("OFF"))))

    @command(reg, "announce", "general", ADMIN, category="admin",
             usage="announce status | channels <#a #b> | <news|tournaments|wars|servers_down|daily_digest> on|off",
             admin_log=True)
    def announce_cmd(app_, ctx):
        ann = app.services["announcer"]
        sub = ctx.arg(0).lower()
        kinds = ("news", "tournaments", "wars", "servers_down", "daily_digest")
        if sub == "channels" and len(ctx.args) > 1:
            chans = [c.lower() for c in ctx.args[1:] if c.startswith("#")]
            app.db.cfg_set("announce.channels", " ".join(chans))
            return Reply().say(irc.line("ADMIN", irc.label(app.t("admin.announce_channels", ctx.lang), " ".join(chans))))
        if sub in kinds and ctx.arg(1).lower() in ("on", "off"):
            app.db.cfg_set(f"announce.{sub}", "1" if ctx.arg(1).lower() == "on" else "0")
        state = " ".join((irc.good("●") if ann.kind_enabled(k) else irc.bad("○")) + " " + k for k in kinds)
        return Reply().say(irc.line("ADMIN", irc.label(app.t("admin.announce_channels", ctx.lang),
                                                       " ".join(ann.channels) or "-"), state,
                                    irc.label(app.t("stats.queue", ctx.lang), ann.pending())))

    @command(reg, "adminlog", "general", ADMIN, category="admin", usage="adminlog [n]")
    def adminlog_cmd(app_, ctx):
        n = min(int(ctx.arg(0)) if ctx.arg(0).isdigit() else 5, 15)
        rows = app.db.q("SELECT * FROM admin_logs ORDER BY id DESC LIMIT ?", (n,))
        reply = Reply()
        for r in rows:
            reply.tell(irc.line("ADMIN", irc.muted(app.fmt_dt(r["ts"], False)), irc.bold(r["nick"] or "?"),
                                irc.accent(r["action"]), irc.clean(r["args"] or "", 120), irc.muted(r["result"] or "")))
        return reply if rows else reply.tell(irc.line("ADMIN", "-"))

    @command(reg, "jobs", "general", ADMIN, category="admin", usage="jobs [run <name>]", admin_log=True)
    def jobs_cmd(app_, ctx):
        sched = app.services["scheduler"]
        if ctx.arg(0).lower() == "run":
            ok = sched.run_now(ctx.arg(1))
            return Reply().tell(irc.line("ADMIN", irc.good("queued") if ok else irc.bad("unknown job")))
        reply = Reply()
        for j in sched.jobs.values():
            state = irc.bad(f"fail x{j.failures}") if j.failures else irc.good("ok")
            nxt = max(0, j.next_run - time.time())
            reply.tell(irc.line("ADMIN", irc.bold(j.name), state, irc.muted(f"runs {j.runs}"),
                                irc.muted(f"next {irc.duration(nxt)}"), irc.clean(j.last_error, 100)))
        return reply
