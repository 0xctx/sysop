import json
import time
import os

from helpers import TempRepoCase


class RosterTest(TempRepoCase):
    def setUp(self):
        super().setUp()
        self.calls = self.tmp / "agy-calls"
        self.env["FAKE_AGY_CALLS"] = str(self.calls)

    def agy_calls(self):
        return self.calls.read_text().count("models") if self.calls.exists() else 0

    def test_detects_both_and_lists_agy_models(self):
        self.use_fakebin()
        r = self.sysop_json("roster")
        self.assertTrue(r["workers"]["claude"]["available"])
        self.assertEqual(r["workers"]["claude"]["models"], ["haiku", "sonnet", "opus"])
        self.assertIn("gemini-3.8-flash-high", r["workers"]["agy"]["models"])
        self.assertNotIn("Fetching available models...", r["workers"]["agy"]["models"])

    def test_missing_cli_is_unavailable(self):
        self.use_fakebin(only=("claude",))
        r = self.sysop_json("roster")
        self.assertFalse(r["workers"]["agy"]["available"])

    def test_listing_failure_degrades_to_unknown_models(self):
        self.use_fakebin()
        self.env["FAKE_AGY_MODELS_FAIL"] = "1"
        agy = self.sysop_json("roster")["workers"]["agy"]
        self.assertTrue(agy["available"])
        self.assertIsNone(agy["models"])
        self.assertIn("not signed in", agy["error"])

    def test_cache_reused_until_refresh(self):
        self.use_fakebin()
        self.sysop_json("roster")
        self.sysop_json("roster")
        self.assertEqual(self.agy_calls(), 1)
        self.sysop_json("roster", "--refresh")
        self.assertEqual(self.agy_calls(), 2)
        self.assertTrue((self.home / ".cache" / "sysop" / "roster.json").is_file())

    def test_stale_cache_is_rebuilt(self):
        self.use_fakebin()
        self.sysop_json("roster")
        cache = self.home / ".cache" / "sysop" / "roster.json"
        data = json.loads(cache.read_text())
        data["checked_at"] -= 25 * 3600
        cache.write_text(json.dumps(data))
        self.sysop_json("roster")
        self.assertEqual(self.agy_calls(), 2)


class RouteTest(TempRepoCase):
    def setUp(self):
        super().setUp()
        self.use_fakebin()
        self.pin_tiers()

    def settings(self, data):
        (self.repo / ".claude").mkdir(exist_ok=True)
        (self.repo / ".claude" / "sysop.json").write_text(json.dumps(data))

    def test_size_maps_to_tier_and_first_entry_wins(self):
        pick = self.sysop_json("route", "small")
        self.assertEqual((pick["tier"], pick["worker"], pick["model"]),
                         ("fast", "agy", "gemini-3.8-flash-high"))
        self.assertEqual(pick["family"], "google")  # the free pi key comes first; pi is not installed here
        self.assertEqual(pick["skipped"], [{"entry": "pi:google/gemini-3.8-flash", "reason": "pi not installed"}])

    def test_missing_cli_falls_through_with_reason(self):
        self.use_fakebin(only=("claude",))
        (self.tmp / "bin" / "agy").unlink()
        self.sysop("roster", "--refresh")
        pick = self.sysop_json("route", "small")
        self.assertEqual(pick["worker"], "claude")
        self.assertEqual([s["reason"] for s in pick["skipped"]], ["pi not installed", "agy not installed"])

    def test_exclude_entry_and_whole_worker(self):
        pick = self.sysop_json("route", "fast", "--exclude", "agy:gemini-3.8-flash-high")
        self.assertEqual(pick["model"], "haiku")
        self.assertEqual(pick["skipped"][1]["reason"], "excluded")
        pick = self.sysop_json("route", "deep", "--exclude", "claude")
        self.assertEqual(pick["worker"], "agy")

    def test_cross_family_review(self):
        pick = self.sysop_json("route", "large", "--role", "review", "--not-family", "claude")
        self.assertEqual((pick["worker"], pick["model"]), ("agy", "gemini-3.8-flash-high"))
        self.assertIn("same vendor as the author (anthropic)", pick["skipped"][0]["reason"])

    def test_exhausted_tier_exits_3(self):
        pick = self.sysop_json("route", "fast", "--exclude", "agy", "--exclude", "claude", code=3)
        self.assertIsNone(pick["worker"])
        self.assertEqual([s["reason"] for s in pick["skipped"]], ["pi not installed", "excluded", "excluded", "pi not installed"])

    def test_model_not_offered_is_skipped(self):
        self.settings({"tiers": {"fast": ["agy:gemini-9-imaginary", "claude:haiku"]}})
        pick = self.sysop_json("route", "fast")
        self.assertEqual(pick["worker"], "claude")
        self.assertIn("not offered", pick["skipped"][0]["reason"])

    def test_unknown_name_and_malformed_entry(self):
        self.assertEqual(self.sysop("route", "huge").returncode, 1)
        self.settings({"tiers": {"fast": ["nonsense", "claude:haiku"]}})
        pick = self.sysop_json("route", "fast")
        self.assertIn("malformed", pick["skipped"][0]["reason"])

    def test_prompt_file_emits_quoted_command_per_role(self):
        pick = self.sysop_json("route", "small", "--prompt-file", "/tmp/my brief.md")
        self.assertIn("agy -p", pick["cmd"])
        self.assertIn("'/tmp/my brief.md'", pick["cmd"])
        self.assertIn("--dangerously-skip-permissions", pick["cmd"])
        review = self.sysop_json("route", "small", "--role", "review", "--prompt-file", "/tmp/r.md")
        self.assertIn("--mode plan", review["cmd"])
        self.assertNotIn("skip-permissions", review["cmd"])
        claude = self.sysop_json("route", "large", "--role", "review", "--prompt-file", "/tmp/r.md")
        self.assertIn("--permission-mode plan", claude["cmd"])

    def test_custom_worker_routes_but_cannot_shadow_builtin(self):
        fake = self.tmp / "bin" / "codex"
        fake.write_text("#!/bin/sh\necho codex\n")
        fake.chmod(0o755)
        self.settings({"workers": {"codex": {"build": "codex exec -m {model} {prompt_file}"},
                                   "agy": {"build": "echo hijacked {prompt_file}"}},
                       "tiers": {"fast": ["codex:gpt-5", "claude:haiku"]}})
        pick = self.sysop_json("route", "fast", "--prompt-file", "/tmp/b.md")
        self.assertEqual((pick["worker"], pick["family"]), ("codex", "openai"))
        self.assertEqual(pick["cmd"], "codex exec -m gpt-5 /tmp/b.md")
        agy = self.sysop_json("route", "fast", "--exclude", "codex", "--exclude", "claude",
                              code=3)
        self.assertIsNone(agy["worker"])
        self.settings({"workers": {"agy": {"build": "echo hijacked {prompt_file}"}}})
        real = self.sysop_json("route", "small", "--prompt-file", "/tmp/b.md")
        self.assertNotIn("hijacked", real["cmd"])


class FallbackXreviewTest(TempRepoCase):
    def setUp(self):
        super().setUp()
        self.use_fakebin()
        self.brief = self.repo / "brief.md"
        self.brief.write_text("do the thing")

    def test_route_emits_absolute_prompt_path(self):
        pick = self.sysop_json("route", "small", "--prompt-file", "brief.md")
        self.assertIn(str(self.brief), pick["cmd"])

    def test_fallback_cools_down_logs_and_routes_past_every_tried_worker(self):
        self.sysop("ticket", "add", "t1", "--title", "x", "--size", "small")
        self.sysop("launch", "t1", str(self.repo), "exit 1", "--worker", "agy:gemini-3.8-flash-high")
        self.sysop("wait", "t1", "--timeout", "10", "--poll", "0.2")
        pick = self.sysop_json("fallback", "t1", "--from", "agy:gemini-3.8-flash-high", "--size", "small",
                               "--reason", "quota reached", "--cooldown-seconds", "3600",
                               "--whole-worker", "--prompt-file", str(self.brief))
        self.assertEqual((pick["worker"], pick["model"]), ("claude", "haiku"))
        self.assertIn("--dangerously-skip-permissions", pick["cmd"])
        self.assertIn("agy", self.sysop_json("cooldown", "list"))
        md = (self.repo / ".sysop" / "tickets.md").read_text()
        self.assertIn("fallback agy:gemini-3.8-flash-high -> claude:haiku: quota reached", md)
        # everything tried: nothing left
        self.sysop("launch", "t1", str(self.repo), "exit 1", "--worker", "claude:haiku")
        self.sysop("wait", "t1", "--timeout", "10", "--poll", "0.2")
        none = self.sysop_json("fallback", "t1", "--from", "claude:haiku", "--size", "small",
                               "--reason", "down", code=3)
        self.assertIn("claude:haiku", self.sysop_json("cooldown", "list"))  # default cooldown
        self.assertIsNone(none["worker"])
        self.assertIn("none left", (self.repo / ".sysop" / "tickets.md").read_text())

    def test_xreview_runs_external_reviewer_and_writes_the_artifact(self):
        from helpers import git
        git(self.repo, "checkout", "-q", "-b", "feat")
        self.commit("a.py", "x = 1\n", "add a")
        git(self.repo, "checkout", "-q", "main")
        start = self.sysop_json("xreview", "start", "feat", "--base", "main", "--author-family", "claude")
        self.assertEqual(start["status"], "running")
        self.assertEqual(start["worker"], "agy:gemini-3.8-flash-high")  # review tier, first entry
        got = self.sysop_json("xreview", "collect", "feat", "--timeout", "20")
        self.assertEqual(got["status"], "done")
        text = (self.repo / ".sysop" / "reports" / "feat.xreview.md").read_text()
        self.assertTrue(text.startswith("**Cross-family review:** agy:"))
        self.assertIn("REVIEW-START", text)
        record = json.loads((self.repo / ".sysop" / "reports" / "feat.xreview.json").read_text())
        self.assertEqual(record["status"], "done")
        prompt = (self.repo / ".sysop" / "reports" / "feat.xreview-prompt.md").read_text()
        self.assertIn("+x = 1", prompt)
        run = self.repo / ".sysop" / "runs" / "xreview-feat" / "1"
        self.assertIn(".sysop/wt/xreview-feat", (run / "log").read_text())  # ran at the branch head
        self.assertFalse((self.repo / ".sysop" / "wt" / "xreview-feat").exists())  # cleaned up

    def test_a_review_that_does_not_show_coverage_is_no_review(self):
        from helpers import git
        self.sysop("models", "set", "review", "agy:gemini-3.8-flash-high", "pi:google/gemini-3.5-flash")
        self.use_fakebin(only=("agy", "claude", "pi"))
        self.sysop("roster", "--refresh")
        git(self.repo, "checkout", "-q", "-b", "feat")
        self.commit("a.py", "x = 1\n", "add a")
        git(self.repo, "checkout", "-q", "main")
        self.env["FAKE_NO_COVERAGE"] = "1"  # a bare verdict, like the free reviewer's on 2026-10-07
        self.sysop_json("xreview", "start", "feat", "--base", "main", "--author-family", "claude")
        got = self.sysop_json("xreview", "collect", "feat", "--timeout", "30")
        self.assertEqual(got["status"], "unavailable")  # neither counted
        self.assertEqual(sorted(got["failed_before"]), ["agy:gemini-3.8-flash-high", "pi:google/gemini-3.5-flash"])
        import sys
        sys.path.insert(0, str(__import__("helpers").ROOT / "scripts"))
        import workers
        self.assertEqual(workers.uncovered("REVIEW-START\nok\nCOVERAGE\nsrc/a.py: read", ["src/a.py"]), [])
        self.assertEqual(workers.uncovered("REVIEW-START\nok\nCOVERAGE\na.py: read", ["src/a.py", "b.py"]), ["b.py"])
        self.assertEqual(workers.uncovered("REVIEW-START\nREADY", ["a.py"]), ["a.py"])
        self.assertEqual(workers.uncovered("REVIEW-START\nREADY", []), [])  # an empty diff has nothing to cover

    def test_a_reviewer_out_of_quota_is_cooled_down(self):
        self.sysop("models", "set", "review", "agy:gemini-3.8-flash-high", "agy:gemini-3.1-pro-high")
        self.env.update(FAKE_AGY_OUTPUT='{"status":"RESOURCE_EXHAUSTED","code":429} Resets in 2h0m0s.',
                        FAKE_AGY_EXIT="3")
        self.sysop_json("xreview", "start", "main", "--base", "main", "--author-family", "claude")
        got = self.sysop_json("xreview", "collect", "main", "--timeout", "30")
        self.assertEqual(got["status"], "unavailable")
        cooling = self.sysop_json("cooldown", "list")
        self.assertAlmostEqual(cooling["agy:gemini-3.8-flash-high"]["until"] - time.time(), 7200, delta=120)
        self.assertIn("agy:gemini-3.1-pro-high", cooling)
        self.env["FAKE_AGY_OUTPUT"] = "Error: authentication failed or timed out"
        self.sysop("cooldown", "clear", "agy:gemini-3.8-flash-high")
        from helpers import git
        git(self.repo, "branch", "other")  # a fresh review: reviewers tried on `main` are not excluded
        self.sysop_json("xreview", "start", "other", "--base", "main", "--author-family", "claude")
        self.sysop_json("xreview", "collect", "other", "--timeout", "30")
        self.assertIn("sign-in expired", self.sysop_json("cooldown", "list")["agy"]["reason"])

    def risky_setup(self, *tier):
        from helpers import git
        self.use_fakebin(only=("agy", "claude", "pi"))
        self.sysop("roster", "--refresh")
        self.sysop("models", "set", "review", *tier)
        git(self.repo, "checkout", "-q", "-b", "feat")
        self.commit("a.py", "x = 1\n", "add a")
        git(self.repo, "checkout", "-q", "main")
        return self.sysop_json("xreview", "start", "feat", "--base", "main", "--author-family", "claude", "--risky")

    def test_risky_work_gets_two_reviewers_from_two_other_vendors(self):
        start = self.risky_setup("agy:gemini-3.8-flash-high", "pi:cline/google/gemma-4-31b-it:free",
                                 "pi:cline/nvidia/nemotron-3-super-120b-a12b:free")
        self.assertEqual(start["worker"], "agy:gemini-3.8-flash-high")
        self.assertEqual(start["second"]["worker"], "pi:cline/nvidia/nemotron-3-super-120b-a12b:free")  # not Google again
        got = self.sysop_json("xreview", "collect", "feat", "--timeout", "30")
        self.assertEqual((got["status"], got["second"]["status"]), ("done", "done"))
        text = (self.repo / ".sysop" / "reports" / "feat.xreview.md").read_text()
        self.assertIn("## Second cross-family reviewer", text)
        self.assertIn("**Cross-family review:** pi:cline/nvidia/nemotron-3-super-120b-a12b:free, exit 0", text)
        record = json.loads((self.repo / ".sysop" / "reports" / "feat.xreview.json").read_text())
        self.assertEqual(record["worker"], "agy:gemini-3.8-flash-high; pi:cline/nvidia/nemotron-3-super-120b-a12b:free")

    def test_without_a_second_vendor_one_reviewer_stands_with_the_reason(self):
        start = self.risky_setup("agy:gemini-3.8-flash-high", "pi:cline/google/gemma-4-31b-it:free")
        self.assertEqual(start["second"]["status"], "unavailable")
        got = self.sysop_json("xreview", "collect", "feat", "--timeout", "30")
        self.assertEqual(got["status"], "done")
        text = (self.repo / ".sysop" / "reports" / "feat.xreview.md").read_text()
        self.assertIn("or the first reviewer's (google)", text)

    def test_a_failed_second_reviewer_leaves_the_first_standing(self):
        self.env["FAKE_PI_EXIT"] = "1"  # the pi reviewer produces no review (collect judges each reviewer alike)
        self.risky_setup("agy:gemini-3.8-flash-high", "pi:cline/nvidia/nemotron-3-super-120b-a12b:free")
        got = self.sysop_json("xreview", "collect", "feat", "--timeout", "30")
        self.assertEqual((got["status"], got["second"]["status"]), ("done", "unavailable"))

    def test_xreview_unavailable_and_claude_cases_are_recorded(self):
        self.sysop("cooldown", "add", "agy", "--seconds", "3600", "--reason", "quota")
        got = self.sysop_json("xreview", "start", "main", "--base", "main", "--author-family", "claude")
        self.assertEqual(got["status"], "unavailable")
        text = (self.repo / ".sysop" / "reports" / "main.xreview.md").read_text()
        self.assertIn("unavailable", text)
        self.assertIn("cooling down", text)
        got = self.sysop_json("xreview", "start", "main", "--base", "main", "--author-family", "gemini")
        self.assertEqual(got["status"], "claude-agents")


class ModelsTest(TempRepoCase):
    def test_show_and_set(self):
        self.use_fakebin()
        out = self.sysop("models").stdout
        self.assertIn("review  (cross-family review)", out)
        self.assertNotIn("pi:cline/", out)  # pi-free is never a default (finding A3)
        self.assertEqual(self.sysop("models", "set", "review", "agy:claude-sonnet-4-6", "claude:opus").returncode, 0)
        data = json.loads((self.home / ".claude" / "sysop.json").read_text())
        self.assertEqual(data["tiers"]["review"], ["agy:claude-sonnet-4-6", "claude:opus"])
        self.sysop("models", "route", "small", "standard", "--scope", "repo")
        self.assertEqual(self.sysop_json("settings", "--key", "routes")["small"], "standard")
        self.assertEqual(self.sysop("models", "set", "fast", "nobody:x").returncode, 1)
        self.assertEqual(self.sysop("models", "set", "fast", "agy").returncode, 1)


class KindsEffortAgentsTest(TempRepoCase):
    def setUp(self):
        super().setUp()
        self.use_fakebin()

    def test_configured_kind_beats_size_and_defaults_only_raise(self):
        self.assertEqual(self.sysop_json("route", "small", "--kind", "security")["tier"], "deep")
        self.assertEqual(self.sysop_json("route", "large", "--kind", "docs")["tier"], "standard")  # unmapped
        self.sysop("models", "route", "docs", "fast")
        self.assertEqual(self.sysop_json("route", "large", "--kind", "docs")["tier"], "fast")
        self.assertEqual(self.sysop_json("route", "deep", "--kind", "docs")["tier"], "deep")  # explicit tier wins

    def test_effort_suffix_reaches_the_command(self):
        self.sysop("models", "set", "fast", "claude:sonnet@low", "agy:gpt-oss-120b-medium@high")
        pick = self.sysop_json("route", "small", "--prompt-file", "/tmp/b.md")
        self.assertEqual((pick["model"], pick["effort"], pick["family"]), ("sonnet", "low", "anthropic"))
        self.assertTrue(pick["cmd"].endswith("--effort low"))
        self.assertEqual(self.sysop("models", "set", "fast", "claude:sonnet@turbo").returncode, 1)

    def test_cooldown_on_model_covers_its_effort_variants(self):
        self.sysop("models", "set", "fast", "agy:gpt-oss-120b-medium@high", "claude:haiku")
        self.sysop("cooldown", "add", "agy:gpt-oss-120b-medium", "--seconds", "600", "--reason", "503")
        self.assertEqual(self.sysop_json("route", "small")["worker"], "claude")

    def test_agent_models_are_settings(self):
        self.assertEqual(self.sysop("models", "agent", "sysop:code-reviewer", "sonnet").returncode, 0)
        self.assertEqual(self.sysop_json("settings", "--key", "agents"),
                         {"default": "sonnet", "sysop:code-reviewer": "sonnet"})
        self.assertEqual(self.sysop("models", "agent", "default", "gpt-5").returncode, 1)
        self.assertIn("sysop:code-reviewer = sonnet", self.sysop("models").stdout)

    def test_ticket_and_ledger_carry_kind(self):
        self.sysop("ticket", "add", "t1", "--title", "x", "--size", "small", "--kind", "tests")
        self.assertIn("**Kind:** tests", (self.repo / ".sysop" / "tickets.md").read_text())
        row = self.sysop_json("ledger", "add", "--ticket", "t1", "--kind", "tests")
        self.assertEqual(row["kind"], "tests")


class XreviewAdvanceTest(TempRepoCase):
    def test_collect_moves_past_a_failed_reviewer_to_a_terminal_result(self):
        self.use_fakebin()
        from helpers import git
        (self.repo / ".claude").mkdir()
        (self.repo / ".claude" / "sysop.json").write_text(json.dumps({"tiers": {"review": [
            "agy:gpt-oss-120b-medium", "agy:gemini-3.1-pro-high", "claude:sonnet"]}}))
        git(self.repo, "checkout", "-q", "-b", "feat")
        self.commit("a.py", "x = 1\n", "add a")
        git(self.repo, "checkout", "-q", "main")
        self.env["FAKE_AGY_EXIT"] = "3"  # every agy reviewer fails, like a quota
        self.assertEqual(self.sysop_json("xreview", "start", "feat", "--base", "main",
                                         "--author-family", "claude")["status"], "running")
        got = self.sysop_json("xreview", "collect", "feat", "--timeout", "30")
        self.assertEqual(got["status"], "unavailable")  # claude is the author's family
        self.assertEqual(sorted(got["failed_before"]), ["agy:gemini-3.1-pro-high", "agy:gpt-oss-120b-medium"])
        rec = json.loads((self.repo / ".sysop" / "reports" / "feat.xreview.json").read_text())
        self.assertEqual(rec["status"], "unavailable")


class WorkerAttributionTest(TempRepoCase):
    def test_claude_worker_commits_without_attribution_unless_allowed(self):
        self.use_fakebin()
        self.pin_tiers()
        cmd = self.sysop_json("route", "large", "--prompt-file", "/tmp/b.md")["cmd"]
        self.assertIn('"includeCoAuthoredBy": false', cmd)
        agy = self.sysop_json("route", "small", "--prompt-file", "/tmp/b.md")["cmd"]
        self.assertNotIn("--settings", agy)
        (self.repo / ".claude").mkdir()
        (self.repo / ".claude" / "sysop.json").write_text('{"allow_attribution": true}')
        self.assertNotIn("--settings", self.sysop_json("route", "large", "--prompt-file", "/tmp/b.md")["cmd"])


class UnifiedRoutingTest(TempRepoCase):
    def setUp(self):
        super().setUp()
        self.use_fakebin()
        self.pin_tiers()

    def test_vendor_families(self):
        import sys
        sys.path.insert(0, str(__import__("helpers").ROOT / "scripts"))
        import workers
        cases = {"haiku": "anthropic", "claude-sonnet-4-6": "anthropic",
                 "openrouter/anthropic/claude-3.7-sonnet": "anthropic", "gemini-3.1-pro-high": "google",
                 "google/gemma-4-31b-it": "google", "gpt-oss-120b-medium": "openai", "o3-mini": "openai",
                 "openai/gpt-5": "openai", "qwen3-coder-30b-a3b": "alibaba", "meta-llama/llama-4-scout": "meta",
                 "devstral-small-2": "mistral", "deepseek-v3.2": "deepseek", "kimi-k2": "moonshot",
                 "glm-4.6": "zhipu", "x-ai/grok-4": "xai", "minimax-m2": "minimax", "phi-4": "microsoft",
                 "nvidia/nemotron-ultra": "nvidia", "something-new": "unknown",
                 "claude": "anthropic", "gemini": "google", "anthropic": "anthropic"}
        for model, vendor in cases.items():
            self.assertEqual(workers.family(model), vendor, model)

    def test_kind_beats_size_and_explicit_tier_wins(self):
        self.assertEqual(self.sysop_json("route", "small", "--kind", "security")["tier"], "deep")
        self.assertEqual(self.sysop_json("route", "large", "--kind", "unmapped")["tier"], "standard")
        self.assertEqual(self.sysop_json("route", "deep", "--kind", "writing")["tier"], "deep")
        self.assertEqual(self.sysop("route", "huge").returncode, 1)
        self.sysop("models", "route", "docs", "nope")  # refused: unknown tier
        self.assertNotIn("docs", self.sysop_json("settings", "--key", "routes"))
        refused = self.sysop("models", "route", "fast", "auto:code")  # a tier name always wins: dead route
        self.assertEqual(refused.returncode, 1)
        self.assertIn("is a tier name", refused.stderr)
        self.assertNotIn("fast", self.sysop_json("settings", "--key", "routes"))

    def test_auto_chain_ranks_by_prefer_and_skips_cooling(self):
        pick = self.sysop_json("route", "large", "--kind", "writing")
        self.assertEqual(pick["tier"], "auto:writing")
        self.assertEqual(pick["chain"][0], "claude:sonnet")              # balanced: best, then speed
        self.sysop("config", "set", "prefer", "economy")
        chain = self.sysop_json("route", "large", "--kind", "writing")["chain"]
        self.assertEqual(chain[:2], ["claude:sonnet", "agy:claude-sonnet-4-6"])  # both plans: own CLI first
        self.env["ANTHROPIC_API_KEY"] = "sk-test"  # claude billed per token
        chain = self.sysop_json("route", "large", "--kind", "writing")["chain"]
        self.assertTrue(chain[0].startswith("agy:"), chain)              # plan before paid
        del self.env["ANTHROPIC_API_KEY"]
        self.sysop("cooldown", "add", "agy", "--seconds", "600", "--reason", "quota")
        chain = self.sysop_json("route", "large", "--kind", "writing")["chain"]
        self.assertFalse(any(e.startswith("agy:") for e in chain), chain)
        self.assertEqual(self.sysop("config", "set", "prefer", "cheapest").returncode, 1)

    def test_user_catalog_entries_join_auto_chains(self):
        (self.repo / ".claude").mkdir()
        (self.repo / ".claude" / "sysop.json").write_text(json.dumps({"prefer": "quality", "catalog": {
            "agy:gemini-3.8-flash-high": {"skills": {"writing": 9}}}}))
        self.assertEqual(self.sysop_json("route", "large", "--kind", "writing")["chain"][0],
                         "agy:gemini-3.8-flash-high")

    def test_effort_translated_per_worker(self):
        self.sysop("models", "set", "fast", "agy:gemini-3.8-flash-high@max", "claude:haiku@max")
        agy = self.sysop_json("route", "small", "--prompt-file", "/tmp/b.md")["cmd"]
        self.assertTrue(agy.endswith("--effort high"), agy)
        claude = self.sysop_json("route", "small", "--exclude", "agy", "--prompt-file", "/tmp/b.md")["cmd"]
        self.assertTrue(claude.endswith("--effort max"), claude)

    def test_models_available_and_set_warning(self):
        out = self.sysop("models", "available", "gemini").stdout
        self.assertIn("agy:gemini-3.8-flash-high", out)
        self.assertIn("code4", out)
        self.assertNotIn("claude:haiku", out)
        proc = self.sysop("models", "set", "fast", "agy:gemini-9-imaginary", "claude:haiku")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("does not list 'gemini-9-imaginary'", proc.stderr)

    def test_models_check_cools_failing_entries(self):
        self.env["FAKE_AGY_EXIT"] = "3"   # agy answers with an error
        self.env["FAKE_AGY_PWD"] = str(self.repo / "probe-pwd")
        proc = self.sysop("models", "check", "fast", timeout=120)
        probe_dir = (self.repo / "probe-pwd").read_text().strip()
        self.assertNotIn(str(self.repo), probe_dir)        # probes never start inside the repo
        self.assertFalse(os.path.exists(probe_dir))        # and their empty dir is removed
        self.assertEqual(proc.returncode, 1)
        self.assertIn("FAIL", proc.stdout)
        self.assertIn("agy:gemini-3.8-flash-high", self.sysop_json("cooldown", "list"))
        self.assertNotIn("claude:haiku", self.sysop_json("cooldown", "list"))
