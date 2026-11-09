"""SQLite storage with versioned migrations.

All queries use parameters (never string formatting) to prevent SQL injection.
A single connection is shared and protected by a lock; WAL mode keeps readers
fast while the scheduler writes. The interface is small on purpose so that a
PostgreSQL implementation can replace it later (see docs/ARCHITECTURE.md).
"""
from __future__ import annotations

import shutil
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import log

L = log.get("core")


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


MIGRATIONS: list[tuple[int, str]] = [
    (1, """
    CREATE TABLE IF NOT EXISTS bot_config (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS news_categories (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        keywords TEXT NOT NULL DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS news_sources (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        label TEXT NOT NULL,
        type TEXT NOT NULL,
        url TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        announce INTEGER NOT NULL DEFAULT 1,
        interval INTEGER NOT NULL DEFAULT 1800,
        category TEXT NOT NULL DEFAULT 'general',
        colour INTEGER NOT NULL DEFAULT 7,
        options TEXT NOT NULL DEFAULT '{}',
        origin TEXT NOT NULL DEFAULT 'config',
        etag TEXT, last_modified TEXT,
        last_fetch TEXT, last_ok TEXT, last_error TEXT,
        error_count INTEGER NOT NULL DEFAULT 0,
        next_fetch REAL NOT NULL DEFAULT 0,
        initialized INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS news (
        id INTEGER PRIMARY KEY,
        source_id INTEGER NOT NULL REFERENCES news_sources(id) ON DELETE CASCADE,
        guid TEXT NOT NULL,
        url TEXT NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        image TEXT,
        category TEXT NOT NULL DEFAULT 'general',
        published_at TEXT,
        fetched_at TEXT NOT NULL,
        updated_at TEXT,
        content_hash TEXT NOT NULL,
        changes INTEGER NOT NULL DEFAULT 0,
        announced INTEGER NOT NULL DEFAULT 0,
        UNIQUE (source_id, guid)
    );
    CREATE INDEX IF NOT EXISTS idx_news_published ON news(published_at DESC);
    CREATE INDEX IF NOT EXISTS idx_news_category ON news(category);
    CREATE INDEX IF NOT EXISTS idx_news_url ON news(url);
    CREATE INDEX IF NOT EXISTS idx_news_pending ON news(announced) WHERE announced = 0;

    CREATE TABLE IF NOT EXISTS tournaments (
        id INTEGER PRIMARY KEY,
        provider TEXT NOT NULL,
        ext_id TEXT NOT NULL,
        slug TEXT,
        name TEXT NOT NULL,
        game TEXT NOT NULL DEFAULT 'Call of Duty 2',
        state TEXT NOT NULL DEFAULT 'ANNOUNCE',
        mode TEXT,
        start_at TEXT,
        reg_start TEXT, reg_end TEXT, checkin_start TEXT, checkin_end TEXT,
        teams_count INTEGER, max_teams INTEGER, player_base INTEGER,
        map_pool TEXT NOT NULL DEFAULT '',
        url TEXT,
        winner TEXT,
        notes TEXT,
        first_seen TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        last_seen TEXT NOT NULL,
        detail_at TEXT,
        announced_state TEXT NOT NULL DEFAULT '',
        reminded INTEGER NOT NULL DEFAULT 0,
        created_by TEXT,
        UNIQUE (provider, ext_id)
    );
    CREATE INDEX IF NOT EXISTS idx_tournaments_start ON tournaments(start_at);
    CREATE INDEX IF NOT EXISTS idx_tournaments_state ON tournaments(state);

    CREATE TABLE IF NOT EXISTS teams (
        id INTEGER PRIMARY KEY,
        provider TEXT NOT NULL,
        ext_id TEXT NOT NULL,
        slug TEXT,
        name TEXT NOT NULL,
        tag TEXT,
        country TEXT,
        members TEXT NOT NULL DEFAULT '',
        members_count INTEGER,
        matches_count INTEGER,
        url TEXT,
        updated_at TEXT NOT NULL,
        UNIQUE (provider, ext_id)
    );
    CREATE INDEX IF NOT EXISTS idx_teams_name ON teams(name COLLATE NOCASE);
    CREATE INDEX IF NOT EXISTS idx_teams_tag ON teams(tag COLLATE NOCASE);

    CREATE TABLE IF NOT EXISTS tournament_teams (
        tournament_id INTEGER NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
        team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
        PRIMARY KEY (tournament_id, team_id)
    );

    CREATE TABLE IF NOT EXISTS ladder_entries (
        id INTEGER PRIMARY KEY,
        provider TEXT NOT NULL,
        league_id TEXT NOT NULL,
        league_name TEXT NOT NULL,
        region TEXT,
        team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
        rank INTEGER, elo INTEGER, wins INTEGER, losses INTEGER, draws INTEGER,
        updated_at TEXT NOT NULL,
        UNIQUE (provider, league_id, team_id)
    );
    CREATE INDEX IF NOT EXISTS idx_ladder_league ON ladder_entries(league_id, elo DESC);

    CREATE TABLE IF NOT EXISTS clans (
        id INTEGER PRIMARY KEY,
        tag TEXT NOT NULL UNIQUE COLLATE NOCASE,
        name TEXT NOT NULL,
        website TEXT, irc TEXT, country TEXT, description TEXT, contact TEXT,
        owner_account TEXT,
        created_by TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS players (
        id INTEGER PRIMARY KEY,
        nick TEXT NOT NULL UNIQUE COLLATE NOCASE,
        account TEXT COLLATE NOCASE,
        country TEXT,
        clan_id INTEGER REFERENCES clans(id) ON DELETE SET NULL,
        role TEXT,
        description TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_players_account ON players(account);
    CREATE INDEX IF NOT EXISTS idx_players_clan ON players(clan_id);

    CREATE TABLE IF NOT EXISTS matches (
        id INTEGER PRIMARY KEY,
        tournament_id INTEGER REFERENCES tournaments(id) ON DELETE SET NULL,
        team1 TEXT NOT NULL, team2 TEXT NOT NULL,
        score1 INTEGER, score2 INTEGER,
        map TEXT,
        played_at TEXT,
        source TEXT NOT NULL DEFAULT 'manual',
        created_by TEXT,
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_matches_played ON matches(played_at DESC);

    CREATE TABLE IF NOT EXISTS servers (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        host TEXT NOT NULL,
        port INTEGER NOT NULL,
        favorite INTEGER NOT NULL DEFAULT 1,
        monitor INTEGER NOT NULL DEFAULT 1,
        added_by TEXT,
        created_at TEXT NOT NULL,
        last_check TEXT, last_online TEXT,
        online INTEGER NOT NULL DEFAULT 0,
        fail_count INTEGER NOT NULL DEFAULT 0,
        down_announced INTEGER NOT NULL DEFAULT 0,
        hostname TEXT, map TEXT, gametype TEXT, version TEXT,
        players INTEGER, maxplayers INTEGER, ping INTEGER,
        UNIQUE (host, port)
    );

    CREATE TABLE IF NOT EXISTS server_history (
        id INTEGER PRIMARY KEY,
        server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
        ts TEXT NOT NULL,
        online INTEGER NOT NULL,
        players INTEGER, map TEXT, ping INTEGER
    );
    CREATE INDEX IF NOT EXISTS idx_server_history ON server_history(server_id, ts DESC);

    CREATE TABLE IF NOT EXISTS wars (
        id INTEGER PRIMARY KEY,
        clan1 TEXT NOT NULL COLLATE NOCASE,
        clan2 TEXT NOT NULL COLLATE NOCASE,
        format TEXT NOT NULL DEFAULT '5v5',
        scheduled_at TEXT,
        server TEXT,
        maps TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'scheduled',
        score1 INTEGER, score2 INTEGER,
        winner TEXT,
        channel TEXT,
        created_by TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        reminded TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_wars_sched ON wars(status, scheduled_at);
    CREATE INDEX IF NOT EXISTS idx_wars_clans ON wars(clan1, clan2);

    CREATE TABLE IF NOT EXISTS kb (
        id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL,
        key TEXT NOT NULL COLLATE NOCASE,
        title TEXT NOT NULL,
        body TEXT NOT NULL,
        extra TEXT NOT NULL DEFAULT '{}',
        UNIQUE (kind, key)
    );

    CREATE TABLE IF NOT EXISTS maps (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        title TEXT NOT NULL,
        location TEXT, size TEXT, modes TEXT, competitive INTEGER NOT NULL DEFAULT 0,
        description TEXT NOT NULL DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS weapons (
        id INTEGER PRIMARY KEY,
        key TEXT NOT NULL UNIQUE COLLATE NOCASE,
        name TEXT NOT NULL,
        class TEXT NOT NULL,
        faction TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS quotes (
        id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL DEFAULT 'quote',
        text TEXT NOT NULL UNIQUE,
        author TEXT,
        added_by TEXT,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS admin_logs (
        id INTEGER PRIMARY KEY,
        ts TEXT NOT NULL,
        nick TEXT, handle TEXT, host TEXT, channel TEXT,
        level INTEGER NOT NULL,
        action TEXT NOT NULL,
        args TEXT,
        result TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_admin_logs_ts ON admin_logs(ts DESC);

    CREATE TABLE IF NOT EXISTS announcements (
        id INTEGER PRIMARY KEY,
        created_at TEXT NOT NULL,
        channel TEXT NOT NULL,
        kind TEXT NOT NULL,
        text TEXT NOT NULL,
        priority INTEGER NOT NULL DEFAULT 5,
        dedupe_key TEXT,
        sent_at TEXT,
        UNIQUE (channel, dedupe_key)
    );
    CREATE INDEX IF NOT EXISTS idx_ann_pending ON announcements(channel, sent_at, priority);

    CREATE TABLE IF NOT EXISTS command_stats (
        command TEXT PRIMARY KEY,
        count INTEGER NOT NULL DEFAULT 0,
        last_used TEXT
    );
    """),
]


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=15,
                                    isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA synchronous = NORMAL")
        self.migrate()

    # -- schema --------------------------------------------------------
    def migrate(self) -> None:
        with self.lock:
            self.conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL, applied_at TEXT NOT NULL)")
            row = self.conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
            current = row["v"] or 0
            for version, sql in MIGRATIONS:
                if version <= current:
                    continue
                L.info("applying database migration %d", version)
                self.conn.execute("BEGIN")
                try:
                    for stmt in _split_sql(sql):
                        self.conn.execute(stmt)
                    self.conn.execute("INSERT INTO schema_version VALUES (?, ?)", (version, utcnow()))
                    self.conn.execute("COMMIT")
                except Exception:
                    self.conn.execute("ROLLBACK")
                    raise

    def schema_version(self) -> int:
        return self.one("SELECT MAX(version) AS v FROM schema_version")["v"] or 0

    # -- helpers -------------------------------------------------------
    def q(self, sql: str, params: tuple | list | dict = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple | list | dict = ()) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute(sql, params).fetchone()

    def x(self, sql: str, params: tuple | list | dict = ()) -> sqlite3.Cursor:
        with self.lock:
            return self.conn.execute(sql, params)

    def scalar(self, sql: str, params: tuple | list | dict = (), default=None):
        row = self.one(sql, params)
        return row[0] if row is not None and row[0] is not None else default

    @contextmanager
    def tx(self):
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise

    # -- key/value config ----------------------------------------------
    def cfg_get(self, key: str, default: str | None = None) -> str | None:
        row = self.one("SELECT value FROM bot_config WHERE key = ?", (key,))
        return row["value"] if row else default

    def cfg_set(self, key: str, value: str) -> None:
        self.x("INSERT INTO bot_config(key, value, updated_at) VALUES (?, ?, ?) "
               "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
               (key, str(value), utcnow()))

    # -- maintenance ---------------------------------------------------
    def backup(self, directory: Path, keep: int = 14) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        dest = directory / f"cod2bot-{time.strftime('%Y%m%d-%H%M%S')}.db"
        with self.lock:
            target = sqlite3.connect(str(dest))
            try:
                self.conn.backup(target)
            finally:
                target.close()
        old = sorted(directory.glob("cod2bot-*.db"))
        for f in old[:-keep] if keep > 0 else []:
            try:
                f.unlink()
            except OSError:
                pass
        return dest

    def cleanup(self, news_days: int, history_days: int, logs_days: int, ann_days: int) -> dict:
        res = {}
        with self.lock:
            res["news"] = self.conn.execute(
                "DELETE FROM news WHERE fetched_at < datetime('now', ?)", (f"-{news_days} days",)).rowcount
            res["server_history"] = self.conn.execute(
                "DELETE FROM server_history WHERE ts < datetime('now', ?)", (f"-{history_days} days",)).rowcount
            res["admin_logs"] = self.conn.execute(
                "DELETE FROM admin_logs WHERE ts < datetime('now', ?)", (f"-{logs_days} days",)).rowcount
            res["announcements"] = self.conn.execute(
                "DELETE FROM announcements WHERE created_at < datetime('now', ?)", (f"-{ann_days} days",)).rowcount
        return res

    def integrity_ok(self) -> bool:
        return self.scalar("PRAGMA quick_check") == "ok"

    def close(self) -> None:
        with self.lock:
            self.conn.close()


def _split_sql(sql: str) -> list[str]:
    return [s.strip() for s in sql.split(";") if s.strip()]


def restore(backup_file: Path, db_path: Path) -> None:
    """Restore a backup (the bot backend must be stopped)."""
    shutil.copy2(backup_file, db_path)
