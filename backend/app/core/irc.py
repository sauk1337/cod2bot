"""IRC formatting: mIRC colour theme, CoD colour codes, safe truncation, paging.

Look of every message:

    [COD2][NEWS] FPSChallenge » Title of the news · 2h ago
    ^^^^^^ ^^^^^^ two-tone badge: COD2 (black on yellow) + section colour

All colour codes use two digits ("\\x0304") so a following number is never
eaten by the colour code. Channels with mode +c are stripped by the Tcl side.
"""
from __future__ import annotations

import re
import unicodedata

B = "\x02"   # bold
C = "\x03"   # colour
R = "\x0f"   # reset
U = "\x1f"   # underline
I = "\x1d"   # italic

# mIRC palette numbers
WHITE, BLACK, NAVY, GREEN, RED, MAROON, PURPLE, ORANGE = 0, 1, 2, 3, 4, 5, 6, 7
YELLOW, LIME, TEAL, CYAN, BLUE, PINK, GREY, SILVER = 8, 9, 10, 11, 12, 13, 14, 15

# Section badge colours: (foreground, background)
SECTIONS = {
    "NEWS": (WHITE, GREEN),
    "SERVER": (WHITE, BLUE),
    "SERVERS": (WHITE, BLUE),
    "TOURNEY": (WHITE, MAROON),
    "LADDER": (WHITE, MAROON),
    "RESULT": (BLACK, LIME),
    "WAR": (WHITE, RED),
    "CLAN": (WHITE, PURPLE),
    "PLAYER": (WHITE, TEAL),
    "MAP": (BLACK, ORANGE),
    "WEAPON": (BLACK, SILVER),
    "INFO": (WHITE, GREY),
    "HELP": (BLACK, CYAN),
    "FUN": (BLACK, PINK),
    "ADMIN": (YELLOW, BLACK),
    "DIGEST": (WHITE, NAVY),
    "ERROR": (WHITE, RED),
}

THEME = {
    "brand_fg": BLACK, "brand_bg": YELLOW,
    "label": GREY, "sep": GREY, "muted": GREY, "url": BLUE,
    "good": LIME, "bad": RED, "warn": ORANGE, "accent": YELLOW, "info": CYAN,
    "brand_text": "COD2",
}


def configure(cfg) -> None:
    """Optional overrides from [theme] in cod2bot.ini (numbers 0-15)."""
    if not cfg.cp.has_section("theme"):
        return
    for key in list(THEME):
        val = cfg.get("theme", key, "")
        if not val:
            continue
        if key == "brand_text":
            THEME[key] = val[:12]
        elif val.isdigit() and 0 <= int(val) <= 15:
            THEME[key] = int(val)


def col(text: str, fg: int, bg: int | None = None) -> str:
    if bg is None:
        return f"{C}{fg:02d}{text}{C}"
    return f"{C}{fg:02d},{bg:02d}{text}{C}"


def bold(text: str) -> str:
    return f"{B}{text}{B}"


def badge(section: str) -> str:
    fg, bg = SECTIONS.get(section.upper(), (WHITE, GREY))
    brand = col(f" {THEME['brand_text']} ", THEME["brand_fg"], THEME["brand_bg"])
    return f"{B}{brand}{col(f' {section.upper()} ', fg, bg)}{B}"


def sep(char: str = "·") -> str:
    return " " + col(char, THEME["sep"]) + " "


def arrow() -> str:
    return " " + col("»", THEME["sep"]) + " "


def label(name: str, value) -> str:
    return f"{col(name + ':', THEME['label'])} {value}"


def url(link: str) -> str:
    # trailing space after the colour reset keeps the link clickable everywhere
    return col(link, THEME["url"])


def good(text: str) -> str:
    return col(bold(text), THEME["good"])


def bad(text: str) -> str:
    return col(bold(text), THEME["bad"])


def warn(text: str) -> str:
    return col(text, THEME["warn"])


def accent(text: str) -> str:
    return col(text, THEME["accent"])


def info(text: str) -> str:
    return col(text, THEME["info"])


def muted(text: str) -> str:
    return col(text, THEME["muted"])


def coloured(text: str, colour: int) -> str:
    return col(text, max(0, min(15, int(colour))))


def line(section: str, *parts: str) -> str:
    body = sep().join(p for p in parts if p)
    return f"{badge(section)} {body}"


def error(text: str) -> str:
    return f"{badge('ERROR')} {text}"


def bar(value: int, maximum: int, width: int = 10) -> str:
    """Small player-count bar: ▰▰▰▱▱ coloured by fill level."""
    if not maximum:
        return ""
    filled = max(0, min(width, round(width * value / maximum)))
    ratio = value / maximum
    colour = THEME["good"] if ratio < 0.7 else (THEME["warn"] if ratio < 1 else THEME["bad"])
    return col("▰" * filled, colour) + col("▱" * (width - filled), THEME["muted"])


# -- Call of Duty colour codes (^0..^9) ---------------------------------
COD_TO_MIRC = {"0": GREY, "1": RED, "2": LIME, "3": YELLOW, "4": BLUE, "5": CYAN,
               "6": PINK, "7": None, "8": ORANGE, "9": SILVER}
_COD_RE = re.compile(r"\^([0-9])")


def cod_colours(text: str, enabled: bool = True) -> str:
    """Convert ^1Red^7White to mIRC colours (or strip them)."""
    if not enabled:
        return strip_cod(text)
    out, opened = [], False
    pos = 0
    for m in _COD_RE.finditer(text):
        out.append(text[pos:m.start()])
        colour = COD_TO_MIRC[m.group(1)]
        out.append(C if colour is None else f"{C}{colour:02d}")
        opened = colour is not None
        pos = m.end()
    out.append(text[pos:])
    if opened:
        out.append(C)
    return "".join(out)


def strip_cod(text: str) -> str:
    return _COD_RE.sub("", text)


_MIRC_RE = re.compile(r"\x03(\d{1,2}(,\d{1,2})?)?|[\x02\x0f\x16\x1d\x1f\x1e\x11]")


def strip(text: str) -> str:
    return _MIRC_RE.sub("", text)


def clean(text: str, limit: int = 0) -> str:
    """Single-line, control-character free text coming from the web."""
    text = re.sub(r"[\x00-\x1f\x7f]", " ", str(text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    if limit and len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def ascii_fold(text: str) -> str:
    repl = {"»": ">>", "«": "<<", "·": "-", "…": "...", "▰": "#", "▱": "-", "–": "-", "—": "-",
            "“": '"', "”": '"', "’": "'", "‘": "'", "★": "*", "☆": "*", "●": "*", "○": "o"}
    for k, v in repl.items():
        text = text.replace(k, v)
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def truncate(text: str, max_bytes: int) -> str:
    """Cut a line to max_bytes (UTF-8) without breaking a character or a
    colour code, and close any open formatting."""
    if len(text.encode("utf-8")) <= max_bytes:
        return text
    ell = "…" + R
    budget = max_bytes - len(ell.encode("utf-8"))
    out, used, i = [], 0, 0
    while i < len(text):
        ch = text[i]
        token = ch
        if ch == C:
            m = re.match(r"\x03(\d{1,2}(,\d{1,2})?)?", text[i:])
            token = m.group(0)
        size = len(token.encode("utf-8"))
        if used + size > budget:
            break
        out.append(token)
        used += size
        i += len(token)
    return "".join(out).rstrip() + ell


def ago(seconds: float, lang: str = "en") -> str:
    s = int(max(0, seconds))
    units = [(86400 * 365, "y", "a"), (86400 * 30, "mo", "m"), (86400, "d", "d"), (3600, "h", "h"), (60, "m", "min")]
    for size, en, es in units:
        if s >= size:
            n = s // size
            return f"{n}{en} ago" if lang == "en" else f"hace {n}{es}"
    return "just now" if lang == "en" else "ahora"


def duration(seconds: float) -> str:
    s = int(max(0, seconds))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if d:
        return f"{d}d {h}h {m}m"
    if h:
        return f"{h}h {m}m"
    return f"{m}m {s}s"


def paginate(items: list, page: int, size: int) -> tuple[list, int, int]:
    pages = max(1, (len(items) + size - 1) // size)
    page = max(1, min(page, pages))
    return items[(page - 1) * size: page * size], page, pages
