"""The fleet engine: tickets as a graph, `next` as the state machine, halt, stacking, resume."""
import hashlib
import json
from pathlib import Path

from helpers import TempRepoCase, git


class FleetCase(TempRepoCase):
    """A repo with a store, a green check and no cross-family review, plus what the fleet skill does per action."""
    def setUp(self):
        super().setUp()
        self.sysop("store")
        self.store = self.repo / ".sysop"
        self.sysop("config", "set", "cross_review", "false")
        self.sysop("config", "set", "check", "exit 0")

    # --- helpers: what the fleet skill does for each action -------------------------------------------
    def add(self, tid, depends="", authority="none"):
        args = ["ticket", "add", tid, "--title", f"ticket {tid}", "--size", "small", "--kind", "chore",
                "--base", "main", "--authority", authority]
        if depends:
            args += ["--depends", depends]
        return self.sysop(*args)

    def next(self) -> dict:
        return self.sysop_json("next")

    def actions(self, kind=None) -> list[dict]:
        acts = self.next()["actions"]
        return [a for a in acts if kind is None or a["do"] == kind]

    def do_launch(self, act, cmd=None):
        wt = self.repo / act["worktree"] if not act["worktree"].startswith("/") else act["worktree"]
        git(self.repo, "worktree", "add", "-q", "-b", act["branch"], str(wt), act["base_ref"])
        cmd = cmd or (f"echo {act['ticket']} > {act['ticket']}.txt && git add -A && "
                      f"git -c user.name=w -c user.email=w@x commit -qm 'build {act['ticket']}'")
        self.sysop_json("launch", act["ticket"], str(wt), cmd)
        stack = ["--stacked-on", act["stacked_on"], "--fork", act["fork"]] if act["stacked_on"] else []
        self.sysop("ticket", "set", act["ticket"], "--status", "running", "--worker", "claude:haiku", *stack)
        return wt

    def finish(self, tid):
        self.sysop_json("wait", tid, "--timeout", "20", "--poll", "0.1")

    def built(self, tid):
        self.sysop("ticket", "set", tid, "--status", "built")

    def review(self, tid, verdict="READY"):
        report = self.store / "reports" / f"sysop-{tid}.review.md"
        report.parent.mkdir(exist_ok=True)
        report.write_text(f"# Review\n\n**Verdict:** {verdict}\n")
        proc = self.sysop("review", "seal", f"sysop/{tid}")
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def through_review(self, act, verdict="READY"):
        self.do_launch(act)
        self.finish(act["ticket"])
        self.built(act["ticket"])
        self.review(act["ticket"], verdict)

    # --- the graph ------------------------------------------------------------------------------------

class NextTest(FleetCase):
    def test_graph_refuses_unknown_cycles_and_self(self):
        self.assertEqual(self.add("a").returncode, 0)
        self.assertIn("unknown dependency", self.add("b", "zz").stderr)
        self.assertIn("depends on itself", self.add("c", "c").stderr)
        self.assertEqual(self.add("d", "a").returncode, 0)
        proc = self.sysop("ticket", "add", "a", "--title", "x", "--size", "small", "--depends", "d")
        self.assertIn("cycle", proc.stderr)  # a -> d -> a
        data = json.loads((self.store / "tickets.json").read_text())
        self.assertEqual(data["tickets"]["a"]["depends"], [])  # refused: nothing changed
        self.assertEqual(data["tickets"]["d"]["branch"], "sysop/d")

    def test_authority_is_one_of_three_facts(self):
        self.assertIn("must be one of", self.add("a", authority="yes please").stderr)
        self.assertIn("do not grant it", self.add("a", authority="standing land: true").stderr)
        self.sysop("config", "set", "land", "true")
        self.assertEqual(self.add("a", authority="standing land: true").returncode, 0)

    def test_waves_under_max_parallel(self):
        self.sysop("config", "set", "max_parallel", "2")
        for tid in ("t1", "t2", "t3", "t4", "t5", "t6"):
            self.add(tid, authority="--land given")
        launches = self.actions("launch")
        self.assertEqual([a["ticket"] for a in launches], ["t1", "t2"])
        for act in launches:
            self.do_launch(act, cmd="sleep 30")
        nxt = self.next()
        self.assertEqual([a["do"] for a in nxt["actions"]], ["wait"])
        self.assertEqual(nxt["actions"][0]["tickets"], ["t1", "t2"])
        for tid in ("t1", "t2"):
            self.sysop("ticket", "set", tid, "--status", "blocked")  # the slots free up
        self.assertEqual([a["ticket"] for a in self.actions("launch")], ["t3", "t4"])

    def test_a_ticket_flows_from_launch_to_land(self):
        self.add("t1", authority="--land given")
        [act] = self.actions("launch")
        self.assertEqual((act["branch"], act["base_ref"], act["stacked_on"]), ("sysop/t1", "main", None))
        self.do_launch(act)
        self.finish("t1")
        self.assertEqual([a["do"] for a in self.actions()], ["check"])
        self.built("t1")
        self.assertEqual(self.actions()[0]["do"], "review")
        self.review("t1")
        [land] = self.actions()
        self.assertEqual((land["do"], land["branch"], land["base"]), ("land", "sysop/t1", "main"))
        out = self.sysop_json("land", "sysop/t1", "--base", "main", "--land")
        self.assertTrue(out["landed"])
        t1 = json.loads((self.store / "tickets.json").read_text())["tickets"]["t1"]
        self.assertEqual(t1["status"], "landed")  # land records it: a fact, not a claim
        done = self.next()
        self.assertEqual(done["actions"][0]["do"], "done")
        self.assertEqual(done["actions"][0]["landed"], ["t1"])

    def test_needs_fixes_is_a_correction(self):
        self.add("t1")
        [act] = self.actions("launch")
        self.through_review(act, verdict="NEEDS FIXES")
        [corr] = self.actions()
        self.assertEqual((corr["do"], corr["ticket"]), ("correct", "t1"))
        self.assertIn("review", corr["why"])

    def test_failed_runs_are_corrected_then_blocked(self):
        self.sysop("config", "set", "max_attempts", "2")
        self.add("t1")
        [act] = self.actions("launch")
        wt = self.do_launch(act, cmd="exit 3")
        self.finish("t1")
        [corr] = self.actions()
        self.assertEqual((corr["do"], corr["attempt"]), ("correct", 1))
        self.assertIn("exit 3", corr["why"])
        self.sysop_json("launch", "t1", str(wt), "exit 4")
        self.finish("t1")
        [up] = self.actions()  # the author had its tries: one last one a tier up (small -> fast -> standard)
        self.assertEqual((up["do"], up["escalate"]), ("correct", "standard"))
        self.assertEqual(self.actions()[0]["escalate"], "standard")  # asked again, the same answer
        self.sysop_json("launch", "t1", str(wt), "exit 5")
        self.finish("t1")
        blocked, done = self.actions()
        self.assertEqual((blocked["do"], done["do"], done["blocked"]), ("blocked", "done", ["t1"]))
        self.assertIn("the last one a tier up (standard)", blocked["why"])

    # --- stacking without authority (finding A6) ------------------------------------------------------
    def test_a_dependent_pair_without_authority_stacks_and_both_reach_reviewed(self):
        self.add("t1")
        self.add("t2", depends="t1")
        [act1] = self.actions("launch")
        self.assertEqual(act1["ticket"], "t1")  # t2 waits for t1's review
        self.through_review(act1)
        sealed = git(self.repo, "rev-parse", "sysop/t1")
        [act2] = self.actions("launch")
        self.assertEqual((act2["ticket"], act2["stacked_on"], act2["base_ref"], act2["fork"]),
                         ("t2", "t1", sealed, sealed))
        self.through_review(act2)
        [done] = self.actions()
        self.assertEqual(done["do"], "done")
        self.assertEqual(sorted(done["ready"]), ["t1", "t2"])
        statuses = {k: v["status"] for k, v in json.loads((self.store / "tickets.json").read_text())["tickets"].items()}
        self.assertEqual(statuses, {"t1": "reviewed", "t2": "reviewed"})

    def test_stacked_child_lands_only_after_its_parent_and_replays_onto_base(self):
        self.add("t1", authority="--land given")
        self.add("t2", depends="t1", authority="--land given")
        self.through_review(self.actions("launch")[0])
        self.through_review(self.actions("launch")[0])
        self.commit("other.txt", "x\n", "someone else lands first")  # main moves on
        refused = self.sysop("land", "sysop/t2", "--base", "main", "--land")
        self.assertIn("land t1 first", refused.stderr)
        self.assertEqual([a["ticket"] for a in self.actions("land")], ["t1"])  # one at a time, in order
        self.assertTrue(self.sysop_json("land", "sysop/t1", "--base", "main", "--land")["landed"])
        out = self.sysop_json("land", "sysop/t2", "--base", "main", "--land")
        self.assertTrue(out["landed"])
        log = git(self.repo, "log", "--format=%s", "main")
        self.assertEqual(log.splitlines()[:3], ["build t2", "build t1", "someone else lands first"])

    def test_a_stacked_child_whose_own_change_moved_is_refused_and_reset(self):
        self.add("t1", authority="--land given")
        self.add("t2", depends="t1", authority="--land given")
        self.through_review(self.actions("launch")[0])
        self.through_review(self.actions("launch")[0])
        sealed = git(self.repo, "rev-parse", "sysop/t2")
        data = json.loads((self.store / "tickets.json").read_text())
        data["tickets"]["t2"]["fork"] = git(self.repo, "rev-parse", "main")  # replays t1's change too
        (self.store / "tickets.json").write_text(json.dumps(data))
        self.sysop_json("land", "sysop/t1", "--base", "main", "--land")
        proc = self.sysop("land", "sysop/t2", "--base", "main", "--land")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Re-review", proc.stderr)
        self.assertEqual(git(self.repo, "rev-parse", "sysop/t2"), sealed)  # back to what was reviewed

    def test_a_landing_conflict_becomes_a_rebase_correction(self):
        self.add("t1", authority="--land given")
        act = self.actions("launch")[0]
        wt = self.do_launch(act, cmd="echo theirs > README.md && git add -A && "
                                      "git -c user.name=w -c user.email=w@x commit -qm 'edit readme'")
        self.finish("t1")
        self.built("t1")
        self.review("t1")
        self.commit("README.md", "ours\n", "main edits the same line")
        proc = self.sysop("land", "sysop/t1", "--base", "main", "--land")
        self.assertIn("does not rebase cleanly", proc.stderr)
        [corr] = self.actions()
        self.assertEqual(corr["do"], "correct")
        self.assertIn("rebase onto main", corr["why"])
        self.assertTrue(Path(wt).exists())

    # --- halt and resume -----------------------------------------------------------------------------
    def test_halt_stops_new_launches_only(self):
        self.add("t1")
        self.add("t2")
        act = self.actions("launch")[0]
        self.do_launch(act, cmd="sleep 30")
        self.sysop("halt", "--reason", "going to bed")
        nxt = self.next()
        self.assertEqual([a["do"] for a in nxt["actions"]], ["wait"])
        self.assertTrue(nxt["halted"])
        self.assertIn("going to bed", nxt["next"])
        self.sysop("unhalt")
        self.assertEqual([a["ticket"] for a in self.actions("launch")], ["t2"])

    def test_resume_after_a_crash_yields_the_same_actions(self):
        for tid in ("t1", "t2", "t3"):
            self.add(tid)
        self.add("t4", depends="t1,t2")
        first = self.next()
        again = self.next()  # a new coordinator reads the same files
        self.assertEqual(first, again)
        self.assertEqual(hashlib.sha256(json.dumps(first, sort_keys=True).encode()).hexdigest(),
                         hashlib.sha256(json.dumps(self.next(), sort_keys=True).encode()).hexdigest())


class RunPauseTest(TempRepoCase):
    def stop(self, session="S1"):
        return self.hook("stop_gate.py", {"hook_event_name": "Stop", "cwd": str(self.repo), "session_id": session,
                                          "stop_hook_active": False, "transcript_path": None})

    def test_resume_without_a_run_starts_one(self):
        out = self.sysop_json("run", "resume", "fleet", "--session", "S1")
        self.assertEqual((out["slug"], out["session"], out["step"]), ("fleet", "S1", 0))
        self.assertEqual(self.stop().returncode, 2)

    def test_pause_releases_the_gate_and_resume_rebinds(self):
        self.sysop("run", "start", "fleet", "--session", "S1")
        self.assertEqual(self.stop().returncode, 2)
        self.sysop("run", "pause", "fleet")
        self.assertEqual(self.stop().returncode, 0)  # a paused tick may end its turn
        self.env["CLAUDE_CODE_SESSION_ID"] = "S2"
        resumed = self.sysop_json("run", "resume", "fleet")
        self.assertEqual((resumed["session"], resumed.get("paused")), ("S2", False))
        self.assertEqual(self.stop(session="S2").returncode, 2)
        self.assertEqual(self.stop(session="S1").returncode, 0)


class WaitOutputTest(TempRepoCase):
    def test_tails_only_for_failures(self):
        self.sysop_json("launch", "ok", str(self.repo), "echo fine")
        self.sysop_json("launch", "bad", str(self.repo), "echo broken; exit 2")
        states = {s["ticket"]: s for s in self.sysop_json("wait", "ok", "bad", "--timeout", "20", "--poll", "0.1")}
        self.assertNotIn("tail", states["ok"])
        self.assertIn("broken", states["bad"]["tail"])


class WakeTest(FleetCase):
    """The mod asks `wake` every few seconds: wake the coordinator only when it has something to do."""
    def wake(self, session="S1"):
        return self.sysop_json("wake", "--session", session)

    def test_wakes_only_for_this_sessions_run_when_more_than_waits_are_due(self):
        self.assertEqual(self.wake()["why"], "no fleet run here")
        self.add("t1")
        self.sysop("run", "start", "fleet", "--session", "S1")
        first = self.wake()
        self.assertTrue(first["wake"])  # t1 is due to launch
        self.assertIn("launch", first["key"])
        self.assertEqual(self.wake("S2")["why"], "the fleet run belongs to another session")
        act = self.actions("launch")[0]
        self.do_launch(act, cmd="sleep 30")
        self.assertEqual(self.wake()["why"], "only waits are due")  # the worker is building
        self.sysop("halt", "--reason", "x")
        self.assertEqual(self.wake()["why"], "halted")

    def test_a_finished_worker_wakes_with_a_new_key(self):
        self.add("t1")
        self.sysop("run", "start", "fleet", "--session", "S1")
        self.do_launch(self.actions("launch")[0])
        self.finish("t1")
        woke = self.wake()
        self.assertTrue(woke["wake"])
        self.assertNotIn("launch", woke["key"])  # the next step for t1, not the launch already done

    def test_a_run_started_from_another_repo_is_found(self):
        self.add("t1")
        other = self.tmp / "app"
        other.mkdir()
        git(other, "init", "-q", "-b", "main")
        self.sysop("run", "start", "fleet", "--session", "S1")
        self.assertTrue(self.sysop_json("wake", "--session", "S1", cwd=other)["wake"])  # asked from the app repo



class LandingWaitTest(FleetCase):
    def test_the_ledger_records_how_long_ready_work_waited_to_land(self):
        self.add("t1")
        self.through_review(self.actions("launch")[0])
        self.actions()  # next moves it to reviewed: ready from now
        self.sysop("land", "sysop/t1", "--land")
        row = self.sysop_json("ledger", "add", "--ticket", "t1", "--landed", "yes")
        self.assertGreaterEqual(row["landing_seconds"], 0)
        self.assertLess(row["landing_seconds"], 60)
        landing = self.sysop_json("ledger", "summary", "--json")["landing"]
        self.assertEqual((landing["n"], landing["most_ready_together"]), (1, 1))


class LandEverythingReadyTest(FleetCase):
    def test_land_with_no_branch_lands_every_ready_ticket_in_order(self):
        self.add("a")
        self.add("b")
        for act in self.actions("launch"):
            self.through_review(act)
        self.actions()  # both reviewed; neither has authority, so next only says they are ready
        self.assertEqual(self.actions("land"), [])
        out = self.sysop_json("land")
        self.assertEqual(sorted(out["landed"]), ["a", "b"])
        statuses = {t: d["status"] for t, d in json.loads((self.store / "tickets.json").read_text())["tickets"].items()}
        self.assertEqual(statuses, {"a": "landed", "b": "landed"})
        self.assertEqual(self.sysop_json("land")["next"], "nothing was ready to land")

    def test_autoland_is_local_and_never_dirties_the_checkout(self):
        before = git(self.repo, "status", "--porcelain")
        self.sysop("config", "set", "land", "true", "--scope", "local")
        self.assertEqual(git(self.repo, "status", "--porcelain"), before)
        self.assertTrue(self.sysop_json("settings", "--key", "land"))
        self.add("c")
        self.through_review(self.actions("launch")[0])
        self.assertEqual([a["ticket"] for a in self.actions("land")], ["c"])  # the standing grant applies
