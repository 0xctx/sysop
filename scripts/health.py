"""One-screen answers: `status` (where the work stands, and the next step) and `doctor` (is this machine
ready, and the one fix for each thing that is not). Neither creates the store nor spends tokens.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time

import presets
from pathlib import Path

from fleet import _has_authority, explain_state, next_actions
from store import (
    PLUGIN_ROOT, SysopError, git, load_settings, load_tickets, main_root, registered_repos, roster_path, run_alive,
    store_path,
)
from workers import LIMIT_NAMES, active_cooldowns, load_roster, plan_reserved, plan_usage


MODS_SINCE = (2, 1, 287)  # the first Claude Code that loads a plugin's hooks module


def _run(cmd: list[str], timeout: int = 15) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return proc.returncode, (proc.stdout + proc.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def plan_line(settings: dict) -> str:
    """The Claude plan's limits as the mod last read them, and whether delegated work is kept off Claude."""
    usage = plan_usage()
    if not usage:
        return ""
    used = ", ".join(f"{LIMIT_NAMES.get(r.get('kind'), r.get('kind'))} {r['percentUsed']:g}%" for r in usage)
    held = plan_reserved(settings)
    return f"Claude plan: {used}" + (f"; delegated work goes elsewhere ({held})" if held else "")


def session_model(cwd: str, settings: dict) -> str | None:
    """The session model the active preset suggests (haiku, sonnet, opus), for the mod's mismatch hint."""
    try:
        return (presets.get(cwd, settings["preset"]) or {}).get("session") if settings.get("preset") else None
    except (SysopError, KeyError, TypeError):
        return None


def spend_today() -> float | None:
    try:
        import advice  # noqa: PLC0415 - the opt-in layer owns the spend record
        return advice.spend_today()
    except ImportError:
        return None


def spend_line(settings: dict) -> str:
    spent = spend_today()
    if spent is None:
        return "Spend today: unknown"
    budget = float(settings.get("budget") or 0)
    return f"Spend today (per-token): ${spent:.2f}" + (f" of ${budget:.2f} budget" if budget else "")


COLUMNS = ("needs_you", "working", "ready", "landed")


def board(repo: str, label: bool, rows: list | None = None) -> tuple[dict, list[str], bool]:
    """One repo's tickets in the four columns, plus its fleet hint and whether it is halted. With `rows`,
    also one record per ticket for the mod's board: where it is, its last log line, and what can be pressed."""
    fleet = next_actions(repo)  # moves statuses that facts decide, exactly as the coordinator would see them
    data = load_tickets(repo)
    settings = load_settings(repo, "")
    cols: dict = {c: [] for c in COLUMNS}
    for tid, t in data["tickets"].items():
        st = t["status"]
        name = (f"{Path(repo).name}:" if label else "") + (f"{t['lane']}/" if t.get("lane") else "") + tid
        column, actions = "working", ["why"]
        if st == "blocked":
            column = "needs_you"
            cols["needs_you"].append(f"{name}: {(t['log'] or ['?'])[-1]}")
        elif st in ("reviewed", "verified"):
            column = "ready"
            cols["ready"].append(name)
            lane_settings = load_settings(repo, t["lane"]) if t.get("lane") else settings
            if not _has_authority(data, lane_settings, t):
                column = "needs_you"
                actions.insert(0, "land")
                cols["needs_you"].append(f"{name}: ready; merging waits for your word (`/sysop land`)")
        elif st == "landed":
            column = "landed"
            cols["landed"].append(name)
        else:
            cols["working"].append(f"{name} ({'in review' if st == 'built' else st})")
        if rows is not None:
            reports = Path(repo) / ".sysop" / "reports"
            report = next((str(p) for p in (reports / f"sysop-{tid}.review.md", reports / f"sysop-{tid}.build.md")
                           if p.is_file()), None)
            rows.append({"id": tid, "name": name, "repo": repo, "lane": t.get("lane"), "status": st,
                         "column": column, "last": (t.get("log") or [""])[-1], "report": report,
                         "actions": actions + ([] if column == "landed" else ["halt"])})
    hint = fleet["next"]
    if hint.startswith("do each action"):  # the coordinator's instruction, said for a person
        due = [a for a in fleet["actions"] if a["do"] not in ("wait", "blocked", "done")]
        hint = f"{len(due)} step(s) due" if due else "workers are building"
    return cols, [hint], fleet["halted"]


def status(cwd: str, everywhere: bool = False) -> dict:
    settings = load_settings(cwd)
    where = store_path(cwd)
    usage = plan_usage()
    out: dict = {"preset": presets.status_line(cwd), "runs": [], "workers_live": 0,
                 **{c: [] for c in COLUMNS}, "resting": [], "spend": spend_line(settings),
                 "plan": plan_line(settings), "tickets": [], "repos": [], "halted": [],
                 "plan_percent": max((r["percentUsed"] for r in usage), default=None),
                 "spend_usd": spend_today(), "session_model": session_model(cwd, settings)}
    cooling = active_cooldowns()
    out["resting"] = sorted(f"{entry} until {c['until_iso']}" for entry, c in cooling.items())
    if where.is_dir():
        for marker in sorted((where / "active").glob("*.json")):
            try:
                data = json.loads(marker.read_text())
            except (OSError, ValueError):
                continue
            state = "paused" if data.get("paused") else "active"
            out["runs"].append(f"{marker.stem} ({state}, step {data.get('step', '?')})")
    repos = registered_repos() if everywhere else []
    if (where / "tickets.json").is_file() and str(main_root(cwd)) not in repos:
        repos.insert(0, str(main_root(cwd)))
    hints = []
    for repo in repos:
        out["workers_live"] += sum(1 for pid in (Path(repo) / ".sysop" / "runs").glob("*/*/pid")
                                   if not (pid.parent / "exit").exists() and run_alive(pid.parent))
        try:
            cols, hint, halted = board(repo, label=everywhere, rows=out["tickets"])
            out["repos"].append(repo)
        except (SysopError, ValueError, KeyError) as exc:  # one broken record must not hide the rest
            out["needs_you"].append(f"{Path(repo).name}: the ticket file cannot be read ({exc}); "
                                    "fix or remove .sysop/tickets.json")
            continue
        for c in COLUMNS:
            out[c] += cols[c]
        if halted:
            out["halted"].append(repo)
            out["needs_you"].append(f"{Path(repo).name}: halted; `sysop.py unhalt` resumes it")
        hints += [f"{Path(repo).name}: {h}" if everywhere else h for h in hint]
    if out["needs_you"]:
        out["next"] = out["needs_you"][0]
    elif hints:
        out["next"] = "; ".join(hints)
        if "step(s) due" in out["next"] and not out["runs"]:  # nobody is coordinating: say how to start
            out["next"] += " (`/sysop:fleet --all --yes` runs them)"
    else:
        out["next"] = ("set sysop up for this repo: `/sysop:setup`" if not settings.get("check")
                       else "say what you want: `/sysop:go <a change, a batch or an idea>`")
    return out


def render_status(s: dict) -> str:
    lines = [s["preset"] or "sysop preset: none"]
    lines.append("Runs: " + (", ".join(s["runs"]) if s["runs"] else "none")
                 + (f"; {s['workers_live']} worker(s) running" if s["workers_live"] else ""))
    titles = {"needs_you": "Needs you", "working": "Working", "ready": "Ready to land", "landed": "Landed"}
    for c in COLUMNS:
        if s[c]:
            lines.append(f"{titles[c]} ({len(s[c])}):")
            lines += [f"  {item}" for item in s[c][:12]] + ([f"  … and {len(s[c]) - 12} more"] if len(s[c]) > 12 else [])
    lines.append("Resting models: " + ("; ".join(s["resting"]) if s["resting"] else "none"))
    lines.append(s["spend"])
    if s.get("plan"):
        lines.append(s["plan"])
    lines.append(f"Next: {s['next']}")
    return "\n".join(lines)


def cmd_status(args: argparse.Namespace) -> int:
    s = status(args.cwd, args.all)
    print(json.dumps(s, indent=2) if args.json else render_status(s))
    return 0


def pi_sources(settings: dict, roster: dict, cooling: dict, probe_them: bool = False) -> dict:
    """Each pi source: absent (pi lists nothing from it), resting (its cooldown's reason), listed, or, with
    a probe (one 1-token request each, free models first), answering or refusing with the reason."""
    from workers import probe  # noqa: PLC0415
    models = (roster["workers"].get("pi") or {}).get("models") or []
    tiers = [e for es in settings.get("tiers", {}).values() for e in es]
    catalog = json.loads((PLUGIN_ROOT / "scripts" / "catalog.json").read_text())["models"]
    free = [e[3:] for e, info in catalog.items() if e.startswith("pi:") and info.get("cost") == "free"]
    states, pick = {}, {}
    for need, prefix in presets.PI_SOURCES.items():
        if need == "kaggle":
            continue  # served only during `serve up`: `serve status` speaks for it
        listed = [m for m in models if m.startswith(prefix)]
        rest = cooling.get(f"pi:{prefix}")
        if not listed:
            states[need] = "absent"
        elif rest:
            states[need] = f"resting ({rest.get('reason') or 'unavailable'})"[:120]
        else:
            states[need] = "listed"
            mine = [e[3:].split("@")[0] for e in tiers if e.startswith(f"pi:{prefix}")]
            pick[need] = next((m for m in mine + free if m in listed), None) or next(
                (m for m in listed if m.endswith(":free")), listed[0])
    if probe_them and pick:
        from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415
        with ThreadPoolExecutor(max_workers=4) as pool:
            answers = dict(zip(pick, pool.map(lambda n: probe(settings, f"pi:{pick[n]}", timeout=60), pick)))
        for need, (ok, _, reason) in answers.items():
            states[need] = "answering" if ok else f"refusing ({reason or 'no answer'})"[:120]
    return states


def doctor(cwd: str, probe_them: bool = False) -> list[dict]:
    """Each check: name, ok (True, False, or None for optional and absent), what was seen, and the one fix."""
    checks: list[dict] = []

    def add(name: str, ok: bool | None, seen: str, fix: str = "") -> None:
        checks.append({"check": name, "ok": ok, "seen": seen, "fix": "" if ok else fix})

    v = sys.version_info
    add("python", v >= (3, 11), f"{v.major}.{v.minor}.{v.micro}", "install Python 3.11 or newer")
    add("git", bool(shutil.which("git")), shutil.which("git") or "not found", "install git")
    try:
        root = git(["rev-parse", "--show-toplevel"], cwd)
        add("repository", True, root)
    except SysopError:
        add("repository", None, "not inside a git repository", "run sysop from inside a git repository")
    settings = load_settings(cwd)
    add("check command", bool(settings.get("check")), settings.get("check") or "not set",
        "`/sysop:setup` finds your test command and sets it")
    if shutil.which("claude"):
        code, text = _run(["claude", "auth", "status"])
        try:
            signed = json.loads(text).get("loggedIn") is True
        except ValueError:
            signed = code == 0
        add("claude", signed, "signed in" if signed else "installed, not signed in", "run `claude` once and sign in")
        _, version = _run(["claude", "--version"])
        parts = tuple(int(x) for x in version.split()[0].split(".")) if re.match(r"\d+\.\d+\.\d+\b", version) else ()
        add("mods", parts >= MODS_SINCE if parts else None, f"Claude Code {version.split()[0]}" if parts else "unknown",
            "mods off: the board and `/sysop` need Claude Code 2.1.287+ (`claude update`)")
    else:
        add("claude", False, "not found", "install Claude Code")
    cooling = active_cooldowns()
    if shutil.which("agy"):
        expired = any("sign-in expired" in (c.get("reason") or "") for e, c in cooling.items() if e.startswith("agy"))
        add("agy", not expired, "sign-in expired" if expired else "installed",
            "run `agy` once to sign in, then `sysop.py cooldown clear agy`")
    else:
        add("agy", None, "not installed (optional: Gemini on your Google plan)", "")
    roster = load_roster(settings, cwd=cwd)
    pi = roster["workers"].get("pi") or {}
    if shutil.which("pi"):
        _, version = _run(["pi", "--version"])
        parts = tuple(int(x) for x in version.split()[-1].split(".")[:2] if x.isdigit()) if version else ()
        add("pi", parts >= (0, 86), f"version {version or '?'}", "update pi: npm i -g @earendil-works/pi-coding-agent")
        states = pi_sources(settings, roster, cooling, probe_them)
        add("pi models", any(st in ("listed", "answering") for st in states.values()),
            ", ".join(f"{name} {st}" for name, st in states.items()),
            "export GEMINI_API_KEY (free at aistudio.google.com) or OPENROUTER_API_KEY, then `sysop.py roster --refresh`")
    else:
        add("pi", None, "not installed (optional: free and open models)", "")
    try:
        hooks = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text())["hooks"]
        files = [h["command"].split("/hooks/")[-1].strip('"') for entries in hooks.values()
                 for e in entries for h in e["hooks"]]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        files = ["hooks.json"]  # unreadable: reported as missing, never a crash
    missing = [f for f in files if not (PLUGIN_ROOT / "hooks" / f).is_file() or f == "hooks.json"]
    guard = PLUGIN_ROOT / "hooks" / "guard.py"
    probe = json.dumps({"tool_name": "Read", "tool_input": {"file_path": "/x/.ssh/id_rsa"}, "cwd": cwd})
    try:
        blocks = subprocess.run([sys.executable, str(guard)], input=probe, capture_output=True, text=True,
                                timeout=10).returncode == 2
    except (OSError, subprocess.TimeoutExpired):
        blocks = False
    add("hooks", not missing and blocks, "guard, context and stop gate wired; the guard blocks a key read"
        if not missing and blocks else f"missing {missing}" if missing else "the guard did not block a key read",
        "reinstall the plugin: `/plugin install sysop@sysop`")
    domain = (settings.get("serve") or {}).get("domain")
    if domain:
        add("kaggle", bool(shutil.which("kaggle")), shutil.which("kaggle") or "not found",
            "pip install kaggle, then `kaggle auth login`")
        add("ngrok domain", True, f"{domain} (its token goes up once with `sysop.py serve token`)")
    plan = plan_line(settings)
    if plan:
        add("claude plan", True, plan.removeprefix("Claude plan: "))  # a reserve held is working as meant
    ready = presets.status_line(cwd)
    if ready:
        add("preset", "✗" not in ready, ready.removeprefix("sysop preset: "),
            "`sysop.py preset show " + str(settings.get("preset")) + "` names what is missing")
    return checks


def cmd_doctor(args: argparse.Namespace) -> int:
    checks = doctor(args.cwd, args.probe)
    if args.json:
        print(json.dumps(checks, indent=2))
    else:
        for c in checks:
            mark = {True: "ok  ", False: "FAIL", None: "--  "}[c["ok"]]
            print(f"{mark} {c['check']:<14} {c['seen']}")
            if c["fix"] and c["ok"] is False:
                print(f"     fix: {c['fix']}")
    return 1 if any(c["ok"] is False and c["check"] in ("python", "git") for c in checks) else 0


def session_facts(cwd: str) -> dict:
    """The facts a skill needs about this session, in one line: whether a person is in it (interactive) or
    it runs headless (`claude -p`); whether to narrate each step for someone learning sysop (`explain`,
    auto until the repo's first landing); and whether the sysop mod will wake the coordinator (`wakes`)."""
    import os  # noqa: PLC0415
    try:
        narrate = explain_state(cwd)
    except SysopError:  # outside git: skills inject this, and an injected command must exit 0
        narrate = {"explain": True, "why": "no git repository"}
    return {"interactive": os.environ.get("CLAUDE_CODE_ENTRYPOINT") == "cli", "explain": narrate["explain"],
            "explain_why": narrate["why"], "wakes": mod_wakes(os.environ.get("CLAUDE_CODE_SESSION_ID"))}


def mod_wakes(session: str | None) -> bool:
    """The sysop mod is alive in this session (its heartbeat is under a minute old), so it will wake the
    coordinator when workers finish: the coordinator ends its turn instead of waiting."""
    if not session:
        return False
    try:
        beat = json.loads((roster_path().parent / f"mod-{session}.json").read_text())
        return time.time() - float(beat["at"]) < 60
    except (OSError, ValueError, KeyError, TypeError):
        return False


def cmd_session(args: argparse.Namespace) -> int:
    print(json.dumps(session_facts(args.cwd)))
    return 0


def add_parsers(sub) -> None:
    p = sub.add_parser("session", help="this session's facts: interactive, explain (narrate), wakes")
    p.set_defaults(func=cmd_session)
    p = sub.add_parser("status", help="one screen: what needs you, what is working, ready, landed; preset, spend, next")
    p.add_argument("--json", action="store_true")
    p.add_argument("--all", action="store_true", help="every lane in every repo sysop knows")
    p.set_defaults(func=cmd_status)
    p = sub.add_parser("doctor", help="is this machine ready? each failure with its one fix")
    p.add_argument("--json", action="store_true")
    p.add_argument("--probe", action="store_true", help="send one 1-token request to each pi source")
    p.set_defaults(func=cmd_doctor)
