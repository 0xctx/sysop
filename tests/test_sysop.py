import json
import subprocess
import sys

from helpers import ROOT, TempRepoCase


class StoreTest(TempRepoCase):
    def test_store_is_created_at_main_root_and_ignores_itself(self):
        proc = self.sysop("store")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        store = self.repo / ".sysop"
        self.assertEqual(proc.stdout.strip(), str(store))
        self.assertEqual((store / ".gitignore").read_text(), "*\n")
        (store / "plan.md").write_text("x")
        status = self.sysop("store")  # idempotent
        self.assertEqual(status.returncode, 0)

    def test_store_from_worktree_resolves_to_main_checkout(self):
        wt = self.worktree()
        proc = self.sysop("store", cwd=wt)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), str(self.repo / ".sysop"))
        self.assertFalse((wt / ".sysop").exists())

    def test_store_outside_git_fails_cleanly(self):
        proc = self.sysop("store", cwd=self.home)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("sysop:", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_soft_store_outside_git_exits_zero(self):
        proc = self.sysop("store", "--soft", cwd=self.home)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("no git repository", proc.stdout)
        self.assertEqual(self.sysop("store", "--soft").stdout.strip(), str(self.repo / ".sysop"))


class CliTest(TempRepoCase):
    def test_cwd_is_accepted_after_the_subcommand(self):
        before = self.sysop("--cwd", str(self.repo), "settings", "--key", "base", cwd=self.tmp)
        after = self.sysop("settings", "--key", "base", "--cwd", str(self.repo), cwd=self.tmp)
        joined = self.sysop("settings", f"--cwd={self.repo}", "--key", "base", cwd=self.tmp)
        self.assertEqual(before.returncode, 0, before.stderr)
        self.assertEqual((after.returncode, after.stdout), (0, before.stdout), after.stderr)
        self.assertEqual((joined.returncode, joined.stdout), (0, before.stdout), joined.stderr)

    def test_help_lists_the_everyday_commands_and_help_all_every_one(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import sysop  # noqa: PLC0415
        parser = sysop.build_parser()
        names = next(a for a in parser._actions if a.dest == "command").choices
        self.assertGreater(len(names), 20)
        short = self.sysop("--help").stdout
        usage = short.split("\n\n")[0]
        self.assertIn("{" + ",".join(sysop.EVERYDAY) + "}", usage.replace("\n", "").replace(" ", ""))
        for name in ("launch", "xreview", "ledger", "next"):
            self.assertNotIn(f"    {name} ", short)  # plumbing for skills: not offered by default
        self.assertIn("--help-all", short)
        full = self.sysop("--help-all")
        self.assertEqual(full.returncode, 0)
        for name in names:
            if name in sysop.HIDDEN:
                self.assertNotIn(f"    {name} ", full.stdout)  # developer-only or an alias: never offered
            else:
                self.assertIn(name, full.stdout)
        self.assertEqual(self.sysop("launch", "--help").returncode, 0)  # plumbing stays callable

    def test_store_module_stays_light(self):
        """Hooks import only `store`: it must not pull in workers, intel or Kaggle."""
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import store; "
                "print(sorted(m for m in ('workers', 'land', 'fleet', 'advice', 'intel', 'kaggle_jobs', 'kaggle_serve', "
                "'repo_profile', 'urllib.request') if m in sys.modules))")
        proc = subprocess.run([sys.executable, "-c", code, str(ROOT / "scripts")], capture_output=True, text=True)
        self.assertEqual(proc.stdout.strip(), "[]", proc.stderr)


class CoreRoutingTest(TempRepoCase):
    def test_named_tiers_route_with_the_intel_layer_unimportable(self):
        """Core routing (tiers, cooldowns, recorded fallback, cross-vendor) never needs the opt-in layer."""
        code = """
import json, sys
sys.path.insert(0, sys.argv[1])
sys.modules["advice"] = sys.modules["intel"] = None  # importing either now raises ImportError
import store, workers
settings = store.load_settings(sys.argv[2])  # doors on (the default): a door is tried, then skipped
roster = {"workers": {"claude": {"available": True, "models": ["haiku", "sonnet", "opus"]},
                      "agy": {"available": True, "models": ["gemini-3.8-flash-high"]},
                      "pi": {"available": False, "models": None}}}
picks = [workers.route(settings, roster, "large"),
         workers.route(settings, roster, "review", role="review", not_family="claude"),
         workers.route(settings, roster, "small", exclude=["agy"])]
print(json.dumps([p["entry"] for p in picks]))
try:
    workers.route(settings, roster, "writing", kind="writing")
except ImportError:
    print("auto routes need the layer")
"""
        proc = subprocess.run([sys.executable, "-c", code, str(ROOT / "scripts"), str(self.repo)],
                              capture_output=True, text=True, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        first, second = proc.stdout.splitlines()
        self.assertEqual(json.loads(first), ["claude:sonnet", "agy:gemini-3.8-flash-high", "claude:haiku"])
        self.assertEqual(second, "auto routes need the layer")


class SettingsTest(TempRepoCase):
    def settings(self, **kw):
        proc = self.sysop("settings", **kw)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout), proc.stderr

    def write_user(self, data):
        (self.home / ".claude").mkdir(exist_ok=True)
        (self.home / ".claude" / "sysop.json").write_text(json.dumps(data))

    def write_repo(self, data):
        (self.repo / ".claude").mkdir(exist_ok=True)
        (self.repo / ".claude" / "sysop.json").write_text(json.dumps(data))

    def test_defaults(self):
        data, _ = self.settings()
        self.assertEqual(data["routes"]["small"], "fast")
        preset = json.loads((ROOT / "presets" / "claude-plan.json").read_text())
        self.assertEqual(data["preset"], "claude-plan")
        for key in ("tiers", "agents"):  # the defaults are the claude-plan preset
            self.assertEqual(data[key], preset["settings"][key])
        self.assertFalse(data["land"])
        self.assertTrue(data["cross_review"])

    def test_three_layers_repo_wins_dicts_merge_lists_replace(self):
        self.write_user({"land": True, "check": "make test", "tiers": {"fast": ["claude:haiku"]}})
        self.write_repo({"check": "npm test", "tiers": {"deep": ["claude:opus"]}})
        data, _ = self.settings()
        self.assertTrue(data["land"])                       # user layer
        self.assertEqual(data["check"], "npm test")          # repo beats user
        self.assertEqual(data["tiers"]["fast"], ["claude:haiku"])  # list replaced
        self.assertEqual(data["tiers"]["deep"], ["claude:opus"])
        self.assertIn("standard", data["tiers"])             # dict merged, not replaced

    def test_builtin_workers_cannot_be_overridden(self):
        self.write_repo({"workers": {"agy": {"cmd": "evil"}, "codex": {"cmd": "codex exec {prompt}"}}})
        data, err = self.settings()
        self.assertNotIn("agy", data["workers"])
        self.assertIn("codex", data["workers"])
        self.assertIn("built-in", err)

    def test_invalid_json_names_the_file(self):
        (self.repo / ".claude").mkdir()
        (self.repo / ".claude" / "sysop.json").write_text("{nope")
        proc = self.sysop("settings")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("sysop.json", proc.stderr)

    def test_key_selects_one_value(self):
        proc = self.sysop("settings", "--key", "routes")
        self.assertEqual(json.loads(proc.stdout)["risky"], "deep")
        self.assertEqual(self.sysop("settings", "--key", "nope").returncode, 1)


class ReportTest(TempRepoCase):
    def test_creates_from_template_and_keeps_existing(self):
        path = self.sysop("report", "build", "sysop-x").stdout.strip()
        self.assertTrue(path.endswith(".sysop/reports/sysop-x.build.md"))
        text = open(path).read()
        self.assertIn("# Build report: sysop-x", text)
        self.assertIn("## Simplifier", text)
        open(path, "a").write("filled in\n")
        self.assertEqual(self.sysop("report", "build", "sysop-x").stdout.strip(), path)
        self.assertIn("filled in", open(path).read())
        self.sysop("report", "build", "sysop-x", "--fresh")
        self.assertNotIn("filled in", open(path).read())

    def test_all_kinds_have_templates(self):
        for kind, where in (("plan", "plans/s.md"), ("review", "reports/s.review.md")):
            path = self.sysop("report", kind, "s").stdout.strip()
            self.assertTrue(path.endswith(where), path)
        self.assertEqual(self.sysop("report", "build", "../x").returncode, 1)
        self.assertTrue(self.sysop("report", "brief", "t1").stdout.strip().endswith(".sysop/briefs/t1.md"))


class CleanupAndLimitsTest(TempRepoCase):
    def test_cleanup_only_sysop_worktrees(self):
        self.sysop("store")
        wt = self.repo / ".sysop" / "wt" / "review-x"
        from helpers import git
        git(self.repo, "worktree", "add", "-q", "--detach", str(wt))
        (wt / "__pycache__").mkdir()
        (wt / "__pycache__" / "a.pyc").write_bytes(b"x")
        proc = self.sysop("cleanup", str(wt))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertFalse(wt.exists())
        other = self.worktree()
        refused = self.sysop("cleanup", str(other))
        self.assertEqual(refused.returncode, 1)
        self.assertIn("not a sysop worktree", refused.stderr)
        self.assertTrue(other.exists())

    def test_store_has_plans_and_reports(self):
        self.sysop("store")
        self.assertTrue((self.repo / ".sysop" / "reports").is_dir())
        self.assertTrue((self.repo / ".sysop" / "plans").is_dir())

    def test_oversized_prompt_refused(self):
        self.use_fakebin()  # a worker must route for the prompt to be measured (CI has no agent CLIs)
        big = self.tmp / "big.md"
        big.write_text("x" * 100_001)
        proc = self.sysop("route", "small", "--prompt-file", str(big))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("limit", proc.stderr)


class TicketTest(TempRepoCase):
    def test_add_set_log_render(self):
        req = self.tmp / "req.txt"
        req.write_text("add calc/stats.py with mean(xs)")
        con = self.tmp / "con.txt"
        con.write_text("Outcome: mean exists in calc/stats.py")
        path = self.sysop("ticket", "add", "t1", "--title", "stats mean", "--size", "small",
                          "--request-file", str(req), "--contract-file", str(con),
                          "--base", "main", "--authority", "--land given").stdout.strip()
        self.sysop("ticket", "add", "t2", "--title", "geometry", "--size", "large", "--depends", "t1")
        self.assertEqual(self.sysop("ticket", "set", "t1", "--status", "running",
                                    "--worker", "agy:gemini-3.8-flash-high").returncode, 0)
        self.sysop("ticket", "log", "t1", "fallback agy:gemini-3.8-flash-high -> claude:haiku: quota")
        md = open(path).read()
        self.assertIn("> add calc/stats.py with mean(xs)", md)
        self.assertIn("**Status:** running", md)
        self.assertIn("queued -> running", md)
        self.assertIn("fallback agy:gemini-3.8-flash-high -> claude:haiku", md)
        self.assertIn("**Depends:** t1", md)
        data = json.loads((self.repo / ".sysop" / "tickets.json").read_text())
        self.assertEqual(data["tickets"]["t1"]["worker"], "agy:gemini-3.8-flash-high")
        self.assertIn("stats mean", self.sysop("ticket", "show").stdout)

    def test_rejects_bad_status_and_unknown_ticket(self):
        self.sysop("ticket", "add", "t1", "--title", "x", "--size", "small")
        self.assertEqual(self.sysop("ticket", "set", "t1", "--status", "done").returncode, 1)
        self.assertEqual(self.sysop("ticket", "log", "nope", "m").returncode, 1)


class CooldownTest(TempRepoCase):
    def setUp(self):
        super().setUp()
        self.use_fakebin()
        self.pin_tiers()

    def test_cooling_entry_is_skipped_with_reason_then_expires(self):
        self.sysop("cooldown", "add", "agy", "--seconds", "3600", "--reason", "quota reached")
        pick = self.sysop_json("route", "small")
        self.assertEqual(pick["worker"], "claude")
        self.assertIn("cooling down until", pick["skipped"][1]["reason"])
        self.assertIn("quota reached", pick["skipped"][1]["reason"])
        self.sysop("cooldown", "clear", "agy")
        self.assertEqual(self.sysop_json("route", "small")["worker"], "agy")
        self.sysop("cooldown", "add", "agy:gemini-3.8-flash-high", "--seconds", "-1", "--reason", "old")
        self.assertEqual(self.sysop_json("route", "small")["worker"], "agy")  # expired

    def test_unknown_worker_rejected(self):
        self.assertEqual(self.sysop("cooldown", "add", "nope", "--seconds", "5", "--reason", "x").returncode, 1)


class ConfigTest(TempRepoCase):
    def test_set_get_and_types(self):
        self.assertEqual(self.sysop("config", "set", "check", "python3 -m unittest -q").returncode, 0)
        self.assertEqual(self.sysop_json("config", "get", "check"), "python3 -m unittest -q")
        self.assertEqual(self.sysop("config", "set", "land", "true").returncode, 0)
        self.assertIs(self.sysop_json("config", "get", "land"), True)
        self.assertEqual(self.sysop("config", "set", "land", "yes").returncode, 1)          # not a bool
        self.assertEqual(self.sysop("config", "set", "check_timeout", "true").returncode, 1)
        self.assertEqual(self.sysop("config", "set", "tiers", "{}").returncode, 1)          # models owns it
        data = json.loads((self.repo / ".claude" / "sysop.json").read_text())
        self.assertEqual(data, {"check": "python3 -m unittest -q", "land": True})
        self.sysop("config", "set", "cross_review", "false", "--scope", "user")
        self.assertFalse(json.loads((self.home / ".claude" / "sysop.json").read_text())["cross_review"])
