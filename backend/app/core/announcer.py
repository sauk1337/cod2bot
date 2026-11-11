"""Automatic announcements queue with per-channel anti-flood.

Modules push messages; the Eggdrop script pulls them every few seconds.
A channel receives at most one message per `min_interval` seconds and
`max_per_hour` messages per hour. Lower priority number = sent first.
Messages older than 6 hours that never got a slot are dropped.
"""
from __future__ import annotations

import time

from . import irc, log
from .db import utcnow

L = log.get("core")

PRIO_URGENT, PRIO_HIGH, PRIO_NORMAL, PRIO_LOW = 1, 3, 5, 8


class Announcer:
    def __init__(self, app):
        self.app = app
        self.last_sent: dict[str, float] = {}

    @property
    def channels(self) -> list[str]:
        override = self.app.db.cfg_get("announce.channels")
        if override is not None:
            return override.split()
        return self.app.cfg.getlist("announcer", "channels", "")

    def kind_enabled(self, kind: str) -> bool:
        override = self.app.db.cfg_get(f"announce.{kind}")
        if override is not None:
            return override == "1"
        return self.app.cfg.getbool("announcer", kind, True)

    def push(self, kind: str, text: str, priority: int = PRIO_NORMAL, dedupe_key: str | None = None,
             channels: list[str] | None = None, force: bool = False) -> int:
        """Queue a message. Returns how many channels it was queued for."""
        if not force and (not self.app.module_enabled("announcer") or not self.kind_enabled(kind)):
            return 0
        queued = 0
        for chan in channels or self.channels:
            cur = self.app.db.x(
                "INSERT OR IGNORE INTO announcements(created_at, channel, kind, text, priority, dedupe_key) "
                "VALUES (?,?,?,?,?,?)", (utcnow(), chan.lower(), kind, text, priority, dedupe_key))
            queued += cur.rowcount
        if queued:
            L.info("queued %s announcement for %d channel(s): %s", kind, queued, dedupe_key or text[:60])
        return queued

    def pull(self, channels: list[str]) -> list[tuple[str, str]]:
        """Return at most one due message per channel and mark it as sent.
        A message may hold several IRC lines separated by "\\n" (max. 5); each line is
        returned as its own (channel, line) pair, in order."""
        if self.app.maintenance:
            return []
        min_interval = self.app.cfg.getint("announcer", "min_interval", 90)
        max_hour = self.app.cfg.getint("announcer", "max_per_hour", 12)
        max_bytes = self.app.cfg.getint("irc", "max_line_bytes", 400)
        now = time.time()
        out = []
        db = self.app.db
        db.x("UPDATE announcements SET sent_at = 'expired' WHERE sent_at IS NULL "
             "AND created_at < datetime('now', '-6 hours')")
        for chan in {c.lower() for c in channels}:
            if now - self.last_sent.get(chan, 0) < min_interval:
                continue
            sent_hour = db.scalar("SELECT COUNT(*) FROM announcements WHERE channel = ? AND sent_at IS NOT NULL "
                                  "AND sent_at != 'expired' AND sent_at > datetime('now', '-1 hour')", (chan,), 0)
            if sent_hour >= max_hour:
                continue
            row = db.one("SELECT id, text FROM announcements WHERE channel = ? AND sent_at IS NULL "
                         "ORDER BY priority, id LIMIT 1", (chan,))
            if not row:
                continue
            db.x("UPDATE announcements SET sent_at = ? WHERE id = ?", (utcnow(), row["id"]))
            self.last_sent[chan] = now
            for line in [l for l in row["text"].replace("\r", "").split("\n") if l.strip()][:5]:
                out.append((chan, irc.truncate(line, max_bytes)))
        return out

    def pending(self) -> int:
        return self.app.db.scalar("SELECT COUNT(*) FROM announcements WHERE sent_at IS NULL", (), 0)
