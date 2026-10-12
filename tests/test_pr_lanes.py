"""PR lanes: a reviewed ticket lands through a pull request, and what people say on it goes back to its builder."""
import json
import subprocess

from helpers import git
from test_next import FleetCase


class PrLaneTest(FleetCase):
    def setUp(self):
        super().setUp()
        self.use_fakebin(only=("agy", "claude", "gh"))
        self.env["FAKE_GH_DIR"] = str(self.tmp / "fake-gh")
        self.env["SYSOP_CI_GRACE"] = "0"
        origin = self.tmp / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
        git(self.repo, "remote", "add", "origin", str(origin))
        git(self.repo, "push", "-q", "-u", "origin", "main")
        self.sysop("lane", "add", "pl", "--preset", "claude-only", "--pr", "--land")
        self.sysop("--lane", "pl", "ticket", "add", "t1", "--title", "one", "--size", "small", "--kind", "chore",
                   "--base", "main", "--authority", "none")
        self.wt = self.repo / ".sysop" / "wt" / "t1"
        self.through_review(self.actions("launch")[0])
        self.body = self.tmp / "pr.md"
        self.body.write_text("## Problem\nx\n## Solution\ny\n## Validation\n- tests\n")

    def db(self, **add):
        path = self.tmp / "fake-gh" / "db.json"
        data = json.loads(path.read_text())
        for key, rows in add.items():
            data.setdefault(key, []).extend(rows)
        path.write_text(json.dumps(data))

    def open_pr(self):
        pr = self.sysop_json("pr", "open", "--title", "One", "--body-file", str(self.body), cwd=self.wt)
        self.sysop("ticket", "set", "t1", "--pr", str(pr["number"]))
        return pr

    def test_reviewed_opens_a_pr_then_merges_it(self):
        self.assertEqual([a["do"] for a in self.actions() if a["do"] in ("pr", "land", "pr-merge")], ["pr"])
        pr = self.open_pr()
        self.sysop_json("pr", "comment", str(pr["number"]), "--report", ".sysop/reports/sysop-t1.review.md",
                        "--slug", "sysop-t1")  # sysop's own comment is never feedback
        merge = self.actions("pr-merge")
        self.assertEqual([(a["ticket"], a["number"]) for a in merge], [("t1", pr["number"])])
        self.assertTrue(self.sysop_json("pr", "merge", str(pr["number"]), "--branch", "sysop/t1", "--land")["merged"])
        self.assertEqual(json.loads((self.store / "tickets.json").read_text())["tickets"]["t1"]["status"], "landed")

    def test_review_feedback_goes_back_to_the_builder_once_quoted(self):
        pr = self.open_pr()
        self.db(reviews=[{"pr": pr["number"], "id": 5, "state": "CHANGES_REQUESTED", "body": "Handle zero.",
                          "user": {"login": "op"}, "html_url": "https://github.com/o/r/pull/1#review-5"}],
                comments=[{"pr": pr["number"], "id": 2000, "body": "Also rename x.",
                           "html_url": "https://github.com/o/r/pull/1#issuecomment-2000"}])
        fix = self.actions("correct")
        self.assertEqual(len(fix), 1)
        for text in ("> Handle zero.", "requested changes", "#review-5", "> Also rename x.", "#issuecomment-2000"):
            self.assertIn(text, fix[0]["why"])
        self.assertEqual(self.actions("pr-merge"), [])  # never merged over feedback
        self.assertEqual(len(self.actions("correct")), 1)  # still due until relaunched, never twice-seen
        t = json.loads((self.store / "tickets.json").read_text())["tickets"]["t1"]
        self.assertEqual((t["needs"], t["pr"]["seen"]), ("pr-feedback", ["c2000", "r5"]))
        # the correction: relaunched, committed, re-reviewed; the PR now lags the sealed head
        self.sysop("ticket", "set", "t1", "--status", "running")
        self.commit("t1-fix.txt", "fixed\n", "address review", cwd=self.wt)
        self.built("t1")
        self.review("t1")
        update = self.actions("pr")
        self.assertEqual([(a["ticket"], a.get("number")) for a in update], [("t1", pr["number"])])
        self.open_pr()  # pushes to the same PR
        self.assertEqual(len(self.actions("pr-merge")), 1)

    def test_merge_refuses_feedback_the_ticket_has_not_taken_in(self):
        pr = self.open_pr()
        self.db(comments=[{"pr": pr["number"], "id": 2001, "body": "Wait, one more thing.", "html_url": "u"}])
        out = self.sysop("pr", "merge", str(pr["number"]), "--branch", "sysop/t1", "--land")
        self.assertEqual(out.returncode, 1)
        self.assertIn("has 1 review item(s) the ticket has not taken in", out.stderr)

    def test_feedback_command_lists_human_items_only(self):
        pr = self.open_pr()
        self.sysop_json("pr", "comment", str(pr["number"]), "--report", ".sysop/reports/sysop-t1.review.md",
                        "--slug", "sysop-t1")
        self.db(reviews=[{"pr": pr["number"], "id": 6, "state": "APPROVED", "body": "", "html_url": "a"}],
                line_comments=[{"pr": pr["number"], "id": 7, "path": "t1.txt", "line": 1, "body": "typo",
                                "html_url": "l"}])
        items = self.sysop_json("pr", "feedback", str(pr["number"]))["items"]
        self.assertEqual([(i["id"], i["kind"]) for i in items], [("l7", "line")])
