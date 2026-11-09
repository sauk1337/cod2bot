"""Configuration loading: config/cod2bot.ini, config/news_sources.ini and .env."""
from __future__ import annotations

import configparser
import os
import secrets
from pathlib import Path

# cod2bot/ (project root) = backend/../
ROOT = Path(__file__).resolve().parents[3]


def _load_env(path: Path) -> dict:
    env = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        env[key.strip()] = value
    return env


class Config:
    """Thin wrapper around ConfigParser with typed getters and defaults."""

    def __init__(self, root: Path | None = None, main: str | None = None,
                 sources: str | None = None):
        self.root = Path(root or os.environ.get("COD2BOT_ROOT") or ROOT)
        self.main_path = Path(main) if main else self.root / "config" / "cod2bot.ini"
        self.sources_path = Path(sources) if sources else self.root / "config" / "news_sources.ini"
        self.reload()

    def reload(self) -> None:
        self.cp = configparser.ConfigParser(inline_comment_prefixes=(";",), interpolation=None)
        self.cp.read(self.main_path, encoding="utf-8")
        self.sources = configparser.ConfigParser(inline_comment_prefixes=(";",), interpolation=None)
        self.sources.read(self.sources_path, encoding="utf-8")
        self.env = _load_env(self.root / ".env")
        self.env.update({k: v for k, v in os.environ.items() if k.startswith("COD2BOT_")})

    # -- typed getters -------------------------------------------------
    def get(self, section: str, key: str, default: str = "") -> str:
        return self.cp.get(section, key, fallback=default).strip()

    def getint(self, section: str, key: str, default: int = 0) -> int:
        try:
            return self.cp.getint(section, key, fallback=default)
        except ValueError:
            return default

    def getfloat(self, section: str, key: str, default: float = 0.0) -> float:
        try:
            return self.cp.getfloat(section, key, fallback=default)
        except ValueError:
            return default

    def getbool(self, section: str, key: str, default: bool = False) -> bool:
        try:
            return self.cp.getboolean(section, key, fallback=default)
        except ValueError:
            return default

    def getlist(self, section: str, key: str, default: str = "", sep: str | None = None) -> list:
        raw = self.get(section, key, default)
        parts = raw.split(sep) if sep else raw.replace(",", " ").split()
        return [p.strip() for p in parts if p.strip()]

    def path(self, section: str, key: str, default: str) -> Path:
        p = Path(self.get(section, key, default))
        return p if p.is_absolute() else self.root / p

    # -- secrets -------------------------------------------------------
    def api_token(self) -> str:
        """Shared secret between Eggdrop and the backend.

        Order: COD2BOT_API_TOKEN (.env / environment), then data/api.token.
        If neither exists a token file is generated (mode 600).
        """
        token = self.env.get("COD2BOT_API_TOKEN", "").strip()
        if token:
            return token
        tfile = self.root / "data" / "api.token"
        if tfile.exists():
            token = tfile.read_text(encoding="utf-8").strip()
            if token:
                return token
        tfile.parent.mkdir(parents=True, exist_ok=True)
        token = secrets.token_hex(24)
        tfile.write_text(token + "\n", encoding="utf-8")
        try:
            os.chmod(tfile, 0o600)
        except OSError:
            pass
        return token
