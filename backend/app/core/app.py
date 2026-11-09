"""Application object shared by all modules."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from . import irc, log
from .commands import Registry
from .config import Config
from .db import Database, utcnow
from .http import HttpClient
from .i18n import translate

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9
    ZoneInfo = None

MODULES = ("news", "tournaments", "servers", "cod2", "clans", "players", "wars", "announcer", "fun")
L = log.get("core")


def _zone(name: str):
    if ZoneInfo is None:
        return timezone.utc
    try:
        return ZoneInfo(name)
    except Exception:  # missing tzdata
        L.warning("timezone %s not available, using UTC (install tzdata)", name)
        return timezone.utc


class App:
    def __init__(self, cfg: Config, db: Database | None = None):
        self.cfg = cfg
        self.started = time.time()
        self.db = db or Database(cfg.path("database", "path", "data/cod2bot.db"))
        self.http = HttpClient(
            cfg.get("http", "user_agent", "COD2BOT/1.0"),
            timeout=cfg.getfloat("http", "timeout", 12),
            min_host_interval=cfg.getfloat("http", "min_host_interval", 2),
            max_bytes=cfg.getint("http", "max_bytes", 3_000_000),
            respect_robots=cfg.getbool("http", "respect_robots", True),
        )
        self.registry = Registry()
        self.tz = _zone(cfg.get("general", "timezone", "Europe/Berlin"))
        self.tz_label = cfg.get("general", "timezone_label", "CET")
        self.lang = cfg.get("general", "language", "en")
        self.prefix = cfg.get("general", "prefix", "!")
        self.version = cfg.get("general", "version", "1.0.0")
        self.maintenance = self.db.cfg_get("maintenance", "0") == "1"
        self.services: dict = {}
        irc.configure(cfg)

    # -- i18n ------------------------------------------------------------
    def t(self, key: str, lang: str | None = None, **kw) -> str:
        return translate(key, lang or self.lang, **kw)

    # -- modules ---------------------------------------------------------
    def module_enabled(self, name: str) -> bool:
        if name in ("core", "admin", "general"):
            return True
        override = self.db.cfg_get(f"module.{name}")
        if override is not None:
            return override == "1"
        return self.cfg.getbool("modules", name, True)

    def set_module(self, name: str, enabled: bool) -> None:
        self.db.cfg_set(f"module.{name}", "1" if enabled else "0")

    # -- time --------------------------------------------------------------
    @staticmethod
    def parse_utc(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00").replace("T", " "))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def local(self, value: str | datetime | None) -> datetime | None:
        dt = self.parse_utc(value) if isinstance(value, str) or value is None else value
        return dt.astimezone(self.tz) if dt else None

    def fmt_dt(self, value, with_day: bool = True) -> str:
        dt = self.local(value)
        if not dt:
            return "?"
        fmt = "%a %d/%m %H:%M" if with_day else "%d/%m %H:%M"
        return f"{dt.strftime(fmt)} {self.tz_label}"

    def fmt_date(self, value) -> str:
        dt = self.local(value)
        return dt.strftime("%d/%m/%Y") if dt else "?"

    def seconds_since(self, value) -> float | None:
        dt = self.parse_utc(value) if isinstance(value, str) else value
        if not dt:
            return None
        return (datetime.now(timezone.utc) - dt).total_seconds()

    def parse_user_datetime(self, date_s: str, time_s: str = "") -> datetime | None:
        """Parse dates typed in IRC: 15/11 20:30, 15/11/2026 20:30, 2026-11-15 20:30,
        today 21:00, tomorrow 20:00. Returns UTC."""
        now = datetime.now(self.tz)
        d = date_s.lower()
        try:
            if d in ("today",):
                base = now.date()
            elif d in ("tomorrow",):
                base = (now + timedelta(days=1)).date()
            elif "-" in d:
                base = datetime.strptime(d, "%Y-%m-%d").date()
            else:
                parts = d.split("/")
                if len(parts) == 2:
                    base = datetime(now.year, int(parts[1]), int(parts[0])).date()
                    if base < now.date() - timedelta(days=30):
                        base = base.replace(year=now.year + 1)
                elif len(parts) == 3:
                    year = int(parts[2]) + (2000 if len(parts[2]) == 2 else 0)
                    base = datetime(year, int(parts[1]), int(parts[0])).date()
                else:
                    return None
            hh, mm = 21, 0
            if time_s:
                t = time_s.lower().replace("h", ":").rstrip(":")
                hh, _, mm_s = t.partition(":")
                hh, mm = int(hh), int(mm_s or 0)
            local_dt = datetime(base.year, base.month, base.day, hh, mm, tzinfo=self.tz)
        except (ValueError, TypeError):
            return None
        return local_dt.astimezone(timezone.utc)

    # -- admin log ---------------------------------------------------------
    def log_admin(self, ctx, action: str, args: str = "", result: str = "") -> None:
        self.db.x("INSERT INTO admin_logs(ts, nick, handle, host, channel, level, action, args, result) "
                  "VALUES (?,?,?,?,?,?,?,?,?)",
                  (utcnow(), ctx.nick, ctx.handle, ctx.host, ctx.chan, ctx.level, action, args[:400], result[:200]))
        log.get("admin").info("%s (%s, level %s) on %s: %s %s -> %s", ctx.nick, ctx.handle, ctx.level, ctx.chan,
                              action, args, result)

    def count_command(self, name: str) -> None:
        self.db.x("INSERT INTO command_stats(command, count, last_used) VALUES (?, 1, ?) "
                  "ON CONFLICT(command) DO UPDATE SET count = count + 1, last_used = excluded.last_used",
                  (name, utcnow()))
