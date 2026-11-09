"""Runs scripts/cod2bot/cod2bot.tcl in a simulated Eggdrop (tclsh) against a real
backend API started in this process."""
import shutil
import subprocess
import unittest

from helpers import ROOT, AppTestCase

TCLSH = shutil.which("tclsh") or shutil.which("tclsh8.6")
SCRIPT = ROOT / "eggdrop" / "scripts" / "cod2bot" / "cod2bot.tcl"   # canonical copy


@unittest.skipUnless(TCLSH and SCRIPT.exists(), "tclsh or cod2bot.tcl not available")
class TclIntegrationTest(AppTestCase):
    def test_eggdrop_script(self):
        from app.api.server import ApiServer
        token = "tcl-test-token"
        api = ApiServer(self.app, self.router, "127.0.0.1", 0, token)
        api.start()
        try:
            egg = self.tmp / "egg"
            egg.mkdir()
            shutil.copy(SCRIPT, egg / "cod2bot.tcl")
            (self.tmp / "api.token").write_text(token + "\n", encoding="utf-8")
            conf = SCRIPT.with_name("cod2bot.conf").read_text(encoding="utf-8")
            conf = conf.replace('variable api "http://127.0.0.1:8765"', f'variable api "http://127.0.0.1:{api.port}"')
            conf = conf.replace('variable tokenfile "cod2bot/data/api.token"',
                                f'variable tokenfile "{(self.tmp / "api.token").as_posix()}"')
            (egg / "cod2bot.conf").write_text(conf, encoding="utf-8")
            self.app.services["announcer"].push("news", "\x0304TEST ANNOUNCEMENT\x03", dedupe_key="tcl",
                                                channels=["#cod2bot", "#nocolour"])
            test = ROOT / "backend" / "tests" / "tcl" / "test_cod2bot.tcl"
            proc = subprocess.run([TCLSH, str(test), egg.as_posix()], capture_output=True, text=True,
                                  timeout=120, encoding="utf-8", errors="replace")
            print("\n" + proc.stdout + proc.stderr)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        finally:
            api.stop()


if __name__ == "__main__":
    unittest.main()
