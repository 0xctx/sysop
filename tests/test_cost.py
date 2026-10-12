import json
from pathlib import Path

from helpers import ROOT, TempRepoCase


class CostLedgerTest(TempRepoCase):
    def setUp(self):
        super().setUp()
        self.use_fakebin()
        (self.repo / "brief.md").write_text("do it")

    def build(self, ticket):
        self.sysop_json("launch", ticket, str(self.repo), "--worker", "claude:haiku", "--prompt-file", "brief.md")
        [state] = self.sysop_json("wait", ticket, "--timeout", "20", "--poll", "0.2")
        return state

    def ledger(self, ticket):
        return self.sysop_json("ledger", "add", "--ticket", ticket, "--size", "small", "--landed", "yes")

    def test_a_stated_cost_reaches_the_ledger_and_tails_read_as_text(self):
        state = self.build("t1")
        self.assertNotIn("tail", state)  # a clean exit: no tail in the coordinator's context
        import sys  # noqa: PLC0415
        sys.path.insert(0, str(ROOT / "scripts"))
        import workers  # noqa: PLC0415
        self.assertIn("fake claude ran OK", workers.worker_output(Path(state["log"]).read_text()))  # decoded
        row = self.ledger("t1")
        self.assertEqual((row["usd"], row["tokens"], row["billed"]), (0.0123, 1500, "plan"))
        self.assertIn("$0.00", self.sysop("models").stdout)  # a plan's stated cost is not spend

    def test_paid_spend_counts_and_the_budget_steers_auto_routes(self):
        self.env["ANTHROPIC_API_KEY"] = "sk-test"
        self.env["FAKE_CLAUDE_COST"] = "4.5"
        self.build("t1")
        self.assertEqual(self.ledger("t1")["billed"], "paid")
        balanced = self.sysop_json("route", "large", "--kind", "writing")["chain"]
        self.sysop("config", "set", "budget", "5")
        out = self.sysop("models").stdout
        self.assertIn("$4.50 of $5.00 budget", out)
        self.assertIn("near the limit", out)
        steered = self.sysop_json("route", "large", "--kind", "writing")["chain"]
        self.assertNotEqual(balanced, steered)  # economy order: plan before per-token
        self.assertTrue(steered[0].startswith("agy:"), steered)

    def test_runs_that_state_no_cost_record_none(self):
        self.sysop_json("launch", "t2", str(self.repo), "echo plain text worker")
        self.sysop_json("wait", "t2", "--timeout", "20", "--poll", "0.2")
        row = self.sysop_json("ledger", "add", "--ticket", "t2", "--size", "small", "--landed", "yes",
                              "--worker", "agy", "--model", "gemini-3.8-flash-high")
        self.assertNotIn("usd", row)


class LearnTest(TempRepoCase):
    def setUp(self):
        super().setUp()
        self.use_fakebin()
        self.sysop("store")

    def rows(self, worker, model, landed, n=5, kind="feature"):
        with (self.repo / ".sysop" / "ledger.jsonl").open("a") as fh:
            for i in range(n):
                fh.write(json.dumps({"ticket": f"{model}-{i}", "worker": worker, "model": model, "kind": kind,
                                     "landed": i < landed, "review": "READY" if i < landed else "NEEDS FIXES",
                                     "fallbacks": 0}) + "\n")

    def test_outcomes_propose_one_step_changes_with_evidence(self):
        self.rows("claude", "haiku", landed=5)                 # code 3 -> 4
        self.rows("agy", "gemini-3.8-flash-high", landed=2)    # code 5 -> 4
        self.rows("claude", "sonnet", landed=4, n=4)           # too few to count
        out = self.sysop_json("models", "learn")
        changes = {(c["entry"], c["skill"]): (c["from"], c["to"]) for c in out["changes"]}
        self.assertEqual(changes[("claude:haiku", "code")], (3, 4))
        self.assertEqual(changes[("agy:gemini-3.8-flash-high", "code")][1],
                         changes[("agy:gemini-3.8-flash-high", "code")][0] - 1)
        self.assertNotIn(("claude:sonnet", "code"), changes)
        self.assertFalse(out["applied"])
        self.assertIn("5/5 landed", out["changes"][0]["evidence"] + out["changes"][1]["evidence"])

    def test_apply_writes_the_catalog_and_the_record(self):
        self.rows("claude", "haiku", landed=5, kind="writing")
        out = self.sysop_json("models", "learn", "--apply")
        self.assertTrue(out["applied"])
        data = json.loads((self.home / ".claude" / "sysop.json").read_text())
        self.assertEqual(data["catalog"]["claude:haiku"]["skills"]["writing"], 4)
        self.assertIn("learned", (self.repo / ".sysop" / "runlog.jsonl").read_text())
        self.assertEqual(self.sysop_json("models", "learn")["changes"][0]["from"], 4)  # one step per pass

    def test_standing_grant_applies_without_the_flag(self):
        self.rows("claude", "haiku", landed=5)
        self.sysop("config", "set", "learn", "true")
        self.assertTrue(self.sysop_json("models", "learn")["applied"])


class WorkerOutputTest(TempRepoCase):
    def test_lines_around_the_result_stay_visible(self):
        import sys
        from helpers import ROOT
        sys.path.insert(0, str(ROOT / "scripts"))
        import workers
        log = 'warming up\n{"type":"result","result":"done: 3 files","total_cost_usd":0.5}\nError: hook failed\n'
        out = workers.worker_output(log)
        self.assertEqual(out.splitlines(), ["warming up", "done: 3 files", "Error: hook failed"])
        self.assertEqual(workers.run_usage(log)["usd"], 0.5)
        self.assertEqual(workers.worker_output("plain text\n"), "plain text\n")
