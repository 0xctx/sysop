import json

from helpers import TempRepoCase, git


class RaceTest(TempRepoCase):
    def setUp(self):
        super().setUp()
        self.use_fakebin()
        (self.repo / "brief.md").write_text("add mean()")

    def start(self, n="2"):
        return self.sysop_json("race", "start", "t1", "--n", n, "--size", "small", "--prompt-file", "brief.md")

    def test_racers_are_different_vendors_in_their_own_worktrees(self):
        race = self.start()
        families = [r["family"] for r in race["racers"]]
        self.assertEqual(len(set(families)), 2, families)
        for r in race["racers"]:
            self.assertTrue((self.repo / ".sysop" / "wt" / f"t1-{r['id']}").is_dir())
        self.sysop_json("wait", "t1-r1", "t1-r2", "--timeout", "20", "--poll", "0.2")
        status = self.sysop_json("race", "status", "t1")
        self.assertEqual({r["state"] for r in status["racers"]}, {"exited"})
        self.assertIn("already exists", self.sysop("race", "start", "t1", "--prompt-file", "brief.md").stderr)

    def test_settle_keeps_the_winner_and_archives_losers_without_losing_work(self):
        self.start()
        self.sysop_json("wait", "t1-r1", "t1-r2", "--timeout", "20", "--poll", "0.2")
        r1, r2 = self.repo / ".sysop" / "wt" / "t1-r1", self.repo / ".sysop" / "wt" / "t1-r2"
        self.commit("stats.py", "def mean(xs):\n    return sum(xs) / len(xs)\n", "mean", cwd=r1)
        self.commit("stats.py", "def mean(xs): return 0\n", "loser commit", cwd=r2)
        (r2 / "notes.txt").write_text("uncommitted thoughts\n")  # must survive settle
        out = self.sysop_json("race", "settle", "t1", "--winner", "r1")
        self.assertEqual((out["winner"], out["branch"]), ("r1", "race/t1/r1"))
        self.assertTrue(r1.is_dir())
        self.assertFalse(r2.exists())
        self.assertNotIn("race/t1/r2", git(self.repo, "branch", "--list", "race/*"))
        archived = out["archived"][0]["ref"]
        self.assertEqual(archived, "refs/sysop/race/t1/r2")
        files = git(self.repo, "ls-tree", "-r", "--name-only", archived)
        self.assertIn("notes.txt", files)  # the uncommitted file is in the archive
        self.assertIn("loser commit", git(self.repo, "log", "--format=%s", archived))
        rows = [json.loads(line) for line in (self.repo / ".sysop" / "ledger.jsonl").read_text().splitlines()]
        self.assertEqual((rows[0]["ticket"], rows[0]["landed"]), ("t1-r2", False))
        self.assertIn("already settled", self.sysop("race", "settle", "t1", "--winner", "r2").stderr)

    def test_a_race_needs_two_vendors(self):
        self.use_fakebin(only=("claude",))
        (self.tmp / "bin" / "agy").unlink()
        self.sysop("roster", "--refresh")
        self.assertIn("a race needs two", self.sysop("race", "start", "t9", "--prompt-file", "brief.md").stderr)

    def test_settle_stops_a_running_loser_before_archiving(self):
        self.sysop("models", "set", "fast", "agy:gemini-3.8-flash-high", "claude:haiku")
        self.env["FAKE_AGY_OUTPUT"] = "slow"
        race = self.start()
        loser = next(r for r in race["racers"] if r["entry"].startswith("agy"))
        winner = next(r for r in race["racers"] if r["id"] != loser["id"])
        # make the loser a long-running worker: relaunch its run as a sleeper writing a file at the end
        self.sysop_json("wait", f"t1-{winner['id']}", "--timeout", "20", "--poll", "0.2")
        import subprocess
        import time
        run = loser["run"]
        proc = subprocess.Popen(["bash", "-c", f"sleep 30; echo late > {loser['worktree']}/late.txt"],
                                start_new_session=True)
        open(f"{run}/pid", "w").write(f"{proc.pid}\n")
        import os
        os.remove(f"{run}/pstart") if os.path.exists(f"{run}/pstart") else None
        os.remove(f"{run}/exit") if os.path.exists(f"{run}/exit") else None
        start = time.time()
        out = self.sysop_json("race", "settle", "t1", "--winner", winner["id"])
        self.assertLess(time.time() - start, 15)
        self.assertIsNotNone(proc.poll())  # the loser was stopped, not left writing
        self.assertEqual(out["archived"][0]["id"], loser["id"])
