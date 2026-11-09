"""Command dispatcher: permissions, modules, rate limits, output shaping."""
from __future__ import annotations

import re
import threading
import time
import traceback
from collections import defaultdict, deque

from . import irc, log
from .commands import LEVEL_NAMES, MODERATOR, Context, Reply

L = log.get("api")
CMD_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,31}$")


class RateLimiter:
    def __init__(self, max_calls: int, window: float, cooldown: float):
        self.max_calls, self.window, self.cooldown = max_calls, window, cooldown
        self.calls: dict[str, deque] = defaultdict(deque)
        self.last_cmd: dict[tuple[str, str], float] = {}
        self.lock = threading.Lock()

    def check(self, who: str, cmd: str) -> bool:
        now = time.time()
        with self.lock:
            q = self.calls[who]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.max_calls:
                return False
            if now - self.last_cmd.get((who, cmd), 0) < self.cooldown:
                return False
            q.append(now)
            self.last_cmd[(who, cmd)] = now
            if len(self.last_cmd) > 5000:
                self.last_cmd = {k: v for k, v in self.last_cmd.items() if now - v < 600}
            return True


class Router:
    def __init__(self, app):
        self.app = app
        cfg = app.cfg
        self.limiter = RateLimiter(cfg.getint("ratelimit", "user_max", 5), cfg.getint("ratelimit", "user_window", 30),
                                   cfg.getfloat("ratelimit", "command_cooldown", 3))

    def handle(self, ctx: Context, colours: bool = True) -> Reply:
        reply = self._dispatch(ctx)
        return self.shape(reply, colours)

    def _dispatch(self, ctx: Context, depth: int = 0) -> Reply:
        app = self.app
        name = ctx.command.lower()
        if not CMD_RE.match(name):
            return Reply()
        cmd = app.registry.get(name)
        if cmd is None:
            return Reply()  # unknown commands are ignored silently (other scripts may handle them)
        if app.maintenance and ctx.level < MODERATOR and cmd.name not in ("ping", "version"):
            return Reply().tell(irc.line("INFO", irc.warn(app.t("general.maintenance", ctx.lang))))
        if not app.module_enabled(cmd.module):
            return Reply().tell(irc.line("INFO", app.t("general.module_off", ctx.lang, m=cmd.module)))
        if ctx.level < cmd.level:
            return Reply().tell(irc.error(app.t("perm.need", ctx.lang, level=LEVEL_NAMES[cmd.level])))
        if depth == 0 and ctx.level < MODERATOR and not self.limiter.check(ctx.host or ctx.nick, cmd.name):
            L.info("rate limited %s (%s)", ctx.who, cmd.name)
            return Reply()
        try:
            result = cmd.func(app, ctx)
        except Exception as e:
            L.error("command %s by %s failed: %s\n%s", cmd.name, ctx.who, e, traceback.format_exc())
            return Reply().tell(irc.error(app.t("general.error", ctx.lang)))
        if isinstance(result, dict) and "redirect" in result and depth < 1:
            sub = Context(**{**ctx.__dict__, "command": result["redirect"], "args": result["args"]})
            return self._dispatch(sub, depth + 1)
        app.count_command(cmd.name)
        if cmd.admin_log:
            app.log_admin(ctx, cmd.name, " ".join(ctx.args), "ok")
        return result if isinstance(result, Reply) else Reply()

    def shape(self, reply: Reply, colours: bool = True) -> Reply:
        cfg = self.app.cfg
        max_bytes = cfg.getint("irc", "max_line_bytes", 400)
        max_chan = cfg.getint("irc", "max_channel_lines", 4)
        max_notice = cfg.getint("irc", "max_notice_lines", 10)
        use_colours = colours and cfg.getbool("irc", "colors", True)
        ascii_only = cfg.getbool("irc", "ascii_only", False)

        def fix(line: str) -> str:
            line = line.replace("\r", " ").replace("\n", " ")
            if not use_colours:
                line = irc.strip(line)
            if ascii_only:
                line = irc.ascii_fold(line)
            return irc.truncate(line, max_bytes)

        chan = [fix(l) for l in reply.chan]
        notice = [fix(l) for l in reply.notice]
        if len(chan) > max_chan:  # overflow goes to the user by notice
            notice = chan[max_chan:] + notice
            chan = chan[:max_chan]
        return Reply(chan, notice[:max_notice])
