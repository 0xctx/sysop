import json
import sys
import time

from helpers import ROOT, TempRepoCase

sys.path.insert(0, str(ROOT / "scripts"))
import intel  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "intel"


class IntelPureTest(TempRepoCase):
    def test_versions_and_lines(self):
        self.assertEqual(intel.line_and_version("gemini-3.5-flash"), ("gemini-#-flash", (3, 5)))
        self.assertEqual(intel.line_and_version("qwen3.8-27b-low"), ("qwen#-27b", (3, 8)))
        self.assertEqual(intel.line_and_version("gemma-4-31b-it")[0], "gemma-#-31b-it")  # size stays in the line
        self.assertTrue(intel.newer_than("pi:google/gemini-3.5-flash", "pi:google/gemini-3.8-flash", {}))
        self.assertFalse(intel.newer_than("pi:google/gemma-4-12b-it", "pi:google/gemma-4-31b-it", {}))
        self.assertFalse(intel.newer_than("agy:gemini-3.5-flash", "pi:google/gemini-3.8-flash", {}))  # other CLI
        self.assertFalse(intel.newer_than("pi:cline/google/gemini-3.5-flash", "pi:google/gemini-3.8-flash", {}))
        dates = {"pi:google/gemini-3.5-flash": {"released": 200}, "pi:google/gemini-3.8-flash": {"released": 100}}
        self.assertFalse(intel.newer_than("pi:google/gemini-3.5-flash", "pi:google/gemini-3.8-flash", dates))

    def test_aa_matches_variants_never_siblings(self):
        aa = {k: {"coding": v.get("codingIndex"), "gpqa": v.get("gpqa")}
              for k, v in json.loads((FIXTURES / "benchmarks.json").read_text()).items()}
        self.assertEqual(intel.aa_match("gemini-3.5-flash", aa)["coding"], 70.1)  # -high, not -lite
        self.assertEqual(intel.aa_match("claude-sonnet-5", aa)["coding"], 71.5)
        self.assertIsNone(intel.aa_match("gemini-3.8-flash", aa))
        tricky = {"m-2-flash-lite": {"coding": 90}, "m-2-flash-high": {"coding": 50}}  # sibling scores higher
        self.assertEqual(intel.aa_match("m-2-flash", tricky)["coding"], 50)

    def test_inferred_scores_leave_unknown_skills_out(self):
        entry = intel.infer_entry({"coding": 70.1, "gpqa": 0.91, "ctx": 1048576, "free": True}, latency=12)
        self.assertEqual(entry["skills"], {"code": 5, "reasoning": 5, "review": 5, "writing": 5, "long": 5})
        self.assertEqual((entry["cost"], entry["speed"], entry["source"]), ("free", 4, "inferred"))
        self.assertNotIn("writing", entry["index"])  # a proxy score never gets a raw tie-breaker
        bare = intel.infer_entry({"ctx": 64000})
        self.assertEqual(bare["skills"], {"long": 2})
        self.assertNotIn("speed", bare)


class IntelCliTest(TempRepoCase):
    def setUp(self):
        super().setUp()
        self.use_fakebin(only=("agy", "claude", "pi"))
        self.env["SYSOP_INTEL_FIXTURES"] = str(FIXTURES)

    def intel_file(self):
        return json.loads((self.home / ".cache" / "sysop" / "intel.json").read_text())

    def test_core_roster_refresh_leaves_the_intel_layer_alone(self):
        self.sysop_json("roster", "--refresh")
        self.assertFalse((self.home / ".cache" / "sysop" / "intel.json").exists())  # no network, no facts
        self.assertEqual(self.sysop_json("route", "large")["entry"], "claude:sonnet")
        self.assertFalse((self.home / ".cache" / "sysop" / "intel.json").exists())  # named tiers need none

    def test_refresh_builds_facts_offline_from_fixtures(self):
        self.sysop("config", "set", "intel", "true")
        self.sysop_json("roster", "--refresh")
        models = self.intel_file()["models"]
        self.assertEqual(models["pi:google/gemini-3.5-flash"]["coding"], 70.1)
        self.assertTrue(models["pi:google/gemini-3.5-flash"]["free"])
        self.assertEqual(models["claude:sonnet"]["coding"], 71.5)              # alias -> newest sonnet
        self.assertEqual(models["agy:gpt-oss-120b-medium"]["coding"], 30.4)    # effort suffix stripped
        self.assertFalse(models["pi:kaggle/qwen3-coder-30b"]["reasoning"])     # pi's own listing

    def test_offline_keeps_working_and_says_why(self):
        self.env["SYSOP_INTEL_FIXTURES"] = str(self.tmp / "nothing")
        proc = self.sysop("models", "advise", "--refresh")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("quality data: estimates only", proc.stdout)  # no snapshot, no key: said plainly
        self.assertFalse(self.intel_file()["sources"]["openrouter"]["ok"])

    def test_uncatalogued_model_joins_auto_chains(self):
        self.sysop_json("roster", "--refresh")
        self.sysop("models", "route", "docs", "auto:code")
        self.sysop("config", "set", "prefer", "economy")
        chain = self.sysop_json("route", "large", "--kind", "docs")["chain"]
        self.assertEqual(chain[0], "pi:google/gemini-3.5-flash")  # not in catalog.json: free, benchmark code 70.1
        self.assertNotIn("pi:kaggle/qwen3-coder-30b", chain)       # no benchmark, no code score: not a candidate

    def test_advise_names_newer_release_and_command(self):
        self.env["FAKE_PI_EXTRA"] = "google      gemini-3.8-flash              1M       65K      yes       yes"
        self.sysop("models", "set", "fast", "pi:google/gemini-3.5-flash@high", "claude:haiku")
        self.sysop_json("roster", "--refresh")
        out = self.sysop("models", "advise", "fast").stdout
        self.assertIn("newer: pi:google/gemini-3.5-flash@high -> pi:google/gemini-3.8-flash", out)
        self.assertIn("apply: sysop.py models set fast pi:google/gemini-3.8-flash@high claude:haiku", out)
        self.assertEqual(self.sysop_json("settings", "--key", "tiers")["fast"][0],
                         "pi:google/gemini-3.5-flash@high")  # advice writes nothing

    def test_follow_latest_applies_same_line_upgrades_under_the_grant(self):
        self.sysop("models", "set", "fast", "pi:google/gemini-3.5-flash@high", "claude:haiku", "--scope", "repo")
        self.sysop("config", "set", "follow_latest", "true")
        self.sysop_json("roster", "--refresh")  # first refresh: 3.8 not runnable yet
        self.env["FAKE_PI_EXTRA"] = "google      gemini-3.8-flash              1M       65K      yes       yes"
        self.sysop_json("roster", "--refresh")
        repo_tiers = json.loads((self.repo / ".claude" / "sysop.json").read_text())["tiers"]
        self.assertEqual(repo_tiers["fast"], ["pi:google/gemini-3.8-flash@high", "claude:haiku"])  # the file that defines it
        data = self.intel_file()
        self.assertIn("follow_latest", data["notice"])
        self.assertTrue(data["applied"])

    def test_follow_latest_keeps_the_effort_variant(self):
        import advice, workers  # noqa: PLC0415
        pool = ["agy:gemini-3.8-flash-low", "agy:gemini-3.8-flash-high", "agy:gemini-3.8-flash-medium"]
        self.assertEqual(advice.newest_in_line("agy:gemini-3.7-flash-high", pool, {}), "agy:gemini-3.8-flash-high")

    def test_no_grant_no_change(self):
        self.sysop("models", "set", "fast", "pi:google/gemini-3.5-flash", "claude:haiku")
        self.sysop("config", "set", "intel", "true")
        self.sysop_json("roster", "--refresh")
        self.env["FAKE_PI_EXTRA"] = "google      gemini-3.8-flash              1M       65K      yes       yes"
        self.sysop_json("roster", "--refresh")
        self.assertEqual(self.sysop_json("settings", "--key", "tiers")["fast"][0], "pi:google/gemini-3.5-flash")

    def test_notice_on_news_only_and_cleared_by_advise(self):
        self.sysop("models", "set", "fast", "pi:google/gemini-3.5-flash", "claude:haiku")
        self.sysop("config", "set", "intel", "true")
        self.sysop_json("roster", "--refresh")
        self.assertFalse(self.intel_file().get("notice"))  # first look learns, says nothing
        self.env["FAKE_PI_EXTRA"] = "google      gemini-3.8-flash              1M       65K      yes       yes"
        self.sysop_json("roster", "--refresh")
        self.assertIn("models advise", self.intel_file()["notice"])
        hook = self.run_hook("context.py", {"cwd": str(self.repo)})
        self.assertIn("new model", hook)
        self.sysop("models", "advise")
        self.assertFalse(self.intel_file().get("notice"))
        self.assertNotIn("new model", self.run_hook("context.py", {"cwd": str(self.repo)}))

    def test_unbenchmarked_release_carries_its_predecessors_scores(self):
        aa = {"m-2-flash-high": {"coding": 66.0}, "m-1-flash": {"coding": 50.0}}
        facts = intel.facts_for("pi:google/m-2.5-flash", {}, aa)
        self.assertEqual((facts["coding"], facts["benchmarks_of"]), (66.0, "m-2-flash"))
        self.assertIsNone(intel.aa_predecessor("m-1-flash", aa))  # nothing older to borrow from
        self.assertIsNone(intel.aa_predecessor("m-3.1-flash", aa))  # never across a major version
        self.assertEqual(intel.aa_match("g-3.1-pro", {"g-3.1-pro-preview": {"coding": 70}})["coding"], 70)
        both = {"g-3.1-pro": {"coding": 60}, "g-3.1-pro-preview": {"coding": 70}}
        self.assertEqual(intel.aa_match("g-3.1-pro", both)["coding"], 60)  # the exact model's own score
        self.assertIn("not benchmarked yet", intel.infer_entry(facts)["source"])

    def test_effort_dropped_for_models_without_reasoning(self):
        self.serve_up("qwen3-coder-30b")
        self.sysop("models", "set", "fast", "pi:kaggle/qwen3-coder-30b@high")
        self.sysop_json("roster", "--refresh")
        pick = self.sysop_json("route", "small", "--prompt-file", "/tmp/b.md")
        self.assertIsNone(pick["effort"])
        self.assertIn("no reasoning", pick["effort_dropped"])
        self.assertNotIn("--thinking", pick["cmd"])

    def test_probe_latency_is_recorded(self):
        self.sysop("models", "set", "fast", "pi:kaggle/qwen3-coder-30b")
        self.sysop("models", "check", "fast")
        lat = self.intel_file()["latency"]["pi:kaggle/qwen3-coder-30b"]
        self.assertEqual(lat["n"], 1)
        self.assertLess(lat["ema"], 30)
        self.assertLess(time.time() - self.intel_file().get("checked_at", time.time()), 3600)


class RepoAwareAdviceTest(TempRepoCase):
    def setUp(self):
        super().setUp()
        self.use_fakebin(only=("agy", "claude", "pi"))
        self.env["SYSOP_INTEL_FIXTURES"] = str(FIXTURES)
        self.sysop_json("roster", "--refresh")

    def commit_files(self, files: dict):
        from helpers import git
        for name, text in files.items():
            (self.repo / name).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / name).write_text(text)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "files")

    def advice(self, *args):
        return self.sysop_json("models", "advise", "--json", *args)

    def test_prose_repo_ranks_by_writing(self):
        self.commit_files({f"book/{i}.md": "The rain kept on. " * 3000 for i in range(4)})
        out = self.advice()
        self.assertEqual(out["profile"]["kind"], "prose")
        self.assertEqual(out["tiers"]["fast"]["skill"], "writing")
        self.assertEqual(out["tiers"]["review"]["skill"], "review")
        self.assertIn("ranked by writing: prose repo", self.sysop("models", "advise").stdout)

    def test_large_repo_flags_small_context_in_big_tiers(self):
        self.commit_files({f"src/m{i}.py": "x = 1\n" * 30000 for i in range(4)})  # about 180K tokens
        self.sysop("models", "set", "standard", "pi:kaggle/qwen3-coder-30b", "claude:sonnet")
        chain = self.advice("standard")["tiers"]["standard"]["chain"]
        self.assertIn("context 64K < repo", chain[0]["context"])
        self.assertIsNone(chain[1]["context"])  # 1M context

    def test_local_outcomes_outweigh_benchmarks(self):
        self.sysop("models", "set", "fast", "claude:haiku", "pi:google/gemini-3.5-flash")
        (self.repo / ".sysop").mkdir(exist_ok=True)
        rows = [{"ticket": f"t{i}", "worker": "pi", "model": "google/gemini-3.5-flash",
                 "landed": i < 2, "check": "pass"} for i in range(6)]
        (self.repo / ".sysop" / "ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        chain = {c["entry"]: c for c in self.advice("fast")["tiers"]["fast"]["chain"]}
        flash = chain["pi:google/gemini-3.5-flash"]
        self.assertTrue(flash["weak_here"])
        self.assertEqual((flash["here"]["landed"], flash["here"]["n"]), (2, 6))
        self.assertIn("(weak here)", self.sysop("models", "advise", "fast").stdout)

    def test_outside_git_falls_back_quietly(self):
        out = self.sysop_json("models", "advise", "--json", cwd=self.tmp)
        self.assertIsNone(out["profile"])
        self.assertEqual(out["tiers"]["fast"]["skill"], "code")
