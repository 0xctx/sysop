"""A guided first run: narration until the first landing, a `next` hint on every stop, refusals that teach."""
import json

from helpers import TempRepoCase, git


class ExplainTest(TempRepoCase):
    def test_auto_narrates_until_the_first_landed_run(self):
        self.assertEqual(self.sysop_json("explain")["explain"], True)
        self.sysop("run", "start", "ship-x", "--session", "S1")
        self.sysop("run", "end", "ship-x", "--outcome", "blocked", "--reason", "x")
        self.assertTrue(self.sysop_json("explain")["explain"])  # a blocked run teaches nothing yet
        self.sysop("run", "start", "ship-y", "--session", "S1")
        self.sysop("run", "end", "ship-y", "--outcome", "landed")
        out = self.sysop_json("explain")
        self.assertEqual((out["explain"], out["mode"]), (False, "auto"))

    def test_on_and_off_override(self):
        self.sysop("config", "set", "explain", "off")
        self.assertFalse(self.sysop_json("explain")["explain"])
        self.sysop("config", "set", "explain", "on")
        self.assertTrue(self.sysop_json("explain")["explain"])

    def test_outside_git_it_still_answers(self):
        out = self.sysop_json("explain", cwd=self.home)
        self.assertTrue(out["explain"])
        self.assertTrue(self.sysop_json("session", cwd=self.home)["explain"])

    def test_session_carries_explain_and_the_old_alias_agrees(self):
        self.sysop("config", "set", "explain", "off")
        facts = self.sysop_json("session")
        self.assertEqual((facts["explain"], facts["wakes"]), (False, False))
        self.assertIn("interactive", facts)
        self.assertEqual(self.sysop_json("explain")["explain"], facts["explain"])

    def test_wakes_when_the_mods_heartbeat_for_this_session_is_fresh(self):
        import time
        self.env["CLAUDE_CODE_SESSION_ID"] = "S9"
        beat = self.home / ".cache" / "sysop" / "mod-S9.json"
        beat.parent.mkdir(parents=True, exist_ok=True)
        beat.write_text(json.dumps({"at": time.time()}))
        self.assertTrue(self.sysop_json("session")["wakes"])
        beat.write_text(json.dumps({"at": time.time() - 120}))  # the mod stopped (closed, reloaded, mods off)
        self.assertFalse(self.sysop_json("session")["wakes"])
        self.env["CLAUDE_CODE_SESSION_ID"] = "other"
        beat.write_text(json.dumps({"at": time.time()}))
        self.assertFalse(self.sysop_json("session")["wakes"])


class RefusalsTeachTest(TempRepoCase):
    """Each recoverable refusal says what it means and the one fix."""

    def setUp(self):
        super().setUp()
        self.sysop("store")
        self.sysop("config", "set", "cross_review", "false")
        wt = self.repo / ".sysop" / "wt" / "feat"
        git(self.repo, "worktree", "add", "-q", "-b", "feat", str(wt))
        self.commit("f.txt", "x\n", "feature", cwd=wt)
        (self.repo / ".sysop" / "reports").mkdir(exist_ok=True)
        (self.repo / ".sysop" / "reports" / "feat.review.md").write_text("**Verdict:** READY\n")
        self.sysop("review", "seal", "feat")
        self.wt = wt

    def test_no_check(self):
        err = self.sysop("land", "feat", "--base", "main", "--land").stderr
        self.assertIn("no check configured", err)
        self.assertIn("Fix: `sysop.py config set check", err)

    def test_no_authority_is_ready_with_the_one_fix(self):
        out = self.sysop_json("land", "feat", "--base", "main", "--check", "true")
        self.assertEqual((out["landed"], out["ready"]), (False, True))
        self.assertIn('`/sysop land`', out["reason"])
        self.assertIn("config set land true", out["reason"])
        self.assertEqual(out["next"], '`/sysop land` merges feat into main (or say "land it")')

    def test_a_landing_says_nothing_is_needed(self):
        out = self.sysop_json("land", "feat", "--base", "main", "--check", "true", "--land")
        self.assertEqual(out["next"], "nothing needed: the change is on main")

    def test_a_stale_seal_names_the_re_review(self):
        self.commit("g.txt", "y\n", "after review", cwd=self.wt)
        err = self.sysop("land", "feat", "--base", "main", "--check", "true", "--land").stderr
        self.assertIn("commits after a review are unreviewed", err)
        self.assertIn("review seal feat --re-review", err)

    def test_every_model_resting_says_when_one_is_back(self):
        self.sysop("models", "set", "fast", "claude:haiku", "--scope", "repo")
        self.sysop("cooldown", "add", "claude:haiku", "--seconds", "3600", "--reason", "quota")
        proc = self.sysop("route", "small")
        self.assertEqual(proc.returncode, 3)
        out = json.loads(proc.stdout)
        self.assertIn("resting after a quota or rate limit", out["why"])
        self.assertIn("back at", out["why"])
        self.assertIn("wait until", out["next"])

    def test_halt_names_unhalt(self):
        self.sysop("halt", "--reason", "lunch")
        self.assertIn("`sysop.py unhalt` to continue", self.sysop_json("next")["next"])

    def test_run_end_says_what_moves_things_forward(self):
        for outcome, expected in (("landed", "nothing needed"), ("ready", '`/sysop land`'),
                                  ("blocked", "decide: the check needs a database")):
            self.sysop("run", "start", "r", "--session", "S1")
            out = self.sysop_json("run", "end", "r", "--outcome", outcome, "--reason", "the check needs a database")
            self.assertIn(expected, out["next"])
