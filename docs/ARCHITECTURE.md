# Architecture

## Why two processes

Eggdrop runs Tcl in its single thread: a slow web request inside a bind would freeze the bot
(and QuakeNet would disconnect it with a ping timeout). Therefore:

- **`cod2bot.tcl`** only does instant work: detecting `!command`, computing the level with `matchattr`,
  anti-flood, and `http::geturl -command` (asynchronous) to `127.0.0.1`. Replies arrive through the Tcl
  event loop that Eggdrop already services.
- **The Python backend** does everything slow (external HTTP, UDP, SQLite) in its own threads.

## Internal protocol (HTTP on localhost)

Mandatory header `X-COD2BOT-Token` (compared in constant time); only loopback clients are accepted.

| Request | Response (UTF-8 text, one line per item) |
|---|---|
| `POST /cmd` `nick host hand chan level cmd args colours` | `C<TAB>text` (to the channel/private) and `N<TAB>text` (notice to the user) |
| `GET /announce?channels=#a,#b` | `#channel<TAB>text` (at most one per channel per call) |
| `GET /commands` | command names and aliases separated by spaces |
| `GET /health` | `OK <version> <uptime seconds>` |

Plain text instead of JSON: Tcl 8.6 has no JSON parser and `tcllib` may not be installed.

## Backend

```
backend/app/
├── __main__.py      entry point: server, --check, --backup, --say
├── bootstrap.py     builds the app, registers modules and jobs, daily maintenance and digest
├── api/server.py    ThreadingHTTPServer on 127.0.0.1
├── core/
│   ├── app.py       App object: config, DB, HTTP, timezone, modules, admin log
│   ├── config.py    .ini + .env + token
│   ├── db.py        SQLite (WAL, FKs, versioned migrations, online backup, cleanup)
│   ├── http.py      polite client: RFC 9309 robots.txt, per-host limit, ETag, max size
│   ├── irc.py       colour theme, badges, CoD ^N colours, byte-safe truncation, paging
│   ├── router.py    permissions, modules, maintenance, rate limit, output shaping
│   ├── scheduler.py jobs in threads, never overlapping, exponential backoff
│   ├── announcer.py priority queue, de-duplication, minimum interval and hourly cap per channel
│   ├── general.py   general commands, help, administration
│   └── i18n.py      message catalogue (English)
├── news/            parsers (RSS, Atom, FPSChallenge, GitHub, HTML) + service and commands
├── tournaments/     FPSChallenge: tournaments, teams, ladder, winners; manual entries
├── servers/         Quake3 protocol (getstatus, master getservers) + monitor + browser
├── community/       clans, players, wars
└── cod2/            knowledge base
```

## Scheduled jobs

| Job | Interval | Does |
|---|---|---|
| `news.poll` | 60 s (each source with its own `interval`) | Downloads due sources, stores, detects changes, announces |
| `tournaments.refresh` | 900 s | Tournament list, details (every 6 h), teams and team profiles (every 24 h), winners from news, announcements on state changes and "starts in N min" |
| `servers.monitor` | 300 s | Queries favourites, history, down after 3 failures, recovery |
| `servers.browser` | 600 s | Master + `getstatus` to every server (32 in parallel, 1 UDP packet each) |
| `wars.reminders` | 60 s | Reminders 60/15 min before |
| `maintenance.daily` | 60 s (acts at `[backup] hour`) | Backup, cleanup, `PRAGMA quick_check` |
| `announcer.digest` | 60 s (acts at `digest_hour`) | Daily digest |

A failing job only delays that job (backoff up to 1 h); the others and the API keep running.

## Database (SQLite)

Tables: `news`, `news_sources`, `news_categories`, `tournaments`, `tournament_teams`, `teams`,
`ladder_entries`, `matches`, `players`, `clans`, `servers`, `server_history`, `wars`, `maps`,
`weapons`, `kb` (modes, classes, configs, commands, mods, tools, guides, tips), `quotes`,
`bot_config`, `admin_logs`, `announcements`, `command_stats`, `schema_version`.

- Indexes on dates, categories, states and lookup keys; foreign keys with `ON DELETE`.
- Duplicates: `UNIQUE(source_id, guid)` + URL de-duplication across sources; `UNIQUE(provider, ext_id)`
  for tournaments and teams; `UNIQUE(channel, dedupe_key)` for announcements.
- Migrations: the `MIGRATIONS` list in `core/db.py`; applied inside a transaction and recorded in `schema_version`.
- Every query uses parameters (`?` / `:name`); the two `UPDATE`s with a dynamic column name use a fixed whitelist.

### Moving to PostgreSQL

Database access is concentrated in `Database` (`q`, `one`, `x`, `scalar`, `tx`, `cfg_get/set`, `backup`,
`cleanup`). For PostgreSQL: implement the same class with `psycopg`, translate placeholders
(`?` → `%s`), `INSERT OR IGNORE` → `ON CONFLICT DO NOTHING`, `datetime('now', ...)` → `now() - interval`,
and `COLLATE NOCASE` → `citext`/`lower()`.
