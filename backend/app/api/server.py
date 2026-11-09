"""Local HTTP interface for the Eggdrop script.

Protocol (UTF-8 text, one item per line, no JSON needed in Tcl):
  POST /cmd       form: nick host hand chan level cmd args colours
                  -> lines "C<TAB>text" (channel) / "N<TAB>text" (notice to nick)
  GET  /announce?channels=#a,#b   -> lines "<#chan><TAB>text"
  GET  /commands  -> space separated command names (incl. aliases)
  GET  /health    -> "OK <version> <uptime-seconds>"
Every request needs the header X-COD2BOT-Token. Only loopback clients are served.
"""
from __future__ import annotations

import hmac
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from ..core import log
from ..core.commands import OWNER, USER, Context

L = log.get("api")
MAX_BODY = 8192


def make_handler(app, router, token: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "COD2BOT"
        sys_version = ""

        def log_message(self, fmt, *args):  # route http.server logs to our logger
            L.debug("%s %s", self.client_address[0], fmt % args)

        def _send(self, code: int, body: str = "") -> None:
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self) -> bool:
            if self.client_address[0] not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
                self._send(403, "forbidden")
                return False
            given = self.headers.get("X-COD2BOT-Token", "")
            if not hmac.compare_digest(given.encode(), token.encode()):
                L.warning("bad token from %s", self.client_address[0])
                self._send(401, "unauthorized")
                return False
            return True

        def do_GET(self):
            if not self._authorized():
                return
            url = urlsplit(self.path)
            qs = parse_qs(url.query)
            if url.path == "/health":
                import time
                return self._send(200, f"OK {app.version} {int(time.time() - app.started)}")
            if url.path == "/commands":
                return self._send(200, " ".join(sorted(app.registry.commands)))
            if url.path == "/announce":
                chans = [c for c in ",".join(qs.get("channels", [""])).split(",") if c.startswith(("#", "&"))]
                items = app.services["announcer"].pull(chans[:50])
                return self._send(200, "\n".join(f"{c}\t{t}" for c, t in items))
            self._send(404, "not found")

        def do_POST(self):
            if not self._authorized():
                return
            url = urlsplit(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._send(413, "too large")
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            form = {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}
            if url.path != "/cmd":
                return self._send(404, "not found")
            try:
                level = max(USER, min(OWNER, int(form.get("level", "0"))))
            except ValueError:
                level = USER
            args = form.get("args", "").split()
            ctx = Context(nick=form.get("nick", "")[:32], host=form.get("host", "")[:120],
                          handle=form.get("hand", "*")[:32], chan=form.get("chan", "")[:64], level=level,
                          command=form.get("cmd", "")[:32], args=args[:40], lang=app.lang, prefix=app.prefix)
            if not ctx.nick:
                return self._send(400, "nick required")
            reply = router.handle(ctx, colours=form.get("colours", "1") == "1")
            lines = [f"C\t{l}" for l in reply.chan] + [f"N\t{l}" for l in reply.notice]
            self._send(200, "\n".join(lines))

    return Handler


class ApiServer:
    def __init__(self, app, router, host: str, port: int, token: str):
        self.httpd = ThreadingHTTPServer((host, port), make_handler(app, router, token))
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="api", daemon=True)

    @property
    def port(self) -> int:
        return self.httpd.server_address[1]

    def start(self) -> None:
        self.thread.start()
        L.info("API listening on %s:%d", *self.httpd.server_address[:2])

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
