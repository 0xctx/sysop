"""The runtime verifier (opt-in: setting `start`). A fresh agent that did not build the change drives the
running app through the repo's journeys and returns a per-journey verdict with quoted evidence.

Spike S1 (docs/spikes/s1-verifier.md) is the evidence behind the design: a verifier limited to curl, given
evidence rules and one JSON verdict line, passed a baseline and failed a planted fault 3/3 each. A verifier
is trusted only after it has been calibrated: the base passes and a deliberate fault fails.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

from land import check_seal, review_verdict
from store import (
    ensure_store, git, load_settings, main_root, PLUGIN_ROOT, remove_worktree, slug, SysopError, write_json,
)
from workers import entry_cmd, family, launch, load_roster, route, run_state, worker_output

VERDICT_RE = re.compile(r'\{"journeys".*\}')
HEALTH_SECONDS = 60


def enabled(settings: dict) -> bool:
    return bool(str(settings.get("start") or "").strip())


def journeys_path(cwd: str, settings: dict) -> Path:
    return main_root(cwd) / (settings.get("journeys") or "JOURNEYS.md")


def journeys_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


def record_path(cwd: str, branch: str) -> Path:
    return ensure_store(cwd) / "reports" / f"{slug(branch)}.verify.json"


def calibration_path(cwd: str) -> Path:
    return ensure_store(cwd) / "verify" / "calibration.json"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def stop_group(proc: subprocess.Popen) -> None:
    """The app and everything it spawned: its own process group, TERM then KILL."""
    for sig, grace in ((signal.SIGTERM, 5), (signal.SIGKILL, 2)):
        try:
            os.killpg(proc.pid, sig)
        except OSError:
            return
        try:
            proc.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            continue


def parse_verdict(text: str) -> dict:
    """The last JSON verdict line; anything else is a failure, never a pass."""
    for line in reversed(text.splitlines()):
        found = VERDICT_RE.search(line)
        if not found:
            continue
        try:
            data = json.loads(found.group(0))
        except json.JSONDecodeError:
            continue
        journeys = data.get("journeys") or []
        ok = data.get("verdict") == "PASS" and journeys and all(j.get("pass") is True for j in journeys)
        return {"verdict": "PASS" if ok else "FAIL", "journeys": journeys}
    return {"verdict": "FAIL", "journeys": [], "reason": "no verdict line in the verifier's output"}


def verify_ref(cwd: str, ref: str, tag: str, author_family: str | None = None) -> dict:
    """Start the app at `ref` in a disposable worktree, let a fresh verifier drive it, stop everything."""
    settings, root, store = load_settings(cwd), main_root(cwd), ensure_store(cwd)
    if not enabled(settings):
        raise SysopError("verification is off: set the app's start command first "
                         "(`sysop.py config set start \"<command that serves on $PORT>\"`)")
    journeys = journeys_path(cwd, settings)
    if not journeys.is_file():
        raise SysopError(f"no journeys at {journeys}: write them first (setup drafts them from the README)")
    sha = git(["rev-parse", "--verify", f"{ref}^{{commit}}"], root)
    tree = store / "wt" / f"verify-{tag}"
    if tree.exists():
        remove_worktree(root, tree)
    git(["worktree", "add", "-q", "--detach", str(tree), sha], root)
    port, app = free_port(), None
    url = f"http://127.0.0.1:{port}"
    try:
        app = subprocess.Popen(settings["start"], shell=True, cwd=str(tree), start_new_session=True,
                               env={**os.environ, "PORT": str(port)}, stdin=subprocess.DEVNULL,
                               stdout=(store / "wt" / f"verify-{tag}.app.log").open("w"), stderr=subprocess.STDOUT)
        health = url + "/" + str(settings.get("health") or "/health").lstrip("/")
        wait_s = min(HEALTH_SECONDS, int(settings.get("verify_timeout") or 600))
        deadline = time.time() + wait_s
        while True:
            try:
                with urllib.request.urlopen(health, timeout=2) as resp:
                    if resp.status < 500:
                        break
            except OSError:
                pass
            if app.poll() is not None or time.time() > deadline:
                return {"sha": sha, "verdict": "FAIL", "journeys": [],
                        "reason": f"the app did not answer {health} within {wait_s}s"
                                  f" (start: `{settings['start']}`)"}
            time.sleep(0.2)
        brief = (PLUGIN_ROOT / "skills" / "ship" / "templates" / "verifier-brief.md").read_text()
        prompt = store / "briefs" / f"verify-{tag}.md"
        prompt.parent.mkdir(parents=True, exist_ok=True)
        prompt.write_text(brief.replace("{URL}", url).replace("{JOURNEYS}", journeys.read_text()))
        roster = load_roster(settings, cwd=cwd)
        pick = route(settings, roster, "verify", "verify", not_family=author_family)
        if not pick["worker"]:
            raise SysopError("no verifier available: " + "; ".join(f"{s['entry']}: {s['reason']}"
                                                                   for s in pick["skipped"]))
        cmd = entry_cmd(settings, roster, pick["entry"], str(prompt), role="verify")
        rec = launch(cwd, f"verify-{tag}", str(tree), cmd, worker=pick["entry"], role="verify")
        run = Path(rec["run_dir"])
        limit = time.time() + int(settings.get("verify_timeout") or 600) + 30
        while run_state(run)["state"] == "running" and time.time() < limit:
            time.sleep(0.2)
        out = parse_verdict(worker_output((run / "log").read_text(errors="replace")))
        return {"sha": sha, **out, "verifier": pick["entry"], "family": family(pick["model"]),
                "log": str(run / "log"), "at": time.time()}
    finally:
        if app is not None:
            stop_group(app)
        # disposable by design: whatever the app or the verifier left there is never work to keep
        subprocess.run(["git", "worktree", "remove", "--force", str(tree)], cwd=str(root), capture_output=True)
        if tree.exists():
            remove_worktree(root, tree)


def run_for_branch(cwd: str, branch: str, author_family: str | None) -> dict:
    """Verify the sealed head of a reviewed branch; the record binds to that sha."""
    report = ensure_store(cwd) / "reports" / f"{slug(branch)}.review.md"
    sealed = check_seal(cwd, branch, report)
    if review_verdict(report) != "READY":
        raise SysopError(f"verify runs after a READY review; {branch}'s review is not READY")
    result = verify_ref(cwd, sealed["sha"], slug(branch), author_family)
    head = git(["rev-parse", f"refs/heads/{branch}"], main_root(cwd))
    if head != sealed["sha"]:  # a verifier may run a shell: it must not have moved what it judged
        result.update(verdict="FAIL", reason=f"{branch} moved during verification ({sealed['sha'][:12]} -> "
                                             f"{head[:12]}); review and verify again")
    result["journeys_sha256"] = journeys_hash(journeys_path(cwd, load_settings(cwd)))
    write_json(record_path(cwd, branch), result)
    lines = [f"# Verification: {branch}", "", f"**Sha:** {result['sha'][:12]} · **Verdict:** {result['verdict']}"
             f" · **Verifier:** {result.get('verifier', '-')}", ""]
    lines += [f"- Journey {j.get('n')}: {'pass' if j.get('pass') else 'FAIL'}: {j.get('evidence', '')}"
              for j in result["journeys"]] or [f"- {result.get('reason', 'no journeys reported')}"]
    record_path(cwd, branch).with_suffix(".md").write_text("\n".join(lines) + "\n")
    return result


def result_at(cwd: str, branch: str, sha: str) -> dict | None:
    """The branch's verification when it was made at this sha (a newer head needs a new one)."""
    try:
        rec = json.loads(record_path(cwd, branch).read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return rec if rec.get("sha") == sha else None


def calibrate(cwd: str, base: str, fault_branch: str) -> dict:
    """Trust the verifier only after it passes the base and fails a deliberate fault."""
    settings, root = load_settings(cwd), main_root(cwd)
    if git(["rev-parse", f"{base}^{{commit}}"], root) == git(["rev-parse", f"{fault_branch}^{{commit}}"], root):
        raise SysopError(f"{fault_branch} is the same commit as {base}: commit the deliberate fault on it first")
    good = verify_ref(cwd, base, "calibrate-base")
    bad = verify_ref(cwd, fault_branch, "calibrate-fault")
    if good["verdict"] != "PASS" or bad["verdict"] != "FAIL":
        half = "the base did not pass" if good["verdict"] != "PASS" else "the fault was not caught"
        raise SysopError(f"calibration failed: {half} (base {good['verdict']}, fault {bad['verdict']}); "
                         "sharpen the journeys or the fault, then calibrate again")
    record = {"at": time.time(), "base_sha": good["sha"], "fault_branch": fault_branch, "fault_sha": bad["sha"],
              "base_verdict": good["verdict"], "fault_verdict": bad["verdict"],
              "fault_evidence": [j for j in bad["journeys"] if not j.get("pass")],
              "verifier": [good.get("verifier"), bad.get("verifier")],
              "journeys_sha256": journeys_hash(journeys_path(cwd, settings))}
    path = calibration_path(cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, record)
    return record


def gate(cwd: str, branch: str, sha: str) -> None:
    """With `start` set, landing needs a fresh calibration and a PASS at the sealed sha."""
    settings = load_settings(cwd)
    if not enabled(settings):
        return
    current = journeys_hash(journeys_path(cwd, settings))
    try:
        cal = json.loads(calibration_path(cwd).read_text())
    except (OSError, json.JSONDecodeError):
        raise SysopError(f"refusing to land {branch}: the verifier is not calibrated yet. Run "
                         "`sysop.py verify calibrate --fault-branch <a branch with a deliberate fault>` once") from None
    if cal.get("journeys_sha256") != current:
        raise SysopError(f"refusing to land {branch}: the journeys changed since the verifier was calibrated; "
                         "calibrate again (`sysop.py verify calibrate …`)")
    try:
        rec = json.loads(record_path(cwd, branch).read_text())
    except (OSError, json.JSONDecodeError):
        raise SysopError(f"refusing to land {branch}: not verified yet. Run `sysop.py verify run {branch}`") from None
    if rec.get("sha") != sha:
        raise SysopError(f"refusing to land {branch}: its verification is for {str(rec.get('sha'))[:12]}, "
                         f"not the reviewed {sha[:12]}; verify again")
    if rec.get("verdict") != "PASS":
        failing = [str(j.get("n")) for j in rec.get("journeys", []) if not j.get("pass")]
        raise SysopError(f"refusing to land {branch}: the verifier failed journey(s) {', '.join(failing) or '-'} "
                         f"({rec.get('reason') or 'see the .verify.md report'}); fix, re-review, verify again")


def status(cwd: str) -> dict:
    settings = load_settings(cwd)
    try:
        cal = json.loads(calibration_path(cwd).read_text())
    except (OSError, json.JSONDecodeError):
        cal = None
    fresh = bool(cal) and cal.get("journeys_sha256") == journeys_hash(journeys_path(cwd, settings))
    runs = sorted((ensure_store(cwd) / "reports").glob("*.verify.json"))
    return {"enabled": enabled(settings), "start": settings.get("start") or None,
            "journeys": str(journeys_path(cwd, settings)), "calibrated": fresh,
            "calibration": cal, "verified": {p.name[:-len(".verify.json")]: json.loads(p.read_text()).get("verdict")
                                             for p in runs}}


def cmd_verify(args: argparse.Namespace) -> int:
    if args.action == "run":
        out = run_for_branch(args.cwd, args.branch, args.author_family)
    elif args.action == "calibrate":
        base = args.base or load_settings(args.cwd).get("base") or git(["rev-parse", "--abbrev-ref", "HEAD"],
                                                                       main_root(args.cwd))
        out = calibrate(args.cwd, base, args.fault_branch)
    else:
        out = status(args.cwd)
    print(json.dumps(out, indent=2))
    return 0 if args.action != "run" or out["verdict"] == "PASS" else 1
