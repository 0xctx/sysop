"""Named presets: every shipped one passes the safety rules; use, undo, readiness and the session line."""
import json
import sys

from helpers import ROOT, TempRepoCase

sys.path.insert(0, str(ROOT / "scripts"))
import presets  # noqa: E402
import workers  # noqa: E402


def preset(**over):
    base = {"name": "x", "summary": "s", "capacity": "c", "caveats": ["k"], "needs": ["claude"],
            "settings": {"tiers": {"fast": ["claude:haiku"], "standard": ["claude:sonnet"], "deep": ["claude:opus"],
                                   "review": ["pi:google/gemini-3.8-flash"], "verify": ["claude:haiku"]}}}
    base.update(over)
    return base


class SafetyRulesTest(TempRepoCase):
    def test_every_shipped_preset_passes(self):
        shipped = sorted((ROOT / "presets").glob("*.json"))
        self.assertEqual(len(shipped), 14)
        for path in shipped:
            data = json.loads(path.read_text())
            with self.subTest(preset=path.stem):
                self.assertEqual(data["name"], path.stem)
                self.assertEqual(presets.problems(data), [])
                self.assertTrue(data.get("session"), "every preset names the session model")

    def test_each_rule_bites(self):
        tiers = preset()["settings"]["tiers"]
        cases = {
            "known worker": {"settings": {"tiers": {**tiers, "fast": ["nope:x"]}}},
            "unknown effort": {"settings": {"tiers": {**tiers, "fast": ["claude:haiku@ultra"]}}},
            "cannot verify": {"settings": {"tiers": {**tiers, "verify": ["agy:gemini-3.8-flash-high"]}}},
            "pi-free named": {"settings": {"tiers": {**tiers, "fast": ["pi:cline/poolside/laguna-s-2.1:free"]}}},
            "no vendor other": {"settings": {"tiers": {**tiers, "review": ["claude:sonnet"]}}},
            "stated in caveats": {"settings": {"tiers": {**tiers, "review": ["claude:sonnet"]}, "cross_review": False}},
            "caveats is empty": {"caveats": []},
            "readiness can check": {"optional": ["fastrouter (untested)"]},
            "may only hold": {"settings": {"tiers": tiers, "land": True}},
        }
        for words, over in cases.items():
            with self.subTest(rule=words):
                self.assertTrue(any(words in p for p in presets.problems(preset(**over))), presets.problems(preset(**over)))
        ok = preset(caveats=["cross_review: false, said plainly"],
                    settings={"tiers": {**tiers, "review": ["claude:sonnet"]}, "cross_review": False})
        self.assertEqual(presets.problems(ok), [])


class PresetCommandTest(TempRepoCase):
    def setUp(self):
        super().setUp()
        self.use_fakebin()  # claude and agy only: no pi

    def user_settings(self):
        return json.loads((self.home / ".claude" / "sysop.json").read_text())

    def test_use_then_undo_with_previous(self):
        (self.home / ".claude").mkdir(exist_ok=True)
        old = {"land": True, "max_parallel": 5, "tiers": {"fast": ["claude:sonnet"]}}  # partial tiers: still undoable
        (self.home / ".claude" / "sysop.json").write_text(json.dumps(old))
        out = self.sysop("preset", "use", "claude-only").stdout
        self.assertIn("preset use previous", out)
        data = self.user_settings()
        self.assertEqual((data["preset"], data["cross_review"], data["tiers"]["review"]),
                         ("claude-only", False, ["claude:sonnet"]))
        self.assertTrue(data["land"])  # keys the preset does not set are untouched
        self.assertEqual(self.sysop_json("settings", "--key", "preset"), "claude-only")
        self.sysop("preset", "use", "previous")
        self.assertEqual(self.user_settings(), old)  # back exactly, added keys gone

    def test_repo_scope_and_a_repo_preset_wins(self):
        folder = self.repo / ".claude" / "sysop-presets"
        folder.mkdir(parents=True)
        (folder / "claude-only.json").write_text(json.dumps(preset(name="claude-only", summary="team copy")))
        self.assertIn("team copy", self.sysop("preset", "show", "claude-only").stdout)
        (self.repo / ".claude" / "sysop.json").write_text(json.dumps({"tiers": {"fast": ["claude:sonnet"]}}))
        out = self.sysop("preset", "use", "haiku-workhorse").stdout
        self.assertIn("this repo's own settings still decide tiers", out)
        self.sysop("preset", "use", "haiku-workhorse", "--scope", "repo")
        repo = json.loads((self.repo / ".claude" / "sysop.json").read_text())
        self.assertEqual(repo["tiers"]["fast"][0], "claude:haiku")

    def test_list_marks_readiness_and_the_active_preset(self):
        rows = {r["name"]: r for r in self.sysop_json("preset", "list", "--json")}
        self.assertTrue(rows["claude-plan"]["active"])  # the built-in default
        self.assertEqual(rows["claude-only"]["status"], "ready")
        self.assertEqual(rows["openrouter-free"]["status"], "missing")
        self.assertEqual(rows["openrouter-free"]["missing"], ["pi", "openrouter"])
        self.assertEqual(rows["claude-plan"]["status"], "partly")  # pi's Gemini entries cannot run here
        self.assertIn("OPENROUTER_API_KEY", self.sysop("preset", "show", "openrouter-free").stdout)

    def test_a_broken_preset_file_is_skipped_not_fatal(self):
        folder = self.home / ".claude" / "sysop-presets"
        folder.mkdir(parents=True)
        (folder / "bad.json").write_text('{"settings": {"tiers": {"fast": "claude:haiku"}}}')
        (folder / "worse.json").write_text("{not json")
        proc = self.sysop("preset", "list", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("skipping preset", proc.stderr)
        self.assertNotIn("bad", {r["name"] for r in json.loads(proc.stdout)})

    def test_diff_and_save(self):
        self.assertIn("claude-plan: your settings already match", self.sysop("preset", "diff", "claude-plan").stdout)
        self.assertIn('cross_review: true -> false', self.sysop("preset", "diff", "claude-only").stdout)
        self.sysop("preset", "save", "mine")
        saved = json.loads((self.home / ".claude" / "sysop-presets" / "mine.json").read_text())
        self.assertEqual(saved["settings"]["tiers"]["fast"][0], "claude:haiku")
        self.assertEqual(self.sysop("preset", "save", "mine").returncode, 1)  # never overwrites silently

    def test_session_line(self):
        self.sysop("roster")
        context = lambda: json.loads(self.run_hook("context.py", {"cwd": str(self.repo)}))[
            "hookSpecificOutput"]["additionalContext"]
        self.assertIn("sysop preset: ◐ claude-plan: 2 model(s) unavailable", context())
        self.sysop("models", "set", "fast", "claude:sonnet")
        self.assertIn("(edited since)", context())


class ClaudeIdentityTest(TempRepoCase):
    def test_an_exact_claude_release_routes(self):
        self.use_fakebin()
        (self.repo / ".claude").mkdir()
        (self.repo / ".claude" / "sysop.json").write_text(json.dumps({"tiers": {"fast": ["claude:claude-sonnet-4-6"]}}))
        pick = self.sysop_json("route", "small")
        self.assertEqual((pick["worker"], pick["model"], pick["family"]), ("claude", "claude-sonnet-4-6", "anthropic"))

    def test_the_resolved_release_is_read_from_claude_json(self):
        log = json.dumps({"type": "result", "result": "OK", "total_cost_usd": 0.01, "usage": {"output_tokens": 5},
                          "modelUsage": {"claude-haiku-4-5-20251001": {"outputTokens": 5},
                                         "claude-sonnet-4-6": {"outputTokens": 1}}})
        self.assertEqual(workers.run_usage(log)["resolved"], "claude-haiku-4-5-20251001")

    def test_out_of_credit_is_availability_with_a_long_rest(self):
        from unittest import mock
        self.enterContext(mock.patch.dict("os.environ", {"XDG_CACHE_HOME": str(self.home / ".cache")}))
        text = '402: {"message":"Insufficient credits. This account never purchased credits."}'
        self.assertEqual(workers.cool_unavailable("pi:openrouter/deepseek/deepseek-v4-pro", text), "out of credit")
        cooling = workers.active_cooldowns()["pi:openrouter/deepseek/deepseek-v4-pro"]
        self.assertGreater(cooling["until"] - __import__("time").time(), 5 * 3600)
