"""Call of Duty 2 server query protocol (Quake III style, UDP).

Verified on 2026-11-06 against the live network:
  - server:  "\\xff\\xff\\xff\\xffgetstatus\\n" -> "statusResponse\\n\\\\key\\\\value...\\n<score> <ping> \\"name\\"\\n..."
  - master:  cod2master.activision.com:20710, "getservers <proto> full empty"
             proto 118 = 1.3, 117 = 1.2, 115 = 1.0 -> "getserversResponse" + 6-byte entries
CoD2 does not use GameSpy for its server browser.
"""
from __future__ import annotations

import re
import socket
import struct
import time
from dataclasses import dataclass, field

PREFIX = b"\xff\xff\xff\xff"
BACKSLASH = b"\\"
HOST_RE = re.compile(r"^(?:\d{1,3}(?:\.\d{1,3}){3}|[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?)+)$",
                     re.IGNORECASE)

GAMETYPES = {"dm": "Deathmatch", "tdm": "Team Deathmatch", "sd": "Search & Destroy", "ctf": "Capture the Flag",
             "hq": "Headquarters", "ctb": "Capture the Bunker", "htf": "Hold the Flag", "codjumper": "CoDJumper",
             "zom": "Zombies", "ftag": "Freeze Tag", "gg": "Gun Game", "bel": "Behind Enemy Lines"}


@dataclass
class Player:
    name: str
    score: int
    ping: int


@dataclass
class Status:
    host: str
    port: int
    online: bool
    ping: int = 0
    info: dict = field(default_factory=dict)
    players: list = field(default_factory=list)
    error: str = ""

    @property
    def hostname(self) -> str:
        return self.info.get("sv_hostname", "")

    @property
    def map(self) -> str:
        return self.info.get("mapname", "")

    @property
    def gametype(self) -> str:
        return self.info.get("g_gametype", "")

    @property
    def maxplayers(self) -> int:
        try:
            return int(self.info.get("sv_maxclients", 0))
        except ValueError:
            return 0

    @property
    def version(self) -> str:
        return self.info.get("shortversion", "")

    @property
    def password(self) -> bool:
        return self.info.get("pswrd", "0") == "1" or self.info.get("g_needpass", "0") == "1"

    @property
    def punkbuster(self) -> bool:
        return self.info.get("sv_punkbuster", "0") == "1"

    @property
    def mod(self) -> str:
        return self.info.get("fs_game", "") or ("mod" if self.info.get("mod") == "1" else "")


def parse_address(text: str, default_port: int = 28960) -> tuple[str, int] | None:
    text = text.strip()
    if text.count(":") == 1:
        host, _, port_s = text.partition(":")
        if not port_s.isdigit():
            return None
        port = int(port_s)
    else:
        host, port = text, default_port
    if not HOST_RE.match(host) or not 0 < port < 65536:
        return None
    if host[0].isdigit() and all(p.isdigit() for p in host.split(".")):
        if any(int(p) > 255 for p in host.split(".")):
            return None
    return host.lower(), port


def is_public(host: str) -> bool:
    """Refuse loopback/private/link-local targets (no LAN scanning from IRC)."""
    try:
        ip = socket.gethostbyname(host)
    except OSError:
        return False
    import ipaddress
    addr = ipaddress.ip_address(ip)
    return not (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast
                or addr.is_reserved or addr.is_unspecified)


def parse_status(data: bytes, host: str, port: int, ping: int) -> Status:
    if not data.startswith(PREFIX + b"statusResponse"):
        return Status(host, port, False, error="unexpected reply")
    lines = data[4:].split(b"\n")
    raw = lines[1].decode("latin-1") if len(lines) > 1 else ""
    kv = raw.split("\\")[1:]
    info = dict(zip(kv[::2], kv[1::2]))
    players = []
    for line in lines[2:]:
        m = re.match(rb'^(-?\d+) (\d+) "(.*)"$', line.strip())
        if m:
            players.append(Player(m.group(3).decode("latin-1"), int(m.group(1)), int(m.group(2))))
    players.sort(key=lambda p: -p.score)
    return Status(host, port, True, ping, info, players)


def query(host: str, port: int, timeout: float = 2.0, retries: int = 1) -> Status:
    try:
        addr = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_DGRAM)[0][4]
    except OSError as e:
        return Status(host, port, False, error=f"dns: {e}")
    last_err = "timeout"
    for _ in range(retries + 1):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        try:
            start = time.time()
            sock.sendto(PREFIX + b"getstatus\n", addr)
            data, _ = sock.recvfrom(65535)
            ping = int((time.time() - start) * 1000)
            return parse_status(data, host, port, ping)
        except socket.timeout:
            last_err = "timeout"
        except OSError as e:
            last_err = str(e)
        finally:
            sock.close()
    return Status(host, port, False, error=last_err)


def parse_master_packet(data: bytes) -> list[tuple[str, int]]:
    """getserversResponse: header, then fixed 7-byte entries "\\" + 4 IP bytes +
    2 port bytes, ended by "\\EOT". Entries are read by position because IP or
    port bytes may themselves be 0x5C ("\\")."""
    out = []
    if not data.startswith(PREFIX + b"getserversResponse"):
        return out
    i = data.find(BACKSLASH)
    while i >= 0 and i + 7 <= len(data):
        if data[i:i + 1] != BACKSLASH:
            i = data.find(BACKSLASH, i)
            continue
        if data[i + 1:i + 7] == b"EOT\x00\x00\x00":
            break
        chunk = data[i + 1:i + 7]
        port = struct.unpack(">H", chunk[4:6])[0]
        if port:
            out.append((".".join(str(b) for b in chunk[:4]), port))
        i += 7
    return out


def master_list(master: str, protocols: list[int], timeout: float = 4.0) -> set[tuple[str, int, int]]:
    mhost, _, mport = master.partition(":")
    addr = (socket.gethostbyname(mhost), int(mport or 20710))
    servers: set[tuple[str, int, int]] = set()
    for proto in protocols:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        try:
            sock.sendto(PREFIX + b"getservers %d full empty" % proto, addr)
            deadline = time.time() + timeout + 1
            while time.time() < deadline:
                try:
                    data, _ = sock.recvfrom(65535)
                except socket.timeout:
                    break
                servers.update((ip, port, proto) for ip, port in parse_master_packet(data))
        finally:
            sock.close()
    return servers
