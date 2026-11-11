"""Builds the application: config, database, modules, scheduler, router."""
from __future__ import annotations

from datetime import datetime

from .core import general, irc, log
from .core.announcer import PRIO_LOW, Announcer
from .core.app import App
from .core.config import Config
from .core.router import Router
from .core.scheduler import Scheduler

L = log.get("core")


def build(cfg: Config, start_jobs: bool = True, console: bool = False):
    log.setup(cfg.path("logging", "dir", "logs"), cfg.get("logging", "level", "INFO"),
              cfg.getint("logging", "max_mb", 5), cfg.getint("logging", "backups", 5), console=console)
    app = App(cfg)
    scheduler = Scheduler()
    app.services["scheduler"] = scheduler
    app.services["announcer"] = Announcer(app)

    from .cod2 import service as cod2
    from .community import service as community
    from .news import service as news
    from .servers import service as servers
    from .tournaments import service as tournaments

    general.setup(app, scheduler)
    cod2.setup(app, scheduler)
    tournaments.setup(app, scheduler)   # before news: provides the provider timezone
    news.setup(app, scheduler)
    servers.setup(app, scheduler)
    community.setup(app, scheduler)

    maint = Maintenance(app)
    app.services["maintenance"] = maint
    scheduler.add("maintenance.daily", maint.daily, 60, delay=60)
    scheduler.add("announcer.digest", maint.digest, 60, delay=90,
                  enabled=lambda: app.module_enabled("announcer"))
    if start_jobs:
        scheduler.start()
    return app, Router(app), scheduler


class Maintenance:
    """Daily backup + cleanup + integrity check, and the daily digest."""

    def __init__(self, app):
        self.app = app

    def daily(self) -> None:
        app = self.app
        hour = app.cfg.getint("backup", "hour", 5)
        now = datetime.now(app.tz)
        today = now.strftime("%Y-%m-%d")
        if hour < 0 or now.hour != hour or app.db.cfg_get("maintenance.last") == today:
            return
        app.db.cfg_set("maintenance.last", today)
        path = app.db.backup(app.cfg.path("backup", "dir", "backups"), app.cfg.getint("backup", "keep", 14))
        res = app.db.cleanup(app.cfg.getint("database", "keep_news_days", 365),
                             app.cfg.getint("database", "keep_server_history_days", 30),
                             app.cfg.getint("database", "keep_admin_logs_days", 180),
                             app.cfg.getint("database", "keep_announcements_days", 14))
        ok = app.db.integrity_ok()
        L.info("daily maintenance: backup %s, cleanup %s, integrity %s", path.name, res, "ok" if ok else "FAILED")
        if not ok:
            L.error("database integrity check FAILED - restore the last backup (see docs/OPERATIONS.md)")

    def digest(self) -> None:
        app = self.app
        now = datetime.now(app.tz)
        today = now.strftime("%Y-%m-%d")
        if now.hour != app.cfg.getint("announcer", "digest_hour", 20) or app.db.cfg_get("digest.last") == today:
            return
        app.db.cfg_set("digest.last", today)
        text = self.build_digest()
        if text:
            app.services["announcer"].push("daily_digest", text, PRIO_LOW, f"digest:{today}")

    def build_digest(self) -> str:
        """Summary line + one line (with its link) per live/open tournament and the next war.
        Lines are separated by "\\n"; the announcer sends them together (max. 5)."""
        app = self.app
        lang = app.lang
        news = app.db.scalar("SELECT COUNT(*) FROM news WHERE fetched_at > datetime('now','-1 day')", (), 0)
        tours = app.db.q("SELECT * FROM tournaments WHERE state IN ('ONGOING','WAITING_FOR_START','REGISTRATION',"
                         "'ANNOUNCE','MANUAL') ORDER BY state != 'ONGOING', start_at LIMIT 3")
        wars = app.db.q("SELECT * FROM wars WHERE status='scheduled' AND scheduled_at "
                        "BETWEEN datetime('now') AND datetime('now','+1 day') ORDER BY scheduled_at LIMIT 1")
        if news == 0 and not tours and not wars:
            return ""
        live = sum(1 for t in tours if t["state"] == "ONGOING")
        parts = [irc.label(app.t("digest.news", lang), irc.bold(news))]
        if live:
            parts.append(irc.label(app.t("digest.live", lang), irc.bold(live)))
        if len(tours) - live:
            parts.append(irc.label(app.t("digest.open", lang), irc.bold(len(tours) - live)))
        if wars:
            parts.append(irc.label("Wars", irc.bold(len(wars))))
        lines = [irc.line("DIGEST", *parts)]
        lines += [app.services["tournaments"].format_line(t, lang) for t in tours]
        lines += [app.services["wars"].format_war(w, lang) for w in wars]
        return "\n".join(lines)
