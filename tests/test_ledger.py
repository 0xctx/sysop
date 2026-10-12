import json

from helpers import TempRepoCase


class LedgerTest(TempRepoCase):
    def add(self, ticket, worker="agy", model="gemini-3.8-flash-high", **kw):
        args = ["ledger", "add", "--ticket", ticket, "--worker", worker, "--model", model,
                "--size", "small", "--check", kw.get("check", "pass"),
                "--landed", kw.get("landed", "yes"), "--seconds", str(kw.get("seconds", 10)),
                "--fallbacks", str(kw.get("fallbacks", 0)), "--review", "READY"]
        return self.sysop_json(*args)

    def summary(self):
        return self.sysop_json("ledger", "summary", "--json")

    def test_add_appends_one_row_each(self):
        row = self.add("t1")
        self.assertEqual((row["family"], row["landed"]), ("google", True))
        self.add("t2", worker="claude", model="haiku", fallbacks=1, landed="no", check="fail")
        lines = (self.repo / ".sysop" / "ledger.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[1])["fallbacks"], 1)

    def test_summary_groups_and_threshold(self):
        for i in range(4):
            self.add(f"a{i}", seconds=10 + i)
        self.add("c1", worker="claude", model="haiku", landed="no", check="fail", fallbacks=1)
        s = self.summary()
        g = s["groups"]["agy:gemini-3.8-flash-high"]
        self.assertEqual((s["total"], g["n"], g["landed"], g["check_pass"]), (5, 4, 4, 4))
        self.assertEqual(g["mean_seconds"], 11.5)
        self.assertFalse(g["enough_data"])
        self.add("a4")
        self.assertTrue(self.summary()["groups"]["agy:gemini-3.8-flash-high"]["enough_data"])
        c = self.summary()["groups"]["claude:haiku"]
        self.assertEqual((c["landed"], c["fallbacks"]), (0, 1))

    def test_malformed_lines_counted_and_skipped(self):
        self.add("t1")
        with (self.repo / ".sysop" / "ledger.jsonl").open("a") as fh:
            fh.write("{broken\n")
        s = self.summary()
        self.assertEqual((s["total"], s["malformed"]), (1, 1))

    def test_summary_outside_git_is_soft(self):
        proc = self.sysop("ledger", "summary", "--json", cwd=self.home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["total"], 0)

    def test_text_summary_and_empty_ledger(self):
        proc = self.sysop("ledger", "summary")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("0 ticket(s)", proc.stdout)
        self.add("t1")
        self.assertIn("fewer than 5", self.sysop("ledger", "summary").stdout)


class LedgerFactsTest(TempRepoCase):
    def launch(self, worker, code):
        self.sysop("launch", "t1", str(self.repo), f"exit {code}", "--worker", worker)
        self.sysop("wait", "t1", "--timeout", "10", "--poll", "0.2")

    def test_derives_worker_and_fallbacks_from_launch_records(self):
        self.launch("flaky:m1", 1)
        self.launch("agy:gemini-3.8-flash-high", 1)
        self.launch("claude:haiku", 0)
        row = self.sysop_json("ledger", "add", "--ticket", "t1", "--landed", "yes")
        self.assertEqual((row["worker"], row["model"], row["fallbacks"]), ("claude", "haiku", 2))

    def test_refuses_a_claim_that_contradicts_the_records(self):
        self.launch("flaky:m1", 1)
        self.launch("claude:haiku", 0)
        proc = self.sysop("ledger", "add", "--ticket", "t1", "--worker", "flaky", "--model", "m1",
                          "--fallbacks", "0")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("contradicts the launch records", proc.stderr)
        self.assertFalse((self.repo / ".sysop" / "ledger.jsonl").exists())
