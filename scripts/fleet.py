"""Fleet: tickets, fallback, run markers, notify, the ledger and race mode."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import kaggle_jobs  # scripts/kaggle_jobs.py: finite private Kaggle notebooks (batch inference, ML scripts)
import kaggle_serve  # scripts/kaggle_serve.py: one model served on Kaggle for a work session (opt-in)

import verify  # opt-in runtime verification (setting `start`)
from land import gh_json, pr_feedback, quote_feedback, seal_path
from store import (
    _now, active_markers, current_lane, ensure_store, find_repo, git, load_settings, load_tickets, main_root,
    registered_repos, run_alive,
    save_tickets, store_path, SysopError, TICKET_RE, write_json,
)
from workers import (
    _attempts, add_cooldown, billed, cool_refused, cool_signin, enforce_deadline, family, KIND_SKILL, launch, ledger_summary,
    load_roster, route, run_state, run_usage, spend_path, stated_reset, strip_ansi, tried_workers, worker_cmd,
)


def cmd_fallback(args: argparse.Namespace) -> int:
    """One step for an availability failure, so no part of it is forgotten."""
    entry = args.from_.split(":")[0] if args.whole_worker else args.from_
    if args.whole_worker and args.from_.startswith("pi:") and "/" in args.from_:
        entry = args.from_.split("/")[0] + "/"  # pi is many providers: one's quota never rests the others
    runs = store_path(args.cwd) / "runs" / args.ticket
    last = max((d for d in runs.glob("*") if d.name.isdigit()), key=lambda d: int(d.name), default=None) \
        if runs.is_dir() else None
    log = strip_ansi((last / "log").read_text(errors="replace")) if last and (last / "log").is_file() else ""
    log = log if len(log) <= 8000 else f"{log[:4000]}\n...\n{log[-4000:]}"  # startup errors sit at the head
    evidence = f"{args.reason}\n{log}"  # the attempt's own log, not only the agent's summary of it
    if args.cooldown_seconds is None:  # a stated reset wins; capacity blips clear fast
        args.cooldown_seconds = stated_reset(evidence) or 300
    if cool_signin(args.from_, evidence) or cool_refused(args.from_, evidence):
        pass  # a missing login, a refused key or a model not enabled: that cooldown (with its fix) covers it
    elif args.cooldown_seconds > 0:
        add_cooldown(entry, args.cooldown_seconds, args.reason)
    settings = load_settings(args.cwd)
    exclude = sorted(set(tried_workers(args.cwd, args.ticket) + [args.from_, entry]))
    pick = route(settings, load_roster(settings, cwd=args.cwd), args.size, "build", exclude, None, args.kind)
    if pick["worker"] and args.prompt_file:
        prompt = os.path.abspath(os.path.join(args.cwd, args.prompt_file))
        pick["cmd"] = worker_cmd(settings, pick["worker"], "build", pick["model"], prompt,
                                 pick["effort"])
    nxt = f"{pick['worker']}:{pick['model']}" if pick["worker"] else "none left (blocked)"
    tickets = load_tickets(args.cwd)["tickets"] if (store_path(args.cwd) / "tickets.json").is_file() else {}
    if args.ticket in tickets:
        args.action, args.message, args.id = "log", (
            f"fallback {args.from_} -> {nxt}: {args.reason}"
            + (f" (cooldown {entry} {args.cooldown_seconds}s)" if args.cooldown_seconds > 0 else "")), args.ticket
        with open(os.devnull, "w") as quiet:
            stdout, sys.stdout = sys.stdout, quiet
            try:
                cmd_ticket(args)
            finally:
                sys.stdout = stdout
    print(json.dumps(pick, indent=2))
    return 0 if pick["worker"] else 3


TICKET_STATUSES = ("queued", "running", "built", "reviewed", "verified", "landed", "blocked")
LEGACY_STATUS = {"reviewing": "built"}  # records written before the fleet engine


AUTHORITIES = ("none", "--land given", "standing land: true")


def check_authority(cwd: str, authority: str | None) -> None:
    """Landing authority is one of three facts, never free text; a standing grant must really exist."""
    if authority is None:
        return
    if authority not in AUTHORITIES:
        raise SysopError(f"--authority must be one of {', '.join(repr(a) for a in AUTHORITIES)}: "
                         "'--land given' only when the operator's request contains --land")
    if authority == "standing land: true" and load_settings(cwd).get("land") is not True:
        raise SysopError("--authority 'standing land: true' but the settings do not grant it (land is not true)")


def check_graph(tickets: dict) -> None:
    """Refuse unknown dependencies, self-dependencies and cycles: a ticket graph must be buildable."""
    for tid, t in tickets.items():
        for dep in t["depends"]:
            if dep == tid:
                raise SysopError(f"ticket {tid} depends on itself")
            if ":" in dep:  # another repo's ticket: <repo>:<ticket>
                if external_status(dep) is None:
                    raise SysopError(f"ticket {tid}: no ticket '{dep}' in any repo sysop knows "
                                     "(add it there first; <repo> is the repo's folder name)")
            elif dep not in tickets:
                raise SysopError(f"ticket {tid}: unknown dependency '{dep}'")
    state: dict = {}

    def visit(tid: str, path: list[str]) -> None:
        if state.get(tid) == "done":
            return
        if state.get(tid) == "open":
            loop = path[path.index(tid):] + [tid]
            raise SysopError(f"dependency cycle: {' -> '.join(loop)}")
        state[tid] = "open"
        for dep in tickets[tid]["depends"]:
            if ":" not in dep:  # a cross-repo edge cannot close a loop here: its ticket must land first
                visit(dep, [*path, tid])
        state[tid] = "done"

    for tid in tickets:
        visit(tid, [])


def external_status(dep: str) -> str | None:
    """The status of `<repo>:<ticket>` in its own repo's records; None when either is unknown."""
    repo, _, tid = dep.partition(":")
    root = find_repo(repo)
    if not root:
        return None
    t = load_tickets(root)["tickets"].get(tid)
    return LEGACY_STATUS.get(t["status"], t["status"]) if t else None


def cmd_ticket(args: argparse.Namespace) -> int:
    data = load_tickets(args.cwd)
    tickets = data["tickets"]
    if args.action == "add":
        if not TICKET_RE.match(args.id):
            raise SysopError(f"invalid ticket id '{args.id}'")
        read = lambda f: Path(f).read_text().strip() if f else ""  # noqa: E731
        candidate = {**tickets, args.id: {
            "title": args.title, "size": args.size, "kind": args.kind, "status": "queued",
            "worker": None, "branch": f"sysop/{args.id}", "stacked_on": None, "fork": None, "needs": None,
            "depends": [d for d in (args.depends or "").split(",") if d],
            "request": read(args.request_file) or args.title,
            "contract": read(args.contract_file), "lane": current_lane(), "authority": args.authority,
            "log": [f"{_now()} queued" + (f" in lane {current_lane()}" if current_lane() else "")]}}
        check_graph(candidate)  # refused: nothing is written
        check_authority(args.cwd, args.authority)
        data["tickets"] = tickets = candidate
        if args.base:
            data["base"] = args.base
        if args.authority:
            data["authority"] = args.authority
    elif args.action == "import":
        data, changes = import_slices(args.cwd, args.file, args.skeleton, args.base, args.authority)
        print("\n".join([*render_tree(data["tickets"]),
                         f"{len(data['tickets'])} ticket(s); {', '.join(changes) or 'no changes'}"]))
        return 0
    elif args.action != "show":
        if args.id not in tickets:
            raise SysopError(f"no ticket '{args.id}'")
        t = tickets[args.id]
        if args.action == "set":
            if args.status:
                if args.status not in TICKET_STATUSES:
                    raise SysopError(f"status must be one of {', '.join(TICKET_STATUSES)}")
                t["log"].append(f"{_now()} {t['status']} -> {args.status}")
                t["status"] = args.status
                if args.status == "running":
                    t["needs"] = None  # a relaunch is the answer to whatever was needed
            if args.worker:
                t["log"].append(f"{_now()} worker {t.get('worker') or '-'} -> {args.worker}")
                t["worker"] = args.worker
            if getattr(args, "pr", None):
                t["pr"] = {**(t.get("pr") or {}), "number": args.pr}
                t["log"].append(f"{_now()} pull request #{args.pr}")
            if args.stacked_on:
                if args.stacked_on not in t["depends"]:
                    raise SysopError(f"{args.id} can stack only on one of its dependencies")
                t.update(stacked_on=args.stacked_on, fork=args.fork)
                t["log"].append(f"{_now()} stacked on {args.stacked_on} at {str(args.fork)[:12]}")
        elif args.action == "log":
            t["log"].append(f"{_now()} {args.message}")
    md = save_tickets(args.cwd, data)
    print(md if args.action != "show" else md.read_text())
    return 0


ISSUE_CONTRACT = ("Problem: GitHub issue #{n}: {title}\nOutcome: (the coordinator states it from the request)\n"
                  "Scope: (to fill)\nAcceptance: (to fill)\nConstraints: (to fill)\nNon-goals: (to fill)")


def intake(cwd: str, lane: str, label: str | None = None) -> dict:
    """Open GitHub issues labelled for a lane become its tickets `issue-<n>`, once each: the request is
    the issue body verbatim, the contract a skeleton the coordinator fills, and the issue's URL is kept
    so `land` can comment and close it."""
    settings = load_settings(cwd, "")
    conf = (settings.get("lanes") or {}).get(lane)
    if conf is None:
        raise SysopError(f"no lane '{lane}' (`sysop.py lane add {lane} --preset <name>` creates it)")
    label = label or (conf.get("intake") or {}).get("label") or f"sysop:{lane}"
    issues = gh_json(["issue", "list", "--label", label, "--state", "open", "--limit", "100",
                      "--json", "number,title,body,url"], main_root(cwd))
    data = load_tickets(cwd)
    added, known = [], {t.get("issue", {}).get("number") for t in data["tickets"].values() if t.get("issue")}
    for issue in sorted(issues or [], key=lambda i: i["number"]):
        tid = f"issue-{issue['number']}"
        if issue["number"] in known or tid in data["tickets"]:
            continue
        data["tickets"][tid] = {
            "title": issue["title"][:120], "size": "small", "kind": None, "status": "queued", "worker": None,
            "branch": f"sysop/{tid}", "stacked_on": None, "fork": None, "needs": None, "depends": [],
            "request": issue.get("body") or issue["title"],
            "contract": ISSUE_CONTRACT.format(n=issue["number"], title=issue["title"]),
            "lane": lane, "authority": "standing land: true" if conf.get("land") else "none",
            "issue": {"number": issue["number"], "url": issue["url"]},
            "log": [f"{_now()} queued in lane {lane} from {issue['url']}"]}
        added.append(tid)
    if added:
        save_tickets(cwd, data)
    return {"lane": lane, "label": label, "added": added, "open_issues": len(issues or [])}


SLICE_KEYS = ("id", "title", "size", "depends", "request", "contract", "acceptance", "check")
SIZES = ("small", "large", "risky")
SKELETON_ID = "t0-skeleton"
SKELETON = {
    "id": SKELETON_ID, "title": "walking skeleton: the app starts, answers a health check, and is gated",
    "size": "large", "kind": "skeleton", "depends": [],
    "request": "Make the product runnable and checkable before any feature lands.",
    "contract": ("Problem: there is no way to start, check or gate this product yet.\n"
                 "Outcome: one command starts the app on $PORT; a health check answers; a test suite runs "
                 "and passes; CI runs it; AGENTS.md says how; and the check is committed in this branch as "
                 "`.claude/sysop.json` containing {\"check\": \"<the test command>\"} (it reaches main by landing; "
                 "never edit the main checkout).\n"
                 "Acceptance: a fresh clone can start the app, see the health check answer, and run the check green."),
    "acceptance": ["the start command serves the app on $PORT", "the health check answers",
                   "the check command runs green locally and in CI", "AGENTS.md names the commands"],
    "check": "start the app, open the health check, see it answer",
}


def import_slices(cwd: str, path: str, skeleton: str = "auto", base: str | None = None,
                  authority: str | None = None) -> tuple[dict, list[str]]:
    """Merge slices (JSON written by the slice skill) into the tickets, by id; validate before writing."""
    try:
        slices = json.loads(Path(path).read_text())["slices"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise SysopError(f"cannot read slices from {path}: {exc}") from None
    seen: set = set()
    for sl in slices:
        missing = [k for k in SLICE_KEYS if k not in sl]
        if missing:
            raise SysopError(f"slice {sl.get('id', '?')}: missing {', '.join(missing)}")
        if not TICKET_RE.match(str(sl["id"])):
            raise SysopError(f"slice id '{sl['id']}' is not a valid ticket id")
        if sl["id"] in seen:
            raise SysopError(f"duplicate slice id '{sl['id']}'")
        if sl["size"] not in SIZES:
            raise SysopError(f"slice {sl['id']}: size must be one of {', '.join(SIZES)}")
        seen.add(sl["id"])
    data = load_tickets(cwd)
    tickets = dict(data["tickets"])
    own_skeleton = any(sl.get("kind") == "skeleton" for sl in slices)
    add_skeleton = (skeleton == "auto" and not own_skeleton and SKELETON_ID not in tickets
                    and not str(load_settings(cwd).get("check") or "").strip())
    if add_skeleton or (SKELETON_ID in tickets and not own_skeleton):  # a re-import keeps roots on it
        roots = {sl["id"] for sl in slices if not sl["depends"] and sl["id"] != SKELETON_ID}
        slices = [*([SKELETON] if add_skeleton else []),
                  *[{**sl, "depends": [SKELETON_ID]} if sl["id"] in roots else sl for sl in slices]]
    changes = []
    for sl in slices:
        fields = {k: sl[k] for k in ("title", "size", "depends", "request", "contract", "acceptance", "check")}
        fields["kind"] = sl.get("kind")
        if sl["id"] in tickets:
            t = {**tickets[sl["id"]], **fields}
            if any(tickets[sl["id"]].get(k) != v for k, v in fields.items()):
                t["log"] = [*t["log"], f"{_now()} updated from slices"]
                changes.append(f"updated {sl['id']}")
        else:
            t = {**fields, "status": "queued", "worker": None, "branch": f"sysop/{sl['id']}", "stacked_on": None,
                 "fork": None, "needs": None, "source": "slice", "log": [f"{_now()} queued (from slices)"]}
            changes.append(f"added {sl['id']}")
        tickets[sl["id"]] = t
    check_graph(tickets)  # refused: nothing is written
    check_authority(cwd, authority)
    data["tickets"] = tickets
    if base:
        data["base"] = base
    if authority:
        data["authority"] = authority
    save_tickets(cwd, data)
    return data, changes


def render_tree(tickets: dict) -> list[str]:
    """Roots first; each ticket under every dependency it waits on, so the order to build is visible."""
    lines: list[str] = []

    def walk(tid: str, depth: int) -> None:
        t = tickets[tid]
        lines.append(f"{'    ' * depth}{tid} [{t['size']}, {t['status']}] {t['title']}")
        for child, c in tickets.items():
            if tid in c["depends"]:
                walk(child, depth + 1)

    for tid, t in tickets.items():
        if not t["depends"]:
            walk(tid, 0)
    return lines


def halt_path(cwd: str) -> Path:
    return ensure_store(cwd) / "HALT"


def cmd_halt(args: argparse.Namespace) -> int:
    """The off switch: while .sysop/HALT exists, `next` launches nothing new (running work finishes)."""
    path = halt_path(args.cwd)
    if args.command == "halt":
        path.write_text((args.reason or "halted by the operator") + "\n")
    else:
        path.unlink(missing_ok=True)
    print(json.dumps({"halted": path.exists(), "reason": args.reason if args.command == "halt" else None}))
    return 0


def _has_authority(data: dict, settings: dict, t: dict | None = None) -> bool:
    """A standing grant (the repo's, or the ticket's lane's), or --land in the request that added it."""
    given = (t or {}).get("authority") or data.get("authority")
    return settings.get("land") is True or given == "--land given"


def _sealed(cwd: str, t: dict, root: Path) -> dict | None:
    """The ticket's seal when it binds the branch's current head; None otherwise."""
    try:
        seal = json.loads(seal_path(cwd, t["branch"]).read_text())
        head = git(["rev-parse", f"refs/heads/{t['branch']}"], root)
    except (OSError, json.JSONDecodeError, SysopError):
        return None
    return seal if seal.get("sha") == head else None


TIER_LADDER = ("fast", "standard", "deep")


def tier_above(settings: dict, t: dict) -> str | None:
    """The next tier up the ladder from the one this ticket routes to; None at the top or off the ladder."""
    from workers import resolve_tier  # noqa: PLC0415
    try:
        tier = resolve_tier(settings, t["size"], t.get("kind"))
    except SysopError:
        return None
    if tier not in TIER_LADDER or tier == TIER_LADDER[-1]:
        return None
    up = TIER_LADDER[TIER_LADDER.index(tier) + 1]
    return up if up in settings.get("tiers", {}) else None


def next_actions(cwd: str, grant: bool = False) -> dict:
    """The fleet's due actions, computed from the records alone (so a resumed coordinator gets the same
    answer). Statuses move only where a fact decides them: a READY seal on the head means reviewed."""
    settings, data, store, root = load_settings(cwd, ""), load_tickets(cwd), ensure_store(cwd), main_root(cwd)
    tickets = data["tickets"]
    base = data.get("base") or settings.get("base") or git(["rev-parse", "--abbrev-ref", "HEAD"], root)
    per_lane: dict = {}

    def lane_settings(tid: str) -> dict:
        """Each ticket follows its lane's preset, cap and authority; the default lane is the repo's own."""
        lane = tickets[tid].get("lane") or ""
        if lane not in per_lane:
            per_lane[lane] = load_settings(cwd, lane) if lane else settings
        return per_lane[lane]

    def allowed(tid: str) -> bool:  # `grant`: the operator's word for this call (`land --ready`)
        return grant or _has_authority(data, lane_settings(tid), tickets[tid])

    authority = any(allowed(tid) for tid in tickets)
    max_attempts = max(1, int(settings.get("max_attempts") or 3))
    halt = halt_path(cwd)
    halted = halt.is_file()
    verifying = verify.enabled(settings)
    landable = ("verified",) if verifying else ("reviewed", "verified")
    acts: list[dict] = []
    waiting: list[str] = []
    changed = False

    def status(tid: str) -> str:
        if ":" in tid:  # another repo's ticket: its own records decide
            return external_status(tid) or "missing"
        return LEGACY_STATUS.get(tickets[tid]["status"], tickets[tid]["status"])

    def move(tid: str, to: str, why: str) -> None:
        nonlocal changed
        t = tickets[tid]
        t["log"].append(f"{_now()} {t['status']} -> {to}: {why}")
        t["status"], changed = to, True
        if to in landable:  # the wait for landing is measured from the last time it became ready
            t["ready_at"] = time.time()
        elif to == "landed":
            t["landed_at"] = time.time()

    for tid, t in tickets.items():
        t.setdefault("branch", f"sysop/{tid}")
        st = status(tid)
        attempts = _attempts(store / "runs" / tid)
        if st == "running" and attempts:
            run = run_state(store / "runs" / tid / str(attempts[-1]))
            if run["state"] == "running":
                waiting.append(tid)
            elif run["code"] == 0:
                acts.append({"do": "check", "ticket": tid, "branch": t["branch"],
                             "worktree": str(store / "wt" / tid)})
            elif len(attempts) >= max_attempts:
                up = (t.get("escalated") or {}).get("tier") or tier_above(lane_settings(tid), t)
                after = (t.get("escalated") or {}).get("after", len(attempts))
                if up and len(attempts) == after:  # the author had its tries; one stronger model before the operator
                    if not t.get("escalated"):
                        t["escalated"], changed = {"tier": up, "after": len(attempts)}, True
                        t["log"].append(f"{_now()} {len(attempts)} attempts failed: one last try a tier up ({up})")
                    acts.append({"do": "correct", "ticket": tid, "attempt": len(attempts), "escalate": up,
                                 "why": f"{len(attempts)} attempts failed; route this correction with "
                                        f"`S route {up}` (a stronger model) and record the switch"})
                else:
                    move(tid, "blocked", f"{len(attempts)} attempts failed"
                         + (f", the last one a tier up ({t['escalated']['tier']})" if t.get("escalated") else ""))
            else:
                why = f"attempt {len(attempts)}: " + (f"exit {run['code']}" if run["state"] == "exited" else "died")
                acts.append({"do": "correct", "ticket": tid, "attempt": len(attempts),
                             "why": why + " (classify: availability -> `fallback`; work -> a correction brief)"})
        elif st in ("built", "reviewed", "verified"):
            seal = _sealed(cwd, t, root)
            if t.get("needs") == "rebase":
                acts.append({"do": "correct", "ticket": tid, "attempt": len(attempts),
                             "why": f"landing conflict: in its worktree, rebase onto {base} and resolve, run "
                                    "the check, commit; the resolution is re-reviewed"})
            elif t.get("needs") == "pr-feedback":
                acts.append({"do": "correct", "ticket": tid, "attempt": len(attempts),
                             "why": (t.get("pr") or {}).get("feedback", "review feedback on the PR")
                                    + "\n(one correction brief to the same worker; then re-review)"})
            elif seal is None:
                if st != "built":
                    move(tid, "built", "the head moved past its seal")
                acts.append({"do": "review", "ticket": tid, "branch": t["branch"], "author": t.get("worker")})
            elif seal.get("verdict") == "READY":
                if st == "built":
                    move(tid, "reviewed", f"sealed READY at {seal['sha'][:12]}")
                    st = "reviewed"
                if verifying and st == "reviewed":
                    rec = verify.result_at(cwd, t["branch"], seal["sha"])
                    if rec is None:
                        acts.append({"do": "verify", "ticket": tid, "branch": t["branch"], "author": t.get("worker")})
                    elif rec["verdict"] == "PASS":
                        move(tid, "verified", f"journeys pass at {seal['sha'][:12]} ({rec.get('verifier')})")
                    else:
                        failing = "; ".join(f"journey {j.get('n')}: {j.get('evidence', '')}"
                                            for j in rec.get("journeys", []) if not j.get("pass"))
                        acts.append({"do": "correct", "ticket": tid, "attempt": len(attempts),
                                     "why": "verification failed: " + (failing or rec.get("reason") or "no verdict")
                                            + " (one correction brief, then re-review and verify again)"})
            elif seal.get("verdict") == "NEEDS FIXES":
                acts.append({"do": "correct", "ticket": tid, "attempt": len(attempts),
                             "why": f"review: NEEDS FIXES ({seal.get('report')}): one correction brief to the "
                                    "same worker, then re-review"})
            else:
                acts.append({"do": "review", "ticket": tid, "branch": t["branch"], "author": t.get("worker")})

    # queued tickets: blocked by a blocked dependency, launched from base, or stacked on a reviewed one
    slots: dict = {}  # per lane: its cap minus what it already runs
    for tid in tickets:
        lane = tickets[tid].get("lane") or ""
        if lane not in slots:
            cap = max(1, int(lane_settings(tid).get("max_parallel") or 3))
            busy = sum(1 for x in tickets if (tickets[x].get("lane") or "") == lane and status(x) == "running")
            slots[lane] = 0 if halted else max(0, cap - busy)
    for tid, t in tickets.items():
        if status(tid) == "running" and not _attempts(store / "runs" / tid):
            move(tid, "queued", "marked running, but no launch was recorded")
        if status(tid) != "queued":
            continue
        deps = {d: status(d) for d in t["depends"]}
        bad = [d for d, s in deps.items() if s == "blocked"]
        if bad:
            move(tid, "blocked", f"dependency {', '.join(bad)} is blocked")
            continue
        gone = [d for d, s in deps.items() if s == "missing"]
        if gone:  # the other repo moved or its ticket went away: waiting would be silent forever
            move(tid, "blocked", f"dependency {', '.join(gone)} cannot be found (was its repo moved?)")
            continue
        open_deps = [d for d, s in deps.items() if s != "landed"]
        launch = None
        if not open_deps:
            launch = {"base_ref": base, "stacked_on": None, "fork": None}
        elif len(open_deps) == 1 and ":" not in open_deps[0] and deps[open_deps[0]] in ("reviewed", "verified"):
            seal = _sealed(cwd, tickets[open_deps[0]], root)
            if seal and seal.get("verdict") == "READY":
                launch = {"base_ref": seal["sha"], "stacked_on": open_deps[0], "fork": seal["sha"]}
        lane = t.get("lane") or ""
        if launch and slots[lane] > 0:
            slots[lane] -= 1
            acts.append({"do": "launch", "ticket": tid, "branch": t["branch"], "worktree": str(store / "wt" / tid),
                         **launch, "size": t["size"], "kind": t.get("kind"),
                         "attempt": len(_attempts(store / "runs" / tid)) + 1})

    # PR lanes: a reviewed ticket goes to GitHub, and what people say there goes back to its builder
    pr_lane = {tid for tid in tickets if ":" not in tid and lane_settings(tid).get("pr")}
    mergeable: set = set()
    for tid in sorted(pr_lane):
        t = tickets[tid]
        if status(tid) not in landable or t.get("needs"):
            continue
        rec = t.get("pr")
        if not rec:
            acts.append({"do": "pr", "ticket": tid, "branch": t["branch"], "base": base,
                         "worktree": str(store / "wt" / tid)})
            continue
        try:
            view = gh_json(["pr", "view", str(rec["number"]), "--json", "state,headRefOid,url"], root)
            feedback = pr_feedback(cwd, rec["number"], set(rec.get("seen") or []))
        except SysopError:
            continue  # GitHub did not answer: the next `next` asks again
        if view["state"] == "MERGED":
            move(tid, "landed", f"PR #{rec['number']} merged")
        elif view["state"] == "CLOSED":
            move(tid, "blocked", f"PR #{rec['number']} was closed without merging")
        elif feedback["new"]:
            rec["seen"] = sorted(set(rec.get("seen") or []) | {i["id"] for i in feedback["new"]})
            rec["feedback"], t["needs"], changed = quote_feedback(feedback["new"]), "pr-feedback", True
            t["log"].append(f"{_now()} {len(feedback['new'])} review item(s) on PR #{rec['number']}: "
                            "back to the builder")
            acts.append({"do": "correct", "ticket": tid, "attempt": len(_attempts(store / "runs" / tid)),
                         "why": rec["feedback"] + "\n(one correction brief to the same worker; then re-review)"})
        elif (seal := _sealed(cwd, t, root)) and view["headRefOid"] != seal["sha"]:
            acts.append({"do": "pr", "ticket": tid, "branch": t["branch"], "base": base,
                         "worktree": str(store / "wt" / tid), "number": rec["number"]})
        else:
            mergeable.add(tid)

    # landing: serial, in dependency order, one at a time, under authority
    if authority:
        order: list[str] = []

        def topo(tid: str) -> None:
            if tid not in order:
                for dep in tickets[tid]["depends"]:
                    if ":" not in dep:
                        topo(dep)
                order.append(tid)

        for tid in tickets:
            topo(tid)
        for tid in order:
            t = tickets[tid]
            if (status(tid) in landable and not t.get("needs") and allowed(tid)
                    and all(status(d) == "landed" for d in t["depends"])
                    and (tid not in pr_lane or tid in mergeable)):
                acts.append({"do": "pr-merge", "ticket": tid, "number": t["pr"]["number"], "branch": t["branch"]}
                            if tid in pr_lane else {"do": "land", "ticket": tid, "branch": t["branch"], "base": base})
                break

    for act in acts:  # the coordinator passes --lane on every command for the ticket
        if act.get("ticket") in tickets and tickets[act["ticket"]].get("lane"):
            act["lane"] = tickets[act["ticket"]]["lane"]
    if changed:
        save_tickets(cwd, data)
    counts: dict = {}
    for tid in tickets:
        counts[status(tid)] = counts.get(status(tid), 0) + 1
    blocked = [tid for tid in tickets if status(tid) == "blocked"]
    acts += [{"do": "blocked", "ticket": tid, "why": (tickets[tid]["log"] or ["?"])[-1]} for tid in blocked]
    if waiting:  # back as soon as one finishes, so a freed slot is used at once
        acts.append({"do": "wait", "tickets": waiting, "any": True})
    if not [a for a in acts if a["do"] != "blocked"]:
        stalled = [tid for tid in tickets if status(tid) == "queued"]
        acts.append({"do": "done", "landed": [tid for tid in tickets if status(tid) == "landed"],
                     "ready": [tid for tid in tickets if status(tid) in ("reviewed", "verified")],
                     "blocked": blocked, "waiting_on_landing": stalled})
    reason = halt.read_text().strip() if halted else ""
    if halted:
        hint = f"halted ({reason}): running work finishes, nothing new starts; `sysop.py unhalt` to continue"
    elif acts[-1]["do"] == "done" and acts[-1]["waiting_on_landing"]:
        waits = acts[-1]["waiting_on_landing"]
        deps = sorted({d for tid in waits for d in tickets[tid]["depends"] if status(d) != "landed"})
        hint = f"{', '.join(waits)} waits for {', '.join(deps) or 'its dependencies'} to land"
    elif acts[-1]["do"] == "done":
        ready = acts[-1]["ready"]
        unlanded = [tid for tid in ready if not _has_authority(data, lane_settings(tid), tickets[tid])]
        hint = ("ready to land: `/sysop land` merges it (or say `land it`)" if unlanded
                else "finished: write the final message")
    else:
        hint = "do each action in order, then run `sysop.py next` again"
    return {"actions": acts, "halted": halted, "counts": counts, "next": hint}


def land_ready(cwd: str, everywhere: bool = False) -> dict:
    """The operator's word for everything ready: land (or merge the PR of) each ready ticket, in dependency
    order, repo by repo, through the same gates; stop at the first refusal (a later one may depend on it)."""
    from land import land, pr_merge  # noqa: PLC0415 - land's gates, called as the coordinator would
    repos = registered_repos() if everywhere else [str(main_root(cwd))]
    landed, refused = [], None
    for repo in repos:
        while refused is None:
            act = next((a for a in next_actions(repo, grant=True)["actions"] if a["do"] in ("land", "pr-merge")), None)
            if act is None:
                break
            try:
                if act["do"] == "land":
                    land(repo, act["branch"], act["base"], None, None, False, True, False)
                else:
                    pr_merge(repo, act["number"], act["branch"], None, True)
                landed.append(f"{Path(repo).name}:{act['ticket']}" if everywhere else act["ticket"])
            except SysopError as exc:
                refused = {"ticket": act["ticket"], "why": str(exc)}
    return {"landed": landed, **({"refused": refused} if refused else {}),
            "next": refused["why"] if refused else ("nothing was ready to land" if not landed else "nothing needed")}


def next_all(cwd: str) -> dict:
    """Every lane in every registered repo: one list of due actions, each tagged with its repo, under a
    machine-wide cap on running workers (`max_parallel_all`)."""
    cap = max(1, int(load_settings(cwd, "").get("max_parallel_all") or 6))
    repos = registered_repos()
    here = str(main_root(cwd)) if (store_path(cwd) / "tickets.json").is_file() else None
    if here and here not in repos:
        repos.append(here)
    acts, counts, hints = [], {}, []
    running = 0
    for repo in repos:
        result = next_actions(repo)
        running += result["counts"].get("running", 0)
        for k, v in result["counts"].items():
            counts[k] = counts.get(k, 0) + v
        for act in result["actions"]:
            if act["do"] != "done":
                acts.append({**act, "repo": repo})
        if result["actions"] and result["actions"][-1]["do"] != "done" or result["halted"]:
            hints.append(f"{Path(repo).name}: {result['next']}")
    waits = [f"{Path(a['repo']).name}:{t}" for a in acts if a["do"] == "wait" for t in a["tickets"]]
    acts = [a for a in acts if a["do"] != "wait"]
    if waits:  # one bounded wait over every lane, returning when any worker finishes
        acts.append({"do": "wait", "tickets": waits, "any": True})
    room = max(0, cap - running)
    kept = []
    for act in acts:  # launches beyond the machine-wide cap wait for the next pass
        if act["do"] == "launch":
            if room <= 0:
                continue
            room -= 1
        kept.append(act)
    if not [a for a in kept if a["do"] not in ("blocked",)]:
        kept.append({"do": "done"})
    return {"actions": kept, "counts": counts, "repos": repos,
            "next": "; ".join(hints) or "nothing due in any lane"}


def cmd_next(args: argparse.Namespace) -> int:
    print(json.dumps(next_all(args.cwd) if args.all else next_actions(args.cwd), indent=2))
    return 0


# A long skill (ship, fleet) marks itself active; hooks/stop_gate.py refuses to end the
# session's turn while the marker exists, because a text-only message ends a headless
# turn even when the skill has steps left.

def _marker(cwd: str, slug: str) -> Path:
    if not TICKET_RE.match(slug):
        raise SysopError(f"invalid run name '{slug}'")
    return ensure_store(cwd) / "active" / f"{slug}.json"


def cmd_run(args: argparse.Namespace) -> int:
    path = _marker(args.cwd, args.slug)
    if args.action == "resume" and not path.is_file():
        args.action, args.steps = "start", 10  # nothing to resume (a first `--resume`, or one already ended)
    if args.action == "start":
        # The environment is the fact: an agent once passed an invented `--session fleet-$(date +%s)`,
        # and the Stop gate, which guards only its own session's runs, stood down mid-fleet.
        session = os.environ.get("CLAUDE_CODE_SESSION_ID") or (
            args.session if args.session and "${" not in args.session else None)
        data = {"slug": args.slug, "session": session, "step": 0, "steps": args.steps,
                "started": time.time(), "blocks": 0, "last_block_tools": -1, "all": bool(getattr(args, "all", False))}
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, data)
    elif not path.is_file():
        raise SysopError(f"no active run '{args.slug}'")
    elif args.action == "step":
        data = json.loads(path.read_text())
        data.update(step=args.n, note=args.note)
        write_json(path, data)
    elif args.action in ("pause", "resume"):
        data = json.loads(path.read_text())
        data["paused"] = args.action == "pause"  # a paused run holds no turn open (spike S2)
        if args.action == "resume":
            data["session"] = os.environ.get("CLAUDE_CODE_SESSION_ID") or (
                args.session if args.session and "${" not in args.session else data.get("session"))
            data.update(started=time.time(), blocks=0, last_block_tools=-1)
        write_json(path, data)
    else:  # end
        data = json.loads(path.read_text())
        data.update(ended=time.time(), outcome=args.outcome, reason=args.reason)
        with (path.parent.parent / "runlog.jsonl").open("a") as fh:
            fh.write(json.dumps(data) + "\n")
        path.unlink()
        data["notified"] = notify(load_settings(args.cwd), f"sysop: {args.slug} {args.outcome or 'ended'}",
                                  args.reason or f"run {args.slug} ended: {args.outcome or 'done'}")["sent"]
        served = kaggle_serve.load_state()
        if served and served.get("run") == args.slug:  # the run that started a session ends it
            try:
                data["serve_down"] = kaggle_serve.down()
            except kaggle_jobs.JobError as exc:
                data["serve_down"] = {"error": str(exc)}
    if args.action == "end":
        data["next"] = {"landed": "nothing needed", "ready": "`/sysop land` merges what is ready",
                        "blocked": f"decide: {args.reason or 'see the final message'}"}.get(
            args.outcome or "", "nothing needed")
    print(json.dumps(data))
    return 0


def _read_marker(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def wake_state(cwd: str, session: str) -> dict:
    """Whether the sysop mod should wake this session's fleet coordinator: its run is active here and not
    halted, and `next` has an action beyond waiting. `key` names that set of actions, so one set wakes once."""
    runs = [(p, r) for p, r in ((p, _read_marker(p)) for p in active_markers(cwd) if p.name == "fleet.json") if r]
    if not runs:
        return {"wake": False, "why": "no fleet run here"}
    mine = [(p, r) for p, r in runs if r.get("session") == session]
    if not mine:
        return {"wake": False, "why": "the fleet run belongs to another session"}
    marker, run = mine[0]
    cwd = str(marker.parent.parent.parent)  # the repo the run was started from
    if halt_path(cwd).exists():
        return {"wake": False, "why": "halted"}
    result = next_all(cwd) if run.get("all") else next_actions(cwd)
    due = [a for a in result["actions"] if a.get("do") != "wait"]
    if not due:
        return {"wake": False, "why": "only waits are due"}
    key = ";".join(sorted(f"{a.get('repo', '')}:{a.get('ticket', '')}:{a['do']}" for a in due))
    return {"wake": True, "key": key, "why": ", ".join(f"{a['do']} {a.get('ticket', '')}".strip() for a in due)}


def cmd_wake(args: argparse.Namespace) -> int:
    print(json.dumps(wake_state(args.cwd, args.session)))
    return 0


def explain_state(cwd: str) -> dict:
    """Narrate each step (one plain line) for people learning sysop: `auto` until the repo's first landing."""
    mode = str(load_settings(cwd).get("explain") or "auto").lower()
    if mode in ("on", "off"):
        return {"explain": mode == "on", "mode": mode, "why": f"set to {mode}"}
    store = store_path(cwd)
    landed = False
    for name, test in (("runlog.jsonl", lambda r: r.get("outcome") == "landed"),
                       ("ledger.jsonl", lambda r: r.get("landed") in (True, "yes"))):
        try:
            landed = landed or any(test(json.loads(line)) for line in (store / name).read_text().splitlines() if line)
        except (OSError, json.JSONDecodeError):
            pass
    return {"explain": not landed, "mode": "auto",
            "why": "this repo has a landed run" if landed else "no landed run yet in this repo"}


def cmd_explain(args: argparse.Namespace) -> int:
    try:
        out = explain_state(args.cwd)
    except SysopError:  # outside git: skills inject this, and an injected command must exit 0
        out = {"explain": True, "mode": "auto", "why": "no git repository"}
    print(json.dumps(out))
    return 0


def notify(settings: dict, title: str, message: str) -> dict:
    """Best effort and bounded: a notification never fails or delays a run by more than seconds."""
    target = (settings.get("notify") or "").strip()
    if not target:
        return {"sent": False, "reason": "notify is off (config set notify notify-send, or ntfy:<topic>)"}
    try:
        if target.startswith("ntfy:"):
            topic = target[len("ntfy:"):]
            url = topic if topic.startswith(("http://", "https://")) else f"https://ntfy.sh/{topic}"
            req = urllib.request.Request(url, data=message.encode(), headers={"Title": title})
            urllib.request.urlopen(req, timeout=10).close()
        else:
            proc = subprocess.run([*shlex.split(target), title, message], capture_output=True, text=True,
                                  timeout=15, stdin=subprocess.DEVNULL)
            if proc.returncode != 0:
                return {"sent": False, "reason": (proc.stderr or proc.stdout).strip()[-200:] or f"exit {proc.returncode}"}
        return {"sent": True, "to": target.split()[0]}
    except Exception as exc:  # noqa: BLE001 - a missing notifier is never a failed run
        return {"sent": False, "reason": str(exc)[:200]}


def cmd_notify(args: argparse.Namespace) -> int:
    print(json.dumps(notify(load_settings(args.cwd), args.title, args.message)))
    return 0


def run_facts(cwd: str, ticket: str) -> dict | None:
    """Who actually ran a ticket, from its launch records: last worker:model and switches."""
    ticket_dir = store_path(cwd) / "runs" / ticket
    workers = [(ticket_dir / str(n) / "worker").read_text().strip() for n in _attempts(ticket_dir)
               if (ticket_dir / str(n) / "worker").is_file()]
    if not workers:
        return None
    switches = sum(1 for a, b in zip(workers, workers[1:]) if a != b)
    worker, _, model = workers[-1].partition(":")
    first = ticket_dir / str(_attempts(ticket_dir)[0]) / "started"
    seconds = round(time.time() - float(first.read_text()), 1) if first.is_file() else None
    usage: dict = {}
    for n in _attempts(ticket_dir):  # every attempt costs, the failed ones too
        log = ticket_dir / str(n) / "log"
        for k, v in (run_usage(log.read_text(errors="replace")) if log.is_file() else {}).items():
            usage[k] = v if isinstance(v, str) else round(usage.get(k, 0) + v, 4)  # resolved: the last run's
    return {"worker": worker, "model": model, "fallbacks": switches, "seconds": seconds, **usage}


def cmd_ledger_add(args: argparse.Namespace) -> int:
    facts = run_facts(args.cwd, args.ticket)
    if facts:  # prose is a claim; the launch records are the fact
        claimed = {"worker": args.worker, "model": args.model, "fallbacks": args.fallbacks}
        wrong = {k: (claimed[k], facts[k]) for k in claimed
                 if claimed[k] not in (None, facts[k])}
        if wrong:
            raise SysopError("ledger claim contradicts the launch records for "
                             f"{args.ticket}: " + ", ".join(f"{k} claimed {c!r}, recorded {r!r}"
                                                            for k, (c, r) in wrong.items()))
        args.worker, args.model, args.fallbacks = facts["worker"], facts["model"], facts["fallbacks"]
        args.seconds = facts["seconds"] if facts["seconds"] is not None else args.seconds
    row = {
        "ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "ticket": args.ticket, "size": args.size, "kind": args.kind, "tier": args.tier,
        "worker": args.worker,
        "model": args.model, "family": family(args.model) if args.model else None,
        "fallbacks": args.fallbacks or 0, "check": args.check, "review": args.review,
        "landed": args.landed == "yes", "seconds": args.seconds, "note": args.note,
    }
    if facts and "usd" in facts:  # stated by the CLI itself; never estimated
        row.update(usd=facts["usd"], billed=billed(args.worker))
        spend_path().parent.mkdir(parents=True, exist_ok=True)
        with spend_path().open("a") as fh:
            fh.write(json.dumps({"day": dt.date.today().isoformat(), "usd": facts["usd"], "billed": row["billed"],
                                 "ticket": args.ticket}) + "\n")
    if facts and "tokens" in facts:
        row["tokens"] = facts["tokens"]
    if facts and facts.get("resolved"):
        row["resolved_model"] = facts["resolved"]  # the exact release the alias ran as, stated by the CLI
    t = load_tickets(args.cwd)["tickets"].get(args.ticket, {}) if (store_path(args.cwd) / "tickets.json").is_file() else {}
    if t.get("ready_at") and t.get("landed_at"):  # R4: how long ready work waited to land
        row.update(ready_at=t["ready_at"], landed_at=t["landed_at"],
                   landing_seconds=round(t["landed_at"] - t["ready_at"], 1))
    row["model_source"] = "launch records" if facts else "self-reported"
    with (ensure_store(args.cwd) / "ledger.jsonl").open("a") as fh:
        fh.write(json.dumps(row) + "\n")
    print(json.dumps(row))
    return 0


def race_path(cwd: str, ticket: str) -> Path:
    return ensure_store(cwd) / "races" / f"{ticket}.json"


def race_picks(settings: dict, roster: dict, size: str, kind: str | None, n: int) -> list[dict]:
    """N entries from N different vendors: the size's tier first, then the kind's auto chain."""
    picks, used, exclude = [], set(), []
    for source in (size, f"auto:{KIND_SKILL.get(kind or '', 'code')}"):
        while len(picks) < n:
            pick = route(settings, roster, source, "build", exclude, None, kind)
            if not pick["worker"]:
                break
            exclude.append(f"{pick['worker']}:{pick['model']}")
            if pick["family"] not in used:
                used.add(pick["family"])
                picks.append(pick)
    return picks


def race_start(cwd: str, ticket: str, n: int, size: str, kind: str | None, prompt_file: str,
               base: str | None) -> dict:
    if not TICKET_RE.match(ticket) or n < 2:
        raise SysopError("race: a valid ticket id and --n 2 or more")
    path = race_path(cwd, ticket)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:  # reserve it atomically: two starts for one ticket must not both launch racers
        os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        raise SysopError(f"race {ticket} already exists (race status {ticket})") from None
    try:
        return _race_launch(cwd, ticket, n, size, kind, prompt_file, base, path)
    except BaseException:
        if path.is_file() and path.stat().st_size == 0:
            path.unlink()  # nothing launched: release the reservation
        raise


def _race_launch(cwd: str, ticket: str, n: int, size: str, kind: str | None, prompt_file: str,
                 base: str | None, path: Path) -> dict:
    settings, root = load_settings(cwd), main_root(cwd)
    roster = load_roster(settings, cwd=cwd)
    picks = race_picks(settings, roster, size, kind, n)
    if len(picks) < 2:
        raise SysopError(f"race: only {len(picks)} vendor(s) available for {size}; a race needs two")
    base = base or settings.get("base") or git(["rev-parse", "--abbrev-ref", "HEAD"], root)
    prompt = os.path.abspath(os.path.join(cwd, prompt_file))
    racers = []
    for i, pick in enumerate(picks, 1):
        rid, branch = f"r{i}", f"race/{ticket}/r{i}"
        tree = ensure_store(cwd) / "wt" / f"{ticket}-r{i}"
        git(["worktree", "add", "-q", "-b", branch, str(tree), base], root)
        cmd = worker_cmd(settings, pick["worker"], "build", pick["model"], prompt, pick["effort"])
        rec = launch(cwd, f"{ticket}-{rid}", str(tree), cmd, f"{pick['worker']}:{pick['model']}")
        racers.append({"id": rid, "entry": pick["entry"], "family": pick["family"], "branch": branch,
                       "worktree": str(tree), "run": rec["run_dir"]})
    data = {"ticket": ticket, "base": base, "size": size, "kind": kind, "started": time.time(), "racers": racers}
    write_json(path, data)
    return data


def race_status(cwd: str, ticket: str) -> dict:
    path = race_path(cwd, ticket)
    if not path.is_file():
        raise SysopError(f"no race {ticket}")
    data = json.loads(path.read_text())
    root = main_root(cwd)
    for r in data["racers"]:
        run = Path(r["run"])
        enforce_deadline(run)
        exit_file = run / "exit"
        r["state"] = "exited" if exit_file.exists() else "running" if run_alive(run) else "dead"
        r["code"] = int(exit_file.read_text()) if exit_file.exists() else None
        try:
            r["commits"] = int(git(["rev-list", "--count", f"{data['base']}..{r['branch']}"], root))
        except SysopError:
            r["commits"] = None  # settled: the branch is archived
    return data


def race_settle(cwd: str, ticket: str, winner: str) -> dict:
    """Keep the winner; archive every loser's work under refs/sysop/race/ (nothing is lost), stop it,
    and remove its worktree and branch. The operator's --race is the authority for this cleanup."""
    data = race_status(cwd, ticket)
    if data.get("winner"):
        raise SysopError(f"race {ticket} is already settled (winner {data['winner']})")
    if winner not in {r["id"] for r in data["racers"]}:
        raise SysopError(f"race {ticket} has no racer {winner}")
    root, archived = main_root(cwd), []
    for r in data["racers"]:
        ref = f"refs/sysop/race/{ticket}/{r['id']}"
        if r["id"] == winner:
            continue
        if subprocess.run(["git", "rev-parse", "--verify", "-q", f"refs/heads/{r['branch']}"], cwd=str(root),
                          capture_output=True).returncode != 0 and subprocess.run(
                ["git", "rev-parse", "--verify", "-q", ref], cwd=str(root), capture_output=True).returncode == 0:
            archived.append({"id": r["id"], "entry": r["entry"], "ref": ref})  # done by an earlier, interrupted settle
            continue
        run = Path(r["run"])
        if run_alive(run):
            try:
                os.killpg(int((run / "pid").read_text()), 15)  # the launcher leads its own group
            except (OSError, ValueError):
                pass
            for _ in range(40):  # a stopping worker may still be writing: archive a settled tree
                if not run_alive(run):
                    break
                time.sleep(0.25)
        tree = Path(r["worktree"])
        if tree.exists() and git(["status", "--porcelain"], tree):
            git(["add", "-A"], tree)  # uncommitted work goes into the archive too
            git(["-c", "user.name=sysop", "-c", "user.email=sysop@localhost", "commit", "-q", "--no-verify",
                 "-m", f"race {ticket} {r['id']}: uncommitted work at settle"], tree)
        head = git(["rev-parse", r["branch"]], root)
        git(["update-ref", ref, head], root)
        if tree.exists():
            git(["worktree", "remove", "--force", str(tree)], root)  # safe: everything is committed and archived
        git(["update-ref", "-d", f"refs/heads/{r['branch']}"], root)
        archived.append({"id": r["id"], "entry": r["entry"], "ref": ref, "head": head})
        worker, _, model = r["entry"].partition(":")
        with (ensure_store(cwd) / "ledger.jsonl").open("a") as fh:  # every attempt is on record
            fh.write(json.dumps({"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                                 "ticket": f"{ticket}-{r['id']}", "size": data["size"], "kind": data["kind"],
                                 "worker": worker, "model": model.split("@")[0], "family": r["family"],
                                 "fallbacks": 0, "landed": False, "note": f"race {ticket}: lost to {winner}"})
                     + "\n")
    data.update(winner=winner, archived=archived, settled=time.time())
    write_json(race_path(cwd, ticket), data)
    win = next(r for r in data["racers"] if r["id"] == winner)
    return {"ticket": ticket, "winner": winner, "branch": win["branch"], "worktree": win["worktree"],
            "entry": win["entry"], "archived": archived}


def cmd_race(args: argparse.Namespace) -> int:
    if args.action == "start":
        out = race_start(args.cwd, args.ticket, args.n, args.size, args.kind, args.prompt_file, args.base)
    elif args.action == "status":
        out = race_status(args.cwd, args.ticket)
    else:
        out = race_settle(args.cwd, args.ticket, args.winner)
    print(json.dumps(out, indent=2))
    return 0


def cmd_ledger_summary(args: argparse.Namespace) -> int:
    try:
        data = ledger_summary(args.cwd)
    except SysopError:  # outside git: skills inject this at load, and a failure aborts them
        data = {"total": 0, "malformed": 0, "groups": {}, "note": "no git repository"}
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    print(f"{data['total']} ticket(s)" + (f", {data['malformed']} malformed line(s)"
                                          if data["malformed"] else ""))
    print(f"{'worker:model':<34}{'n':>4}{'landed':>8}{'check✓':>8}{'fallb':>7}{'mean s':>9}")
    for key, g in sorted(data["groups"].items()):
        print(f"{key:<34}{g['n']:>4}{g['landed']:>8}{g['check_pass']:>8}{g['fallbacks']:>7}"
              f"{g['mean_seconds'] if g['mean_seconds'] is not None else '-':>9}"
              + ("" if g["enough_data"] else "  (fewer than 5 rows)"))
    return 0
