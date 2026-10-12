"""status and doctor: one screen each, read-only, with the next step or the one fix."""
import json

from helpers import TempRepoCase
from test_next import FleetCase


class StatusTest(TempRepoCase):
    def setUp(self):
        super().setUp()
        self.use_fakebin()

    def test_a_fresh_repo_points_at_setup_and_creates_nothing(self):
        out = self.sysop("status").stdout
        self.assertIn("sysop preset: ", out)
        self.assertIn("Runs: none", out)
        self.assertIn("Next: set sysop up for this repo: `/sysop:setup`", out)
        self.assertFalse((self.repo / ".sysop").exists())

    def test_tickets_runs_and_resting_models(self):
        self.sysop("config", "set", "check", "true", "--scope", "repo")
        self.sysop("ticket", "add", "t1", "--title", "one", "--size", "small", "--kind", "chore",
                   "--base", "main", "--authority", "none")
        self.sysop("ticket", "set", "t1", "--status", "blocked")
        self.sysop("ticket", "log", "t1", "needs a product decision")
        self.sysop("cooldown", "add", "agy", "--seconds", "600", "--reason", "quota")
        self.sysop("run", "start", "fleet", "--session", "S1")
        s = self.sysop_json("status", "--json")
        self.assertEqual(len(s["needs_you"]), 1)
        self.assertTrue(s["needs_you"][0].startswith("t1: ") and s["needs_you"][0].endswith("needs a product decision"))
        self.assertEqual(s["next"], s["needs_you"][0])  # what needs you comes first
        self.assertTrue(any(r.startswith("fleet (active") for r in s["runs"]))
        self.assertTrue(s["resting"][0].startswith("agy until"))
        self.assertIn("Spend today", s["spend"])
        row = s["tickets"][0]  # the mod's board reads these rows
        self.assertEqual((row["id"], row["column"], row["repo"]), ("t1", "needs_you", str(self.repo)))
        self.assertTrue(row["last"].endswith("needs a product decision"))
        self.assertEqual(row["actions"], ["why", "halt"])
        self.assertEqual(s["repos"], [str(self.repo)])
        self.assertEqual(s["session_model"], "sonnet")  # claude-plan, the default preset

    def test_an_unreadable_ticket_file_is_reported_not_fatal(self):
        self.sysop("store")
        (self.repo / ".sysop" / "tickets.json").write_text("{broken")
        proc = self.sysop("status")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("cannot be read", proc.stdout)


class DoctorTest(TempRepoCase):
    def test_each_failure_has_its_fix_and_only_essentials_fail_the_exit(self):
        self.use_fakebin()
        proc = self.sysop("doctor", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        checks = {c["check"]: c for c in json.loads(proc.stdout)}
        self.assertTrue(checks["python"]["ok"])
        self.assertTrue(checks["hooks"]["ok"], checks["hooks"])  # wired, and the guard really blocks
        self.assertFalse(checks["check command"]["ok"])
        self.assertIn("/sysop:setup", checks["check command"]["fix"])
        self.assertIsNone(checks["pi"]["ok"])  # optional and absent is not a failure
        self.assertTrue(checks["mods"]["ok"], checks["mods"])
        for c in checks.values():
            if c["ok"] is False:
                self.assertTrue(c["fix"], c)

    def test_an_old_claude_code_says_mods_are_off(self):
        self.use_fakebin()
        self.env["FAKE_CLAUDE_VERSION"] = "2.1.200"
        checks = {c["check"]: c for c in json.loads(self.sysop("doctor", "--json").stdout)}
        self.assertFalse(checks["mods"]["ok"])
        self.assertIn("2.1.287+", checks["mods"]["fix"])


class BoardRowsTest(FleetCase):
    def test_a_ready_ticket_without_authority_offers_land(self):
        self.add("t2")
        self.through_review(self.actions("launch")[0])
        self.sysop("halt", "--reason", "x")
        s = self.sysop_json("status", "--json")
        row = s["tickets"][0]
        self.assertEqual((row["column"], row["actions"]), ("needs_you", ["land", "why", "halt"]))
        self.assertTrue(row["report"].endswith("sysop-t2.review.md"))
        self.assertEqual(s["halted"], [str(self.repo)])


class PiSourcesTest(TempRepoCase):
    def test_each_pi_source_is_listed_resting_absent_or_answering(self):
        self.use_fakebin(only=("claude", "pi"))
        self.env["FAKE_PI_EXTRA"] = "kilo  nvidia/nemotron-3.5-lightning:free  128K  8K  yes  no"
        self.sysop("roster", "--refresh")
        self.sysop("cooldown", "add", "pi:cline/", "--seconds", "600", "--reason", "not signed in")
        line = lambda *a: {c["check"]: c for c in self.sysop_json("doctor", "--json", *a)}["pi models"]["seen"]
        seen = line()
        for part in ("kilo listed", "gemini-key listed", "pi-free resting (not signed in)", "groq absent",
                     "openrouter absent"):
            self.assertIn(part, seen)
        self.assertIn("kilo answering", line("--probe"))  # the fake answers OK to the 1-token probe
