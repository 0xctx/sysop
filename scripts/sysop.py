#!/usr/bin/env python3
"""sysop mechanics: the command line over scripts/{store,workers,land,fleet}.py. Standard library only.

Every exact, repeatable step sysop needs lives behind this CLI; skills and agents own judgment.
`sysop.py --help` lists the everyday commands, `--help-all` every one; `sysop.py <subcommand> --help` gives
each one's contract.
`--cwd DIR` (before or after the subcommand) acts from DIR.

Exit codes: 0 ok, 1 refusal or error (stderr says why), 3 no worker available (route),
124 wait timed out with workers still running.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import health  # scripts/health.py: status and doctor
import kaggle_jobs  # scripts/kaggle_jobs.py: finite private Kaggle notebooks (batch inference, ML scripts)
import kaggle_serve  # scripts/kaggle_serve.py: one model served on Kaggle for a work session (opt-in)
import lint  # scripts/lint.py: the craft gate (pure); publish runs it on the export
import presets  # scripts/presets.py: named model setups (preset list/show/diff/use/save)
import prose_check  # scripts/prose_check.py: the writing gate (pure)

from store import (
    active_runs, find_repo, main_root, registered_repos, cmd_config, cmd_settings, cmd_store, ensure_store, git, load_settings, roster_path,
    settings_path, SysopError,
)
from workers import (
    cmd_cooldown, cmd_launch, cmd_models, cmd_roster, cmd_route, cmd_wait, cmd_xreview,
)
from land import cmd_cleanup, cmd_land, cmd_pr, cmd_report, cmd_review, REPORT_TEMPLATES
from fleet import (
    cmd_explain, cmd_fallback, cmd_halt, cmd_ledger_add, cmd_ledger_summary, cmd_next, cmd_notify, cmd_race,
    cmd_run, cmd_ticket, cmd_wake,
)
from verify import cmd_verify

E2E_DIR = Path(__file__).resolve().parent.parent / "tests" / "e2e"
HIDDEN = ("e2e", "publish", "explain")  # developer commands and old aliases: they work, but no help offers them
EVERYDAY = ("status", "doctor", "preset", "lane", "models", "config", "halt", "unhalt", "serve", "kaggle")


def cmd_e2e(args: argparse.Namespace) -> int:
    """Developers only (not in --help): run end-to-end scripts one at a time, each bounded, then a table.
    They spend real tokens; `all` means every script in tests/e2e/."""
    import re  # noqa: PLC0415
    import subprocess  # noqa: PLC0415
    import tempfile  # noqa: PLC0415
    import time  # noqa: PLC0415
    names = sorted(p.stem for p in E2E_DIR.glob("*.sh") if p.stem != "lib") if args.names == ["all"] else args.names
    root = Path(tempfile.mkdtemp(prefix="sysop-e2e-"))
    rows = []
    for name in names:
        script = E2E_DIR / f"{name}.sh"
        if not script.is_file():
            rows.append((name, "missing", "-", "-"))
            continue
        log, start = root / f"{name}.log", time.time()
        with log.open("w") as fh:
            try:
                code = subprocess.run(["bash", str(script)], stdout=fh, stderr=subprocess.STDOUT, timeout=args.timeout,
                                      env={**os.environ, "E2E_ROOT": str(root / name)}).returncode
            except subprocess.TimeoutExpired:
                code = 124
        text = log.read_text(errors="replace")
        fails = re.findall(r"== \S+: (\d+) failure", text)
        cost = sum(float(c) for c in re.findall(r"cost: \$([0-9.]+)", text))
        verdict = "PASS" if code == 0 else ("TIMEOUT" if code == 124 else f"FAIL ({fails[-1] if fails else '?'})")
        rows.append((name, verdict, f"{time.time() - start:.0f}s", f"${cost:.2f}"))
        print(f"{name}: {verdict}", file=sys.stderr, flush=True)
    width = max([len(r[0]) for r in rows] + [6])
    print(f"{'script':<{width}}  result       time    cost")
    for name, verdict, secs, cost in rows:
        print(f"{name:<{width}}  {verdict:<11}  {secs:>6}  {cost:>6}")
    print(f"logs: {root}")
    return 0 if all(r[1] == "PASS" for r in rows) else 1


# The build's paperwork, not the product: milestone plans, reports and reviews (.claude/), the build log, the
# design history and a dated quota snapshot. The export stands on its own skills, docs and tests.
PUBLISH_EXCLUDE = (".claude/", "docs/history/", "docs/build-log.md/", "docs/preset-matrix.md/")
SECRET_PATTERNS = (r"sk-ant-[A-Za-z0-9_-]{10,}", r"AIza[0-9A-Za-z_-]{20,}", r"gh[pousr]_[0-9A-Za-z]{20,}",
                   r"-----BEGIN [A-Z ]*PRIVATE KEY-----", r"\b[a-z]+-[a-z]+-[a-z]+\.ngrok-free\.dev\b",
                   r"xox[baprs]-[0-9A-Za-z-]{10,}")


def publish(cwd: str, out: str) -> dict:
    """A fresh, squashed export of the committed tree: no build paperwork, then a scan for anything personal
    (this machine's home path, the git email, a personal ngrok domain, key-shaped strings). Never pushes."""
    import io  # noqa: PLC0415
    import re  # noqa: PLC0415
    import subprocess  # noqa: PLC0415
    import tarfile  # noqa: PLC0415
    from store import git, main_root  # noqa: PLC0415
    root, dest = main_root(cwd), Path(out).resolve()
    if dest.exists() and any(dest.iterdir()):
        raise SysopError(f"{dest} is not empty: publish writes a fresh export")
    tar = subprocess.run(["git", "archive", "--format=tar", "HEAD"], cwd=str(root), capture_output=True, check=True)
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(tar.stdout)) as archive:
        members = [m for m in archive.getmembers() if not (m.name + "/").startswith(PUBLISH_EXCLUDE)]
        archive.extractall(dest, members=members, filter="data")
    personal = [re.escape(os.path.expanduser("~")) + "/"]
    email = subprocess.run(["git", "config", "user.email"], cwd=str(root), capture_output=True, text=True).stdout.strip()
    if email and not email.endswith(("@example.com", "users.noreply.github.com")):
        personal.append(re.escape(email))
    pattern = re.compile("|".join([*personal, *SECRET_PATTERNS]))
    findings = []
    for path in sorted(p for p in dest.rglob("*") if p.is_file()):
        for n, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            hit = pattern.search(line)
            if hit:
                findings.append(f"{path.relative_to(dest)}:{n}: {hit.group(0)[:40]}")
    for path in sorted(dest.rglob("*.md")):  # nothing exported may point at what was left out, or at a missing file
        findings += [f"{f['file']}:{f['line']}: {f['issue']}" for f in prose_check.check_file(path, dest)[0]
                     if f["issue"].startswith("broken link")]
        for n, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            if re.search(r"docs/history/|build-log\.md|preset-matrix\.md|\.claude/(plans|reports|handoff)", line):
                findings.append(f"{path.relative_to(dest)}:{n}: names a file the export leaves out")
    findings += [f"lint: {e}" for e in lint.collect(dest)[0].errors]  # the export must pass the gate it ships with
    if findings:
        raise SysopError("refusing to publish: personal or secret strings, broken links or lint failures in the export (fix them "
                         "in the repo, commit, publish again):\n  " + "\n  ".join(findings[:50]))
    git(["init", "-q", "-b", "main"], dest)
    for key in ("user.name", "user.email"):  # the identity this repo commits under is the one it publishes under
        value = subprocess.run(["git", "config", key], cwd=str(root), capture_output=True, text=True).stdout.strip()
        if value:
            git(["config", key, value], dest)
    git(["add", "-A"], dest)
    version = json.loads((dest / ".claude-plugin" / "plugin.json").read_text()).get("version", "")
    git(["commit", "-q", "-m", f"sysop {version}".strip()], dest)
    dirty = bool(git(["status", "--porcelain"], root).strip())
    return {"out": str(dest), "files": len(members), "commit": git(["rev-parse", "HEAD"], dest),
            "excluded": list(PUBLISH_EXCLUDE), **({"note": "uncommitted changes were not exported"} if dirty else {}),
            "next": f"create the GitHub repo, then: git -C {dest} remote add origin <url> && "
                    f"git -C {dest} push -u origin main"}


def cmd_publish(args: argparse.Namespace) -> int:
    print(json.dumps(publish(args.cwd, args.out), indent=2))
    return 0


def cmd_profile(args: argparse.Namespace) -> int:
    import advice  # noqa: PLC0415 - the opt-in layer loads only for the commands that use it
    return advice.cmd_profile(args)


def cmd_prose_check(args: argparse.Namespace) -> int:
    try:
        root = Path(git(["rev-parse", "--show-toplevel"], args.cwd))
    except SysopError:
        root = Path(args.cwd)
    result = prose_check.check(root, args.paths, args.words)
    print(json.dumps(result, indent=2) if args.json else prose_check.render(result))
    return 1 if result["errors"] else 0


def cmd_kaggle(args: argparse.Namespace) -> int:
    if args.action == "models":  # no store needed: the batch skill injects this anywhere
        for mid, m in kaggle_jobs.models().items():
            speed = f"{m['tok_s']} tok/s" if m.get("tok_s") else "speed not measured yet"
            print(f"{mid:<20} {m['repo']}  ({m['license']}; {speed})")
        return 0
    store = ensure_store(args.cwd)
    try:
        if args.action == "run":
            rec = kaggle_jobs.run(Path(os.path.join(args.cwd, args.spec)), store)
            rec["quota"] = kaggle_jobs.quota()
            print(json.dumps(rec, indent=2))
            return 0
        if args.action == "status":
            print(json.dumps(kaggle_jobs.status(store, args.name), indent=2))
            return 0
        if args.action == "wait":
            st = kaggle_jobs.wait(store, args.name, args.timeout, args.poll)
            print(json.dumps(st, indent=2))
            return 0 if st["state"] in kaggle_jobs.TERMINAL else 124
        summary = kaggle_jobs.pull(store, args.name)
        summary["quota"] = kaggle_jobs.quota()
        print(json.dumps(summary, indent=2))
        return 0
    except kaggle_jobs.JobError as exc:
        raise SysopError(str(exc)) from exc


def cmd_serve(args: argparse.Namespace) -> int:
    settings = load_settings(args.cwd)
    try:
        if args.action == "list":
            print("\n".join(kaggle_serve.listing(settings)))
            return 0
        if args.action == "token":
            out = kaggle_serve.token()
        elif args.action == "up":
            try:  # the run that starts a session ends it at `run end`
                runs = active_runs(args.cwd, os.environ.get("CLAUDE_CODE_SESSION_ID"))
            except SysopError:
                runs = []  # outside git: no runs
            out = kaggle_serve.up(settings, args.model, runs[0][1]["slug"] if len(runs) == 1 else None)
            out["quota"] = kaggle_jobs.quota()
        elif args.action == "status":
            out = kaggle_serve.status(settings)
        elif args.action == "down":
            out = kaggle_serve.down()
        else:  # add
            out = kaggle_serve.add(settings_path(args.cwd, args.scope), args.repo, args.id)
        if args.action in ("up", "status", "down"):
            roster_path().unlink(missing_ok=True)  # pi's model list changed with its kaggle provider
        print(json.dumps(out, indent=2))
        return 0
    except kaggle_jobs.JobError as exc:
        raise SysopError(str(exc)) from exc


def build_parser(everyday: bool = True) -> argparse.ArgumentParser:
    """`everyday` keeps --help to the commands a person types; skills' plumbing shows under --help-all."""
    parser = argparse.ArgumentParser(
        prog="sysop.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Every command, plumbing included: sysop.py --help-all" if everyday else None)
    parser.add_argument("--help-all", action="store_true", help="list every command, plumbing included")
    parser.add_argument("--cwd", default=os.getcwd(), help="directory to act from (default: cwd)")
    parser.add_argument("--lane", help="act for this lane: its preset, parallel cap and landing authority")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("store", help="print the shared artifact store path, creating it")
    p.add_argument("--soft", action="store_true", help="outside git, say so and exit 0")
    p.set_defaults(func=cmd_store)

    p = sub.add_parser("settings", help="print merged settings as JSON")
    p.add_argument("--key", help="print only this top-level key")
    p.set_defaults(func=cmd_settings)

    p = sub.add_parser("roster", help="available workers and models (cached 24h)")
    p.add_argument("--refresh", action="store_true", help="ignore the cache")
    p.set_defaults(func=cmd_roster)

    presets.add_parser(sub)
    presets.add_lane_parser(sub)
    health.add_parsers(sub)

    p = sub.add_parser("route", help="first available worker:model for a size or tier")
    p.add_argument("name", help="size (small|large|risky) or tier (fast|standard|deep)")
    p.add_argument("--role", choices=["build", "review"], default="build")
    p.add_argument("--exclude", action="append", default=[], metavar="W[:M]",
                   help="skip this worker:model (or every model of a worker); repeatable")
    p.add_argument("--not-family", help="skip models of this family (cross-family review)")
    p.add_argument("--kind", help="task kind (docs, tests, security, …); a configured kind beats size")
    p.add_argument("--prompt-file", help="also emit the command line that runs this prompt")
    p.add_argument("--ticket", help="route for this ticket: its lane's models (size and kind still given)")
    p.set_defaults(func=cmd_route)

    p = sub.add_parser("pr", help="the GitHub path: open, canonical review comment, bounded CI wait, guarded merge")
    psub = p.add_subparsers(dest="action", required=True)
    k = psub.add_parser("open", help="push and open (or reuse) the PR for the current branch; or local-only: <reason>")
    k.add_argument("--base")
    k.add_argument("--title")
    k.add_argument("--body-file")
    k.add_argument("--draft", action="store_true")
    k = psub.add_parser("comment", help="create or edit the one canonical review comment")
    k.add_argument("number", type=int)
    k.add_argument("--report", required=True)
    k.add_argument("--slug", required=True)
    k = psub.add_parser("feedback", help="human reviews and comments on a PR (sysop's own comment left out)")
    k.add_argument("number", type=int)
    k = psub.add_parser("checks", help="wait for CI (bounded): exit 0 green/none, 1 red, 124 pending")
    k.add_argument("number", type=int)
    k.add_argument("--timeout", type=float, default=540)
    k.add_argument("--poll", type=float, default=20)
    k = psub.add_parser("merge", help="merge under the land gates (READY, cross-family, green CI, authority)")
    k.add_argument("number", type=int)
    k.add_argument("--branch", required=True)
    k.add_argument("--review")
    k.add_argument("--land", action="store_true", help="the operator's authority for this merge")
    p.set_defaults(func=cmd_pr)

    p = sub.add_parser("kaggle", help="batch jobs on Kaggle: finite private notebooks (inference, ML scripts)")
    ksub = p.add_subparsers(dest="action", required=True)
    k = ksub.add_parser("run", help="upload inputs, push the notebook, return at once")
    k.add_argument("spec", help="job spec JSON (name, kind infer|script, model, inputs, max_minutes, params)")
    for action in ("status", "pull"):
        k = ksub.add_parser(action)
        k.add_argument("name")
    k = ksub.add_parser("wait", help="poll until the job ends or the timeout passes (exit 124)")
    k.add_argument("name")
    k.add_argument("--timeout", type=float, default=540)
    k.add_argument("--poll", type=float, default=30)
    ksub.add_parser("models", help="models for batch inference, with measured speed")
    p.set_defaults(func=cmd_kaggle)

    p = sub.add_parser("race", help="several vendors build one ticket in parallel; the first green+READY lands")
    rsub2 = p.add_subparsers(dest="action", required=True)
    k = rsub2.add_parser("start")
    k.add_argument("ticket")
    k.add_argument("--n", type=int, default=2)
    k.add_argument("--size", default="small")
    k.add_argument("--kind")
    k.add_argument("--prompt-file", required=True)
    k.add_argument("--base")
    k = rsub2.add_parser("status")
    k.add_argument("ticket")
    k = rsub2.add_parser("settle", help="keep the winner; archive losers under refs/sysop/race/ (nothing lost)")
    k.add_argument("ticket")
    k.add_argument("--winner", required=True)
    p.set_defaults(func=cmd_race)

    p = sub.add_parser("prose-check", help="the writing gate: broken links, unclosed fences, markers, word count")
    p.add_argument("paths", nargs="*", help="files or folders (default: every tracked prose file)")
    p.add_argument("--words", type=int, help="a word-count target to report progress against")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_prose_check)

    p = sub.add_parser("notify", help="send the operator a notification (setting `notify`); never fails")
    p.add_argument("message")
    p.add_argument("--title", default="sysop")
    p.set_defaults(func=cmd_notify)

    p = sub.add_parser("serve", help="opt-in: one model on Kaggle's GPUs for this work session (pi:kaggle/<model>)")
    ssub = p.add_subparsers(dest="action", required=True)
    s = ssub.add_parser("up", help="start the session and wait until it answers (writes pi's kaggle provider)")
    s.add_argument("model", nargs="?", help="a serve model id (default: serve.model)")
    ssub.add_parser("status", help="health, idle time and time left")
    ssub.add_parser("down", help="stop the session now (it also stops itself when idle)")
    ssub.add_parser("list", help="models that can be served, with measured speed")
    ssub.add_parser("token", help="upload NGROK_AUTHTOKEN once, as a private Kaggle dataset")
    s = ssub.add_parser("add", help="add a Hugging Face GGUF repo that fits 2xT4")
    s.add_argument("repo")
    s.add_argument("--id")
    s.add_argument("--scope", choices=("user", "repo"), default="user")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("profile", help="what this repository is: code, prose or mixed; languages; size")
    p.add_argument("--json", action="store_true")
    p.add_argument("--refresh", action="store_true")
    p.set_defaults(func=cmd_profile)

    p = sub.add_parser("launch", help="run a worker detached; prints its run record")
    p.add_argument("ticket")
    p.add_argument("workdir", help="directory the worker runs in (its worktree)")
    p.add_argument("cmd", nargs="?", help="a shell command line (custom work); workers use --worker + --prompt-file")
    p.add_argument("--worker", help="worker:model[@effort] this attempt runs (recorded for the ledger)")
    p.add_argument("--prompt-file", help="the brief: sysop builds the worker's command from --worker")
    p.add_argument("--role", choices=["build", "review"], default="build")
    p.set_defaults(func=cmd_launch)

    p = sub.add_parser("wait", help="block until tickets finish or the timeout passes")
    p.add_argument("tickets", nargs="+", help="ticket ids; <repo>:<ticket> for another repo's")
    p.add_argument("--timeout", type=float, default=540, help="seconds (default 540)")
    p.add_argument("--poll", type=float, default=2.0)
    p.add_argument("--any", action="store_true", help="return when the first one finishes")
    p.set_defaults(func=cmd_wait)

    p = sub.add_parser("land", help="rebase, re-check, fast-forward, clean up a reviewed branch")
    p.add_argument("branch", nargs="?",
                   help="sysop/<ticket> (in whichever registered repo holds it) or <repo>:sysop/<ticket>; "
                        "left out: every ready ticket, in dependency order (your word for all of them)")
    p.add_argument("--ready", action="store_true", help="land every ready ticket (the default with no branch)")
    p.add_argument("--all", action="store_true", help="with --ready: in every repo sysop knows")
    p.add_argument("--base", help="default: settings.base, else the main checkout's branch")
    p.add_argument("--review", help="review report (default <store>/reports/<branch>.review.md)")
    p.add_argument("--check", help="check command (default: settings.check)")
    p.add_argument("--no-check", action="store_true", help="land with no check configured")
    p.add_argument("--land", action="store_true", help="actually fast-forward (else: dry run)")
    p.add_argument("--push", action="store_true", help="push the base to origin after landing")
    p.set_defaults(func=cmd_land)

    p = sub.add_parser("run", help="mark a multi-step run active / advance / end it")
    rsub = p.add_subparsers(dest="action", required=True)
    r = rsub.add_parser("start")
    r.add_argument("slug")
    r.add_argument("--session", help="the session id (skills pass ${CLAUDE_SESSION_ID})")
    r.add_argument("--steps", type=int, default=10)
    r.add_argument("--all", action="store_true", help="the run covers every repo (`next --all`); wake-ups follow it")
    r = rsub.add_parser("step")
    r.add_argument("slug")
    r.add_argument("n", type=int)
    r.add_argument("--note")
    r = rsub.add_parser("pause", help="keep the run resumable but let this turn end (a /loop tick)")
    r.add_argument("slug")
    r = rsub.add_parser("resume", help="un-pause and bind the run to this session")
    r.add_argument("slug")
    r.add_argument("--session", help="the session id (skills pass ${CLAUDE_SESSION_ID})")
    r = rsub.add_parser("end")
    r.add_argument("slug")
    r.add_argument("--outcome", required=True,
                   choices=["landed", "ready", "blocked", "abandoned", "done"])
    r.add_argument("--reason")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("cooldown", help="mark a worker or worker:model unavailable for a while")
    csub = p.add_subparsers(dest="action", required=True)
    c = csub.add_parser("add")
    c.add_argument("entry", help="worker, worker:model, or worker:provider/ (every model of a pi provider)")
    c.add_argument("--seconds", type=int, required=True, help="from the failure's reset time")
    c.add_argument("--reason", required=True)
    c = csub.add_parser("clear")
    c.add_argument("entry")
    csub.add_parser("list")
    p.set_defaults(func=cmd_cooldown)

    p = sub.add_parser("verify", help="opt-in runtime verifier: drive the running app through its journeys")
    vsub = p.add_subparsers(dest="action", required=True)
    k = vsub.add_parser("run", help="verify a reviewed branch at its sealed commit")
    k.add_argument("branch")
    k.add_argument("--author-family", help="the builder's vendor or model; the verifier is another one")
    k = vsub.add_parser("calibrate", help="trust the verifier: the base must pass and a deliberate fault must fail")
    k.add_argument("--fault-branch", required=True)
    k.add_argument("--base")
    vsub.add_parser("status", help="settings, calibration and the last verification per branch")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("e2e")  # developers only: kept out of --help (it spends real tokens)
    p.add_argument("names", nargs="+", help="script names in tests/e2e, or `all`")
    p.add_argument("--timeout", type=int, default=3600, help="seconds per script")
    p.set_defaults(func=cmd_e2e)

    p = sub.add_parser("publish")  # developers only: a squashed, scanned export for a public repo
    p.add_argument("out", help="an empty or new directory")
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser("explain")  # an alias of `session`'s explain field, kept for one release
    p.set_defaults(func=cmd_explain)

    p = sub.add_parser("wake", help="the mod's question: does this session's fleet run need its coordinator now?")
    p.add_argument("--session", required=True)
    p.set_defaults(func=cmd_wake)

    p = sub.add_parser("next", help="the fleet's due actions as JSON (launch, wait, check, review, land, …)")
    p.add_argument("--all", action="store_true", help="every lane in every repo sysop knows, under one cap")
    p.set_defaults(func=cmd_next)

    p = sub.add_parser("halt", help="stop launching new fleet work (running work finishes)")
    p.add_argument("--reason")
    p.set_defaults(func=cmd_halt)
    p = sub.add_parser("unhalt", help="let the fleet launch again")
    p.set_defaults(func=cmd_halt, reason=None)

    p = sub.add_parser("ticket", help="fleet tickets: add, set status/worker, log, show")
    tsub = p.add_subparsers(dest="action", required=True)
    t = tsub.add_parser("add")
    t.add_argument("id")
    t.add_argument("--title", required=True)
    t.add_argument("--size", choices=["small", "large", "risky"], required=True)
    t.add_argument("--depends", help="comma-separated ticket ids")
    t.add_argument("--kind", help="task kind; routes by `kinds` when configured")
    t.add_argument("--request-file", help="the operator's words for this ticket, verbatim")
    t.add_argument("--contract-file", help="the six-element contract")
    t.add_argument("--base")
    t.add_argument("--authority", help='e.g. "--land given" or "standing land: true"')
    t = tsub.add_parser("set")
    t.add_argument("id")
    t.add_argument("--status")
    t.add_argument("--worker", help="worker:model")
    t.add_argument("--stacked-on", help="the dependency whose sealed branch this ticket starts from")
    t.add_argument("--fork", help="that dependency's sealed sha (the launch action's `fork`)")
    t.add_argument("--pr", type=int, help="the pull request a PR lane's ticket opened")
    t = tsub.add_parser("import", help="merge slices JSON (from the slice skill) into the tickets, by id")
    t.add_argument("file")
    t.add_argument("--skeleton", choices=["auto", "never"], default="auto",
                   help="auto: a repo without a check gets ticket t0-skeleton first")
    t.add_argument("--base")
    t.add_argument("--authority")
    t = tsub.add_parser("log")
    t.add_argument("id")
    t.add_argument("message")
    t = tsub.add_parser("show")
    t.add_argument("id", nargs="?", default=None)
    p.set_defaults(func=cmd_ticket)

    p = sub.add_parser("config", help="set or get one settings key")
    csub = p.add_subparsers(dest="action", required=True)
    c = csub.add_parser("get")
    c.add_argument("key")
    c = csub.add_parser("set")
    c.add_argument("key")
    c.add_argument("value", help="JSON (true, 600, \"x\") or a bare string")
    c.add_argument("--scope", choices=["user", "repo", "local"], default="repo",
                   help="repo: committed with the code; local: this machine only, never dirties the checkout")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("models", help="show or change which models each size/tier uses")
    msub = p.add_subparsers(dest="action")
    m = msub.add_parser("set", help="replace a tier's ordered fallback chain")
    m.add_argument("tier")
    m.add_argument("entries", nargs="+", metavar="WORKER:MODEL")
    m.add_argument("--scope", choices=["user", "repo"], default="user")
    m = msub.add_parser("route", help="send a size or task kind to a tier, or to auto:<skill>")
    m.add_argument("key", help="small | large | risky | a task kind (writing, tests, security, …)")
    m.add_argument("target", help="a tier name or auto:<skill> (code, writing, reasoning, review, long)")
    m.add_argument("--scope", choices=["user", "repo"], default="user")
    m = msub.add_parser("available", help="models each installed CLI offers, with catalog skills")
    m.add_argument("search", nargs="?")
    m = msub.add_parser("advise", help="newer and better models you can run, per tier and kind (suggestions)")
    m.add_argument("target", nargs="?", help="one tier (default: all tiers and routed kinds)")
    m.add_argument("--refresh", action="store_true", help="fetch model facts and benchmarks now")
    m.add_argument("--json", action="store_true")
    m = msub.add_parser("learn", help="score changes from this repo's outcomes (applies with --apply or learn: true)")
    m.add_argument("--apply", action="store_true")
    m = msub.add_parser("check", help="preflight: a 1-token probe per entry; failures cool down")
    m.add_argument("tier", nargs="?", help="a tier, or all (default)")
    m = msub.add_parser("agent", help="model for a sysop agent (or 'default'): inherit|haiku|sonnet|opus")
    m.add_argument("name", help="e.g. default, sysop:code-reviewer, sysop:explorer")
    m.add_argument("model")
    m.add_argument("--scope", choices=["user", "repo"], default="user")
    p.set_defaults(func=cmd_models, action=None)

    p = sub.add_parser("fallback", help="availability failure: cooldown, ticket log, route past tried workers")
    p.add_argument("ticket")
    p.add_argument("--from", dest="from_", required=True, metavar="WORKER:MODEL")
    p.add_argument("--reason", required=True)
    p.add_argument("--size", required=True)
    p.add_argument("--prompt-file")
    p.add_argument("--cooldown-seconds", type=int,
                   help="until the failure's stated reset time (default 300; 0 for none)")
    p.add_argument("--whole-worker", action="store_true", help="the whole CLI is unavailable")
    p.add_argument("--kind", help="the ticket's task kind, so fallback stays in the same tier")
    p.set_defaults(func=cmd_fallback)

    p = sub.add_parser("xreview", help="cross-family review run by a script: start / collect")
    xsub = p.add_subparsers(dest="action", required=True)
    x = xsub.add_parser("start")
    x.add_argument("branch")
    x.add_argument("--base", required=True)
    x.add_argument("--author-family", required=True,
                   help="the author's vendor, or a model id/alias (claude, gemini, …) to derive it")
    x.add_argument("--context", action="append", default=[], help="contract/plan/request files")
    x.add_argument("--exclude", action="append", default=[])
    x.add_argument("--workdir", help="where the reviewer runs (default: the main checkout)")
    x.add_argument("--risky", action="store_true",
                   help="risky work: `xreview_risky` reviewers (default 2), each from a different vendor")
    x = xsub.add_parser("collect")
    x.add_argument("branch")
    x.add_argument("--timeout", type=float, default=540)
    p.set_defaults(func=cmd_xreview)

    p = sub.add_parser("cleanup", help="remove a sysop worktree (caches cleared, work never deleted)")
    p.add_argument("worktree")
    p.set_defaults(func=cmd_cleanup)

    p = sub.add_parser("review", help="seal a review to the commit it read; file out-of-scope discoveries")
    rsub = p.add_subparsers(dest="action", required=True)
    k = rsub.add_parser("seal", help="bind the review report to the branch head (review's last step)")
    k.add_argument("branch")
    k.add_argument("--review", help="report path (default: <store>/reports/<slug>.review.md)")
    k.add_argument("--sha", help="the commit the review read (step 1's pin); refused if the branch moved")
    k.add_argument("--re-review", action="store_true",
                   help="light re-review of a correction: the old seal must be an ancestor of the head")
    k = rsub.add_parser("discovery", help="append a real out-of-scope defect to discoveries.md, once per file+cause")
    k.add_argument("file")
    k.add_argument("cause")
    k.add_argument("summary")
    k.add_argument("--ticket")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("report", help="create a plan/report from its template in the store")
    p.add_argument("kind", choices=sorted(REPORT_TEMPLATES))
    p.add_argument("slug")
    p.add_argument("--fresh", action="store_true", help="overwrite an existing one")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("ledger", help="per-ticket outcome ledger")
    lsub = p.add_subparsers(dest="ledger_command", required=True)
    a = lsub.add_parser("add", help="append one finished ticket")
    a.add_argument("--ticket", required=True)
    for flag in ("--size", "--kind", "--tier", "--worker", "--model", "--review", "--note"):
        a.add_argument(flag)
    a.add_argument("--fallbacks", type=int, help="default: derived from launch records, else 0")
    a.add_argument("--check", choices=["pass", "fail", "none"])
    a.add_argument("--landed", choices=["yes", "no"], default="no")
    a.add_argument("--seconds", type=float)
    a.set_defaults(func=cmd_ledger_add)
    sm = lsub.add_parser("summary", help="aggregate per worker:model")
    sm.add_argument("--json", action="store_true")
    sm.set_defaults(func=cmd_ledger_summary)
    shown = [n for n in sub.choices if n not in HIDDEN and (not everyday or n in EVERYDAY)]
    if everyday:
        shown.sort(key=EVERYDAY.index)
    sub._choices_actions = sorted((a for a in sub._choices_actions if a.dest in shown), key=lambda a: shown.index(a.dest))
    sub.metavar = "{" + ",".join(shown) + "}"
    return parser


def hoist_cwd(argv: list[str]) -> list[str]:
    """Accept `--cwd DIR` and `--lane NAME` after the subcommand too: move each to the front."""
    for flag in ("--lane", "--cwd"):
        for i, arg in enumerate(argv):
            if arg == "--":
                break
            if arg.startswith(flag + "="):
                argv = [arg, *argv[:i], *argv[i + 1:]]
                break
            if arg == flag and i + 1 < len(argv):
                argv = [*argv[i:i + 2], *argv[:i], *argv[i + 2:]]
                break
    return argv


def ticket_lane(args: argparse.Namespace) -> str | None:
    """A command about a ticket (or its sysop/<ticket> branch) acts in that ticket's lane by itself, so a
    forgotten --lane can never route a lane's ticket on the repo's default models."""
    names = [getattr(args, "ticket", None), getattr(args, "id", None)]
    branch = getattr(args, "branch", None)
    if isinstance(branch, str) and branch.startswith("sysop/"):
        names.append(branch[len("sysop/"):])
    try:
        path = main_root(args.cwd) / ".sysop" / "tickets.json"
        tickets = json.loads(path.read_text())["tickets"] if path.is_file() else {}
    except (SysopError, OSError, ValueError, KeyError):
        return None
    for name in names:
        if isinstance(name, str) and name in tickets and tickets[name].get("lane"):
            return tickets[name]["lane"]
    return None


def ticket_home(args: argparse.Namespace) -> str | None:
    """`land` of a ticket this repo does not hold acts in the registered repo that does: `<repo>:sysop/<t>`
    names it, and a bare `sysop/<t>` held by exactly one registered repo finds it (the board's Land button)."""
    branch = getattr(args, "branch", None)
    if args.command != "land" or not isinstance(branch, str):
        return None
    repo, _, branch = branch.rpartition(":")
    args.branch = branch
    if repo:
        home = find_repo(repo)
        if not home:
            raise SysopError(f"no single registered repo is called {repo!r}; `sysop.py status --all` lists them")
        return home
    if not branch.startswith("sysop/"):
        return None
    ticket = branch[len("sysop/"):]

    def holds(root: str | Path) -> bool:
        try:
            return ticket in json.loads((Path(root) / ".sysop" / "tickets.json").read_text())["tickets"]
        except (OSError, ValueError, KeyError, TypeError):
            return False
    try:
        if holds(main_root(args.cwd)):
            return None
    except SysopError:
        pass
    homes = [r for r in registered_repos() if holds(r)]
    return homes[0] if len(homes) == 1 else None


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--help-all" in argv:
        build_parser(everyday=False).print_help()
        return 0
    args = build_parser().parse_args(hoist_cwd(argv))
    try:
        args.cwd = ticket_home(args) or args.cwd
    except SysopError as exc:
        print(f"sysop: {exc}", file=sys.stderr)
        return 1
    lane = args.lane or ticket_lane(args)
    if lane:  # every command below reads the lane's settings through load_settings
        os.environ["SYSOP_LANE"] = lane
    try:
        return args.func(args)
    except SysopError as exc:
        print(f"sysop: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
