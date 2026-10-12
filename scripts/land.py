"""Landing: the review gate, land, worktree cleanup, the GitHub path (pr) and report templates."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from store import (
    _now, _worktrees, ensure_store, git, load_settings, load_tickets, main_root, PLUGIN_ROOT, remove_worktree,
    run_check, save_tickets, slug, store_path, SysopError, TICKET_RE, write_json,
)

NO_AUTHORITY = ("not merged: merging needs your say-so. Fix: `/sysop land` (or say \"land it\"), "
                "or grant it for this repo: `sysop.py config set land true`")


def review_gate(cwd: str, branch: str, review: str | None) -> Path:
    """The gate every landing path shares: exactly one `Verdict: READY`, and the cross-family result
    written by `xreview` when cross review is on. Returns the review report."""
    settings, store = load_settings(cwd), ensure_store(cwd)
    report = Path(review) if review else store / "reports" / f"{slug(branch)}.review.md"
    if not report.is_file() or not report.read_text().strip():
        raise SysopError(f"refusing to land {branch}: no review report at {report}")
    verdict = review_verdict(report)
    if verdict != "READY":
        raise SysopError(f"refusing to land {branch}: the review verdict is {verdict or 'missing'} "
                         f"(needs a 'Verdict: READY' line in {report})")
    if settings.get("cross_review") is True:
        xr = store / "reports" / f"{slug(branch)}.xreview.md"
        try:
            rec = json.loads(xr.with_suffix(".json").read_text())
            genuine = rec.get("sha256") == hashlib.sha256(xr.read_bytes()).hexdigest()
        except (OSError, json.JSONDecodeError):
            rec, genuine = {}, False
        if not genuine or rec.get("status") not in ("done", "unavailable", "claude-agents", "disabled"):
            raise SysopError(f"refusing to land {branch}: no cross-family review result written by "
                             f"`xreview` at {xr} (run `xreview start` and `xreview collect`)")
    sealed = check_seal(cwd, branch, report)
    import verify  # noqa: PLC0415 - verify imports this module; opt-in (setting `start`)
    verify.gate(cwd, branch, sealed["sha"])
    return report


def seal_path(cwd: str, branch: str) -> Path:
    return ensure_store(cwd) / "reports" / f"{slug(branch)}.seal.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_seal(cwd: str, branch: str, report: Path) -> dict:
    """The review binds to the commit it read: no seal, a moved head or an edited report refuses."""
    path = seal_path(cwd, branch)
    try:
        sealed = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        raise SysopError(f"refusing to land {branch}: the review is not sealed to a commit. The review's last "
                         f"step seals it: `sysop.py review seal {branch}`") from None
    if sealed.get("report_sha256") != _sha256(report):
        raise SysopError(f"refusing to land {branch}: {report} changed after it was sealed. Review again, "
                         f"then `sysop.py review seal {branch}`")
    head = git(["rev-parse", f"refs/heads/{branch}"], main_root(cwd))
    if head != sealed.get("sha"):
        raise SysopError(f"refusing to land {branch}: its head {head[:12]} is not the reviewed commit "
                         f"{str(sealed.get('sha'))[:12]}; commits after a review are unreviewed. Re-review "
                         f"(a light re-review of a correction ends with `sysop.py review seal {branch} --re-review`)")
    return sealed


def seal(cwd: str, branch: str, review: str | None, re_review: bool, sha: str | None = None) -> dict:
    """Bind the review report to the branch head it reviewed (review's last step)."""
    store, root = ensure_store(cwd), main_root(cwd)
    report = Path(review) if review else store / "reports" / f"{slug(branch)}.review.md"
    if not report.is_file() or not report.read_text().strip():
        raise SysopError(f"nothing to seal: no review report at {report}")
    head = git(["rev-parse", "--verify", f"refs/heads/{branch}"], root)
    if sha and git(["rev-parse", "--verify", f"{sha}^{{commit}}"], root) != head:
        raise SysopError(f"{branch} moved during the review (reviewed {sha[:12]}, head {head[:12]}): "
                         "review the new commits too")
    path = seal_path(cwd, branch)
    data = {"branch": branch, "sha": head, "verdict": review_verdict(report), "report": str(report),
            "report_sha256": _sha256(report), "sealed_at": time.time(), "re_review": re_review}
    xr = store / "reports" / f"{slug(branch)}.xreview.json"
    try:
        data["xreview_status"] = json.loads(xr.read_text()).get("status")
    except (OSError, json.JSONDecodeError):
        data["xreview_status"] = None
    if re_review:
        try:
            old = json.loads(path.read_text())["sha"]
        except (OSError, json.JSONDecodeError, KeyError):
            raise SysopError(f"--re-review re-seals a reviewed branch, and {branch} has no seal: "
                             "run the full review") from None
        if subprocess.run(["git", "merge-base", "--is-ancestor", old, head], cwd=str(root)).returncode != 0:
            raise SysopError(f"{branch} was rewritten since its review (the sealed {old[:12]} is not an "
                             "ancestor of the head): run the full review")
        data.update(previous=old, added=git(["log", "--format=%h %s", f"{old}..{head}"], root).splitlines())
    write_json(path, data)
    return data


def discovery(cwd: str, file: str, cause: str, summary: str, ticket: str | None) -> dict:
    """File a real defect outside the ticket's scope as a follow-up, once per file and cause."""
    path = ensure_store(cwd) / "discoveries.md"
    lines = path.read_text().splitlines() if path.is_file() else ["# Discoveries", "",
                                                                   "Real defects found outside a ticket's scope."]
    key = f"- [{file}] {cause}:"
    if any(line.startswith(key) for line in lines):
        return {"added": False, "path": str(path)}
    lines.append(f"{key} {summary}" + (f" (from {ticket})" if ticket else ""))
    path.write_text("\n".join(lines) + "\n")
    return {"added": True, "path": str(path)}


def cmd_review(args: argparse.Namespace) -> int:
    if args.action == "seal":
        out = seal(args.cwd, args.branch, args.review, args.re_review, args.sha)
    else:
        out = discovery(args.cwd, args.file, args.cause, args.summary, args.ticket)
    print(json.dumps(out))
    return 0


def land(cwd: str, branch: str, base: str | None, review: str | None, check: str | None,
         no_check: bool, do_land: bool, push: bool) -> dict:
    settings = load_settings(cwd)
    root = main_root(cwd)
    store = ensure_store(cwd)
    report = review_gate(cwd, branch, review)
    git(["rev-parse", "--verify", f"refs/heads/{branch}"], root)
    base = base or settings.get("base") or git(["rev-parse", "--abbrev-ref", "HEAD"], root)
    if base == branch:
        raise SysopError(f"base and branch are both '{branch}'; name the base with --base")
    check = (settings.get("check", "") or branch_check(root, branch)) if check is None else check
    if not check and not no_check:
        raise SysopError("refusing to land: no check configured. sysop merges only what a command has shown "
                         "to work. Fix: `sysop.py config set check \"<your test command>\"` (or run /sysop:setup)")

    data, tid = ticket_for(cwd, branch)
    fork = None
    if tid and data["tickets"][tid].get("stacked_on"):
        dep = data["tickets"][tid]["stacked_on"]
        if data["tickets"].get(dep, {}).get("status") != "landed":
            raise SysopError(f"refusing to land {branch}: it is stacked on ticket {dep}, which has not landed; "
                             f"land {dep} first")
        fork = data["tickets"][tid].get("fork")
    if tid and data["tickets"][tid].get("issue"):
        push = True  # work taken in from a GitHub issue is published where the issue can see it

    trees = _worktrees(root)
    temp_tree = None
    checkout = trees.get(branch)
    if checkout is None:
        temp_tree = store / "wt" / slug(branch)
        git(["worktree", "add", str(temp_tree), branch], root)
        checkout = temp_tree
    try:
        result = _land_from(settings, root, store, checkout, branch, base, report, check, do_land, push, fork)
    except SysopError as exc:
        if tid and "does not rebase cleanly" in str(exc):  # the ticket's worker resolves it (`next`: correct)
            note_ticket(cwd, tid, needs="rebase", log=f"landing conflict with {base}: rebase and resolve")
        raise
    else:
        if tid and result.get("landed"):
            note_ticket(cwd, tid, status="landed", log=f"landed at {result['head'][:12]}", landed_at=time.time())
            if result.get("pushed"):  # an issue closes only when GitHub can see the landed commit
                result["issue"] = close_issue(cwd, tid, f"Landed by sysop as {result['head'][:12]} on `{base}`.")
            elif load_tickets(cwd)["tickets"].get(tid, {}).get("issue"):
                result["issue"] = f"open: landed locally, not pushed ({result.get('push_reason') or 'no push'})"
                note_ticket(cwd, tid, log=f"issue stays open: {result['issue']}")
        return result
    finally:
        if temp_tree is not None and temp_tree.exists():
            remove_worktree(root, temp_tree)


def branch_check(root: Path, branch: str) -> str:
    """The check a branch brings with it (a walking skeleton commits the first one): used only when
    the base has none yet, so the first landing is still gated."""
    proc = subprocess.run(["git", "show", f"{branch}:.claude/sysop.json"], cwd=str(root), capture_output=True,
                          text=True)
    try:
        return str(json.loads(proc.stdout).get("check") or "") if proc.returncode == 0 else ""
    except (json.JSONDecodeError, AttributeError):
        return ""


def own_change(root: Path, start: str, end: str) -> str:
    """A branch's own change, independent of where it sits: the patch-id of its zero-context diff
    (spike S3: range-diff is too strict, because a moved neighbouring line changes a commit's context)."""
    diff = git(["diff", "-U0", start, end], root)
    proc = subprocess.run(["git", "patch-id", "--stable"], cwd=str(root), input=diff + "\n",
                          capture_output=True, text=True)
    return (proc.stdout.split() or [""])[0]


def ticket_for(cwd: str, branch: str) -> tuple[dict, str | None]:
    data = load_tickets(cwd) if (store_path(cwd) / "tickets.json").is_file() else {"tickets": {}}
    tid = next((k for k, t in data["tickets"].items() if t.get("branch", f"sysop/{k}") == branch), None)
    return data, tid


def close_issue(cwd: str, tid: str, message: str) -> str | None:
    """A ticket taken in from a GitHub issue: comment where it landed and close the issue (best effort:
    a landing never fails over it; the ticket log says what happened)."""
    issue = load_tickets(cwd)["tickets"].get(tid, {}).get("issue")
    if not issue:
        return None
    root = main_root(cwd)
    try:
        proc = gh(["issue", "close", str(issue["number"]), "--comment", message], root, 60)
        ok = proc.returncode == 0
        why = "" if ok else (proc.stderr or proc.stdout).strip()[-200:]
    except SysopError as exc:
        ok, why = False, str(exc)
    note_ticket(cwd, tid, log=f"issue #{issue['number']} " + ("closed" if ok else f"not closed: {why}"))
    return "closed" if ok else f"not closed: {why}"


def note_ticket(cwd: str, tid: str, log: str, **fields) -> None:
    data = load_tickets(cwd)
    t = data["tickets"][tid]
    if "status" in fields and fields["status"] != t["status"]:
        log = f"{t['status']} -> {fields['status']}: {log}"
    t.update(fields)
    t["log"].append(f"{_now()} {log}")
    save_tickets(cwd, data)


def _land_from(settings: dict, root: Path, store: Path, checkout: Path, branch: str, base: str,
               report: Path, check: str, do_land: bool, push: bool, fork: str | None = None) -> dict:
    in_place = checkout.resolve() == root.resolve()
    sealed = git(["rev-parse", "HEAD"], checkout)
    try:  # a stacked branch replays only its own commits: robust to a squashed or corrected parent
        git(["rebase", "--onto", base, fork] if fork else ["rebase", base], checkout)
    except SysopError as exc:
        subprocess.run(["git", "rebase", "--abort"], cwd=str(checkout), capture_output=True)
        raise SysopError(f"{branch} does not rebase cleanly onto {base}; nothing changed. Rebase it "
                         f"in {checkout}, resolve the conflicts, then land again.\n{exc}")
    if fork and own_change(root, fork, sealed) != own_change(root, base, git(["rev-parse", "HEAD"], checkout)):
        git(["reset", "-q", "--hard", sealed], checkout)  # sysop's clean worktree, back to what was reviewed
        raise SysopError(f"{branch} changed while replaying onto {base} (its own change is not the reviewed "
                         "one); nothing landed. Re-review it, then land again")
    result: dict = {"branch": branch, "base": base, "review": str(report),
                    "check": {"cmd": check or None, "code": None}}
    if check:
        code, tail = run_check(check, checkout, int(settings.get("check_timeout") or 600))
        result["check"]["code"] = code
        if code != 0:
            raise SysopError(f"check `{check}` is red after rebase (exit {code}); not landing\n{tail}")
    if not (do_land or settings.get("land") is True):
        result.update(landed=False, ready=True, reason=NO_AUTHORITY,
                      next=f"`/sysop land` merges {branch} into {base} (or say \"land it\")")
        return result

    if in_place:
        git(["checkout", base], root)
    elif git(["rev-parse", "--abbrev-ref", "HEAD"], root) != base:
        raise SysopError(f"the main checkout is not on {base}; switch it there to land")
    git(["merge", "--ff-only", branch], root)
    result.update(landed=True, head=git(["rev-parse", "HEAD"], root),
                  next=f"nothing needed: the change is on {base}")

    # Only worktrees sysop created (inside the store) are removed; the operator's stay.
    if not in_place:
        if checkout.resolve().is_relative_to(store.resolve()):
            ok, detail = remove_worktree(root, checkout)
            result["worktree_removed"] = ok
            if not ok:
                result["worktree_left"] = detail
        else:
            result.update(worktree_removed=False, worktree_kept=str(checkout))
    review_tree = store / "wt" / f"review-{slug(branch)}"
    if review_tree.exists():  # a review that did not clean up after itself
        result["review_worktree_removed"] = remove_worktree(root, review_tree)[0]
    if not in_place and checkout.exists():
        # the branch is still checked out there, so it cannot be deleted
        result.update(branch_deleted=False, branch_left=f"still checked out in {checkout}")
    else:
        try:
            git(["branch", "-d", branch], root)
            result["branch_deleted"] = True
        except SysopError as exc:
            result.update(branch_deleted=False, branch_left=str(exc))

    if push:
        if "origin" not in git(["remote"], root).split():
            result.update(pushed=False, push_reason="no origin remote (local-only)")
        else:
            try:
                git(["push", "origin", base], root)
                result["pushed"] = True
            except SysopError as exc:
                result.update(pushed=False, push_reason=str(exc))
    return result


def cmd_cleanup(args: argparse.Namespace) -> int:
    root, store = main_root(args.cwd), store_path(args.cwd)
    path = Path(args.worktree).resolve()
    if not path.is_relative_to((store / "wt").resolve()):
        raise SysopError(f"refusing: {path} is not a sysop worktree (only {store / 'wt'}/* are)")
    ok, detail = remove_worktree(root, path)
    print(json.dumps({"worktree": str(path), "removed": ok, "detail": detail}))
    return 0 if ok else 1


def cmd_land(args: argparse.Namespace) -> int:
    if args.ready or args.branch is None:  # the operator's word for everything ready, in dependency order
        from fleet import land_ready  # noqa: PLC0415 - fleet imports land; this is the one way back
        out = land_ready(args.cwd, args.all)
        print(json.dumps(out, indent=2))
        return 1 if out.get("refused") else 0
    print(json.dumps(land(args.cwd, args.branch, args.base, args.review, args.check,
                          args.no_check, args.land, args.push), indent=2))
    return 0


ATTRIBUTION_RE = re.compile(r"co-authored-by:|generated (with|by) .*(claude|ai\b)", re.IGNORECASE)


REVIEW_MARKER = "<!-- sysop-review: {slug} -->"


def gh(args: list[str], cwd: str | Path, timeout: int = 120) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["gh", *args], cwd=str(cwd), capture_output=True, text=True, errors="replace",
                              timeout=timeout, stdin=subprocess.DEVNULL)
    except FileNotFoundError as exc:
        raise SysopError("the gh CLI is not installed") from exc


def gh_json(args: list[str], cwd: str | Path, timeout: int = 120):
    proc = gh(args, cwd, timeout)
    if proc.returncode != 0:
        raise SysopError(f"gh {' '.join(args[:2])} failed: {(proc.stderr or proc.stdout).strip()[-300:]}")
    return json.loads(proc.stdout or "null")


def remote_route(cwd: str) -> str | None:
    """None when the PR path is open; otherwise why this change stays local."""
    if "origin" not in git(["remote"], cwd).split():
        return "no origin remote"
    if shutil.which("gh") is None:
        return "the gh CLI is not installed"
    if gh(["auth", "status"], cwd, 30).returncode != 0:
        return "gh is not signed in"
    dry = subprocess.run(["git", "push", "--dry-run", "origin", "HEAD"], cwd=str(cwd), capture_output=True,
                         text=True, timeout=120, stdin=subprocess.DEVNULL)
    if dry.returncode != 0:
        return "no push access to origin: " + (dry.stderr.strip().splitlines() or ["refused"])[-1][:200]
    return None


def pr_open(cwd: str, base: str | None, title: str | None, body_file: str | None, draft: bool) -> dict:
    reason = remote_route(cwd)
    if reason:
        return {"local_only": reason}
    branch = git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    existing = gh_json(["pr", "list", "--head", branch, "--state", "all",
                        "--json", "number,url,state,baseRefName"], cwd)
    pr = next((p for p in existing if p["state"] == "OPEN"), None)
    if pr:
        git(["push", "origin", branch], cwd)  # new commits (a correction round) reach the PR
        return {"number": pr["number"], "url": pr["url"], "state": pr["state"], "base": pr["baseRefName"],
                "reused": True}
    merged = next((p for p in existing if p["state"] == "MERGED"), None)
    if merged and int(git(["rev-list", "--count", f"origin/{merged['baseRefName']}..HEAD"], cwd) or 0) == 0:
        return {"number": merged["number"], "url": merged["url"], "state": "MERGED", "base": merged["baseRefName"],
                "reused": True}  # a closed PR is never reopened silently: a new one is created below
    base = base or gh_json(["repo", "view", "--json", "defaultBranchRef"], cwd)["defaultBranchRef"]["name"]
    git(["fetch", "origin", base], cwd)
    if int(git(["rev-list", "--count", f"origin/{base}..HEAD"], cwd)) == 0:
        raise SysopError(f"{branch} has no commits ahead of origin/{base}: nothing to propose")
    if not title or not body_file or not Path(cwd, body_file).is_file():
        raise SysopError("a new PR needs --title and --body-file (problem, solution, validation)")
    body = Path(cwd, body_file).read_text()
    if load_settings(cwd).get("allow_attribution") is not True and ATTRIBUTION_RE.search(f"{title}\n{body}"):
        raise SysopError("the PR title or body credits an AI; this operator's PRs carry no AI attribution")
    git(["push", "-u", "origin", branch], cwd)
    proc = gh(["pr", "create", "--base", base, "--head", branch, "--title", title,
               "--body-file", str(Path(cwd, body_file)), *(["--draft"] if draft else [])], cwd)
    if proc.returncode != 0:
        raise SysopError(f"gh pr create failed: {(proc.stderr or proc.stdout).strip()[-300:]}")
    created = re.search(r"/pull/(\d+)", proc.stdout)  # by number: a branch may also have an old, closed PR
    view = gh_json(["pr", "view", created.group(1) if created else branch, "--json",
                    "number,url,headRefOid,baseRefName,state"], cwd)
    if view["headRefOid"] != git(["rev-parse", "HEAD"], cwd):
        raise SysopError(f"PR #{view['number']} head {view['headRefOid'][:12]} is not the local HEAD")
    return {"number": view["number"], "url": view["url"], "state": view["state"], "base": view["baseRefName"],
            "reused": False}


def pr_comment(cwd: str, number: int, report: str, slug_: str) -> dict:
    """One canonical review comment per PR: created once, then edited in place, then read back."""
    marker = REVIEW_MARKER.format(slug=slug_)
    body = f"{marker}\n{Path(cwd, report).read_text().strip()}\n"
    comments = gh_json(["api", f"repos/{{owner}}/{{repo}}/issues/{number}/comments", "--paginate"], cwd) or []
    ours = [c for c in comments if (c.get("body") or "").startswith(marker)]
    payload = Path(tempfile.mkstemp(prefix="sysop-comment-", suffix=".json")[1])
    try:
        payload.write_text(json.dumps({"body": body}))
        if ours:
            out = gh_json(["api", "-X", "PATCH", f"repos/{{owner}}/{{repo}}/issues/comments/{ours[0]['id']}",
                           "--input", str(payload)], cwd)
        else:
            out = gh_json(["api", "-X", "POST", f"repos/{{owner}}/{{repo}}/issues/{number}/comments",
                           "--input", str(payload)], cwd)
    finally:
        payload.unlink(missing_ok=True)
    back = gh_json(["api", f"repos/{{owner}}/{{repo}}/issues/comments/{out['id']}"], cwd)
    if (back.get("body") or "").strip() != body.strip():
        raise SysopError(f"the review comment {out['id']} did not read back as written")
    result = {"id": out["id"], "url": out.get("html_url"), "action": "edited" if ours else "created"}
    if len(ours) > 1:
        result["duplicates"] = [c["id"] for c in ours[1:]]  # left alone: older rounds, reported
    return result


def has_workflows(cwd: str, ref: str) -> bool:
    """Whether the commit carries GitHub Actions workflows (so `no checks` means `not started yet`)."""
    out = subprocess.run(["git", "ls-tree", "--name-only", ref, ".github/workflows/"], cwd=str(cwd),
                         capture_output=True, text=True)
    return out.returncode == 0 and bool(out.stdout.strip())


def pr_check_state(cwd: str, number: int) -> dict:
    proc = gh(["pr", "checks", str(number), "--json", "name,state,bucket,link"], cwd, 60)
    try:
        checks = json.loads(proc.stdout) if proc.stdout.strip() else []
    except json.JSONDecodeError:
        checks = []
    if not checks:
        return {"state": "none", "checks": []}
    failing = [{"name": c["name"], "link": c.get("link"),
                "run": (re.search(r"/actions/runs/(\d+)", c.get("link") or "") or [None, None])[1]}
               for c in checks if c.get("bucket") in ("fail", "cancel")]
    pending = [c["name"] for c in checks if c.get("bucket") == "pending"]
    state = "red" if failing else "pending" if pending else "green"
    return {"state": state, "checks": [{"name": c["name"], "bucket": c.get("bucket")} for c in checks],
            **({"failing": failing} if failing else {}), **({"pending": pending} if pending else {})}


def pr_checks(cwd: str, number: int, timeout: float, poll: float) -> dict:
    """Wait (bounded) until CI settles. A PR whose workflows have not registered yet gets a grace
    period before `none` is believed: a check that has not started is not a green one."""
    deadline = time.time() + timeout
    head = gh_json(["pr", "view", str(number), "--json", "headRefOid"], cwd)["headRefOid"]
    has_ci = has_workflows(cwd, head)
    grace = time.time() + float(os.environ.get("SYSOP_CI_GRACE", "90"))
    while True:
        st = pr_check_state(cwd, number)
        settled = st["state"] in ("green", "red") or (st["state"] == "none" and (not has_ci or time.time() > grace))
        if settled:
            return st
        if time.time() >= deadline:  # an unsettled `none` is CI that has not started: pending, never green
            return st if st["state"] == "pending" else {**st, "state": "pending",
                                                        "pending": ["CI has not reported yet"]}
        time.sleep(min(poll, max(0.0, deadline - time.time())))


REVIEW_PREFIX = "<!-- sysop-review:"


def pr_feedback(cwd: str, number: int, seen: set | None = None) -> dict:
    """Human feedback on a PR: reviews with a body or a request for changes, inline comments, and
    conversation comments other than sysop's own canonical review comment. Ids are `r`/`l`/`c` + GitHub's id;
    `new` holds what is not in `seen`."""
    def get(path: str) -> list:
        return gh_json(["api", f"repos/{{owner}}/{{repo}}/{path}", "--paginate"], cwd) or []
    items = []
    for r in get(f"pulls/{number}/reviews"):
        if r.get("state") == "CHANGES_REQUESTED" or (r.get("state") == "COMMENTED" and (r.get("body") or "").strip()):
            items.append({"id": f"r{r['id']}", "kind": "review", "state": r.get("state"),
                          "author": (r.get("user") or {}).get("login"), "body": (r.get("body") or "").strip(),
                          "url": r.get("html_url")})
    for c in get(f"pulls/{number}/comments"):
        items.append({"id": f"l{c['id']}", "kind": "line", "path": c.get("path"), "line": c.get("line"),
                      "author": (c.get("user") or {}).get("login"), "body": (c.get("body") or "").strip(),
                      "url": c.get("html_url")})
    for c in get(f"issues/{number}/comments"):
        if not (c.get("body") or "").startswith(REVIEW_PREFIX):
            items.append({"id": f"c{c['id']}", "kind": "comment", "author": (c.get("user") or {}).get("login"),
                          "body": (c.get("body") or "").strip(), "url": c.get("html_url")})
    seen = seen or set()
    new = [i for i in items if i["id"] not in seen]
    return {"number": number, "items": items, "new": new,
            "changes_requested": any(i.get("state") == "CHANGES_REQUESTED" for i in new)}


def quote_feedback(items: list[dict]) -> str:
    """The correction brief's evidence: each item quoted, with where it is and its link."""
    lines = ["Review feedback on the pull request (address every item, then commit):"]
    for i in items:
        where = f" on {i['path']}:{i.get('line') or '?'}" if i["kind"] == "line" else ""
        what = "requested changes" if i.get("state") == "CHANGES_REQUESTED" else i["kind"]
        body = "\n".join("> " + line for line in (i["body"] or "(no text)").splitlines())
        lines.append(f"- {i.get('author') or 'someone'} ({what}{where}, {i.get('url') or 'no link'}):\n{body}")
    return "\n".join(lines)


def pr_merge(cwd: str, number: int, branch: str, review: str | None, do_land: bool) -> dict:
    """Merge through GitHub under the same gates as a local land, then tidy the main checkout."""
    settings, root = load_settings(cwd), main_root(cwd)
    try:
        report = review_gate(cwd, branch, review)
    except SysopError:
        try:  # a re-run after the merge: the branch is gone, and nothing is left to gate
            view = gh_json(["pr", "view", str(number), "--json", "state,url"], cwd)
        except SysopError:
            view = {}
        if view.get("state") == "MERGED":
            return {"merged": True, "already": True, "number": number, "url": view["url"]}
        raise
    if not (do_land or settings.get("land") is True):
        return {"merged": False, "ready": True, "number": number, "reason": NO_AUTHORITY,
                "next": f"`/sysop land` merges PR #{number} (or say \"land it\")"}
    view = gh_json(["pr", "view", str(number), "--json",
                    "state,mergeable,reviewDecision,headRefName,baseRefName,headRefOid,url"], cwd)
    if view["state"] == "MERGED":
        return {"merged": True, "already": True, "number": number, "url": view["url"]}
    if view["headRefName"] != branch:
        raise SysopError(f"PR #{number} is for {view['headRefName']}, not {branch}")
    local = git(["rev-parse", f"refs/heads/{branch}"], root)
    if view["headRefOid"] != local:
        raise SysopError(f"PR #{number} head {view['headRefOid'][:12]} is not the reviewed {branch} {local[:12]}; "
                         "push the reviewed commits first")
    if view.get("mergeable") == "CONFLICTING":
        raise SysopError(f"PR #{number} conflicts with {view['baseRefName']}: rebase, re-check, push, then merge")
    tid = ticket_for(cwd, branch)[1]
    pr_rec = load_tickets(cwd)["tickets"].get(tid, {}).get("pr") if tid else None
    if pr_rec is not None:  # a PR lane: never merge over feedback the builder has not seen
        unseen = pr_feedback(cwd, number, set(pr_rec.get("seen") or []))["new"]
        if unseen:
            raise SysopError(f"PR #{number} has {len(unseen)} review item(s) the ticket has not taken in; "
                             "run `sysop.py next`, which sends them to the builder")
    if view.get("reviewDecision") in ("REVIEW_REQUIRED", "CHANGES_REQUESTED"):
        raise SysopError(f"PR #{number} needs a human review on GitHub ({view['reviewDecision']}); not bypassed")
    ci = pr_check_state(cwd, number)
    if ci["state"] == "none" and has_workflows(root, local):
        ci = {"state": "pending", "pending": ["CI has not reported yet (the PR head has workflows)"]}
    if ci["state"] in ("red", "pending"):
        raise SysopError(f"PR #{number} checks are {ci['state']}: {ci.get('failing') or ci.get('pending')}; "
                         "not merging")
    allowed = gh_json(["repo", "view", "--json", "mergeCommitAllowed,squashMergeAllowed,rebaseMergeAllowed"], cwd)
    method = next((m for m, key in (("merge", "mergeCommitAllowed"), ("squash", "squashMergeAllowed"),
                                    ("rebase", "rebaseMergeAllowed")) if allowed.get(key)), "merge")
    proc = gh(["pr", "merge", str(number), f"--{method}", "--match-head-commit", local], cwd)
    if proc.returncode != 0:
        raise SysopError(f"gh pr merge failed: {(proc.stderr or proc.stdout).strip()[-300:]}")
    base = view["baseRefName"]
    result: dict = {"merged": True, "number": number, "url": view["url"], "method": method, "review": str(report),
                    "checks": ci["state"], "next": f"nothing needed: PR #{number} is merged"}
    if tid:
        note_ticket(cwd, tid, status="landed", log=f"merged as PR #{number}", landed_at=time.time())
        close_issue(cwd, tid, f"Merged by sysop as PR #{number}.")
    subprocess.run(["git", "push", "origin", "--delete", branch], cwd=str(root), capture_output=True, timeout=120)
    try:  # the merge already happened on GitHub: a local hiccup is reported, never raised over it
        git(["fetch", "origin", base], root)
        if git(["rev-parse", "--abbrev-ref", "HEAD"], root) != base:
            raise SysopError(f"the main checkout is not on {base}")
        git(["merge", "--ff-only", f"origin/{base}"], root)
        result["main_updated"] = git(["rev-parse", "HEAD"], root)
    except SysopError as exc:
        result["main_left"] = f"{str(exc)[-200:]}; run `git pull --ff-only origin {base}` there when ready"
    tree = _worktrees(root).get(branch)
    store = ensure_store(cwd)
    if tree is not None and tree.resolve().is_relative_to(store.resolve()):
        result["worktree_removed"] = remove_worktree(root, tree)[0]
    elif tree is not None:
        result["worktree_kept"] = str(tree)
    try:
        git(["branch", "-d", branch], root)
        result["branch_deleted"] = True
    except SysopError as exc:  # a squash or rebase merge leaves the local commits unmerged by ancestry
        result.update(branch_deleted=False, branch_left=str(exc)[-200:])
    return result


def cmd_pr(args: argparse.Namespace) -> int:
    if args.action == "open":
        out = pr_open(args.cwd, args.base, args.title, args.body_file, args.draft)
    elif args.action == "comment":
        out = pr_comment(args.cwd, args.number, args.report, args.slug)
    elif args.action == "feedback":
        out = pr_feedback(args.cwd, args.number)
    elif args.action == "checks":
        out = pr_checks(args.cwd, args.number, args.timeout, args.poll)
        print(json.dumps(out, indent=2))
        return {"green": 0, "none": 0, "red": 1}.get(out["state"], 124)
    else:
        out = pr_merge(args.cwd, args.number, args.branch, args.review, args.land)
    print(json.dumps(out, indent=2))
    return 0


REPORT_TEMPLATES = {  # kind -> (template inside the plugin, path inside the store)
    "plan": ("skills/plan/templates/plan.md", "plans/{slug}.md"),
    "build": ("skills/build/templates/build-report.md", "reports/{slug}.build.md"),
    "review": ("skills/review/templates/review-report.md", "reports/{slug}.review.md"),
    "brief": ("skills/fleet/templates/brief.md", "briefs/{slug}.md"),
}


def cmd_report(args: argparse.Namespace) -> int:
    template, rel = REPORT_TEMPLATES[args.kind]
    if not TICKET_RE.match(args.slug):
        raise SysopError(f"invalid slug '{args.slug}'")
    path = ensure_store(args.cwd) / rel.format(slug=args.slug)
    if path.exists() and not args.fresh:
        print(path)  # keep the existing report: later passes update it in place
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text((PLUGIN_ROOT / template).read_text().replace("<slug>", args.slug))
    print(path)
    return 0


def review_verdict(report: Path) -> str | None:
    """The verdict token on the report's `Verdict:` line (READY, NEEDS FIXES, INCOMPLETE)."""
    for line in report.read_text(errors="replace").splitlines():
        if re.search(r"\bverdict:", line, re.IGNORECASE):
            found = {v.upper() for v in re.findall(r"\b(READY|NEEDS FIXES|INCOMPLETE)\b", line,
                                                   re.IGNORECASE)}
            # exactly one: an unfilled template line lists all three and must not pass
            return found.pop() if len(found) == 1 else None
    return None
