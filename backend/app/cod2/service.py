"""CoD2 knowledge module: maps, weapons, gamemodes, configs, commands, mods, guides,
tips, quotes and facts. Data lives in data/seed/cod2.json (upserted on start)."""
from __future__ import annotations

import json
import random

from ..core import irc, log
from ..core.commands import MODERATOR, USER, Reply, command
from ..core.db import utcnow

L = log.get("cod2")

KB_KINDS = {
    # command: (kind, section badge, i18n title key)
    "gamemodes": ("gamemodes", "INFO", "kb.gamemodes"),
    "classes": ("classes", "WEAPON", "kb.classes"),
    "configs": ("configs", "INFO", "kb.configs"),
    "commands": ("commands", "INFO", "kb.commands"),
    "mods": ("mods", "INFO", "kb.mods"),
    "tools": ("tools", "INFO", "kb.tools"),
    "guides": ("guides", "INFO", "kb.guides"),
}
FACTION_COLOURS = {"american": irc.BLUE, "british": irc.GREEN, "russian": irc.RED, "german": irc.GREY,
                   "all": irc.ORANGE, "american/british": irc.TEAL}


class Cod2Service:
    def __init__(self, app):
        self.app = app
        self.db = app.db
        self.seed()

    def seed(self) -> None:
        path = self.app.cfg.root / "data" / "seed" / "cod2.json"
        if not path.exists():
            L.warning("knowledge file %s not found", path)
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        with self.db.tx() as c:
            for m in data.get("maps", []):
                c.execute("INSERT INTO maps(name, title, location, size, modes, competitive, description) "
                          "VALUES (:name, :title, :location, :size, :modes, :competitive, :description) "
                          "ON CONFLICT(name) DO UPDATE SET title=excluded.title, location=excluded.location, "
                          "size=excluded.size, modes=excluded.modes, competitive=excluded.competitive, "
                          "description=excluded.description", m)
            for w in data.get("weapons", []):
                c.execute("INSERT INTO weapons(key, name, class, faction, description) "
                          "VALUES (:key, :name, :class, :faction, :description) "
                          "ON CONFLICT(key) DO UPDATE SET name=excluded.name, class=excluded.class, "
                          "faction=excluded.faction, description=excluded.description", w)
            for kind, entries in data.get("kb", {}).items():
                for e in entries:
                    c.execute("INSERT INTO kb(kind, key, title, body) VALUES (?,?,?,?) "
                              "ON CONFLICT(kind, key) DO UPDATE SET title=excluded.title, body=excluded.body",
                              (kind, e["key"], e["title"], e["body"]))
            for q in data.get("quotes", []):
                c.execute("INSERT OR IGNORE INTO quotes(kind, text, author, added_by, created_at) VALUES (?,?,?,?,?)",
                          (q.get("kind", "quote"), q["text"], q.get("author"), "seed", utcnow()))

    def find_map(self, name: str):
        n = name.lower().strip()
        row = self.db.one("SELECT * FROM maps WHERE name = ? OR name = ? OR title = ?", (n, "mp_" + n, n))
        if row:
            return row
        like = f"%{n.replace('%', '').replace('_', ' ')}%"
        return self.db.one("SELECT * FROM maps WHERE name LIKE ? OR title LIKE ? ORDER BY competitive DESC, name "
                           "LIMIT 1", (f"%{n}%", like))

    def find_weapon(self, name: str):
        n = name.lower().replace("-", "").replace(" ", "").replace(".", "")
        for row in self.db.q("SELECT * FROM weapons"):
            simple = row["name"].lower().replace("-", "").replace(" ", "").replace(".", "")
            if n in (row["key"], simple) or (len(n) >= 3 and (n in row["key"] or n in simple)):
                return row
        return None

    def weapon_line(self, w) -> str:
        colour = FACTION_COLOURS.get(w["faction"].lower(), irc.GREY)
        return irc.line("WEAPON", irc.bold(w["name"]), irc.coloured(w["faction"], colour),
                        irc.accent(w["class"]), w["description"])

    def map_line(self, m) -> str:
        comp = irc.good("★ competitive") if m["competitive"] else ""
        return irc.line("MAP", f"{irc.bold(m['title'])} {irc.muted(m['name'])}", m["location"] or "",
                        comp, m["description"])


def setup(app, scheduler):
    svc = Cod2Service(app)
    app.services["cod2"] = svc
    reg = app.registry

    @command(reg, "maps", "cod2", USER, category="cod2", usage="maps [all]")
    def maps_cmd(app_, ctx):
        allm = ctx.arg(0).lower() in ("all", "stock")
        rows = app.db.q("SELECT * FROM maps" + ("" if allm else " WHERE competitive = 1") +
                        " ORDER BY competitive DESC, title")
        names = " ".join(irc.accent(r["title"]) if r["competitive"] else irc.muted(r["title"]) for r in rows)
        title = app.t("cod2.maps_all" if allm else "cod2.maps_comp", ctx.lang)
        hint = "" if allm else irc.muted(f"{ctx.prefix}maps all")
        return Reply().say(irc.line("MAP", irc.label(title, names), hint))

    @command(reg, "map", "cod2", USER, category="cod2", usage="map <name>")
    def map_cmd(app_, ctx):
        if not ctx.args:
            return Reply().tell(irc.error(f"{ctx.prefix}map <name>  {irc.muted('(toujane, burgundy, ...)')}"))
        m = svc.find_map(ctx.rest(0)[:30])
        if not m:
            return Reply().say(irc.line("MAP", app.t("cod2.map_none", ctx.lang, name=irc.bold(ctx.rest(0)[:30]),
                                                     p=ctx.prefix)))
        return Reply().say(svc.map_line(m))

    @command(reg, "randommap", "fun", USER, aliases=("rmap",), category="fun", usage="randommap [all]")
    def randommap_cmd(app_, ctx):
        allm = ctx.arg(0).lower() in ("all",)
        rows = app.db.q("SELECT * FROM maps" + ("" if allm else " WHERE competitive = 1"))
        if not rows:
            return Reply()
        m = random.choice(rows)
        return Reply().say(irc.line("MAP", app.t("cod2.random_map", ctx.lang), irc.bold(irc.accent(m["title"])),
                                    irc.muted(m["name"])))

    @command(reg, "weapons", "cod2", USER, category="cod2", usage="weapons [faction|class]")
    def weapons_cmd(app_, ctx):
        f = ctx.arg(0).lower()
        reply = Reply()
        rows = app.db.q("SELECT * FROM weapons ORDER BY faction, class, name")
        if f:
            rows = [r for r in rows if f in r["faction"].lower() or f in r["class"].lower()]
            if not rows:
                return reply.tell(irc.error(app.t("cod2.weapon_filter", ctx.lang)))
            text = ", ".join(f"{irc.bold(r['name'])} {irc.muted(r['class'])}" for r in rows)
            return reply.say(irc.line("WEAPON", text))
        groups: dict[str, list] = {}
        for r in rows:
            if r["class"] in ("pistol", "grenade"):
                continue
            groups.setdefault(r["faction"], []).append(r["name"])
        order = ["American", "American/British", "British", "Russian", "German", "All"]
        for faction in sorted(groups, key=lambda f: order.index(f) if f in order else 99):
            names = groups[faction]
            colour = FACTION_COLOURS.get(faction.lower(), irc.GREY)
            reply.chan.append(f"{irc.coloured(irc.bold(faction), colour)}: {', '.join(names)}")
        line = irc.line("WEAPON", irc.sep("|").join(reply.chan))
        reply.chan = [line]
        return reply

    @command(reg, "weapon", "cod2", USER, category="cod2", usage="weapon <name>")
    def weapon_cmd(app_, ctx):
        if not ctx.args:
            return Reply().tell(irc.error(f"{ctx.prefix}weapon <name>  {irc.muted('(kar98k, thompson, mp44, ...)')}"))
        w = svc.find_weapon(ctx.rest(0)[:30])
        if not w:
            return Reply().say(irc.line("WEAPON", app.t("cod2.weapon_none", ctx.lang, name=irc.bold(ctx.rest(0)[:30]),
                                                        p=ctx.prefix)))
        return Reply().say(svc.weapon_line(w))

    @command(reg, "randomweapon", "fun", USER, aliases=("rweapon",), category="fun")
    def randomweapon_cmd(app_, ctx):
        rows = app.db.q("SELECT * FROM weapons WHERE class NOT IN ('grenade')")
        w = random.choice(rows)
        return Reply().say(irc.line("WEAPON", app.t("cod2.random_weapon", ctx.lang), irc.bold(w["name"]),
                                    irc.muted(w["faction"])))

    def kb_command(name, kind, section, title_key):
        @command(reg, name, "cod2", USER, category="cod2", usage=f"{name} [topic]")
        def kb_cmd(app_, ctx):
            reply = Reply()
            key = ctx.arg(0).lower()
            if key:
                row = app.db.one("SELECT * FROM kb WHERE kind = ? AND (key = ? OR title LIKE ?)",
                                 (kind, key, f"%{key[:30]}%"))
                if not row:
                    return reply.tell(irc.error(app.t("cod2.topic_none", ctx.lang, p=ctx.prefix, cmd=name)))
                return reply.say(irc.line(section, irc.bold(row["title"]), row["body"]))
            rows = app.db.q("SELECT key, title FROM kb WHERE kind = ? ORDER BY id", (kind,))
            if kind in ("gamemodes", "classes"):  # short lists: show everything
                for r in app.db.q("SELECT * FROM kb WHERE kind = ? ORDER BY id", (kind,))[:6]:
                    reply.tell(irc.line(section, irc.bold(r["title"]), r["body"]))
                reply.say(irc.line(section, irc.label(app.t(title_key, ctx.lang),
                                                      " ".join(irc.accent(r["key"]) for r in rows))))
                return reply
            return reply.say(irc.line(section, irc.label(app.t(title_key, ctx.lang),
                                                         " ".join(irc.accent(r["key"]) for r in rows)),
                                      irc.muted(f"{ctx.prefix}{name} <topic>")))
        return kb_cmd

    for cmd_name, (kind, section, title_key) in KB_KINDS.items():
        kb_command(cmd_name, kind, section, title_key)

    @command(reg, "tips", "cod2", USER, aliases=("tip",), category="cod2", usage="tips [add <text>]")
    def tips_cmd(app_, ctx):
        if ctx.arg(0).lower() == "add":
            if ctx.level < MODERATOR:
                return Reply().tell(irc.error(app.t("perm.denied", ctx.lang)))
            text = irc.clean(ctx.rest(1), 300)
            if len(text) < 10:
                return Reply().tell(irc.error(f"{ctx.prefix}tips add <text>"))
            n = app.db.scalar("SELECT COUNT(*) FROM kb WHERE kind = 'tips'", (), 0) + 1
            app.db.x("INSERT OR IGNORE INTO kb(kind, key, title, body) VALUES ('tips', ?, 'Tip', ?)", (f"u{n}", text))
            app.log_admin(ctx, "tips.add", text, "ok")
            return Reply().tell(irc.line("ADMIN", app.t("common.saved", ctx.lang)))
        row = app.db.one("SELECT * FROM kb WHERE kind = 'tips' ORDER BY RANDOM() LIMIT 1")
        if not row:
            return Reply()
        return Reply().say(irc.line("INFO", irc.accent(app.t("cod2.tip", ctx.lang)), row["body"]))

    def quote_like(kind):
        @command(reg, kind, "fun", USER, category="fun",
                 usage=f"{kind} [add <text> [- author]]")
        def quote_cmd(app_, ctx):
            if ctx.arg(0).lower() == "add":
                if ctx.level < MODERATOR:
                    return Reply().tell(irc.error(app.t("perm.denied", ctx.lang)))
                raw = irc.clean(ctx.rest(1), 300)
                text, _, author = raw.partition(" - ")
                if len(text) < 10:
                    return Reply().tell(irc.error(f"{ctx.prefix}{kind} add <text> [- author]"))
                app.db.x("INSERT OR IGNORE INTO quotes(kind, text, author, added_by, created_at) VALUES (?,?,?,?,?)",
                         (kind, text.strip(), author.strip() or None, ctx.nick, utcnow()))
                app.log_admin(ctx, f"{kind}.add", text, "ok")
                return Reply().tell(irc.line("ADMIN", app.t("common.saved", ctx.lang)))
            row = app.db.one("SELECT * FROM quotes WHERE kind = ? ORDER BY RANDOM() LIMIT 1", (kind,))
            if not row:
                return Reply()
            if kind == "fact":
                return Reply().say(irc.line("FUN", irc.accent(app.t("cod2.fact", ctx.lang)), row["text"]))
            author = irc.muted("— " + row["author"]) if row["author"] else ""
            return Reply().say(irc.line("FUN", f"{irc.I}“{row['text']}”{irc.I} {author}".rstrip()))
        return quote_cmd

    quote_like("quote")
    quote_like("fact")
    return svc
