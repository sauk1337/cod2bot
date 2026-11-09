"""Test helpers: isolated app in a temp directory + fake HTTP client."""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
FIX = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(BACKEND))

from app.core.commands import Context, USER  # noqa: E402
from app.core.config import Config  # noqa: E402
from app.core.http import FetchError, Response  # noqa: E402


def fixture(name: str) -> bytes:
    return (FIX / name).read_bytes()


class FakeHttp:
    """Maps URL substrings to fixture files (or exceptions)."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[str] = []
        self.requests = self.errors = 0

    def _find(self, url):
        self.calls.append(url)
        for key, value in self.routes.items():
            if key in url:
                if isinstance(value, Exception):
                    raise value
                return value
        raise FetchError(f"HTTP 404 for {url}", 404)

    def get(self, url, etag=None, last_modified=None, accept="*/*"):
        value = self._find(url)
        body = fixture(value) if isinstance(value, str) else value
        return Response(url, 200, body, {"content-type": "application/xml; charset=utf-8"})

    def get_json(self, url):
        value = self._find(url)
        return json.loads(fixture(value)) if isinstance(value, str) else value


FPS_ROUTES = {
    "/api/v1/news": "fps_news.json",
    "/tournament/powerhouse-cod2-challenge-season-6/teams": "fps_tournament_teams.json",
    "/api/v1/tournament?": "fps_tournaments.json",
    "/api/v1/tournament/": "fps_tournament_detail.json",
    "/api/v1/tournament": "fps_tournaments.json",
}


class AppTestCase(unittest.TestCase):
    """Builds a fresh application (no background jobs) for every test."""

    ini_overrides: dict = {}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cod2bot-test-"))
        shutil.copytree(ROOT / "config", self.tmp / "config")
        (self.tmp / "data" / "seed").mkdir(parents=True)
        shutil.copy(ROOT / "data" / "seed" / "cod2.json", self.tmp / "data" / "seed" / "cod2.json")
        ini = self.tmp / "config" / "cod2bot.ini"
        text = ini.read_text(encoding="utf-8")
        text = text.replace("channels = #yourchannel", "channels = #cod2bot #test")
        text = text.replace("min_interval = 90", "min_interval = 0")
        text = text.replace("command_cooldown = 3", "command_cooldown = 0")
        text = text.replace("user_max = 5", "user_max = 1000")
        for (section, key), value in self.ini_overrides.items():
            text = text.replace(f"[{section}]", f"[{section}]\n{key} = {value}", 1)
        ini.write_text(text, encoding="utf-8")
        from app.bootstrap import build
        self.app, self.router, self.scheduler = build(Config(self.tmp), start_jobs=False)

    def tearDown(self):
        self.app.db.close()
        import logging
        for name in list(logging.Logger.manager.loggerDict):
            if name.startswith("cod2bot"):
                for h in logging.getLogger(name).handlers[:]:
                    h.close()
                    logging.getLogger(name).removeHandler(h)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cmd(self, line: str, level: int = USER, nick: str = "Tester", host: str = "tester@1.2.3.4",
                chan: str = "#cod2bot", handle: str = "*", plain: bool = True):
        from app.core import irc
        words = line.lstrip("!").split()
        ctx = Context(nick=nick, host=host, handle=handle, chan=chan, level=level, command=words[0],
                      args=words[1:], lang=self.app.lang, prefix="!")
        reply = self.router.handle(ctx)
        if plain:
            reply.chan = [irc.strip(l) for l in reply.chan]
            reply.notice = [irc.strip(l) for l in reply.notice]
        return reply

    def all_text(self, reply) -> str:
        return "\n".join(reply.chan + reply.notice)
