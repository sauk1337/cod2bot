"""COD2BOT backend entry point.

  python -m app                 run the backend (API + scheduler)
  python -m app --check         fetch every source once and print a report
  python -m app --backup        make a database backup and exit
  python -m app --say '!news'   run one command locally (debug), print the IRC lines
"""
from __future__ import annotations

import argparse
import signal
import sys
import threading

from .core import irc
from .core.commands import OWNER, Context
from .core.config import Config


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="cod2bot-backend")
    ap.add_argument("--root", help="project root (default: autodetect)")
    ap.add_argument("--console", action="store_true", help="also log to the console")
    ap.add_argument("--check", action="store_true", help="test all news sources and APIs, then exit")
    ap.add_argument("--backup", action="store_true", help="backup the database and exit")
    ap.add_argument("--say", metavar="LINE", help="run one IRC command line as owner and print the output")
    ap.add_argument("--plain", action="store_true", help="strip colours in --say output")
    args = ap.parse_args(argv)

    cfg = Config(args.root)
    from .bootstrap import build
    oneshot = args.check or args.backup or args.say
    app, router, scheduler = build(cfg, start_jobs=not oneshot, console=args.console or bool(oneshot))

    if args.backup:
        print(app.db.backup(cfg.path("backup", "dir", "backups"), cfg.getint("backup", "keep", 14)))
        return 0
    if args.check:
        return check(app)
    if args.say:
        words = args.say.lstrip(app.prefix).split()
        ctx = Context(nick="console", host="console@localhost", handle="console", chan="#console", level=OWNER,
                      command=words[0] if words else "", args=words[1:], lang=app.lang, prefix=app.prefix)
        reply = router.handle(ctx)
        for line in reply.chan:
            print("CHAN  ", irc.strip(line) if args.plain else line)
        for line in reply.notice:
            print("NOTICE", irc.strip(line) if args.plain else line)
        return 0

    from .api.server import ApiServer
    port = cfg.getint("api", "port", 8765)
    try:
        api = ApiServer(app, router, cfg.get("api", "host", "127.0.0.1"), port, cfg.api_token())
    except OSError as e:
        print(f"COD2BOT backend: cannot listen on port {port} ({e}). Another backend is probably "
              f"running: stop it (pkill -f 'python3 -m app') and start again.", file=sys.stderr, flush=True)
        return 2
    api.start()
    pidfile = cfg.root / "data" / "backend.pid"
    try:
        import os
        pidfile.write_text(f"{os.getpid()}\n", encoding="utf-8")
    except OSError:
        pidfile = None
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, lambda *_: stop.set())
        except (ValueError, AttributeError):
            pass
    print(f"COD2BOT backend {app.version} listening on {cfg.get('api', 'host')}:{api.port}", flush=True)
    stop.wait()
    scheduler.stop()
    api.stop()
    app.db.close()
    if pidfile is not None:
        try:
            pidfile.unlink()
        except OSError:
            pass
    return 0


def check(app) -> int:
    """Self-test of every external data source."""
    failures = 0
    news = app.services["news"]
    for src in app.db.q("SELECT * FROM news_sources WHERE enabled = 1"):
        new, upd = news.fetch_source(src)
        row = app.db.one("SELECT last_error, (SELECT COUNT(*) FROM news WHERE source_id = ?) n FROM news_sources "
                         "WHERE id = ?", (src["id"], src["id"]))
        ok = row["last_error"] is None
        failures += 0 if ok else 1
        print(f"[{'OK ' if ok else 'ERR'}] news {src['name']:<14} items={row['n']:<4} new={new:<3} "
              f"{row['last_error'] or ''}")
    try:
        app.services["tournaments"].refresh()
        n = app.db.scalar("SELECT COUNT(*) FROM tournaments", (), 0)
        t = app.db.scalar("SELECT COUNT(*) FROM teams", (), 0)
        print(f"[OK ] tournaments fpschallenge tournaments={n} teams={t}")
    except Exception as e:
        failures += 1
        print(f"[ERR] tournaments: {e}")
    try:
        app.services["servers"].scan_master()
        srv = app.services["servers"]
        print(f"[OK ] servers master listed={srv.browser_total} answered={len(srv.browser)}")
    except Exception as e:
        failures += 1
        print(f"[ERR] servers master: {e}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
