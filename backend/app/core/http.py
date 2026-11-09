"""Polite HTTP client (stdlib only).

- honest User-Agent, timeouts and a maximum download size
- robots.txt is checked (cached 12h) before fetching a URL
- minimum interval between two requests to the same host
- conditional requests (ETag / Last-Modified) to save bandwidth
- gzip support
"""
from __future__ import annotations

import gzip
import json
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from . import log

L = log.get("core")


class FetchError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


@dataclass
class Response:
    url: str
    status: int
    body: bytes
    headers: dict = field(default_factory=dict)
    not_modified: bool = False

    @property
    def text(self) -> str:
        ctype = self.headers.get("content-type", "")
        charset = "utf-8"
        if "charset=" in ctype:
            charset = ctype.split("charset=", 1)[1].split(";")[0].strip() or "utf-8"
        try:
            return self.body.decode(charset, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.body.decode("utf-8", errors="replace"))


class Robots:
    """robots.txt matcher following RFC 9309: the group of the most specific
    matching user-agent (or *) applies; inside it the longest matching rule
    wins, Allow wins ties; '*' and '$' wildcards are supported.
    (urllib.robotparser uses first-match and no wildcards, which wrongly
    blocks e.g. "Disallow: /" + "Allow: /*.xml".)"""

    def __init__(self, text: str, agent: str):
        groups: list[tuple[list[str], list[tuple[bool, str]]]] = []
        agents: list[str] = []
        rules: list[tuple[bool, str]] = []
        last_was_agent = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, value = (p.strip() for p in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if not last_was_agent and (agents or rules):
                    groups.append((agents, rules))
                    agents, rules = [], []
                agents.append(value.lower())
                last_was_agent = True
            elif key in ("allow", "disallow"):
                last_was_agent = False
                if value or key == "allow":
                    rules.append((key == "allow", value))
        if agents or rules:
            groups.append((agents, rules))
        me = agent.lower()
        chosen: list[tuple[bool, str]] | None = None
        best = -1
        for names, rs in groups:
            for name in names:
                score = len(name) if name != "*" and name in me else (0 if name == "*" else -1)
                if score > best:
                    best, chosen = score, list(rs)
                elif score == best and score >= 0 and chosen is not None:
                    chosen.extend(rs)  # same agent in several groups: merge
        self.rules = chosen or []

    @staticmethod
    def _match(pattern: str, path: str) -> bool:
        rx = "".join(".*" if ch == "*" else re.escape(ch) for ch in pattern.rstrip("$"))
        if pattern.endswith("$"):
            rx += "$"
        return re.match(rx, path) is not None

    def allowed(self, path: str) -> bool:
        best_len, verdict = -1, True
        for allow, pattern in self.rules:
            if pattern and self._match(pattern, path):
                if len(pattern) > best_len or (len(pattern) == best_len and allow):
                    best_len, verdict = len(pattern), allow
        return verdict


class HttpClient:
    def __init__(self, user_agent: str, timeout: float = 12, min_host_interval: float = 2,
                 max_bytes: int = 3_000_000, respect_robots: bool = True):
        self.user_agent = user_agent
        self.timeout = timeout
        self.min_host_interval = min_host_interval
        self.max_bytes = max_bytes
        self.respect_robots = respect_robots
        self._last: dict[str, float] = {}
        self._robots: dict[str, tuple[float, "Robots | None"]] = {}
        self._lock = threading.Lock()
        self.requests = 0
        self.errors = 0

    # -- robots.txt ----------------------------------------------------
    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        now = time.time()
        cached = self._robots.get(base)
        if cached is None or now - cached[0] > 12 * 3600:
            rules = None
            try:
                resp = self._raw_get(base + "/robots.txt", {}, check_robots=False)
                if resp.status < 400:
                    rules = Robots(resp.text, self.user_agent.split("/")[0])
            except FetchError as e:
                # 4xx = no robots policy; network errors: allow but retry soon
                if e.status == 0:
                    now -= 11 * 3600
            self._robots[base] = (now, rules)
            cached = self._robots[base]
        rules = cached[1]
        if rules is None:
            return True
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        return rules.allowed(path)

    # -- rate limit ----------------------------------------------------
    def _wait_host(self, host: str) -> None:
        with self._lock:
            last = self._last.get(host, 0)
            wait = self.min_host_interval - (time.time() - last)
            self._last[host] = time.time() + max(0, wait)
        if wait > 0:
            time.sleep(wait)

    # -- requests ------------------------------------------------------
    def get(self, url: str, etag: str | None = None, last_modified: str | None = None,
            accept: str = "*/*") -> Response:
        if not url.lower().startswith(("http://", "https://")):
            raise FetchError("only http(s) URLs are allowed")
        if not self.allowed(url):
            raise FetchError("disallowed by robots.txt", 999)
        headers = {"Accept": accept}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        return self._raw_get(url, headers)

    def get_json(self, url: str):
        resp = self.get(url, accept="application/json")
        try:
            return resp.json()
        except ValueError as e:
            raise FetchError(f"invalid JSON from {url}: {e}", resp.status) from e

    def _raw_get(self, url: str, headers: dict, check_robots: bool = True) -> Response:
        host = urlsplit(url).netloc.lower()
        self._wait_host(host)
        hdrs = {"User-Agent": self.user_agent, "Accept-Encoding": "gzip"}
        hdrs.update(headers)
        req = urllib.request.Request(url, headers=hdrs)
        self.requests += 1
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                body = r.read(self.max_bytes + 1)
                if len(body) > self.max_bytes:
                    raise FetchError(f"response larger than {self.max_bytes} bytes")
                rh = {k.lower(): v for k, v in r.headers.items()}
                if rh.get("content-encoding") == "gzip":
                    body = gzip.decompress(body)
                return Response(r.geturl(), r.status, body, rh)
        except urllib.error.HTTPError as e:
            if e.code == 304:
                return Response(url, 304, b"", {k.lower(): v for k, v in e.headers.items()}, True)
            self.errors += 1
            raise FetchError(f"HTTP {e.code} for {url}", e.code) from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            self.errors += 1
            raise FetchError(f"network error for {url}: {getattr(e, 'reason', e)}") from None
