"""Module tests with an isolated database and fake HTTP (no network)."""
import re
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import helpers
from helpers import FPS_ROUTES, AppTestCase, FakeHttp

from app.core.commands import ADMIN, MODERATOR, OWNER, REGISTERED, USER
from app.core.http import FetchError
from app.news import parsers


class NewsTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.app.http = FakeHttp({**FPS_ROUTES, "zpam3": "github_releases.json", "CoD2x": "github_releases.json",
                                  "articles": "moddb_rss.xml", "downloads": "moddb_rss.xml",
                                  "addons": "moddb_rss.xml"})
        self.news = self.app.services["news"]

    def src(self, name):
        return self.app.db.one("SELECT * FROM news_sources WHERE name = ?", (name,))

    def test_sources_synced_from_config(self):
        names = {r["name"] for r in self.app.db.q("SELECT name FROM news_sources")}
        self.assertTrue({"fpschallenge", "zpam", "cod2x", "moddb", "reddit"} <= names)
        self.assertEqual(self.src("reddit")["enabled"], 0)  # robots.txt disallows
        self.assertEqual(self.src("zpam")["type"], "github_releases")

    def test_first_run_is_silent_then_new_items_announced(self):
        self.news.fetch_source(self.src("fpschallenge"))
        self.assertEqual(self.app.services["announcer"].pending(), 0)  # backfill never announced
        n = self.app.db.scalar("SELECT COUNT(*) FROM news", (), 0)
        self.assertEqual(n, 3)  # only Call of Duty 2 category kept
        # a new CoD2 item appears
        data = __import__("json").loads(helpers.fixture("fps_news.json"))
        data["items"].insert(0, {"title": "NEWERA wins CoD2 Winter Cup!", "slug": "newera-wins-cod2-winter-cup",
                                 "foreword": "NEWERA beat MiCRO in the final.", "image": None,
                                 "categoryName": "Call of Duty 2",
                                 "updatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
        self.app.http.routes["/api/v1/news"] = data
        new, upd = self.news.fetch_source(self.src("fpschallenge"))
        self.assertEqual((new, upd), (1, 0))
        pulled = self.app.services["announcer"].pull(["#cod2bot"])
        self.assertEqual(len(pulled), 1)
        self.assertIn("NEWERA wins CoD2 Winter Cup", pulled[0][1])
        self.assertIn("https://fpschallenge.eu/news/newera-wins-cod2-winter-cup", pulled[0][1])
        # duplicate fetch -> nothing new
        self.assertEqual(self.news.fetch_source(self.src("fpschallenge")), (0, 0))
        # classified as results
        row = self.app.db.one("SELECT category FROM news WHERE guid = 'newera-wins-cod2-winter-cup'")
        self.assertEqual(row["category"], "results")

    def test_change_detection(self):
        self.news.fetch_source(self.src("fpschallenge"))
        data = __import__("json").loads(helpers.fixture("fps_news.json"))
        for it in data["items"]:
            if it["categoryName"] == "Call of Duty 2":
                it["foreword"] = "Updated text"
                break
        self.app.http.routes["/api/v1/news"] = data
        self.assertEqual(self.news.fetch_source(self.src("fpschallenge")), (0, 1))
        self.assertEqual(self.app.db.scalar("SELECT MAX(changes) FROM news"), 1)

    def test_errors_back_off_and_are_reported(self):
        self.app.http.routes = {"rss.moddb.com": FetchError("HTTP 503", 503)}
        before = time.time()
        self.news.fetch_source(self.src("moddb"))
        src = self.src("moddb")
        self.assertEqual(src["error_count"], 1)
        self.assertGreater(src["next_fetch"], before + 7200)
        out = self.run_cmd("!news sources", level=MODERATOR)
        self.assertIn("moddb: error HTTP 503", self.all_text(out))

    def test_news_commands(self):
        for name in ("fpschallenge", "zpam", "moddb"):
            self.news.fetch_source(self.src(name))
        out = self.run_cmd("!news")
        self.assertEqual(len(out.chan), 3)
        self.assertTrue(all(l.startswith(" COD2  NEWS ") for l in out.chan))
        self.assertEqual(len(self.run_cmd("!news 6").notice), 6)  # long lists by notice
        self.assertIn("FPSChallenge", self.all_text(self.run_cmd("!news fpschallenge")))
        self.assertIn("zPAM", self.all_text(self.run_cmd("!news search zpam")))
        self.assertIn("No news found", self.all_text(self.run_cmd("!news search qwertyuiop")))
        self.assertIn("results", self.all_text(self.run_cmd("!news categories")))
        self.assertIn("Unknown option", self.all_text(self.run_cmd("!news nonsense")))
        latest = self.run_cmd("!news latest")
        self.assertEqual(len(latest.chan), 2)  # title + description

    def test_admin_news_commands_need_level(self):
        self.assertIn("don't have access", self.all_text(self.run_cmd("!news addsource https://x.org/feed")))
        self.assertIn("don't have access", self.all_text(self.run_cmd("!news check", level=REGISTERED)))
        out = self.run_cmd("!news check moddb", level=MODERATOR)
        self.assertIn("Checked 1 source", self.all_text(out))
        out = self.run_cmd("!news disable moddb", level=ADMIN)
        self.assertEqual(self.src("moddb")["enabled"], 0)
        self.assertTrue(self.app.db.one("SELECT 1 FROM admin_logs WHERE action = 'news.disable'"))

    def test_addsource_from_irc(self):
        self.app.http.routes["example.org/feed"] = "zpam_atom.xml"
        out = self.run_cmd("!news addsource https://example.org/feed exfeed", level=ADMIN)
        self.assertIn("Source added: exfeed (atom, 2 items)", self.all_text(out))
        self.assertEqual(self.src("exfeed")["origin"], "irc:Tester")
        self.assertIn("removed", self.all_text(self.run_cmd("!news removesource exfeed", level=ADMIN)))
        self.assertIsNone(self.src("exfeed"))
        self.app.http.routes["example.org/page"] = b"<html>no feed</html>"
        out = self.run_cmd("!news addsource https://example.org/page", level=ADMIN)
        self.assertIn("no RSS/Atom feed", self.all_text(out))
        self.assertIn("addsource <url>", self.all_text(self.run_cmd("!news addsource javascript:alert(1)", level=ADMIN)))


class TournamentTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.app.http = FakeHttp(dict(FPS_ROUTES))
        self.t = self.app.services["tournaments"]

    def test_refresh_only_cod2_and_silent_first_run(self):
        self.t.refresh()
        games = {r["game"] for r in self.app.db.q("SELECT DISTINCT game FROM tournaments")}
        self.assertEqual(games, {"Call of Duty 2"})
        self.assertEqual(self.app.services["announcer"].pending(), 0)
        names = [r["name"] for r in self.app.db.q("SELECT name FROM tournaments")]
        self.assertIn("[EU] Powerhouse CoD2 Challenge Season 6", names)
        # provider times are Europe/Berlin -> stored in UTC
        row = self.app.db.one("SELECT start_at FROM tournaments WHERE name LIKE '%Nightcup #13%'")
        self.assertEqual(row["start_at"], "2026-11-08 23:01:00")

    def test_teams_and_ladder(self):
        self.app.http.routes["/api/v1/teams/1832"] = {"data": {
            "id": 1832, "slug": "citrons-team", "name": "CITRONS TEAM", "tag": "CITROON", "country": "cz",
            "membersCount": 9, "matchesCount": 322,
            "leagues": [{"id": 15, "name": "5vs5 S&D", "region": {"slug": "eu"}, "game": {"slug": "cod2"},
                         "rank": 55, "elo": 1779, "wins": 153, "losses": 117, "draws": 0},
                        {"id": 99, "name": "CoD4 league", "game": {"slug": "cod4"}, "rank": 1, "elo": 1}]}}
        self.t.refresh()
        out = self.all_text(self.run_cmd("!team citrons"))
        self.assertIn("CITRONS TEAM [CITROON]", out)
        self.assertIn("5vs5 S&D EU #55 ELO 1779 153-117", out)
        self.assertNotIn("CoD4 league", out)  # only CoD2 leagues
        standings = self.all_text(self.run_cmd("!standings"))
        self.assertIn("#55 CITRONS TEAM 1779", standings)
        self.assertIn("Partial standings", standings)
        self.assertIn("5vs5 S&D", self.all_text(self.run_cmd("!ladders")))
        self.assertIn("CITRONS TEAM", self.all_text(self.run_cmd("!teams powerhouse")))

    def test_commands_and_announcements(self):
        self.t.refresh()
        out = self.run_cmd("!tournaments")
        self.assertTrue(out.chan and all("TOURNEY" in l for l in out.chan))
        detail = self.all_text(self.run_cmd("!tournament nightcup #13"))
        self.assertIn("registration open", detail)
        # state change -> announcement
        self.app.db.x("UPDATE tournaments SET state = 'ONGOING' WHERE name LIKE '%Nightcup #13%'")
        self.t.announce_changes()
        from app.core import irc
        pulled = self.app.services["announcer"].pull(["#cod2bot"])
        self.assertEqual(len(pulled), 1)
        self.assertIn("CoD2 SA - Nightcup #13 · live", irc.strip(pulled[0][1]))

    def test_winners_from_results_news(self):
        news = self.app.services["news"]
        self.app.http.routes["/api/v1/news"] = "fps_news.json"
        news.fetch_source(self.app.db.one("SELECT * FROM news_sources WHERE name = 'fpschallenge'"))
        self.t.refresh()
        out = self.all_text(self.run_cmd("!results"))
        self.assertIn("VirtualGamerz (vs NOOBS) wins CoD2 SA - MIXCUP", out)

    def test_manual_tournament_and_results(self):
        self.assertIn("don't have access", self.all_text(self.run_cmd("!tournament add 20/12 20:00 Xmas Cup")))
        out = self.all_text(self.run_cmd("!tournament add 20/12 20:00 Xmas Cup https://example.org/xmas",
                                         level=MODERATOR))
        self.assertIn("Xmas Cup", out)
        self.assertIn("scheduled", out)
        self.run_cmd("!results add ABC 13-7 XYZ toujane", level=MODERATOR)
        self.assertIn("ABC 13-7 XYZ", self.all_text(self.run_cmd("!results")))
        tid = self.app.db.scalar("SELECT id FROM tournaments WHERE name = 'Xmas Cup'")
        self.run_cmd(f"!tournament winner #{tid} ABC", level=MODERATOR)
        self.assertIn("ABC wins Xmas Cup", self.all_text(self.run_cmd("!results")))

    def test_api_down_is_reported_not_crashing(self):
        self.app.http.routes = {"fpschallenge": FetchError("network error", 0)}
        with self.assertRaises(FetchError):
            self.t.refresh()  # the scheduler catches it and backs off
        self.assertIn("not loaded yet", self.all_text(self.run_cmd("!tournaments")))


class ServerTests(AppTestCase):
    def setUp(self):
        super().setUp()
        from app.servers import query as q
        self.q = q
        self.fake = {}
        self.svc = self.app.services["servers"]
        self.orig_query, self.orig_public = q.query, q.is_public
        q.query = lambda host, port, timeout=2, retries=1: self.fake.get(
            (host, port), q.Status(host, port, False, error="timeout"))
        q.is_public = lambda host: not host.startswith(("127.", "10.", "192.168."))

    def tearDown(self):
        self.q.query, self.q.is_public = self.orig_query, self.orig_public
        super().tearDown()

    def online(self, host, port, players=3):
        st = self.q.Status(host, port, True, 40, {"sv_hostname": "^1Clan^7War #1", "mapname": "mp_toujane",
                                                  "g_gametype": "sd", "sv_maxclients": "12", "shortversion": "1.3"},
                           [self.q.Player(f"^2player{i}", 10 - i, 50) for i in range(players)])
        self.fake[(host, port)] = st
        return st

    def test_add_query_remove(self):
        self.online("203.0.113.10", 28960)
        self.assertIn("don't have access", self.all_text(self.run_cmd("!server add cw1 203.0.113.10:28960")))
        out = self.all_text(self.run_cmd("!server add cw1 203.0.113.10:28960", level=MODERATOR))
        self.assertIn("Server cw1 added", out)
        self.assertIn("ONLINE", out)
        self.assertIn("Map: mp_toujane SD", out)
        self.assertIn("3/12", out)
        self.assertIn("ClanWar #1", self.all_text(self.run_cmd("!server cw1")))
        self.assertIn("player0 10", self.all_text(self.run_cmd("!server players cw1")))
        self.assertIn("Map: mp_toujane (Toujane)", self.all_text(self.run_cmd("!server map cw1")))
        self.assertIn("cw1", self.all_text(self.run_cmd("!servers")))
        self.assertIn("only public", self.all_text(self.run_cmd("!server add lan 192.168.1.5:28960",
                                                                level=MODERATOR)).lower())
        self.assertIn("removed", self.all_text(self.run_cmd("!server remove cw1", level=MODERATOR)))

    def test_down_detection_and_recovery(self):
        self.online("203.0.113.10", 28960)
        self.run_cmd("!server add cw1 203.0.113.10:28960", level=MODERATOR)
        del self.fake[("203.0.113.10", 28960)]
        for _ in range(3):
            self.svc.monitor()
        pulled = self.app.services["announcer"].pull(["#cod2bot"])
        self.assertTrue(pulled and "OFFLINE" in pulled[0][1])
        self.online("203.0.113.10", 28960)
        self.svc.monitor()
        pulled = self.app.services["announcer"].pull(["#cod2bot"])
        self.assertTrue(pulled and "ONLINE" in pulled[0][1])
        hist = self.app.db.scalar("SELECT COUNT(*) FROM server_history", (), 0)
        self.assertGreaterEqual(hist, 5)

    def test_browser_find(self):
        st = self.online("1.1.1.1", 28960)
        self.svc.browser, self.svc.browser_at = [st], time.time()
        self.assertIn("ClanWar #1", self.all_text(self.run_cmd("!server find clanwar")))
        self.assertIn("ClanWar #1", self.all_text(self.run_cmd("!find player1")))
        self.assertIn("1 servers online, 3 players", self.all_text(self.run_cmd("!servers top")))
        self.assertIn("is not playing", self.all_text(self.run_cmd("!find nobodyhere")))


class CommunityTests(AppTestCase):
    Q = "nick@boss.users.quakenet.org"

    def test_clan_register_needs_identity_and_ownership(self):
        self.assertIn("authed with Q", self.all_text(self.run_cmd("!clan register ABC Alpha Bravo")))
        out = self.all_text(self.run_cmd("!clan register ABC Alpha Bravo Clan", host=self.Q))
        self.assertIn("Clan ABC (Alpha Bravo Clan) registered", out)
        self.assertIn("already exists", self.all_text(self.run_cmd("!clan register abc Dup", host=self.Q)))
        self.run_cmd("!clan set ABC website https://abc.example.org", host=self.Q)
        self.run_cmd("!clan set ABC irc abc.clan", host=self.Q)
        self.run_cmd("!clan set ABC country es", host=self.Q)
        prof = self.all_text(self.run_cmd("!clan abc"))
        self.assertIn("[ABC] Alpha Bravo Clan", prof)
        self.assertIn("ES", prof)
        self.assertIn("#abc.clan", prof)
        other = "x@other.users.quakenet.org"
        self.assertIn("Only the clan owner", self.all_text(self.run_cmd("!clan set ABC name Hacked", host=other)))
        self.assertIn("website: http", self.all_text(self.run_cmd("!clan set ABC website ftp://x", host=self.Q)))
        self.assertIn("ABC", self.all_text(self.run_cmd("!clans")))

    def test_player_profile(self):
        self.assertIn("authed with Q", self.all_text(self.run_cmd("!player register es", nick="Bob")))
        self.assertIn("Player Bob registered", self.all_text(self.run_cmd("!player register es", nick="Bob",
                                                                          host="b@bob.users.quakenet.org")))
        self.assertIn("linked to another nick", self.all_text(
            self.run_cmd("!player register", nick="Bob2", host="b@bob.users.quakenet.org")))
        self.run_cmd("!player set role rifle", nick="Bob", host="b@bob.users.quakenet.org")
        self.run_cmd("!clan register ABC Alpha", host=self.Q)
        self.run_cmd("!clan add ABC Bob", host=self.Q)
        prof = self.all_text(self.run_cmd("!profile Bob"))
        self.assertIn("Bob [ABC]", prof)
        self.assertIn("rifle", prof)
        self.assertIn("✔ Q", prof)
        self.assertIn("Members: Bob", self.all_text(self.run_cmd("!clan ABC")))

    def test_war_lifecycle(self):
        when = (datetime.now(self.app.tz) + timedelta(minutes=50))
        cmd = f"!war create ABC XYZ {when.strftime('%d/%m')} {when.strftime('%H:%M')} 5v5 toujane,burgundy server=cw1"
        self.assertIn("authed with Q", self.all_text(self.run_cmd(cmd)))
        out = self.all_text(self.run_cmd(cmd, host=self.Q))
        self.assertIn("ABC vs XYZ", out)
        self.assertIn("Maps: Toujane, Burgundy", out)
        self.assertIn("Server: cw1", out)
        wid = self.app.db.scalar("SELECT id FROM wars")
        # creation announced to the other announce channel (#test), not the one it was created in
        pulled = self.app.services["announcer"].pull(["#cod2bot", "#test"])
        self.assertEqual([c for c, _ in pulled], ["#test"])
        # reminder (60 min) fires once
        self.app.services["wars"].reminders()
        self.app.services["wars"].reminders()
        rem = [r for r in self.app.db.q("SELECT * FROM announcements WHERE dedupe_key LIKE ?", (f"war:{wid}:%",))
               if not r["dedupe_key"].endswith(":new")]
        self.assertEqual(len({r["dedupe_key"] for r in rem}), 1)
        self.assertIn("ABC vs XYZ", self.all_text(self.run_cmd("!war upcoming")))
        self.assertIn("Only who created", self.all_text(self.run_cmd(f"!war result {wid} 13-7",
                                                                    host="z@zz.users.quakenet.org")))
        out = self.all_text(self.run_cmd(f"!war result {wid} 13-7", host=self.Q))
        self.assertIn("ABC 13-7 XYZ", out)
        self.assertIn("winner ABC", out)
        self.assertIn("1W 0L 0D", self.all_text(self.run_cmd("!war history ABC")))
        self.assertIn("ABC 1 - 0 XYZ", self.all_text(self.run_cmd("!war ABC XYZ")))
        self.assertIn("Invalid date", self.all_text(self.run_cmd("!war create A B 99/99 25:00", host=self.Q)))
        self.assertIn("against itself", self.all_text(self.run_cmd("!war create A a 01/01 20:00", host=self.Q)))


class GeneralTests(AppTestCase):
    def test_help_levels_and_redirect(self):
        user_help = self.all_text(self.run_cmd("!help admin"))
        self.assertNotIn("!broadcast", user_help)
        self.assertIn("!broadcast", self.all_text(self.run_cmd("!help admin", level=ADMIN)))
        self.assertTrue(self.run_cmd("!help").notice and not self.run_cmd("!help").chan)  # help by notice
        self.assertIn("Toujane", self.all_text(self.run_cmd("!cod2 map toujane")))
        self.assertIn("Syntax: !war create", self.all_text(self.run_cmd("!help war")))

    def test_permissions(self):
        for cmd, level in (("!reload", ADMIN), ("!broadcast hi all", ADMIN), ("!maintenance on", OWNER),
                           ("!module disable news", ADMIN), ("!backup", ADMIN), ("!admin", MODERATOR)):
            self.assertIn("needs level", self.all_text(self.run_cmd(cmd, level=level - 1)), cmd)

    def test_module_toggle_and_maintenance(self):
        self.run_cmd("!module disable cod2", level=ADMIN)
        self.assertIn("module is disabled", self.all_text(self.run_cmd("!maps")))
        self.run_cmd("!module enable cod2", level=ADMIN)
        self.assertIn("Toujane", self.all_text(self.run_cmd("!maps")))
        self.run_cmd("!maintenance on", level=OWNER)
        self.assertIn("maintenance", self.all_text(self.run_cmd("!maps")))
        self.assertIn("Toujane", self.all_text(self.run_cmd("!maps", level=MODERATOR)))
        self.run_cmd("!maintenance off", level=OWNER)
        self.assertEqual(self.app.db.scalar("SELECT COUNT(*) FROM admin_logs WHERE action IN ('module','maintenance')"), 4)

    def test_broadcast_and_antiflood(self):
        ann = self.app.services["announcer"]
        self.run_cmd("!broadcast Tournament tonight!", level=ADMIN)
        for i in range(3):
            ann.push("news", f"item {i}", dedupe_key=f"n{i}")
        first = ann.pull(["#cod2bot"])
        self.assertIn("Tournament tonight!", first[0][1])  # urgent first
        self.app.cfg.cp.set("announcer", "min_interval", "3600")
        self.assertEqual(ann.pull(["#cod2bot"]), [])  # min interval respected
        self.app.cfg.cp.set("announcer", "min_interval", "0")
        self.app.cfg.cp.set("announcer", "max_per_hour", "2")
        self.assertEqual(len(ann.pull(["#cod2bot"])), 1)
        self.assertEqual(ann.pull(["#cod2bot"]), [])  # hourly cap
        self.assertEqual(ann.push("news", "dup", dedupe_key="n1"), 0)  # dedupe

    def test_rate_limit(self):
        self.router.limiter.max_calls, self.router.limiter.cooldown = 2, 0
        outs = [self.run_cmd("!ping", host="flood@1.1.1.1").chan for _ in range(4)]
        self.assertEqual(sum(1 for o in outs if o), 2)
        self.assertTrue(self.run_cmd("!ping", host="flood@1.1.1.1", level=MODERATOR).chan)  # staff exempt

    def test_output_limits(self):
        from app.core import irc
        reply = self.router.shape(__import__("app.core.commands", fromlist=["Reply"]).Reply(
            ["x" * 900] * 7, []))
        self.assertEqual(len(reply.chan), 4)
        self.assertEqual(len(reply.notice), 3)
        self.assertTrue(all(len(l.encode()) <= 400 for l in reply.chan))
        plain = self.router.shape(__import__("app.core.commands", fromlist=["Reply"]).Reply(
            [irc.good("ok")], []), colours=False)
        self.assertEqual(plain.chan, ["ok"])

    def test_unknown_and_invalid_commands_ignored(self):
        self.assertEqual(self.run_cmd("!doesnotexist").chan, [])
        self.assertEqual(self.run_cmd("!../../etc").chan, [])

    def test_all_commands_run_without_errors(self):
        """Smoke: every command (no args) as owner never returns the generic error."""
        for name in sorted(self.app.registry.primary):
            if name in ("maintenance", "backup", "reload"):
                continue
            out = self.all_text(self.run_cmd(f"!{name}", level=OWNER))
            self.assertNotIn("Something went wrong", out, name)

    def test_i18n_complete(self):
        from app.core.i18n import EN
        src = "\n".join(p.read_text(encoding="utf-8") for p in Path(helpers.BACKEND, "app").rglob("*.py"))
        used = set(re.findall(r'\.t\(\s*"([a-z0-9_.]+)"', src))
        used |= {f"help.{c}" for c in self.app.registry.primary}
        used |= {"news.disabled", "cod2.maps_comp"} | {f"kb.{k}" for k in
                                                        ("gamemodes", "classes", "configs", "commands", "mods",
                                                         "tools", "guides")}
        self.assertEqual(sorted(used - set(EN)), [])


class ApiTests(AppTestCase):
    def setUp(self):
        super().setUp()
        from app.api.server import ApiServer
        self.api = ApiServer(self.app, self.router, "127.0.0.1", 0, "secret-token")
        self.api.start()
        self.base = f"http://127.0.0.1:{self.api.port}"

    def tearDown(self):
        self.api.stop()
        super().tearDown()

    def req(self, path, data=None, token="secret-token"):
        r = urllib.request.Request(self.base + path, data=data, headers={"X-COD2BOT-Token": token})
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8")

    def test_auth(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.req("/health", token="wrong")
        self.assertEqual(cm.exception.code, 401)
        self.assertTrue(self.req("/health")[1].startswith("OK "))

    def test_cmd_and_announce(self):
        from urllib.parse import urlencode
        body = urlencode({"nick": "Bob", "host": "b@h", "hand": "*", "chan": "#cod2bot", "level": "0",
                          "cmd": "map", "args": "toujane", "colours": "0"}).encode()
        code, text = self.req("/cmd", body)
        self.assertEqual(code, 200)
        self.assertTrue(text.startswith("C\t COD2  MAP  Toujane"))
        self.assertNotIn("\x03", text)  # colours=0 strips
        body = urlencode({"nick": "Bob", "chan": "#x", "level": "0", "cmd": "maintenance", "args": "on"}).encode()
        self.assertIn("needs level owner", self.req("/cmd", body)[1])
        self.assertFalse(self.app.maintenance)
        self.app.services["announcer"].push("news", "hello world", dedupe_key="t")
        self.assertEqual(self.req("/announce?channels=%23cod2bot,%23test")[1].count("hello world"), 2)
        self.assertIn("news", self.req("/commands")[1].split())

    def test_level_is_clamped(self):
        from urllib.parse import urlencode
        body = urlencode({"nick": "Bob", "chan": "#x", "level": "99", "cmd": "maintenance", "args": "on"}).encode()
        self.req("/cmd", body)  # level 99 -> clamped to owner (4), still valid
        self.assertTrue(self.app.maintenance)


if __name__ == "__main__":
    unittest.main()
