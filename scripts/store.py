"""The store, settings and the small shared helpers every other module and every hook uses.

Hooks import only this module: it must stay free of intel, Kaggle and network code."""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

STORE_NAME = ".sysop"


PLUGIN_ROOT = Path(__file__).resolve().parent.parent  # plugin resources, never the user's project


# Built-in workers are fixed here and can never be redefined by a settings file:
# a stale custom entry once turned a read-only review into a write-enabled run.
# "review" commands are read-only: claude in plan permission mode, agy in plan mode
# (whose prompt must ask for the full answer as the final reply, or it writes artifacts),
# pi with only its read tools. pi ignores project-local pi resources (-na): a worktree's
# own .pi/ extensions are code the operator never approved.
BUILTIN_CMDS: dict[str, dict[str, str]] = {
    # "verify" commands drive a running app: curl, read-only otherwise (spike S1). agy has none: its
    # plan mode refuses the shell, and its build mode could edit the code it is judging.
    "claude": {
        # --output-format json: the reply plus its cost and tokens; worker_output() decodes it wherever a log is read
        "build": 'claude -p "$(cat {prompt_file})" --model {model} --dangerously-skip-permissions --output-format json',
        "review": 'claude -p "$(cat {prompt_file})" --model {model} --permission-mode plan --output-format json',
        "verify": ('claude -p "$(cat {prompt_file})" --model {model} --allowedTools "Bash(curl:*)" Read '
                   '--output-format json'),
    },
    "agy": {
        "build": 'agy -p "$(cat {prompt_file})" --model {model} --dangerously-skip-permissions',
        "review": 'agy -p "$(cat {prompt_file})" --model {model} --mode plan',
    },
    "pi": {
        "build": 'pi -p "$(cat {prompt_file})" --model {model} --no-session -na',
        "review": 'pi -p "$(cat {prompt_file})" --model {model} --no-session -na '
                  '--tools read,grep,find,ls --no-mcp',
        "verify": 'pi -p "$(cat {prompt_file})" --model {model} --no-session -na --tools read,bash --no-mcp',
    },
}


BUILTIN_WORKERS = tuple(BUILTIN_CMDS)


DEFAULTS: dict = {
    # The claude-plan preset (presets/claude-plan.json; a unit test keeps them equal): Claude builds,
    # Gemini through pi's free key backs it up, and the review comes from another vendor first
    # (the author's own family is skipped at route time). pi-free's pools are never a default.
    "preset": "claude-plan",
    "tiers": {
        "fast": ["claude:haiku", "pi:google/gemini-3.8-flash"],
        "standard": ["claude:sonnet", "pi:google/gemini-3.8-flash@high"],
        "deep": ["claude:opus", "claude:sonnet"],
        "review": ["agy:gemini-3.8-flash-high", "pi:google/gemini-3.8-flash@high", "claude:sonnet"],
        "verify": ["claude:haiku", "pi:google/gemini-3.8-flash"],
    },
    # size or task kind -> tier, or "auto:<skill>" (a chain built from the catalog).
    # A configured kind beats size. Kinds agents use: docs tests chore bugfix feature
    # refactor perf security migration writing research (any word works once mapped).
    "routes": {"small": "fast", "large": "standard", "risky": "deep",
               "security": "deep", "migration": "deep", "writing": "auto:writing"},
    "prefer": "balanced",  # auto chains: quality | balanced | economy
    "catalog": {},  # additions/overrides to scripts/catalog.json
    # models for the Claude agents sysop dispatches in-session ("inherit" = the session's model); the
    # claude-plan preset's choice, so an Opus session does not spend Opus on every reviewer
    "agents": {"default": "sonnet"},
    "workers": {},
    # named queues: {"<lane>": {"preset": "<name>", "max_parallel": N, "land": bool}}; `sysop.py lane`
    "lanes": {},
    "max_parallel_all": 6,  # workers running at once across every lane and repo (`next --all`)
    "land": False,
    "check": "",
    "check_timeout": 600,
    "stop_gate": False,
    "cross_review": True,
    "base": "",
    "allow_attribution": False,  # the guard refuses commits that credit an AI unless true
    "intel": False,  # opt-in: refresh model facts (network) on every roster rebuild and announce new models
    "follow_latest": False,
    "start": "",  # opt-in verification: the command that serves the app on $PORT (empty = off)
    "journeys": "JOURNEYS.md",  # plain-English journeys the verifier drives
    "health": "/health",  # answered once the app is up
    "verify_timeout": 600,  # seconds a verifier may run
    "explain": "auto",  # narrate each step in one plain line: auto = until this repo's first landed run
    "max_parallel": 3,  # fleet: workers running at once
    "max_attempts": 3,  # fleet: runs per ticket before it is blocked for the operator
    "learn": False,  # standing grant: apply `models learn` score changes from this repo's outcomes
    "budget": 0.0,  # USD a day of per-token spend (0 = none); near it, auto routes prefer free and plan models
    # percent of each Claude plan limit kept for you: past 100 - this, delegated work stops routing to claude:
    # (the sysop mod records the plan's usage; with no fresh record, nothing is held back)
    "claude_reserve": 20,
    "xreview_risky": 2,  # cross-vendor reviewers for a risky ticket, each from a different vendor (1 = one)
    # a ping when a run ends or needs a decision: a command given (title, message), e.g. "notify-send",
    # or "ntfy:<topic>" (ntfy.sh; messages hold only the run name and outcome). Empty = off.
    "notify": "",
    "doors": True,  # a rate-limited or signed-out entry falls back to the same model through another CLI first
    "review_timeout": 900,  # seconds a reviewer may run: a review reads one diff, so silence past this is failure
    "worker_timeout": 3600,  # seconds a launched worker may run before it is stopped (0 = none)
    # opt-in Kaggle serving: `serve up` runs one model behind the operator's ngrok static domain
    "serve": {"domain": "", "model": "qwen3.6-35b-a3b", "idle_minutes": 20, "max_hours": 4, "wait_minutes": 12,
              "models": {}},
}


class SysopError(Exception):
    """An expected failure: printed to stderr, exit status 1."""


def git(args: list[str], cwd: str | Path) -> str:
    """Run git and return stripped stdout; raise SysopError with git's own message."""
    try:
        proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                              errors="replace", check=False)
    except FileNotFoundError as exc:
        raise SysopError("git is not installed") from exc
    if proc.returncode != 0:
        # rebase and merge report conflicts on stdout, so keep both streams
        detail = "\n".join(x for x in (proc.stderr.strip(), _tail(proc.stdout, 15)) if x)
        raise SysopError(detail or f"git {' '.join(args)} failed")
    return proc.stdout.strip()


def write_json(path: Path, data, indent: int | None = 2) -> None:
    """Atomic replace: parallel workers and fallbacks must never leave a half-written state file."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=indent) + "\n")
    os.replace(tmp, path)


def _tail(text: str, lines: int) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def expand_check(check: str) -> str:
    """`sysop:prose-check [args]` is the writing gate of whichever sysop is installed (paths move with versions)."""
    sysop_py = f"{shlex.quote(sys.executable)} {shlex.quote(str(PLUGIN_ROOT / 'scripts' / 'sysop.py'))}"
    return re.sub(r"^\s*sysop:prose-check\b", f"{sysop_py} prose-check", check or "")


def run_check(check: str, where: str | Path, timeout: int, env: dict | None = None
              ) -> tuple[int, str]:
    """Run a check command in its own process group; on timeout kill the whole group.

    Killing only the shell would orphan the test processes it started.
    """
    proc = subprocess.Popen(expand_check(check), shell=True, cwd=str(where), env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            errors="replace", start_new_session=True)
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, 9)
        except ProcessLookupError:
            pass
        proc.communicate()
        return 124, f"check timed out after {timeout}s"
    return proc.returncode, _tail(out or "", 30)


def main_root(cwd: str | Path) -> Path:
    """The main checkout's root, identical from every linked worktree."""
    common = Path(git(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd))
    return common.parent if common.name == ".git" else common


def store_path(cwd: str | Path) -> Path:
    """Where the store lives, without creating it."""
    return main_root(cwd) / STORE_NAME


def ensure_store(cwd: str | Path) -> Path:
    """Create the store if needed. It ignores itself, so nothing in it is ever staged."""
    path = store_path(cwd)
    for sub in ("plans", "reports"):
        (path / sub).mkdir(parents=True, exist_ok=True)
    ignore = path / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n")
    return path


def cmd_store(args: argparse.Namespace) -> int:
    try:
        print(ensure_store(args.cwd))
    except SysopError:
        if not args.soft:
            raise
        # skills inject this at load time; a failing injection would silently abort the skill
        print("(no git repository: sysop artifacts unavailable)")
    return 0


def deep_merge(base: dict, over: dict) -> dict:
    """Dicts merge recursively; lists and scalars from `over` replace."""
    out = copy.deepcopy(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise SysopError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SysopError(f"{path} must hold a JSON object")
    return data


def settings_files(cwd: str | Path) -> list[Path]:
    files = [Path(os.path.expanduser("~")) / ".claude" / "sysop.json"]
    try:
        files.append(Path(git(["rev-parse", "--show-toplevel"], cwd)) / ".claude" / "sysop.json")
        files.append(store_path(cwd) / "settings.json")  # this machine's choices for the repo (untracked: autoland)
    except SysopError:
        pass  # outside git only the user layer applies
    return files


LANE_KEYS = ("preset", "max_parallel", "land")  # what a lane may set besides its preset's settings


def current_lane() -> str | None:
    """The lane this command acts for (`sysop.py --lane <name>`); None is the default lane."""
    return os.environ.get("SYSOP_LANE") or None


def load_settings(cwd: str | Path, lane: str | None = None) -> dict:
    """Merged settings: defaults, user, repo, then the lane's preset and limits (`lane`, or the
    `--lane` the command was given; pass "" for the default lane)."""
    merged = copy.deepcopy(DEFAULTS)
    for path in settings_files(cwd):
        merged = deep_merge(merged, _read_json(path))
    lane = current_lane() if lane is None else (lane or None)  # "" means the default lane, whatever the env says
    if lane:  # a lane runs on its own preset, cap and authority; everything else is the repo's
        conf = (merged.get("lanes") or {}).get(lane)
        if conf is None:
            raise SysopError(f"no lane '{lane}' (`sysop.py lane add {lane} --preset <name>` creates it)")
        if conf.get("preset"):
            import presets  # noqa: PLC0415 - presets imports store; loaded only for a lane
            merged = deep_merge(merged, presets.get(str(cwd), conf["preset"]).get("settings") or {})
            merged["preset"] = conf["preset"]
        merged.update({k: conf[k] for k in ("max_parallel", "land", "pr") if k in conf})
        merged["lane"] = lane
    workers = merged.get("workers") or {}
    for name in BUILTIN_WORKERS:
        if name in workers:
            print(f"sysop: ignoring custom worker '{name}': built-in workers cannot be redefined",
                  file=sys.stderr)
            del workers[name]
    merged["workers"] = workers
    return merged


def cmd_settings(args: argparse.Namespace) -> int:
    data = load_settings(args.cwd)
    if args.key:
        if args.key not in data:
            raise SysopError(f"unknown settings key: {args.key}")
        data = expand_check(data[args.key]) if args.key == "check" else data[args.key]
    print(json.dumps(data, indent=2))
    return 0


def roster_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "sysop" / "roster.json"


def claude_usage_path() -> Path:
    """The Claude plan's limits as the sysop mod last read them: {rateLimits: [{kind, percentUsed, resetsAt}], at}."""
    return roster_path().parent / "claude-usage.json"


# What each runnable model is (facts and benchmarks; scripts/intel.py), how fast it answered
# here, and which models the operator has already been told about. Machine-wide, beside
# the roster; written only by this script (guard-protected).

def intel_path() -> Path:
    return roster_path().parent / "intel.json"


def load_intel() -> dict:
    data = _read_json(intel_path()) if intel_path().is_file() else {}
    for key, empty in (("models", {}), ("latency", {}), ("sources", {}), ("seen", []), ("applied", [])):
        data.setdefault(key, empty)
    return data


TICKET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _proc_stat(pid: int) -> tuple[str, str] | None:
    """(state, start time) of a pid: from /proc on Linux, from `ps` elsewhere (macOS); None when unavailable."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        fields = stat[stat.rindex(")") + 2:].split()
        return fields[0], fields[19]
    except (OSError, ValueError, IndexError):
        if Path("/proc").is_dir():
            return None  # Linux, and the process is gone
    try:
        out = subprocess.run(["ps", "-o", "stat=", "-o", "lstart=", "-p", str(pid)], capture_output=True,
                             text=True, timeout=5).stdout.split()
        return (out[0][0], " ".join(out[1:])) if len(out) > 1 else None
    except (OSError, subprocess.SubprocessError, IndexError):
        return None


def run_alive(run: Path) -> bool:
    try:
        pid = int((run / "pid").read_text().strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    recorded = (run / "pstart").read_text().strip() if (run / "pstart").exists() else ""
    stat = _proc_stat(pid)
    if stat is None:
        return not Path("/proc").is_dir()  # nothing to read the process with: os.kill is all we have
    state, start = stat
    if state == "Z":
        return False  # exited; nobody has reaped it yet
    return not (recorded and recorded != start)  # a different start time means a reused pid


def slug(branch: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", branch).strip("-")


def _worktrees(cwd: str | Path) -> dict[str, Path]:
    """branch name -> checkout path, from `git worktree list --porcelain`."""
    out, path = {}, None
    for line in git(["worktree", "list", "--porcelain"], cwd).splitlines():
        if line.startswith("worktree "):
            path = Path(line[9:])
        elif line.startswith("branch refs/heads/") and path:
            out[line[18:]] = path
    return out


# Regenerable caches that running a check leaves in a worktree. Only these are ever
# deleted to let `git worktree remove` (never forced) succeed; any other untracked file
# could be uncommitted work and is reported instead.
CACHE_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".nox",
               "node_modules", ".coverage", ".hypothesis", ".cache"}


def remove_worktree(root: Path, path: Path) -> tuple[bool, str]:
    try:
        git(["worktree", "remove", str(path)], root)
        return True, ""
    except SysopError as first:
        status = git(["status", "--porcelain", "--untracked-files=all", "--ignored=no"], path)
        entries = [line for line in status.splitlines() if line.strip()]
        caches = [e[3:] for e in entries if e.startswith("?? ")
                  and (set(Path(e[3:]).parts) & CACHE_NAMES or e.endswith((".pyc", ".pyo")))]
        if not entries or len(caches) != len(entries):
            other = [e for e in entries if e[3:] not in caches]
            return False, f"{first}; not removed because of: {', '.join(other[:10])}"
        for rel in caches:
            target = path / rel
            top = next((path / Path(*Path(rel).parts[:i + 1]) for i, part in
                        enumerate(Path(rel).parts) if part in CACHE_NAMES), target)
            if top.is_dir():
                shutil.rmtree(top, ignore_errors=True)
            elif target.exists():
                target.unlink()
        try:
            git(["worktree", "remove", str(path)], root)
            return True, "removed regenerable caches first"
        except SysopError as second:
            return False, str(second)


CONFIG_KEYS = ("check", "check_timeout", "land", "explain", "start", "journeys", "health", "verify_timeout",
               "max_parallel", "max_attempts", "stop_gate", "cross_review",
               "base", "allow_attribution", "prefer", "intel", "follow_latest", "worker_timeout", "review_timeout",
               "doors", "notify", "budget", "claude_reserve", "xreview_risky", "learn", "serve.domain", "serve.model", "serve.idle_minutes",
               "serve.max_hours", "serve.wait_minutes")  # tiers/routes/agents/catalog change through `models`


def settings_path(cwd: str, scope: str) -> Path:
    if scope == "user":
        return Path(os.path.expanduser("~")) / ".claude" / "sysop.json"
    if scope == "local":  # inside the store, which git ignores: a change here never dirties the checkout
        return ensure_store(cwd) / "settings.json"
    return Path(git(["rev-parse", "--show-toplevel"], cwd)) / ".claude" / "sysop.json"


def cmd_config(args: argparse.Namespace) -> int:
    if args.key not in CONFIG_KEYS:
        raise SysopError(f"unknown key '{args.key}' (one of {', '.join(CONFIG_KEYS)}; "
                         f"use `models` for tiers, routes and agents)")
    section, _, leaf = args.key.rpartition(".")
    if args.action == "get":
        current = load_settings(args.cwd)
        value = (current.get(section) or {}).get(leaf) if section else current.get(leaf)
        print(json.dumps(expand_check(value) if args.key == "check" else value))
        return 0
    try:
        value = json.loads(args.value)
    except json.JSONDecodeError:
        value = args.value  # bare words are strings: config set check "npm test"
    expected = type(DEFAULTS[section][leaf] if section else DEFAULTS[leaf])
    if expected is float and isinstance(value, int) and not isinstance(value, bool):
        value = float(value)  # `config set budget 5` means 5.0
    if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
        raise SysopError(f"{args.key} takes a {expected.__name__}, got {json.dumps(value)}")
    if args.key == "prefer" and value not in ("quality", "balanced", "economy"):
        raise SysopError("prefer is one of quality, balanced, economy")
    if args.key == "serve.domain" and value and not re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", value):
        raise SysopError("serve.domain is a bare host name, like <name>.ngrok-free.dev")
    path = settings_path(args.cwd, args.scope)
    data = _read_json(path)
    (data.setdefault(section, {}) if section else data)[leaf] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, data)
    print(f"{args.key} = {json.dumps(value)} ({path})")
    return 0


RUN_TTL = 24 * 3600  # a run marker older than this is a crash left behind, not a run in progress


def active_markers(cwd: str | Path) -> list[Path]:
    """Run markers in this repo's store and in every registered repo's: a coordinator that started its run
    from another repo (a lane across repos, `--all`) is still found by the Stop gate and the mod."""
    stores = []
    try:
        stores.append(store_path(cwd))
    except SysopError:
        pass  # outside git: only the registered repos
    stores += [Path(r) / STORE_NAME for r in registered_repos()]
    seen, out = set(), []
    for store in stores:
        key = os.path.realpath(store)
        if key not in seen:
            seen.add(key)
            out += sorted((store / "active").glob("*.json"))
    return out


def active_runs(cwd: str | Path, session: str | None) -> list[tuple[Path, dict]]:
    """Unfinished runs of exactly this session (a marker recorded without a session holds only a
    caller without one), younger than RUN_TTL. Never creates the store."""
    out = []
    for path in active_markers(cwd):
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if (data.get("session") == session and not data.get("paused")
                and time.time() - float(data.get("started") or 0) < RUN_TTL):
            out.append((path, data))
    return out


def tickets_path(cwd: str) -> Path:
    return ensure_store(cwd) / "tickets.json"


def load_tickets(cwd: str) -> dict:
    path = tickets_path(cwd)
    return json.loads(path.read_text()) if path.is_file() else {"tickets": {}}


def _now() -> str:
    return dt.datetime.now().strftime("%H:%M:%S")


def registry_path() -> Path:
    return roster_path().parent / "repos.json"


def register_repo(cwd: str | Path) -> None:
    """Remember a repo that holds tickets, so `next --all` and `status --all` can find it."""
    root = str(main_root(cwd))
    path = registry_path()
    try:
        repos = json.loads(path.read_text()) if path.is_file() else []
    except (OSError, ValueError):
        repos = []
    if root not in repos:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, [*repos, root])


def registered_repos() -> list[str]:
    """Registered repos that still hold tickets (a moved or deleted repo drops out by itself)."""
    try:
        repos = json.loads(registry_path().read_text())
    except (OSError, ValueError):
        return []
    return [r for r in repos if (Path(r) / STORE_NAME / "tickets.json").is_file()]


def find_repo(name: str) -> str | None:
    """A registered repo by its folder name (or full path)."""
    hits = [r for r in registered_repos() if r == name or Path(r).name == name]
    return hits[0] if len(hits) == 1 else None


def save_tickets(cwd: str | Path, data: dict) -> Path:
    """tickets.json (the record) plus tickets.md (for people); only sysop.py writes either."""
    write_json(tickets_path(cwd), data)
    register_repo(cwd)
    md = ensure_store(cwd) / "tickets.md"
    md.write_text(render_tickets(data) + "\n")
    return md


def render_tickets(data: dict) -> str:
    out = ["# Fleet tickets", "",
           f"**Base:** {data.get('base') or '-'} · **Landing authority:** "
           f"{data.get('authority') or 'none (stop at ready)'}", ""]
    for tid, t in data["tickets"].items():
        out += [f"## {t['lane'] + '/' if t.get('lane') else ''}{tid}: {t['title']}",
                f"- **Status:** {t['status']} · **Size:** {t['size']} · "
                f"**Kind:** {t.get('kind') or '-'} · "
                f"**Depends:** {', '.join(t['depends']) or 'none'} · **Worker:** {t.get('worker') or '-'}",
                "- **Request (verbatim):**", *[f"  > {line}" for line in t["request"].splitlines()],
                "- **Contract:**", *[f"  {line}" for line in t["contract"].splitlines()],
                "- **Log:**", *[f"  - {entry}" for entry in t["log"]], ""]
    return "\n".join(out)
