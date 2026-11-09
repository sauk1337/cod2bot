"""Logging: one rotating file per module (logs/<module>.log) plus logs/backend.log."""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

_configured = {"dir": None, "level": logging.INFO, "max_bytes": 5 << 20, "backups": 5}
MODULES = ("core", "api", "news", "tournaments", "servers", "community", "cod2", "admin", "scheduler")


def setup(log_dir: Path, level: str = "INFO", max_mb: int = 5, backups: int = 5,
          console: bool = False) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    _configured.update(dir=log_dir, level=getattr(logging, level.upper(), logging.INFO),
                       max_bytes=max(1, max_mb) << 20, backups=backups)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s [%(name)s] %(message)s")
    root = logging.getLogger("cod2bot")
    root.setLevel(_configured["level"])
    root.handlers.clear()
    h = logging.handlers.RotatingFileHandler(log_dir / "backend.log", maxBytes=_configured["max_bytes"],
                                             backupCount=backups, encoding="utf-8")
    h.setFormatter(fmt)
    root.addHandler(h)
    if console:
        c = logging.StreamHandler()
        c.setFormatter(fmt)
        root.addHandler(c)
    for mod in MODULES:
        lg = logging.getLogger(f"cod2bot.{mod}")
        lg.handlers.clear()
        mh = logging.handlers.RotatingFileHandler(log_dir / f"{mod}.log", maxBytes=_configured["max_bytes"],
                                                  backupCount=backups, encoding="utf-8")
        mh.setFormatter(fmt)
        lg.addHandler(mh)  # propagates to backend.log too


def get(module: str) -> logging.Logger:
    return logging.getLogger(f"cod2bot.{module}")
