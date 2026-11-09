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
        lang = app.lang
        news = app.db.scalar("SELECT COUNT(*) FROM news WHERE fetched_at > datetime('now','-1 day')", (), 0)
        parts = [irc.label(app.t("digest.news", lang), irc.bold(news))]
        reg = app.db.q("SELECT name, reg_end, start_at FROM tournaments WHERE state IN ('REGISTRATION','ANNOUNCE') "
                       "ORDER BY start_at LIMIT 2")
        if reg:
            parts.append(irc.label(app.t("digest.open", lang),
                                   ", ".join(f"{irc.bold(t['name'])} {irc.muted(app.fmt_dt(t['start_at'], False))}"
                                             for t in reg)))
        live = app.db.scalar("SELECT COUNT(*) FROM tournaments WHERE state = 'ONGOING'", (), 0)
        if live:
            parts.append(irc.label(app.t("digest.live", lang), irc.bold(live)))
        wars = app.db.q("SELECT clan1, clan2, scheduled_at FROM wars WHERE status='scheduled' AND scheduled_at "
                        "BETWEEN datetime('now') AND datetime('now','+1 day') ORDER BY scheduled_at LIMIT 2")
        if wars:
            parts.append(irc.label("Wars", ", ".join(
                f"{w['clan1']} vs {w['clan2']} {irc.muted(app.local(w['scheduled_at']).strftime('%H:%M'))}"
                for w in wars)))
        if news == 0 and not reg and not live and not wars:
            return
        app.services["announcer"].push("daily_digest", irc.line("DIGEST", *parts), PRIO_LOW, f"digest:{today}")
