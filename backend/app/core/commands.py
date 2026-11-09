"""Command registry, request context and replies."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

# Permission levels (mapped from Eggdrop flags by the Tcl script)
USER, REGISTERED, MODERATOR, ADMIN, OWNER = 0, 1, 2, 3, 4
LEVEL_NAMES = {USER: "user", REGISTERED: "registered", MODERATOR: "moderator", ADMIN: "admin", OWNER: "owner"}


@dataclass
class Context:
    nick: str
    host: str = ""
    handle: str = "*"
    chan: str = ""
    level: int = USER
    command: str = ""
    args: list = field(default_factory=list)
    lang: str = "en"
    prefix: str = "!"

    @property
    def account(self) -> str:
        """QuakeNet Q account from a +x host (account.users.quakenet.org)."""
        host = self.host.split("@", 1)[-1].lower()
        m = re.fullmatch(r"([a-z0-9_\-\[\]\\`^{}|]+)\.users\.quakenet\.org", host)
        return m.group(1) if m else ""

    @property
    def private(self) -> bool:
        return not self.chan.startswith(("#", "&"))

    @property
    def who(self) -> str:
        return f"{self.nick}!{self.host}" if self.host else self.nick

    def arg(self, i: int, default: str = "") -> str:
        return self.args[i] if len(self.args) > i else default

    def rest(self, start: int) -> str:
        return " ".join(self.args[start:])


@dataclass
class Reply:
    """Lines for the channel (or private target) and lines for a NOTICE to the user."""
    chan: list = field(default_factory=list)
    notice: list = field(default_factory=list)

    def say(self, *lines: str) -> "Reply":
        self.chan.extend(l for l in lines if l)
        return self

    def tell(self, *lines: str) -> "Reply":
        self.notice.extend(l for l in lines if l)
        return self

    def extend(self, other: "Reply") -> "Reply":
        self.chan.extend(other.chan)
        self.notice.extend(other.notice)
        return self


@dataclass
class Command:
    name: str
    func: Callable
    module: str
    level: int = USER
    aliases: tuple = ()
    help_key: str = ""
    category: str = "general"
    usage: str = ""
    admin_log: bool = False
    hidden: bool = False


class Registry:
    def __init__(self):
        self.commands: dict[str, Command] = {}
        self.primary: dict[str, Command] = {}

    def add(self, cmd: Command) -> None:
        self.primary[cmd.name] = cmd
        for name in (cmd.name, *cmd.aliases):
            self.commands[name.lower()] = cmd

    def get(self, name: str) -> Command | None:
        return self.commands.get(name.lower())

    def by_category(self) -> dict[str, list[Command]]:
        out: dict[str, list[Command]] = {}
        for cmd in self.primary.values():
            if not cmd.hidden:
                out.setdefault(cmd.category, []).append(cmd)
        return out


def command(registry: Registry, name: str, module: str, level: int = USER, aliases=(), category: str = "general",
            usage: str = "", help_key: str = "", admin_log: bool = False, hidden: bool = False):
    def deco(func):
        registry.add(Command(name, func, module, level, tuple(aliases), help_key or f"help.{name}", category,
                             usage, admin_log, hidden))
        return func
    return deco


def page_arg(value: str, default: int = 1) -> int | None:
    """Return a page number if value is a positive integer (max 999)."""
    if value.isdigit() and 0 < int(value) < 1000:
        return int(value)
    return None


NAME_RE = re.compile(r"^[\w\-\[\]\\`^{}|.#&+@']{1,32}$", re.UNICODE)


def valid_name(value: str, maxlen: int = 32) -> bool:
    return bool(value) and len(value) <= maxlen and bool(NAME_RE.match(value))


URL_RE = re.compile(r"^https?://[a-z0-9.\-]+(:\d{1,5})?(/[^\s]*)?$", re.IGNORECASE)


def valid_url(value: str) -> bool:
    return bool(URL_RE.match(value)) and len(value) <= 300
