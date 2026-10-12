import json
import os
import signal
import time
from pathlib import Path

from helpers import TempRepoCase


class LaunchWaitTest(TempRepoCase):
    def launch(self, ticket, cmd, workdir=None, cwd=None):
        return self.sysop_json("launch", ticket, str(workdir or self.repo), cmd, cwd=cwd)

    def log_lines(self, state):
        """A clean exit carries no tail in `wait` (finding D5); its log is still there."""
        return Path(state["log"]).read_text().splitlines()

    def wait(self, *tickets, timeout="20", code=0):
        return self.sysop_json("wait", *tickets, "--timeout", timeout, "--poll", "0.2", code=code)

    def test_exit_code_captured(self):
        rec = self.launch("t1", "echo hello; exit 7")
        self.assertEqual(rec["attempt"], 1)
        [state] = self.wait("t1")
        self.assertEqual((state["state"], state["code"]), ("exited", 7))
        self.assertIn("hello", state["tail"])

    def test_worker_timeout_stops_a_silent_worker_and_its_children(self):
        self.sysop("config", "set", "worker_timeout", "2")
        marker = self.tmp / "survived"
        self.launch("t1", f"bash -c 'sleep 8; touch {marker}' & wait")  # a grandchild that would outlive a plain kill
        [state] = self.wait("t1", timeout="20")
        self.assertEqual((state["state"], state["code"]), ("exited", 124))
        self.assertIn("no result within 2s (worker_timeout)", "\n".join(state["tail"]))
        time.sleep(7)
        self.assertFalse(marker.exists())

    def test_the_deadline_needs_no_gnu_timeout(self):
        """macOS has no `timeout`: one that always fails stands in for a missing one (finding A2)."""
        fake = self.tmp / "fakebin"
        fake.mkdir()
        (fake / "timeout").write_text("#!/bin/sh\necho 'timeout: not here' >&2\nexit 127\n")
        (fake / "timeout").chmod(0o755)
        self.env["PATH"] = f"{fake}:{self.env['PATH']}"
        self.sysop("config", "set", "worker_timeout", "2")
        rec = self.launch("t1", "echo started; sleep 30")
        [state] = self.wait("t1", timeout="20")
        self.assertEqual((state["state"], state["code"]), ("exited", 124))
        self.assertIn("started", state["tail"])
        self.assertIn("no result within 2s (worker_timeout)", "\n".join(state["tail"]))
        with self.assertRaises(ProcessLookupError):
            os.killpg(rec["pid"], 0)  # the whole group is gone
        deadline = json.loads((self.repo / ".sysop" / "runs" / "t1" / "1" / "deadline").read_text())
        self.assertEqual((deadline["seconds"], deadline["key"]), (2, "worker_timeout"))

    def test_a_relaunch_stops_an_unwatched_worker_past_its_deadline(self):
        self.sysop("config", "set", "worker_timeout", "1")
        self.launch("t1", "sleep 30")
        time.sleep(1.5)
        self.assertEqual(self.launch("t1", "exit 0")["attempt"], 2)
        first = self.repo / ".sysop" / "runs" / "t1" / "1"
        self.assertEqual((first / "exit").read_text().strip(), "124")

    def test_a_finished_worker_is_never_stopped(self):
        self.sysop("config", "set", "worker_timeout", "1")
        self.launch("t1", "exit 3")
        time.sleep(1.5)
        [state] = self.wait("t1")
        self.assertEqual(state["code"], 3)

    def test_a_reviewer_gets_the_shorter_review_deadline(self):
        self.sysop("config", "set", "review_timeout", "2")  # worker_timeout stays at its hour
        rec = self.sysop_json("launch", "x1", str(self.repo), "sleep 8", "--role", "review")
        [state] = self.wait("x1", timeout="20")
        self.assertEqual(state["code"], 124)
        self.assertIn("no result within 2s (review_timeout)", "\n".join(state["tail"]))
        self.assertTrue(rec["log"])

    def test_exit_inside_command_keeps_code(self):
        # without the subshell wrapper, `exit` would end the wrapper before the code is written
        self.launch("t1", "if true; then exit 4; fi; echo unreachable")
        [state] = self.wait("t1")
        self.assertEqual(state["code"], 4)

    def test_worker_outlives_its_launcher(self):
        rec = self.launch("t1", "sleep 1.5; echo done")
        # the launching process has already exited (self.sysop returned); the worker must not have
        os.kill(rec["pid"], 0)
        self.assertNotEqual(os.getpgid(rec["pid"]), os.getpgid(0))  # its own session/group
        [state] = self.wait("t1")
        self.assertEqual((state["state"], state["code"]), ("exited", 0))
        self.assertIn("done", self.log_lines(state))

    def test_killed_worker_is_reported_dead(self):
        rec = self.launch("t1", "sleep 30")
        os.killpg(rec["pid"], signal.SIGKILL)
        time.sleep(0.3)
        [state] = self.wait("t1")
        self.assertEqual(state["state"], "died")
        self.assertIsNone(state["code"])

    def test_timeout_reports_running_and_exits_124(self):
        rec = self.launch("t1", "sleep 30")
        try:
            [state] = self.wait("t1", timeout="0.5", code=124)
            self.assertEqual(state["state"], "running")
        finally:
            os.killpg(rec["pid"], signal.SIGKILL)

    def test_relaunch_refused_while_running_then_new_attempt(self):
        rec = self.launch("t1", "sleep 30")
        self.assertEqual(self.sysop("launch", "t1", str(self.repo), "true").returncode, 1)
        os.killpg(rec["pid"], signal.SIGKILL)
        time.sleep(0.3)
        second = self.launch("t1", "exit 0")
        self.assertEqual(second["attempt"], 2)
        [state] = self.wait("t1")
        self.assertEqual((state["attempt"], state["code"]), (2, 0))
        self.assertTrue((self.repo / ".sysop" / "runs" / "t1" / "1" / "log").exists())

    def test_runs_from_worktree_land_in_main_store(self):
        wt = self.worktree()
        rec = self.launch("t1", "pwd", workdir=wt, cwd=wt)
        self.assertTrue(rec["run_dir"].startswith(str(self.repo / ".sysop" / "runs")))
        [state] = self.wait("t1")
        self.assertIn(str(wt), self.log_lines(state)[0])

    def test_wait_many(self):
        self.launch("a", "exit 0")
        self.launch("b", "sleep 0.5; exit 2")
        states = {s["ticket"]: s for s in self.wait("a", "b")}
        self.assertEqual((states["a"]["code"], states["b"]["code"]), (0, 2))

    def test_reused_pid_is_not_alive(self):
        rec = self.launch("t1", "sleep 30")
        run = self.repo / ".sysop" / "runs" / "t1" / "1"
        try:
            (run / "pstart").write_text("1\n")  # a different process now owns this pid
            [state] = self.wait("t1")
            self.assertEqual(state["state"], "died")
        finally:
            os.killpg(rec["pid"], signal.SIGKILL)

    def test_trailing_comment_in_command(self):
        self.launch("t1", "echo ok; exit 3 # the comment must not eat the wrapper")
        [state] = self.wait("t1")
        self.assertEqual((state["state"], state["code"]), ("exited", 3))

    def test_non_utf8_output_is_tolerated(self):
        self.launch("t1", r"printf '\xff\xfe bad bytes\n'")
        [state] = self.wait("t1")
        self.assertEqual(state["code"], 0)

    def test_worker_does_not_inherit_claude_session(self):
        self.env.update(CLAUDE_CODE_SESSION_ID="parent-session", CLAUDE_CODE_MESSAGING_TOKEN="tok",
                        CLAUDECODE="1", CLAUDE_PID="42", KEEP_ME="yes")
        self.launch("t1", "python3 -c 'import os,json; print(json.dumps(sorted(os.environ)))'")
        [state] = self.wait("t1")
        keys = json.loads(self.log_lines(state)[-1])
        for gone in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDECODE", "CLAUDE_PID"):
            self.assertNotIn(gone, keys)
        self.assertIn("KEEP_ME", keys)
        self.assertIn("PATH", keys)

    def test_bad_input(self):
        self.assertEqual(self.sysop("launch", "../x", str(self.repo), "true").returncode, 1)
        self.assertEqual(self.sysop("launch", "t1", str(self.tmp / "nope"), "true").returncode, 1)
        proc = self.sysop("wait", "ghost", "--timeout", "1")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("no run recorded", proc.stderr)
