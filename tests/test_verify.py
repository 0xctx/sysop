"""The runtime verifier: a fresh agent drives the running app; landing needs a calibrated PASS at the seal."""
import json
import os
import time
from pathlib import Path

from helpers import TempRepoCase, git

APP = '''import os
from http.server import BaseHTTPRequestHandler, HTTPServer


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        body = (b"ok" if self.path == "/health" else open("state.txt", "rb").read())
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


open("app.pid", "w").write(str(os.getpid()))
HTTPServer(("127.0.0.1", int(os.environ["PORT"])), H).serve_forever()
'''

# Stands in for a verifier agent: it really calls the app and reports what it saw, unless told otherwise.
VERIFIER = '''import json, re, sys, os, time, urllib.request
prompt = open(sys.argv[1]).read()
url = re.search(r"running at (http://127\.0\.0\.1:\d+)", prompt).group(1)
if os.environ.get("FAKE_VERIFIER") == "silent":
    print("I looked around and it seems fine."); sys.exit(0)
if os.environ.get("FAKE_VERIFIER") == "hang":
    time.sleep(60)
seen = urllib.request.urlopen(url + "/").read().decode()
ok = seen.strip() == "all good"
print("curl " + url + "/ -> " + seen)
print(json.dumps({"journeys": [{"n": 1, "pass": ok, "evidence": "GET / said " + seen.strip()}],
                  "verdict": "PASS" if ok else "FAIL"}))
'''


class VerifyTest(TempRepoCase):
    def setUp(self):
        super().setUp()
        self.sysop("store")
        self.store = self.repo / ".sysop"
        self.use_fakebin(only=())  # no real agent CLIs: the roster stays fast and offline
        verifier = self.tmp / "fake_verifier.py"
        verifier.write_text(VERIFIER)
        (self.repo / ".claude").mkdir()
        (self.repo / ".claude" / "sysop.json").write_text(json.dumps({
            "check": "exit 0", "cross_review": False, "start": "python3 app.py", "verify_timeout": 5,
            "workers": {"fakeverify": {"bin": "python3", "verify": f"python3 {verifier} {{prompt_file}}"}},
            "tiers": {"verify": ["fakeverify:v1"]}}))
        (self.repo / "app.py").write_text(APP)
        (self.repo / "state.txt").write_text("all good\n")
        (self.repo / "JOURNEYS.md").write_text("1. A visitor opens / and reads 'all good'.\n")
        (self.repo / ".gitignore").write_text("app.pid\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "app")
        git(self.repo, "branch", "fault")
        wt = self.tmp / "fault"
        git(self.repo, "worktree", "add", "-q", str(wt), "fault")
        self.commit("state.txt", "broken\n", "the deliberate fault", cwd=wt)
        git(self.repo, "worktree", "remove", str(wt))

    def feature(self, state="all good\n"):
        wt = self.store / "wt" / "feat"
        git(self.repo, "worktree", "add", "-q", "-b", "feat", str(wt))
        (wt / "state.txt").write_text(state)
        git(wt, "add", "state.txt")
        self.commit("feature.txt", "a feature\n", "a feature", cwd=wt)
        report = self.store / "reports" / "feat.review.md"
        report.parent.mkdir(exist_ok=True)
        report.write_text("**Verdict:** READY\n")
        self.assertEqual(self.sysop("review", "seal", "feat").returncode, 0)
        return wt

    def calibrate(self):
        return self.sysop_json("verify", "calibrate", "--fault-branch", "fault", "--base", "main", timeout=120)

    def processes_inside_repo(self) -> list[str]:
        """Live processes whose working directory is inside this test's repo (the app runs in a worktree)."""
        found = []
        for proc in Path("/proc").glob("[0-9]*"):
            try:
                if os.readlink(proc / "cwd").startswith(str(self.repo)):
                    found.append((proc / "cmdline").read_text().replace("\0", " "))
            except OSError:
                continue
        return found

    def test_calibration_records_only_a_discriminating_verifier(self):
        cal = self.calibrate()
        self.assertEqual((cal["base_verdict"], cal["fault_verdict"]), ("PASS", "FAIL"))
        self.assertIn("broken", cal["fault_evidence"][0]["evidence"])
        self.assertTrue((self.store / "verify" / "calibration.json").is_file())
        self.env["FAKE_VERIFIER"] = "silent"  # a verifier that never commits to a verdict cannot calibrate
        (self.store / "verify" / "calibration.json").unlink()
        proc = self.sysop("verify", "calibrate", "--fault-branch", "fault", "--base", "main", timeout=120)
        self.assertIn("the base did not pass", proc.stderr)
        self.assertFalse((self.store / "verify" / "calibration.json").exists())

    def test_calibration_needs_a_real_fault(self):
        git(self.repo, "branch", "same", "main")
        self.assertIn("same commit", self.sysop("verify", "calibrate", "--fault-branch", "same", "--base", "main").stderr)

    def test_a_verifier_that_moves_the_branch_fails(self):
        self.calibrate()
        self.feature()
        verifier = self.tmp / "fake_verifier.py"
        verifier.write_text(f"import subprocess\nsubprocess.run(['git', '-C', '{self.repo}', 'update-ref', "
                            f"'refs/heads/feat', 'main'])\n" + VERIFIER)
        out = json.loads(self.sysop("verify", "run", "feat", timeout=120).stdout)
        self.assertEqual(out["verdict"], "FAIL")
        self.assertIn("moved during verification", out["reason"])

    def test_landing_needs_calibration_and_a_pass_at_the_seal(self):
        self.feature()
        proc = self.sysop("land", "feat", "--base", "main", "--land")
        self.assertIn("not calibrated", proc.stderr)
        self.calibrate()
        self.assertIn("not verified yet", self.sysop("land", "feat", "--base", "main", "--land").stderr)
        out = self.sysop_json("verify", "run", "feat", timeout=120)
        self.assertEqual(out["verdict"], "PASS")
        self.assertEqual(out["sha"], git(self.repo, "rev-parse", "feat"))
        self.assertIn("Journey 1: pass", (self.store / "reports" / "feat.verify.md").read_text())
        self.assertTrue(self.sysop_json("land", "feat", "--base", "main", "--land")["landed"])

    def test_a_failed_journey_refuses_the_landing(self):
        self.calibrate()
        self.feature(state="oops\n")
        proc = self.sysop("verify", "run", "feat", timeout=120)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(proc.stdout)["verdict"], "FAIL")
        refused = self.sysop("land", "feat", "--base", "main", "--land")
        self.assertIn("failed journey(s) 1", refused.stderr)

    def test_changed_journeys_or_a_new_head_need_new_evidence(self):
        self.calibrate()
        wt = self.feature()
        self.sysop_json("verify", "run", "feat", timeout=120)
        (self.repo / "JOURNEYS.md").write_text("1. A visitor opens / and reads 'all good'.\n2. And more.\n")
        self.assertIn("journeys changed", self.sysop("land", "feat", "--base", "main", "--land").stderr)
        self.calibrate()
        self.commit("more.txt", "x\n", "after verify", cwd=wt)
        self.sysop("review", "seal", "feat")
        self.assertIn("verification is for", self.sysop("land", "feat", "--base", "main", "--land").stderr)

    def test_no_verdict_is_a_failure_and_the_app_always_stops(self):
        self.calibrate()
        self.feature()
        for mode in ("silent", "hang"):
            with self.subTest(mode=mode):
                self.env["FAKE_VERIFIER"] = mode
                out = json.loads(self.sysop("verify", "run", "feat", timeout=120).stdout)
                self.assertEqual(out["verdict"], "FAIL")
                time.sleep(0.5)
                trees = [p.name for p in (self.store / "wt").iterdir() if p.name.startswith("verify-") and p.is_dir()]
                self.assertEqual(trees, [])  # the disposable worktree is gone
                self.assertEqual(self.processes_inside_repo(), [])

    def test_an_app_that_never_answers_fails_cleanly(self):
        self.calibrate()
        self.feature()
        self.sysop("config", "set", "start", "sleep 600")
        out = json.loads(self.sysop("verify", "run", "feat", timeout=180).stdout)
        self.assertEqual(out["verdict"], "FAIL")
        self.assertIn("did not answer", out["reason"])
        time.sleep(0.5)
        self.assertEqual(self.processes_inside_repo(), [])

    def test_next_verifies_between_review_and_land(self):
        self.calibrate()
        self.sysop("ticket", "add", "t1", "--title", "x", "--size", "small", "--base", "main",
                   "--authority", "--land given")
        wt = self.store / "wt" / "t1"
        git(self.repo, "worktree", "add", "-q", "-b", "sysop/t1", str(wt))
        self.commit("state.txt", "all good\n\n", "touch", cwd=wt)
        self.sysop_json("launch", "t1", str(wt), "true")
        self.sysop("ticket", "set", "t1", "--status", "built")
        (self.store / "reports" / "sysop-t1.review.md").write_text("**Verdict:** READY\n")
        self.sysop("review", "seal", "sysop/t1")
        acts = self.sysop_json("next")["actions"]
        self.assertEqual([a["do"] for a in acts], ["verify"])
        self.sysop_json("verify", "run", "sysop/t1", timeout=120)
        acts = self.sysop_json("next")["actions"]
        self.assertEqual([a["do"] for a in acts], ["land"])
        t1 = json.loads((self.store / "tickets.json").read_text())["tickets"]["t1"]
        self.assertEqual(t1["status"], "verified")

    def test_records_are_script_owned(self):
        for path in (".sysop/reports/feat.verify.json", ".sysop/reports/feat.verify.md",
                     ".sysop/verify/calibration.json"):
            with self.subTest(path=path):
                proc = self.hook("guard.py", {"tool_name": "Write", "cwd": str(self.repo),
                                              "tool_input": {"file_path": path, "content": "{}"}})
                self.assertEqual(proc.returncode, 2)
