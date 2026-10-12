"""Lanes: named queues on their own preset, cap and authority, across repos, under one coordinator."""
import json

from helpers import git
from test_next import FleetCase


class LaneTest(FleetCase):
    def setUp(self):
        super().setUp()
        self.use_fakebin()
        self.sysop("lane", "add", "bg", "--preset", "claude-only", "--max-parallel", "1")
        self.sysop("lane", "add", "auto", "--preset", "haiku-workhorse", "--land")

    def add_in(self, lane, tid, depends="", cwd=None):
        args = ["--lane", lane, "ticket", "add", tid, "--title", f"ticket {tid}", "--size", "small",
                "--kind", "chore", "--base", "main", "--authority", "none"]
        if depends:
            args += ["--depends", depends]
        return self.sysop(*args, cwd=cwd)

    def other_repo(self):
        other = self.tmp / "api"
        other.mkdir()
        git(other, "init", "-q", "-b", "main")
        (other / "README.md").write_text("api\n")
        git(other, "add", "README.md")
        git(other, "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-qm", "init")
        self.sysop("config", "set", "cross_review", "false", cwd=other)
        self.sysop("config", "set", "check", "exit 0", cwd=other)
        return other

    def test_a_lane_routes_on_its_own_preset(self):
        self.assertEqual(self.sysop_json("--lane", "bg", "route", "review", "--role", "review")["chain"],
                         ["claude:sonnet"])  # claude-only's review tier, not the repo's
        self.assertEqual(self.sysop("--lane", "nope", "route", "small").returncode, 1)
        self.assertIn("bg", self.sysop("lane", "list").stdout)
        self.add_in("bg", "b1")  # a command naming a lane's ticket acts in that lane without --lane
        self.assertEqual(self.sysop_json("route", "review", "--role", "review", "--ticket", "b1")["chain"],
                         ["claude:sonnet"])
        self.assertNotEqual(self.sysop_json("route", "review", "--role", "review")["chain"], ["claude:sonnet"])

    def test_each_lane_has_its_own_cap(self):
        for tid in ("a1", "a2"):
            self.add_in("bg", tid)
        self.add("m1")
        launches = self.actions("launch")
        self.assertEqual(sorted(a["ticket"] for a in launches), ["a1", "m1"])  # bg runs one at a time
        self.assertEqual({a["ticket"]: a.get("lane") for a in launches}, {"a1": "bg", "m1": None})

    def test_a_lane_with_a_standing_grant_lands_and_others_wait(self):
        self.add_in("auto", "x1")
        self.add_in("bg", "y1")
        for act in self.actions("launch"):
            self.through_review(act)
        lands = self.actions("land")
        self.assertEqual([a["ticket"] for a in lands], ["x1"])
        self.assertEqual(lands[0]["lane"], "auto")
        board = self.sysop_json("status", "--json")
        self.assertIn("bg/y1", board["ready"])
        self.assertTrue(any(n.startswith("bg/y1: ready") for n in board["needs_you"]))

    def test_a_ticket_waits_for_another_repos_ticket_to_land(self):
        api = self.other_repo()
        self.assertEqual(self.add_in("auto", "client", depends="api:server").returncode, 1)  # unknown: refused
        self.add_in("auto", "server", cwd=api)
        self.assertEqual(self.add_in("auto", "client", depends="api:server").returncode, 0)
        self.assertEqual(self.actions("launch"), [])  # api:server has not landed
        data = json.loads((api / ".sysop" / "tickets.json").read_text())
        data["tickets"]["server"]["status"] = "landed"
        (api / ".sysop" / "tickets.json").write_text(json.dumps(data))
        self.assertEqual([a["ticket"] for a in self.actions("launch")], ["client"])
        self.add_in("auto", "late", depends="api:server")
        data["tickets"]["server"]["status"] = "queued"
        (api / ".sysop" / "tickets.json").write_text(json.dumps(data))
        import shutil
        shutil.rmtree(api / ".sysop")  # the other repo's records are gone: blocked with the reason, not silent
        blocked = {a["ticket"]: a["why"] for a in self.actions("blocked")}
        self.assertEqual(sorted(blocked), ["client", "late"])
        self.assertTrue(all("cannot be found" in why for why in blocked.values()))

    def test_next_all_spans_repos_under_one_cap_with_one_wait(self):
        api = self.other_repo()
        self.sysop("config", "set", "max_parallel", "5")
        self.sysop("config", "set", "max_parallel", "5", cwd=api)
        for tid in ("r1", "r2"):
            self.add(tid)
        self.add_in("auto", "s1", cwd=api)
        (self.home / ".claude" / "sysop.json").write_text(json.dumps(
            {**json.loads((self.home / ".claude" / "sysop.json").read_text()), "max_parallel_all": 2}))
        acts = self.sysop_json("next", "--all")["actions"]
        launches = [a for a in acts if a["do"] == "launch"]
        self.assertEqual(len(launches), 2)  # three are due; the machine-wide cap allows two
        self.assertTrue(all(a["repo"] for a in launches))
        for act in launches:
            if act["repo"] == str(self.repo):
                self.do_launch(act, cmd=f"sleep 2 && echo {act['ticket']} > f && git add -A && "
                                        "git -c user.name=w -c user.email=w@x commit -qm b")
        acts = self.sysop_json("next", "--all")["actions"]
        waits = [a for a in acts if a["do"] == "wait"]
        self.assertEqual(len(waits), 1)
        self.assertTrue(waits[0]["any"])
        self.sysop_json("wait", *waits[0]["tickets"], "--any", "--timeout", "20", "--poll", "0.1")

    def test_status_all_names_repo_and_lane(self):
        api = self.other_repo()
        self.add_in("bg", "s1", cwd=api)
        self.add("m1")
        board = self.sysop_json("status", "--all", "--json")
        working = " ".join(board["working"])
        self.assertIn("api:bg/s1", working)
        self.assertIn("repo:m1", working)

    def test_land_finds_the_repo_that_holds_the_ticket(self):
        api = self.other_repo()
        self.add_in("bg", "x1", cwd=api)
        git(api, "checkout", "-qb", "sysop/x1")
        (api / "x.txt").write_text("x\n")
        git(api, "add", "x.txt")
        git(api, "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-qm", "x")
        git(api, "checkout", "-q", "main")
        here = self.sysop("land", "sysop/x1")  # from this repo, which does not hold x1
        named = self.sysop("land", "api:sysop/x1")
        for out in (here, named):  # unreviewed: the other repo's land gate refuses, naming its report
            self.assertEqual(out.returncode, 1)
            self.assertIn(f"{api}/.sysop/reports/sysop-x1.review.md", out.stderr)
        wrong = self.sysop("land", "nope:sysop/x1")
        self.assertEqual(wrong.returncode, 1)
        self.assertIn("status --all", wrong.stderr)

    def test_issues_feed_a_lane_once_verbatim_and_landing_closes_them(self):
        self.use_fakebin(only=("agy", "claude", "gh"))
        self.env["FAKE_GH_DIR"] = str(self.tmp / "fake-gh")
        body = "Please add `mean`.\n\n```py\nmean([1, 2]) == 1.5\n```\n"
        self.env["FAKE_GH_ISSUES"] = json.dumps([
            {"number": 7, "title": "Add mean", "body": body, "labels": ["sysop:bg"]},
            {"number": 8, "title": "Not ours", "body": "x", "labels": ["bug"]}])
        self.assertEqual(self.sysop_json("lane", "intake", "bg")["added"], ["issue-7"])
        self.assertEqual(self.sysop_json("lane", "intake", "bg")["added"], [])  # once per issue
        t = json.loads((self.repo / ".sysop" / "tickets.json").read_text())["tickets"]["issue-7"]
        self.assertEqual((t["request"], t["lane"], t["issue"]["number"]), (body, "bg", 7))
        self.assertIn("issues/7", t["issue"]["url"])
        self.through_review(self.actions("launch")[0])
        origin = self.tmp / "origin.git"
        git(self.tmp, "init", "-q", "--bare", str(origin))
        git(self.repo, "remote", "add", "origin", str(origin))
        landed = self.sysop_json("land", "sysop/issue-7", "--land")  # an issue ticket pushes by itself
        self.assertEqual(landed["issue"], "closed")
        issue = json.loads((self.tmp / "fake-gh" / "db.json").read_text())["issues"][0]
        self.assertEqual(issue["state"], "CLOSED")
        self.assertIn("Landed by sysop as", issue["closing_comment"])

    def test_intake_settings_are_remembered_for_the_mod(self):
        self.use_fakebin(only=("agy", "claude", "gh"))
        self.env["FAKE_GH_DIR"] = str(self.tmp / "fake-gh")
        self.sysop_json("lane", "intake", "bg", "--label", "todo", "--every", "15")
        self.assertEqual(self.sysop_json("settings", "--key", "lanes")["bg"]["intake"],
                         {"label": "todo", "every_minutes": 15})
        self.assertEqual(self.sysop("lane", "intake", "bg", "--every", "-5").returncode, 1)

    def test_an_unpushed_landing_leaves_the_issue_open(self):
        self.use_fakebin(only=("agy", "claude", "gh"))
        self.env["FAKE_GH_DIR"] = str(self.tmp / "fake-gh")
        self.env["FAKE_GH_ISSUES"] = json.dumps([{"number": 9, "title": "x", "body": "y", "labels": ["sysop:bg"]}])
        self.sysop_json("lane", "intake", "bg")
        self.through_review(self.actions("launch")[0])
        landed = self.sysop_json("land", "sysop/issue-9", "--land")  # no origin to push to
        self.assertTrue(landed["issue"].startswith("open: landed locally, not pushed (no origin"))
        self.assertNotIn("issue close", (self.tmp / "fake-gh" / "calls.log").read_text())


    def test_a_ticket_waiting_on_another_repo_says_so(self):
        api = self.other_repo()
        self.add_in("bg", "s1", cwd=api)
        self.add_in("bg", "c1", depends="api:s1")
        self.assertEqual(self.sysop_json("next")["next"], "c1 waits for api:s1 to land")
