import hashlib
import json
import subprocess

from helpers import TempRepoCase, git


class PullRequestTest(TempRepoCase):
    """The GitHub path against a fake gh whose `origin` is a real local bare repo."""

    def setUp(self):
        super().setUp()
        self.use_fakebin(only=("agy", "claude", "gh"))
        self.env["FAKE_GH_DIR"] = str(self.tmp / "fake-gh")
        self.env["SYSOP_CI_GRACE"] = "0"
        self.origin = self.tmp / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.origin)], check=True)
        git(self.repo, "remote", "add", "origin", str(self.origin))
        git(self.repo, "push", "-q", "-u", "origin", "main")
        self.sysop("store")
        self.wt = self.repo / ".sysop" / "wt" / "feat"
        git(self.repo, "worktree", "add", "-q", "-b", "feat", str(self.wt))
        self.commit("calc.py", "def add(a, b):\n    return a + b\n", "fix add", cwd=self.wt)
        self.body = self.repo / ".sysop" / "reports" / "feat.pr.md"  # the store, never the worktree
        self.body.parent.mkdir(parents=True, exist_ok=True)
        self.body.write_text("## Problem\nadd was wrong\n## Solution\nfixed\n## Validation\n- tests\n")

    def ready(self):
        reports = self.repo / ".sysop" / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        (reports / "feat.review.md").write_text("# Review\n\nVerdict: READY\n")
        md = reports / "feat.xreview.md"
        md.write_text("**Cross-family review:** agy:gemini-3.8-flash-high, exit 0\n")
        md.with_suffix(".json").write_text(json.dumps({"status": "done",
                                                       "sha256": hashlib.sha256(md.read_bytes()).hexdigest()}))
        self.assertEqual(self.sysop("review", "seal", "feat").returncode, 0)

    def open_pr(self) -> dict:
        return self.sysop_json("pr", "open", "--title", "Fix add", "--body-file", str(self.body), cwd=self.wt)

    def test_local_only_reasons(self):
        git(self.repo, "remote", "remove", "origin")
        self.assertEqual(self.sysop_json("pr", "open", cwd=self.wt)["local_only"], "no origin remote")
        git(self.repo, "remote", "add", "origin", str(self.origin))
        self.env["FAKE_GH_NOAUTH"] = "1"
        self.assertEqual(self.sysop_json("pr", "open", cwd=self.wt)["local_only"], "gh is not signed in")

    def test_open_pushes_creates_and_reuses(self):
        pr = self.open_pr()
        self.assertEqual((pr["number"], pr["base"], pr["reused"]), (1, "main", False))
        self.assertIn("refs/heads/feat", git(self.repo, "ls-remote", str(self.origin)))
        self.commit("calc.py", "def add(a, b):\n    return b + a\n", "tidy", cwd=self.wt)
        again = self.open_pr()
        self.assertEqual((again["number"], again["reused"]), (1, True))  # one PR; the new commit is pushed
        head = git(self.wt, "rev-parse", "HEAD")
        self.assertIn(head, git(self.repo, "ls-remote", str(self.origin), "refs/heads/feat"))

    def test_open_refuses_attribution_and_empty_branches(self):
        self.body.write_text("fixed\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n")
        self.assertIn("credits an AI", self.sysop("pr", "open", "--title", "x", "--body-file", str(self.body),
                                                  cwd=self.wt).stderr)
        git(self.repo, "branch", "empty", "main")
        wt2 = self.repo / ".sysop" / "wt" / "empty"
        git(self.repo, "worktree", "add", "-q", str(wt2), "empty")
        self.assertIn("no commits ahead", self.sysop("pr", "open", "--title", "x", "--body-file", "b.md",
                                                     cwd=wt2).stderr)

    def test_one_canonical_review_comment(self):
        self.open_pr()
        (self.repo / "r.md").write_text("Verdict: NEEDS FIXES\n")
        first = self.sysop_json("pr", "comment", "1", "--report", "r.md", "--slug", "feat")
        (self.repo / "r.md").write_text("Verdict: READY\n")
        second = self.sysop_json("pr", "comment", "1", "--report", "r.md", "--slug", "feat")
        self.assertEqual((first["action"], second["action"], first["id"]), ("created", "edited", second["id"]))
        db = json.loads((self.tmp / "fake-gh" / "db.json").read_text())
        self.assertEqual(len(db["comments"]), 1)
        self.assertTrue(db["comments"][0]["body"].startswith("<!-- sysop-review: feat -->\nVerdict: READY"))

    def test_checks_are_classified_and_bounded(self):
        self.open_pr()
        self.assertEqual(self.sysop_json("pr", "checks", "1")["state"], "none")
        self.env["FAKE_GH_CHECKS"] = json.dumps([{"name": "test", "bucket": "pass"}])
        self.assertEqual(self.sysop_json("pr", "checks", "1")["state"], "green")
        self.env["FAKE_GH_CHECKS"] = json.dumps([{"name": "test", "bucket": "fail",
                                                 "link": "https://github.com/o/r/actions/runs/77/job/1"}])
        red = self.sysop_json("pr", "checks", "1", code=1)
        self.assertEqual(red["failing"][0]["run"], "77")
        self.env["FAKE_GH_CHECKS"] = json.dumps([{"name": "test", "bucket": "pending"}])
        self.assertEqual(self.sysop_json("pr", "checks", "1", "--timeout", "0.3", "--poll", "0.1",
                                         code=124)["state"], "pending")

    def test_merge_is_gated(self):
        self.open_pr()
        self.assertIn("no review report", self.sysop("pr", "merge", "1", "--branch", "feat", "--land").stderr)
        self.ready()
        self.assertTrue(self.sysop_json("pr", "merge", "1", "--branch", "feat")["ready"])  # no authority: stop
        self.env["FAKE_GH_CHECKS"] = json.dumps([{"name": "test", "bucket": "fail"}])
        self.assertIn("checks are red", self.sysop("pr", "merge", "1", "--branch", "feat", "--land").stderr)
        self.env["FAKE_GH_CHECKS"] = json.dumps([{"name": "test", "bucket": "pending"}])
        self.assertIn("checks are pending", self.sysop("pr", "merge", "1", "--branch", "feat", "--land").stderr)
        self.env["FAKE_GH_CHECKS"] = "[]"
        self.env["FAKE_GH_MERGEABLE"] = "CONFLICTING"
        self.assertIn("conflicts", self.sysop("pr", "merge", "1", "--branch", "feat", "--land").stderr)
        self.env["FAKE_GH_MERGEABLE"] = "MERGEABLE"
        self.env["FAKE_GH_REVIEW"] = "REVIEW_REQUIRED"
        self.assertIn("needs a human review", self.sysop("pr", "merge", "1", "--branch", "feat", "--land").stderr)
        self.env["FAKE_GH_REVIEW"] = ""
        self.commit("calc.py", "def add(a, b):\n    return a + b  # unreviewed\n", "sneak", cwd=self.wt)
        self.assertIn("not the reviewed", self.sysop("pr", "merge", "1", "--branch", "feat", "--land").stderr)

    def test_merge_lands_and_tidies(self):
        self.open_pr()
        self.ready()
        self.env["FAKE_GH_CHECKS"] = json.dumps([{"name": "test", "bucket": "pass"}])
        out = self.sysop_json("pr", "merge", "1", "--branch", "feat", "--land")
        self.assertEqual((out["merged"], out["method"], out["checks"]), (True, "merge", "green"))
        self.assertTrue(out["worktree_removed"] and out["branch_deleted"])
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), git(self.repo, "rev-parse", "origin/main"))
        self.assertTrue((self.repo / "calc.py").is_file())  # the main checkout has the merged change
        self.assertNotIn("refs/heads/feat", git(self.repo, "ls-remote", str(self.origin)))
        self.assertTrue(self.sysop_json("pr", "merge", "1", "--branch", "feat", "--land")["already"])

    def test_merge_refuses_a_head_past_the_seal(self):
        self.open_pr()
        self.ready()
        self.commit("late.py", "x = 1\n", "after review", cwd=self.wt)
        self.open_pr()  # pushes the unreviewed commit to the same PR
        self.env["FAKE_GH_CHECKS"] = json.dumps([{"name": "test", "bucket": "pass"}])
        proc = self.sysop("pr", "merge", "1", "--branch", "feat", "--land")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("is not the reviewed commit", proc.stderr)

    def test_no_checks_yet_on_a_head_with_workflows_is_pending(self):
        self.env["SYSOP_CI_GRACE"] = "60"
        (self.wt / ".github" / "workflows").mkdir(parents=True)
        self.commit(".github/workflows/ci.yml", "on: pull_request\n", "ci", cwd=self.wt)
        self.open_pr()
        self.ready()
        self.assertEqual(self.sysop_json("pr", "checks", "1", "--timeout", "0.3", "--poll", "0.1",
                                         code=124)["state"], "pending")  # within the grace: not started yet
        self.assertIn("CI has not reported yet",
                      self.sysop("pr", "merge", "1", "--branch", "feat", "--land").stderr)

    def test_a_closed_pr_is_not_reused(self):
        self.open_pr()
        db_path = self.tmp / "fake-gh" / "db.json"
        db = json.loads(db_path.read_text())
        db["prs"][0]["state"] = "CLOSED"
        db_path.write_text(json.dumps(db))
        again = self.open_pr()
        self.assertEqual((again["number"], again["reused"]), (2, False))

    def test_a_merge_is_reported_even_when_main_cannot_fast_forward(self):
        self.open_pr()
        self.ready()
        git(self.repo, "checkout", "-q", "-b", "elsewhere")  # the operator is busy on another branch
        out = self.sysop_json("pr", "merge", "1", "--branch", "feat", "--land")
        self.assertTrue(out["merged"])
        self.assertIn("not on main", out["main_left"])
