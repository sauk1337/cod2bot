"""Unit tests: IRC formatting, robots.txt, CoD2 protocol, parsers, database."""
import json
import struct
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401  (sets sys.path)
from helpers import fixture

from app.core import irc
from app.core.db import Database
from app.core.http import Robots
from app.news import parsers
from app.servers import query as q


class IrcFormatTests(unittest.TestCase):
    def test_colour_codes_always_two_digits(self):
        self.assertEqual(irc.col("5", irc.RED), "\x03045\x03")
        self.assertIn("\x0301,08", irc.badge("NEWS"))

    def test_truncate_respects_bytes_and_utf8(self):
        text = irc.bold("ñ" * 300)
        out = irc.truncate(text, 100)
        self.assertLessEqual(len(out.encode("utf-8")), 100)
        out.encode("utf-8").decode("utf-8")  # no broken characters
        self.assertTrue(out.endswith("…" + irc.R))

    def test_truncate_never_splits_colour_code(self):
        text = "a" * 95 + "\x0304,12xyz"
        out = irc.truncate(text, 99)
        self.assertNotIn("\x0304,1…", out)
        self.assertNotRegex(out, "\x03\\d?,?\\d?…")

    def test_cod_colours(self):
        out = irc.cod_colours("^1Red^7White^2Green")
        self.assertEqual(irc.strip(out), "RedWhiteGreen")
        self.assertIn("\x0304Red", out)
        self.assertEqual(irc.strip_cod("^3Toujane ^7Server"), "Toujane Server")

    def test_strip_and_clean(self):
        self.assertEqual(irc.strip(irc.line("NEWS", irc.good("ok"))), " COD2  NEWS  ok")
        self.assertEqual(irc.clean("a\r\nb\x07  c"), "a b c")

    def test_paginate(self):
        items, page, pages = irc.paginate(list(range(11)), 3, 5)
        self.assertEqual((items, page, pages), ([10], 3, 3))
        self.assertEqual(irc.paginate([], 5, 5)[1:], (1, 1))

    def test_ascii_fold(self):
        self.assertEqual(irc.ascii_fold("Ñandú » ok · ▰▱"), "Nandu >> ok - #-")


class RobotsTests(unittest.TestCase):
    def test_moddb_allow_xml_longest_match(self):
        r = Robots("User-agent: *\nDisallow: /\nAllow: /*.xml\n\nUser-agent: GPTBot\nDisallow: /", "COD2BOT")
        self.assertTrue(r.allowed("/games/call-of-duty-2/articles/feed/rss.xml"))
        self.assertFalse(r.allowed("/games/call-of-duty-2"))

    def test_github_atom_disallowed(self):
        r = Robots("User-agent: *\nDisallow: /*.atom$\nDisallow: /*/*/commits/\n", "COD2BOT")
        self.assertFalse(r.allowed("/eyza-cod2/zpam3/releases.atom"))
        self.assertTrue(r.allowed("/eyza-cod2/zpam3/releases"))

    def test_disallow_all_and_specific_agent(self):
        self.assertFalse(Robots("User-agent: *\nDisallow: /", "COD2BOT").allowed("/r/x/.rss"))
        r = Robots("User-agent: cod2bot\nAllow: /\n\nUser-agent: *\nDisallow: /", "COD2BOT")
        self.assertTrue(r.allowed("/news"))

    def test_empty_disallow_allows(self):
        self.assertTrue(Robots("User-agent: *\nDisallow:\n", "COD2BOT").allowed("/x"))


class ProtocolTests(unittest.TestCase):
    def test_parse_status(self):
        data = (b"\xff\xff\xff\xffstatusResponse\n\\sv_hostname\\^1Clan^7War #1\\mapname\\mp_toujane"
                b"\\g_gametype\\sd\\sv_maxclients\\20\\shortversion\\1.3\\pswrd\\1\n"
                b'12 50 "^2Player^7One"\n30 80 "Two"\n')
        st = q.parse_status(data, "1.2.3.4", 28960, 42)
        self.assertTrue(st.online)
        self.assertEqual((st.map, st.gametype, st.maxplayers, st.version), ("mp_toujane", "sd", 20, "1.3"))
        self.assertEqual([p.name for p in st.players], ["Two", "^2Player^7One"])  # sorted by score
        self.assertTrue(st.password)

    def test_master_packet_with_backslash_bytes(self):
        # IP 92.92.1.2 (0x5C = "\\") and port 23644 (0x5C5C) must survive
        entry1 = b"\\" + bytes([92, 92, 1, 2]) + struct.pack(">H", 0x5C5C)
        entry2 = b"\\" + bytes([203, 0, 113, 10]) + struct.pack(">H", 28960)
        packet = b"\xff\xff\xff\xffgetserversResponse\n" + entry1 + entry2 + b"\\EOT\x00\x00\x00"
        self.assertEqual(q.parse_master_packet(packet), [("92.92.1.2", 0x5C5C), ("203.0.113.10", 28960)])

    def test_parse_address_and_public(self):
        self.assertEqual(q.parse_address("203.0.113.10:28961"), ("203.0.113.10", 28961))
        self.assertEqual(q.parse_address("cod2.example.org"), ("cod2.example.org", 28960))
        self.assertIsNone(q.parse_address("999.1.1.1:28960"))
        self.assertIsNone(q.parse_address("1.2.3.4:abc"))
        self.assertIsNone(q.parse_address("bad host;rm -rf"))
        self.assertFalse(q.is_public("127.0.0.1"))
        self.assertFalse(q.is_public("192.168.1.10"))


class ParserTests(unittest.TestCase):
    def test_rss_moddb(self):
        items = parsers.parse_feed(fixture("moddb_rss.xml"))
        self.assertEqual(len(items), 3)
        self.assertTrue(items[0].url.startswith("https://www.moddb.com/"))
        self.assertRegex(items[0].published, r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")
        self.assertTrue(all(i.title for i in items))

    def test_atom_zpam(self):
        items = parsers.parse_feed(fixture("zpam_atom.xml"))
        self.assertEqual(len(items), 2)
        self.assertIn("zPAM", items[0].title)
        self.assertIn("github.com/eyza-cod2/zpam3/releases/tag/", items[0].url)

    def test_fpschallenge_filter_cod2(self):
        data = json.loads(fixture("fps_news.json"))
        items = parsers.parse_fpschallenge(data, "https://fpschallenge.eu/news/{slug}", ["Call of Duty 2"])
        self.assertEqual(len(items), 3)
        self.assertTrue(all(i.url.startswith("https://fpschallenge.eu/news/") for i in items))
        all_items = parsers.parse_fpschallenge(data, "https://x/{slug}", [])
        self.assertEqual(len(all_items), len(data["items"]))

    def test_github_releases(self):
        items = parsers.parse_github_releases(json.loads(fixture("github_releases.json")))
        self.assertGreaterEqual(len(items), 1)
        self.assertIn("/releases/tag/", items[0].url)

    def test_html_pattern(self):
        html = '<div class="n"><a href="/news/1">First &amp; best</a></div><div class="n"><a href="/news/2">Two</a></div>'
        items = parsers.parse_html(html, r'<div class="n"><a href="(?P<url>[^"]+)">(?P<title>[^<]+)</a>',
                                   "https://example.org/")
        self.assertEqual([i.title for i in items], ["First & best", "Two"])
        self.assertEqual(items[0].url, "https://example.org/news/1")

    def test_rejects_entities_and_garbage(self):
        with self.assertRaises(parsers.ParseError):
            parsers.parse_feed(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><rss></rss>')
        with self.assertRaises(parsers.ParseError):
            parsers.parse_feed(b"not xml")
        with self.assertRaises(parsers.ParseError):
            parsers.parse_fpschallenge({"nope": 1}, "{slug}", [])

    def test_detect_feed(self):
        self.assertEqual(parsers.detect_feed(fixture("moddb_rss.xml")), "rss")
        self.assertEqual(parsers.detect_feed(fixture("zpam_atom.xml")), "atom")
        self.assertIsNone(parsers.detect_feed(b"<html></html>"))


class DatabaseTests(unittest.TestCase):
    def test_migrations_idempotent_backup_cleanup(self):
        tmp = Path(tempfile.mkdtemp())
        db = Database(tmp / "t.db")
        self.assertEqual(db.schema_version(), 1)
        db.migrate()
        self.assertEqual(db.schema_version(), 1)
        db.cfg_set("a", "1")
        db.cfg_set("a", "2")
        self.assertEqual(db.cfg_get("a"), "2")
        self.assertTrue(db.integrity_ok())
        for _ in range(3):
            db.backup(tmp / "b", keep=2)
        self.assertLessEqual(len(list((tmp / "b").glob("*.db"))), 2)
        db.x("INSERT INTO admin_logs(ts, level, action) VALUES ('2000-01-01 00:00:00', 4, 'x')")
        self.assertEqual(db.cleanup(365, 30, 1, 14)["admin_logs"], 1)
        with self.assertRaises(Exception):
            with db.tx() as c:
                c.execute("INSERT INTO bot_config VALUES ('tx', '1', 'now')")
                raise RuntimeError("rollback")
        self.assertIsNone(db.cfg_get("tx"))
        db.close()


if __name__ == "__main__":
    unittest.main()
