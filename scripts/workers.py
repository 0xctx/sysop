"""Workers: roster, model intel, routing, cooldowns, launch and wait, cross-family review, model advice."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import kaggle_serve  # scripts/kaggle_serve.py: one model served on Kaggle for a work session (opt-in)

from store import (
    _proc_stat, _read_json, _tail, BUILTIN_CMDS, BUILTIN_WORKERS, claude_usage_path, ensure_store, find_repo, git,
    intel_path, load_settings, main_root, remove_worktree, roster_path, run_alive, settings_path, slug, store_path,
    SysopError, TICKET_RE, write_json,
)

CLAUDE_MODELS = ["haiku", "sonnet", "opus"]  # claude has no list command; these are its aliases
CLAUDE_ID = re.compile(r"claude-[a-z0-9][a-z0-9.-]*")  # an exact release (claude-sonnet-4-6) pins a preset


EFFORTS = ("low", "medium", "high", "xhigh", "max")  # one vocabulary; EFFORT_FLAGS translates it


# worker -> (flag, translation): each CLI spells effort its own way
EFFORT_FLAGS = {
    "claude": ("--effort", {}),
    "agy": ("--effort", {"xhigh": "high", "max": "high"}),
    "pi": ("--thinking", {}),  # pi >= 0.86 accepts low..max
}


NO_ATTRIBUTION = '{"includeCoAuthoredBy": false, "attribution": {"commit": "", "pr": ""}}'


ROSTER_TTL = 24 * 3600


ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\r")


def strip_ansi(text: str) -> str:
    """Terminal colour and cursor codes cost tokens and mean nothing to an agent."""
    return ANSI.sub("", text)


def _result_line(lines: list[str]) -> tuple[int, dict] | None:
    for i in range(len(lines) - 1, max(-1, len(lines) - 201), -1):
        if lines[i].startswith("{") and '"type"' in lines[i]:
            try:
                rec = json.loads(lines[i])
            except json.JSONDecodeError:
                continue
            if rec.get("type") == "result":
                return i, rec
    return None


def result_record(log: str) -> dict | None:
    """The JSON result a CLI printed (claude --output-format json), if any."""
    found = _result_line(log.strip().splitlines())
    return found[1] if found else None


def worker_output(log: str) -> str:
    """What a worker said: the JSON result line replaced by its decoded reply; every other line kept,
    so an error printed around it stays visible."""
    lines = log.strip().splitlines()
    found = _result_line(lines)
    if found is None:
        return log
    i, rec = found
    return "\n".join(lines[:i] + [str(rec.get("result") or rec.get("error") or "")] + lines[i + 1:])


def run_usage(log: str) -> dict:
    """Cost and tokens a CLI stated about its own run; nothing when it stated nothing (never a guess)."""
    rec = result_record(log) or {}
    usage = rec.get("usage") or {}
    out = {}
    if isinstance(rec.get("total_cost_usd"), (int, float)):
        out["usd"] = round(rec["total_cost_usd"], 4)
    tokens = sum(v for k, v in usage.items() if k.endswith("_tokens") and isinstance(v, int))
    if tokens:
        out["tokens"] = tokens
    models = rec.get("modelUsage")
    if isinstance(models, dict) and models:  # claude names the exact release an alias resolved to
        out["resolved"] = max(models, key=lambda m: (models[m] or {}).get("outputTokens") or 0)
    return out


# Ordered: the first vendor whose marker appears anywhere in the full model id wins.
# Families are vendors, because independent review means a different company's model,
# whichever CLI (claude, agy, pi) serves it.
VENDORS = [
    ("anthropic", ("anthropic", "claude", "haiku", "sonnet", "opus")),
    ("google", ("google", "gemini", "gemma")),
    ("openai", ("openai", "gpt")),
    ("alibaba", ("alibaba", "qwen", "qwq")),
    ("meta", ("meta-", "meta/", "llama")),
    ("mistral", ("mistral", "codestral", "devstral", "magistral", "ministral")),
    ("deepseek", ("deepseek",)),
    ("moonshot", ("moonshot", "kimi")),
    ("zhipu", ("zhipu", "glm")),
    ("xai", ("xai", "grok")),
    ("minimax", ("minimax",)),
    ("microsoft", ("microsoft", "phi-")),
    ("nvidia", ("nvidia", "nemotron")),
    ("poolside", ("poolside", "laguna")),
    ("stepfun", ("stepfun", "step-")),
    ("cohere", ("cohere", "command-r")),
    ("liquid", ("liquid/", "lfm-")),
    ("inclusionai", ("inclusionai", "ling-")),
    ("thinkingmachines", ("thinkingmachines", "inkling")),
    ("sarvam", ("sarvam",)),
    ("rednote", ("dots-studio", "dots.llm", "rednote")),
]


def family(model: str) -> str:
    """The vendor behind a model id (provider prefix and @effort allowed); `unknown` otherwise."""
    m = model.lower().split("@")[0]
    for vendor, markers in VENDORS:  # a marker counts only where a name part starts ("inkling" holds "ling-")
        if any(re.search(r"(^|[^a-z0-9])" + re.escape(marker), m) for marker in markers):
            return vendor
    if re.search(r"(^|/)o\d", m):  # o1, o3, o4-mini
        return "openai"
    return "unknown"


def worker_bin(settings: dict, worker: str) -> str | None:
    if worker in BUILTIN_CMDS:
        return worker
    spec = settings["workers"].get(worker)
    if not isinstance(spec, dict):
        return None
    return spec.get("bin") or shlex.split(spec.get("build", "x"))[0]


LIST_CMDS = {"agy": ["agy", "models"], "pi": ["pi", "--list-models", "--offline"]}


NO_MODELS_HINT = {"pi": "pi lists no models: export GEMINI_API_KEY (free at aistudio.google.com) or "
                         "OPENROUTER_API_KEY, or log into a provider with `pi` then `/login <provider>`"}


def _size(text: str) -> int | None:
    found = re.fullmatch(r"([\d.]+)([KM]?)", text.upper())
    return int(float(found.group(1)) * {"": 1, "K": 1000, "M": 1_000_000}[found.group(2)]) if found else None


def list_models(worker: str, meta: dict | None = None) -> tuple[list[str] | None, str | None]:
    """The models a CLI offers: agy prints `id<TAB>name`; pi prints a `provider model ...` table.

    For pi, `meta` (when given) receives each model's context and thinking support from the table.
    """
    try:
        proc = subprocess.run(LIST_CMDS[worker], stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, errors="replace", timeout=60, env=worker_env())
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"{worker} model listing failed: {exc}"
    lines = strip_ansi(proc.stdout).splitlines()
    if worker == "agy":
        models = [line.split("\t", 1)[0].strip() for line in lines if "\t" in line]
    else:
        # rows follow the `provider model ...` header; without one, pi printed guidance instead
        head = next((i for i, line in enumerate(lines)
                     if [w.lower() for w in line.split()[:2]] == ["provider", "model"]), None)
        rows = [line.split() for line in lines[head + 1:]] if head is not None else []
        models = [f"{r[0]}/{r[1]}" for r in rows if len(r) >= 2]
        if meta is not None and head is not None:  # columns by header name, not position
            cols = [w.lower() for w in lines[head].split()]
            ctx_i = cols.index("context") if "context" in cols else None
            think_i = cols.index("thinking") if "thinking" in cols else None
            for r in rows:
                if len(r) == len(cols):
                    meta[f"{r[0]}/{r[1]}"] = {"ctx": _size(r[ctx_i]) if ctx_i is not None else None,
                                              "thinking": r[think_i].lower() == "yes" if think_i is not None else None}
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout).strip().splitlines()[-1:] or ["no output"]
        return None, f"{worker} model listing exit {proc.returncode}: {err[0]}"
    if not models:  # installed but signed in nowhere: offers nothing, so its entries are skipped
        return [], NO_MODELS_HINT.get(worker, f"{worker} lists no models")
    return models, None


def build_roster(settings: dict) -> dict:
    workers: dict = {}
    for name in [*BUILTIN_WORKERS, *settings["workers"]]:
        binary = worker_bin(settings, name)
        entry: dict = {"available": bool(binary and shutil.which(binary)), "models": None}
        if entry["available"] and name == "claude":
            entry["models"] = list(CLAUDE_MODELS)
        elif entry["available"] and name in LIST_CMDS:
            meta: dict = {}
            entry["models"], err = list_models(name, meta)
            if err:
                entry["error"] = err
            if meta:
                entry["meta"] = meta
        workers[name] = entry
    return {"checked_at": int(time.time()), "workers": workers}


def load_roster(settings: dict, refresh: bool = False, cwd: str | None = None) -> dict:
    """Workers and models (cached a day). A rebuild also refreshes model intel, never failing for it."""
    path = roster_path()
    if not refresh and path.is_file():
        try:
            cached = json.loads(path.read_text())
            fresh = time.time() - cached.get("checked_at", 0) < ROSTER_TTL
            if fresh and set(cached.get("workers", {})) >= {*BUILTIN_WORKERS, *settings["workers"]}:
                return cached
        except (json.JSONDecodeError, AttributeError):
            pass
    roster = build_roster(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, roster)
    # The opt-in layer: facts refresh here only when the operator asked (intel, or follow_latest's standing
    # grant); otherwise they refresh only when an auto route, a door or `models` uses them.
    if settings.get("intel") is True or settings.get("follow_latest") is True:
        try:
            import advice  # noqa: PLC0415
            advice.refresh_intel(settings, roster, cwd)
        except Exception as exc:  # noqa: BLE001 - intel is advice; routing must never depend on it
            print(f"sysop: model intel not refreshed: {exc}", file=sys.stderr)
    return roster


def cmd_roster(args: argparse.Namespace) -> int:
    print(json.dumps(load_roster(load_settings(args.cwd), args.refresh, args.cwd), indent=2))
    return 0


PLAN_WORKERS = ("agy",)  # runs on the operator's subscription, not per-token billing


def runnable(roster: dict) -> list[str]:
    return [f"{w}:{m}" for w, info in roster["workers"].items() if info.get("available")
            for m in info.get("models") or []]


# Quotas and rate limits belong to the account, not the repository, so cooldowns live
# beside the roster cache. The agent classifies a failure (judgement); this records it.

def cooldown_path() -> Path:
    return roster_path().parent / "cooldowns.json"


def active_cooldowns() -> dict:
    try:
        data = json.loads(cooldown_path().read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    now = time.time()
    return {k: v for k, v in data.items() if v.get("until", 0) > now}


def add_cooldown(entry: str, seconds: int, reason: str) -> dict:
    data = active_cooldowns()
    until = time.time() + seconds
    data[entry] = {"until": until, "reason": reason[:200],
                   "until_iso": dt.datetime.fromtimestamp(until, dt.timezone.utc).isoformat(timespec="minutes")}
    cooldown_path().parent.mkdir(parents=True, exist_ok=True)
    write_json(cooldown_path(), data)
    return data


def kaggle_down(entry: str) -> bool:
    """A `pi:kaggle/<model>` entry whose served session is not up (offline: serve.json only)."""
    worker, _, model = entry.partition(":")
    model = model.split("@")[0]
    return worker == "pi" and model.startswith("kaggle/") and not kaggle_serve.is_up(model[len("kaggle/"):])


def cooling_for(cooling: dict, entry: str) -> dict | None:
    """The cooldown covering an entry: exact, model, provider prefix (`pi:cline/`), or worker."""
    worker, _, model_effort = entry.partition(":")
    model = model_effort.split("@")[0]
    keys = [entry, f"{worker}:{model}", worker]
    if "/" in model:
        keys.insert(2, f"{worker}:{model.split('/')[0]}/")
    return next((cooling[k] for k in keys if k in cooling), None)


RESET_RE = re.compile(r"(?:resets? in|retry after|try again in|retrydelay\W*)\s*((?:\d+(?:\.\d+)?\s*[hms]\w*\s*)+|\d+)", re.I)


MAX_RESET = 7 * 86400  # a stated reset longer than a week is more likely a misread than a quota


def stated_reset(text: str) -> int | None:
    """Seconds until a quota resets, when the error states it ("Resets in 150h56m49s", "retry after 3600s").
    A quota of zero ("limit: 0", e.g. Gemini Pro on a free key) never resets: the longest cooldown."""
    if re.search(r"\blimit:\s*0\b", text):
        return MAX_RESET
    found = RESET_RE.search(text)
    if not found:
        return None
    spec = found.group(1)
    if spec.strip().isdigit():
        return min(int(spec), MAX_RESET)
    units = {"h": 3600, "m": 60, "s": 1}
    seconds = sum(float(n) * units[u.lower()] for n, u in re.findall(r"(\d+(?:\.\d+)?)\s*([hms])", spec, re.I))
    return min(int(seconds), MAX_RESET) or None


SIGNIN_RE = re.compile(r"No API key for provider: ([\w.-]+)")


AVAILABILITY_RE = re.compile(r"\b(402|429|503)\b|insufficient credits|quota|rate.?limit|overloaded|resource_exhausted|high demand|"
                             r"temporarily unavailable|authentication (failed|timed out)", re.IGNORECASE)


NOT_ENABLED_RE = re.compile(r"model_not_supported|does not exist or you do not have access", re.IGNORECASE)
# A bare "401" could be a test about HTTP status codes: only a 401 that says it is about the key counts.
KEY_REFUSED_RE = re.compile(r"\b401\b[^\n]{0,60}(unauthori[sz]ed|api.?key|authenticat)|incorrect api key|"
                            r"invalid[ _]api[ _]key", re.IGNORECASE)


def cool_refused(entry: str, text: str) -> str | None:
    """A source that answers but will not serve: a model the account may not use (24 h), or a key it
    refuses (the whole pi provider, a day, like a missing sign-in). Returns the reason, or None."""
    if NOT_ENABLED_RE.search(text):
        add_cooldown(entry, 86400, "this model is not enabled for your account (listed, but refused); "
                     f"`sysop.py cooldown clear {entry}` once it is")
        return "model not enabled for this account"
    if KEY_REFUSED_RE.search(text):
        provider = entry[3:].split("/")[0] if entry.startswith("pi:") and "/" in entry else None
        target = f"pi:{provider}/" if provider else entry
        add_cooldown(target, 86400, f"the key was refused: fix the key in pi ({provider or 'its'} provider), "
                     f"then `sysop.py cooldown clear {target}`")
        return "key refused"
    return None


def cool_unavailable(entry: str, text: str) -> str | None:
    """Record a cooldown when a worker's output says it was unavailable (quota, rate limit, overload,
    sign-in, a refused key or model), so the next route skips it at once. Returns the reason, or None for
    other failures."""
    if cool_signin(entry, text):
        return "provider not signed in"
    refused = cool_refused(entry, text)
    if refused:
        return refused
    if entry.startswith("agy:") and re.search(r"authentication (failed|timed out)", text, re.IGNORECASE):
        add_cooldown("agy", 86400, "agy sign-in expired: run `agy` once to sign in, then `sysop.py cooldown clear agy`")
        return "agy sign-in expired"
    if not AVAILABILITY_RE.search(text):
        return None
    if re.search(r"\b402\b|insufficient credits", text, re.IGNORECASE):  # credit does not come back by waiting
        add_cooldown(entry, 6 * 3600, f"out of credit: top up the account, then `sysop.py cooldown clear {entry}`")
        return "out of credit"
    line = next((l.strip() for l in reversed(text.splitlines()) if AVAILABILITY_RE.search(l)), "unavailable")
    add_cooldown(entry, stated_reset(text) or 300, line[:200])
    return line[:200]


def cool_signin(entry: str, text: str) -> bool:
    """A pi provider without a login fails every model it lists: cool the whole provider for a day."""
    found = SIGNIN_RE.search(text)
    if not found or not entry.startswith("pi:"):
        return False
    provider = found.group(1)
    add_cooldown(f"pi:{provider}/", 86400, f"not signed in: run `pi`, then `/login {provider}` or set its "
                 f"API key; then `sysop.py cooldown clear pi:{provider}/`")
    return True


def cmd_cooldown(args: argparse.Namespace) -> int:
    data = active_cooldowns()
    if args.action == "add":
        if ":" not in args.entry and args.entry not in (*BUILTIN_WORKERS,
                                                         *load_settings(args.cwd)["workers"]):
            raise SysopError(f"unknown worker '{args.entry}' (use worker or worker:model)")
        data = add_cooldown(args.entry, args.seconds, args.reason)
    elif args.action == "clear":
        data.pop(args.entry, None)
        write_json(cooldown_path(), data)
    print(json.dumps(data, indent=2))
    return 0


MAX_PROMPT_BYTES = 100_000  # the prompt travels as one argv string; Linux caps that at 128 KiB


def worker_cmd(settings: dict, worker: str, role: str, model: str, prompt_file: str,
               effort: str | None = None) -> str:
    try:
        size = Path(prompt_file).stat().st_size
    except OSError:
        size = 0  # not written yet; launch will read it
    if size > MAX_PROMPT_BYTES:
        raise SysopError(f"prompt file is {size} bytes (limit {MAX_PROMPT_BYTES}: it is passed as one "
                         f"argument). Put bulky context such as diffs in a file and reference its path.")
    template = (BUILTIN_CMDS.get(worker) or settings["workers"].get(worker) or {}).get(role)
    if not template:
        raise SysopError(f"worker '{worker}' has no '{role}' command")
    cmd = template.replace("{prompt_file}", shlex.quote(prompt_file)).replace(
        "{model}", shlex.quote(model))
    if "{effort}" in cmd:  # a custom worker places it itself
        return cmd.replace("{effort}", shlex.quote(effort or "medium"))
    if worker == "claude" and settings.get("allow_attribution") is not True:
        # a claude worker is its own session, outside sysop's guard: switch the trailer off at source
        cmd += " --settings " + shlex.quote(NO_ATTRIBUTION)
    if effort and worker in EFFORT_FLAGS:
        flag, translate = EFFORT_FLAGS[worker]
        return f"{cmd} {flag} {translate.get(effort, effort)}"
    return cmd


def resolve_tier(settings: dict, name: str, kind: str | None = None) -> str:
    """Explicit tier > configured kind > size. Returns a tier name or `auto:<skill>`."""
    routes = settings.get("routes", {})
    if name.startswith("auto:"):
        return name
    if name in settings["tiers"]:
        target = name
    elif kind and kind in routes:
        target = routes[kind]
    elif name in routes:
        target = routes[name]
    else:
        raise SysopError(f"unknown size, kind or tier: {name}")
    if not target.startswith("auto:") and target not in settings["tiers"]:
        raise SysopError(f"route '{kind or name}' points at unknown tier '{target}'")
    return target


USAGE_FRESH_SECONDS = 15 * 60
LIMIT_NAMES = {"five_hour": "5-hour", "seven_day": "weekly"}


def plan_usage() -> list[dict]:
    """The Claude plan's limits from the mod's fresh record (under 15 minutes old); [] when there is none."""
    try:
        data = json.loads(claude_usage_path().read_text())
        if time.time() - float(data["at"]) > USAGE_FRESH_SECONDS:
            return []
        return [r for r in data.get("rateLimits") or [] if isinstance(r.get("percentUsed"), (int, float))]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return []


def plan_reserved(settings: dict) -> str | None:
    """Why claude: entries rest for delegated work: a plan limit has reached the share kept for the operator."""
    try:
        reserve = float(settings.get("claude_reserve", 20))
    except (TypeError, ValueError):
        reserve = 20.0
    if reserve <= 0:
        return None
    worst = max(plan_usage(), key=lambda r: r["percentUsed"], default=None)
    if worst is None or worst["percentUsed"] < 100 - reserve:
        return None
    name = LIMIT_NAMES.get(worst.get("kind"), worst.get("kind") or "plan")
    return f"your Claude plan is at {worst['percentUsed']:g}% ({name}); kept for you"


def route(settings: dict, roster: dict, name: str, role: str = "build",
          exclude: list[str] | None = None, not_family: str | None = None,
          kind: str | None = None) -> dict:
    tier = resolve_tier(settings, name, kind)
    if tier.startswith("auto:"):
        import advice  # noqa: PLC0415 - auto chains are the opt-in layer's
        entries = advice.auto_chain(settings, roster, tier[5:])
    else:
        entries = settings["tiers"][tier]
    exclude = exclude or []
    avoid = family(not_family) if not_family else None
    cooling = active_cooldowns()
    skipped: list = []
    catalog = None  # loaded only when a door is needed
    reserved = plan_reserved(settings)

    def refusal(entry: str) -> tuple[str | None, bool]:
        """Why an entry cannot run now, and whether that is about availability (another door may work)."""
        worker, _, model_effort = str(entry).partition(":")
        model, _, effort = model_effort.partition("@")
        info = roster["workers"].get(worker)
        cool = cooling_for(cooling, entry)
        if not worker or not model:
            return "malformed entry (want worker:model)", False
        if effort and effort not in EFFORTS:
            return f"unknown effort '{effort}' (want one of {', '.join(EFFORTS)})", False
        if entry in exclude or f"{worker}:{model}" in exclude or worker in exclude:
            return "excluded", False
        if cool:
            return f"cooling down until {cool['until_iso']}: {cool.get('reason') or 'unavailable'}", True
        if reserved and worker == "claude":
            return reserved, False  # no door: another route to Claude is per-token money
        if kaggle_down(entry):
            return "Kaggle endpoint down (`sysop.py serve up` starts it)", True
        if worker not in BUILTIN_CMDS and worker not in settings["workers"]:
            return "unknown worker", False
        if not info or not info.get("available"):
            return f"{worker} not installed", True
        if offered(roster, f"{worker}:{model}") is False:
            return f"model not offered by {worker}", True
        if avoid and family(model) == avoid:
            return f"same vendor as the author ({avoid})", False
        return None, False

    def pick(entry: str, door_for: str | None = None) -> dict:
        worker, _, model_effort = entry.partition(":")
        model, _, effort = model_effort.partition("@")
        info = roster["workers"][worker]
        out = {"entry": entry, "worker": worker, "model": model, "effort": effort or None,
               "family": family(model), "tier": tier, "chain": list(entries), "role": role, "skipped": skipped}
        if door_for:
            out["door_for"] = door_for  # the same model, reached another way
        if effort and ((info.get("meta") or {}).get(model) or {}).get("thinking") is False:
            out["effort"], out["effort_dropped"] = None, "the model reports no reasoning support"
            out["entry"] = f"{worker}:{model}"
        return out

    for entry in entries:
        reason, availability = refusal(entry)
        if reason is None:
            return pick(entry)
        skipped.append({"entry": entry, "reason": reason})
        if availability and settings.get("doors", True):
            try:
                import advice  # noqa: PLC0415 - doors are the opt-in layer's
            except ImportError:  # the layer is optional: the tier's next entry still serves
                continue
            catalog = catalog if catalog is not None else advice.load_catalog(settings, roster)
            for door in advice.doors(entry, roster, cooling, catalog, settings):
                if refusal(door)[0] is None:
                    return pick(door, door_for=entry)
    return {"worker": None, "model": None, "tier": tier, "chain": list(entries), "role": role,
            "skipped": skipped}


def cmd_route(args: argparse.Namespace) -> int:
    settings = load_settings(args.cwd)
    pick = route(settings, load_roster(settings, cwd=args.cwd), args.name, args.role, args.exclude,
                 args.not_family, args.kind)
    if pick["worker"] and args.prompt_file:
        # absolute: the worker runs in its own worktree, where a relative path means nothing
        prompt = os.path.abspath(os.path.join(args.cwd, args.prompt_file))
        pick["cmd"] = worker_cmd(settings, pick["worker"], args.role, pick["model"], prompt,
                                 pick["effort"])
    if not pick["worker"]:
        pick.update(why_none(pick))
    print(json.dumps(pick, indent=2))
    return 0 if pick["worker"] else 3


def why_none(pick: dict) -> dict:
    """No model can take the work: say why in one line, and the one way forward."""
    cooling = active_cooldowns()
    resets = sorted(c["until_iso"] for e in pick["skipped"] if (c := cooling_for(cooling, e["entry"])))
    if resets and len(resets) == len(pick["skipped"]):
        return {"why": f"every model in this tier is resting after a quota or rate limit; the first one is "
                       f"back at {resets[0]}",
                "next": f"wait until {resets[0]}, or add another model to the tier (`sysop.py models`)"}
    return {"why": "no model in this tier can run here: " + "; ".join(f"{e['entry']}: {e['reason']}"
                                                                      for e in pick["skipped"]),
            "next": "install or sign in to one of them, or change the tier (`sysop.py models`)"}


def _attempts(ticket_dir: Path) -> list[int]:
    if not ticket_dir.is_dir():
        return []
    return sorted(int(p.name) for p in ticket_dir.iterdir() if p.name.isdigit())


def worker_env() -> dict:
    """The environment a worker gets: the supervisor's minus its Claude Code session identity.

    A launched CLI must not inherit CLAUDE_CODE_SESSION_ID, the messaging socket or its token:
    that would leak them to another vendor's process and blur which session owns what.
    """
    env = {k: v for k, v in os.environ.items()
           if not (k.startswith("CLAUDE_CODE_") or k in ("CLAUDECODE", "CLAUDE_PID"))}
    env["AUTO_FALLBACK"] = "false"  # pi-free: model switches happen in sysop, where they are recorded
    return env


def launch(cwd: str, ticket: str, workdir: str, cmd: str, worker: str | None = None,
           role: str = "build") -> dict:
    if not TICKET_RE.match(ticket):
        raise SysopError(f"invalid ticket id '{ticket}' (letters, digits, . _ -)")
    if not Path(workdir).is_dir():
        raise SysopError(f"working directory does not exist: {workdir}")
    ticket_dir = ensure_store(cwd) / "runs" / ticket
    attempts = _attempts(ticket_dir)
    if attempts:
        last = ticket_dir / str(attempts[-1])
        enforce_deadline(last)  # a worker past its deadline that nobody waited on is stopped, not "running"
        if not (last / "exit").exists() and run_alive(last):
            raise SysopError(f"ticket {ticket} attempt {attempts[-1]} is still running")
    attempt = (attempts[-1] if attempts else 0) + 1
    run = ticket_dir / str(attempt)
    run.mkdir(parents=True)
    (run / "cmd").write_text(cmd + "\n")
    if worker:
        (run / "worker").write_text(worker + "\n")  # the fact the ledger reads later
    log, exit_tmp, exit_file = run / "log", run / "exit.tmp", run / "exit"
    # The subshell keeps the exit code even when cmd itself calls `exit`.
    # The newline before `)` stops a trailing `# comment` in cmd from eating the parenthesis.
    wrapper = (f"( {cmd}\n) > {shlex.quote(str(log))} 2>&1; rc=$?; echo $rc > {shlex.quote(str(exit_tmp))};"
               f" mv {shlex.quote(str(exit_tmp))} {shlex.quote(str(exit_file))}")
    # A deadline, because some CLIs print nothing until they finish (pi -p): a silent log cannot
    # tell working from hung. A review reads one diff: a reviewer silent past review_timeout is dead,
    # and the next one takes over. It is enforced from Python (enforce_deadline, on every read of the
    # run's state), so no GNU `timeout` is needed: macOS has none.
    key = {"review": "review_timeout", "verify": "verify_timeout"}.get(role, "worker_timeout")
    seconds = int(load_settings(cwd).get(key) or 0)
    proc = subprocess.Popen(["bash", "-c", wrapper], cwd=workdir, env=worker_env(),
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)
    (run / "pid").write_text(f"{proc.pid}\n")
    started = time.time()
    (run / "started").write_text(f"{started}\n")
    if seconds > 0:
        write_json(run / "deadline", {"at": started + seconds, "seconds": seconds, "key": key})
    stat = _proc_stat(proc.pid)
    if stat:
        (run / "pstart").write_text(stat[1] + "\n")
    return {"ticket": ticket, "attempt": attempt, "pid": proc.pid, "log": str(log),
            "run_dir": str(run), "worker": worker}


def entry_cmd(settings: dict, roster: dict, entry: str, prompt_file: str, role: str = "build") -> str:
    """The exact command for a worker:model[@effort] entry: the same code path `route` uses."""
    worker, _, model_effort = entry.partition(":")
    model, _, effort = model_effort.partition("@")
    if not model or (worker not in BUILTIN_CMDS and worker not in settings["workers"]):
        raise SysopError(f"bad worker entry '{entry}': want worker:model[@effort] with a known worker")
    if effort and ((roster["workers"].get(worker) or {}).get("meta") or {}).get(model, {}).get("thinking") is False:
        effort = ""  # the model reports no reasoning support
    return worker_cmd(settings, worker, role, model, prompt_file, effort or None)


def cmd_launch(args: argparse.Namespace) -> int:
    if args.prompt_file:  # sysop builds the command: agents once mistyped flags across CLIs
        if args.cmd or not args.worker:
            raise SysopError("give either a command, or --worker with --prompt-file (not both)")
        settings = load_settings(args.cwd)
        prompt = os.path.abspath(os.path.join(args.cwd, args.prompt_file))
        args.cmd = entry_cmd(settings, load_roster(settings, cwd=args.cwd), args.worker, prompt, args.role)
    if not args.cmd:
        raise SysopError("nothing to run: give a command, or --worker with --prompt-file")
    worker = args.worker.partition("@")[0] if args.worker else None  # the ledger records worker:model
    print(json.dumps(launch(args.cwd, args.ticket, args.workdir, args.cmd, worker, args.role or "build"), indent=2))
    return 0


DEADLINE_GRACE = 10  # seconds between SIGTERM and SIGKILL for a worker past its deadline


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except OSError:
        return False


def enforce_deadline(run: Path) -> bool:
    """Stop a live worker past its recorded deadline: its whole process group (grandchildren too),
    then write exit 124 and say why in its log. True when this call stopped it."""
    try:
        limit = json.loads((run / "deadline").read_text())
        pgid = int((run / "pid").read_text().strip())  # the launcher leads its own group
    except (OSError, ValueError):
        return False
    if (run / "exit").exists() or time.time() < float(limit.get("at", 0)) or not run_alive(run):
        return False
    for sig, grace in ((signal.SIGTERM, DEADLINE_GRACE), (signal.SIGKILL, 2)):
        try:
            os.killpg(pgid, sig)
        except OSError:
            break
        end = time.time() + grace
        while _group_alive(pgid) and time.time() < end:
            time.sleep(0.1)
        if not _group_alive(pgid):
            break
    if (run / "exit").exists() or _group_alive(pgid):  # finished on its own first, or could not be stopped
        return False
    with (run / "log").open("a") as fh:
        fh.write(f"\nsysop: no result within {limit.get('seconds')}s ({limit.get('key')}); stopped\n")
    tmp = run / "exit.tmp"
    tmp.write_text("124\n")
    tmp.replace(run / "exit")
    return True


def run_state(run: Path) -> dict:
    enforce_deadline(run)
    started = float((run / "started").read_text()) if (run / "started").exists() else time.time()
    state: dict = {"attempt": int(run.name), "log": str(run / "log")}
    if (run / "exit").exists():
        state.update(state="exited", code=int((run / "exit").read_text().strip() or -1))
    elif run_alive(run):
        state.update(state="running", code=None)
    else:
        time.sleep(0.2)  # the wrapper may be between `echo` and `mv`
        if (run / "exit").exists():
            state.update(state="exited", code=int((run / "exit").read_text().strip() or -1))
        else:
            state.update(state="died", code=None)
    end = (run / "exit").stat().st_mtime if (run / "exit").exists() else time.time()
    state["seconds"] = round(end - started, 1)
    try:
        state["tail"] = strip_ansi(worker_output((run / "log").read_text(errors="replace"))).splitlines()[-15:]
    except OSError:
        state["tail"] = []
    return state


def wait(cwd: str, tickets: list[str], timeout: float, poll: float,
         any_done: bool = False) -> tuple[list[dict], bool]:
    """Until every ticket's worker finishes (or, with any_done, the first one); a ticket may be
    `<repo>:<ticket>` for another registered repo's ticket."""
    runs = {}
    for ticket in tickets:
        repo, _, tid = ticket.rpartition(":") if ":" in ticket else ("", "", ticket)
        root = find_repo(repo) if repo else cwd
        if not root:
            raise SysopError(f"no repo '{repo}' among the repos sysop knows")
        attempts = _attempts(store_path(root) / "runs" / tid)
        if not attempts:
            raise SysopError(f"no run recorded for ticket '{ticket}'")
        runs[ticket] = store_path(root) / "runs" / tid / str(attempts[-1])
    deadline = time.time() + timeout
    while True:
        states = [{"ticket": t, **run_state(r)} for t, r in runs.items()]
        finished = [s["state"] != "running" for s in states]
        done = any(finished) if any_done else all(finished)
        if done or time.time() >= deadline:
            return states, done
        time.sleep(poll)


def cmd_wait(args: argparse.Namespace) -> int:
    states, done = wait(args.cwd, args.tickets, args.timeout, args.poll, args.any)
    for state in states:  # the coordinator's context: a clean exit needs no tail (finding D5)
        if state["state"] == "exited" and state["code"] == 0:
            state.pop("tail", None)
    print(json.dumps(states, indent=2))
    return 0 if done else 124


def tried_workers(cwd: str, ticket: str) -> list[str]:
    ticket_dir = store_path(cwd) / "runs" / ticket
    return [(ticket_dir / str(n) / "worker").read_text().strip() for n in _attempts(ticket_dir)
            if (ticket_dir / str(n) / "worker").is_file()]


XREVIEW_ASK = (
    "You are an independent code reviewer from a different model family than the author. Review the "
    "change below against its request and contract. Report only real defects (wrong behavior, contract "
    "or acceptance not met, security, data loss, a test that proves nothing), each as: severity "
    "(Critical|Important|Suggestion) | file:line | issue | evidence | fix. If there are none, say so "
    "and name what you checked. Do NOT create any files or artifacts. Everything you need is below; "
    "shell commands are refused in this review mode, so read files when you need more context. Findings "
    "about code outside the changed files go under a heading OUT-OF-SCOPE. End with a heading COVERAGE "
    "and one line per changed file: `<path>: <what you checked>`; a review without it does not count. "
    "Write your complete review as your final reply, starting with the line REVIEW-START.")

COVERAGE_CAP = 20  # a review of a huge diff must name at least this many of its files


def uncovered(review: str, files: list[str]) -> list[str]:
    """Changed files a review never names (by path or file name); [] when it shows its coverage."""
    if not files:
        return []  # nothing changed, nothing to cover
    tail = review[review.rfind("COVERAGE"):] if "COVERAGE" in review else ""
    if not tail:
        return files
    missing = [f for f in files if f not in tail and Path(f).name not in tail]
    return missing if len(files) - len(missing) < min(len(files), COVERAGE_CAP) else []


def _xreview_paths(cwd: str, branch: str, n: int = 1) -> tuple[Path, Path, str, str]:
    """Reviewer n's report, prompt, the branch slug and its run name (the second reviewer's carry `-2`)."""
    store, s = ensure_store(cwd), slug(branch)
    suffix = "" if n == 1 else f"-{n}"
    return (store / "reports" / f"{s}.xreview{suffix}.md", store / "reports" / f"{s}.xreview{suffix}-prompt.md", s,
            f"xreview-{s}{suffix}")


def _xreview_record(out: Path, status: str, worker: str | None = None) -> None:
    """The machine record land trusts; agents may not write it (the guard protects it)."""
    write_json(out.with_suffix(".json"), {
        "status": status, "worker": worker, "sha256": hashlib.sha256(out.read_bytes()).hexdigest()})


def _xreview_start(cwd: str, branch: str, base: str, author_family: str, context: list[str],
                   exclude: list[str], workdir: str | None, n: int = 1, avoid: list[str] | None = None,
                   reviewers: int = 1) -> dict:
    """Start reviewer n: another vendor than the author's and, for the second, than the first reviewer's."""
    out, prompt, s, ticket = _xreview_paths(cwd, branch, n)
    params = out.with_name(f"{out.stem}-start.json")
    avoid = avoid or []
    write_json(params, {"base": base, "author_family": author_family, "context": context, "exclude": exclude,
                        "workdir": workdir, "avoid": avoid, "reviewers": reviewers})
    settings = load_settings(cwd)
    if settings.get("cross_review") is not True:
        out.write_text("**Cross-family review:** disabled by settings (cross_review: false)\n")
        _xreview_record(out, "disabled")
        return {"status": "disabled", "report": str(out)}
    if family(author_family) == "unknown":  # unknown matches no vendor, so nothing would be excluded
        sys.stderr.write(f"sysop: warning: author family '{author_family}' is no known vendor; "
                         "no reviewer is excluded as same-vendor\n")
    tried = tried_workers(cwd, ticket)  # reviewers that already failed on this branch
    taken = [e for e in settings["tiers"].get("review", []) if family(e.partition(":")[2]) in avoid]
    pick = route(settings, load_roster(settings, cwd=cwd), "review", "review", exclude + tried + taken,
                 author_family)
    if pick["worker"] and family(pick["model"]) in avoid:  # a door to the first reviewer's vendor
        pick = {"worker": None, "skipped": [*pick["skipped"], {"entry": pick["entry"],
                                                               "reason": "same vendor as the first reviewer"}]}
    if not pick["worker"]:
        reasons = "; ".join(f"{k['entry']}: {k['reason']}" for k in pick["skipped"])
        others = f" or the first reviewer's ({', '.join(avoid)})" if avoid else ""
        out.write_text(f"**Cross-family review:** unavailable (no reviewer outside the "
                       f"{author_family} family{others}): {reasons}\n")
        _xreview_record(out, "unavailable")
        return {"status": "unavailable", "report": str(out), "skipped": pick["skipped"]}
    if pick["worker"] == "claude":
        out.write_text("**Cross-family review:** the Claude reviewer agents are the other family "
                       f"(author family {author_family}); no external run\n")
        _xreview_record(out, "claude-agents", "claude")
        return {"status": "claude-agents", "report": str(out)}
    root = main_root(cwd)
    diff = git(["diff", f"{base}...{branch}"], root)
    ctx = "".join(f"\n\n## {Path(f).name}\n" + Path(f).read_text(errors="replace")
                  for f in context if Path(f).is_file())
    body = f"{XREVIEW_ASK}{ctx}\n\n## Diff ({base}...{branch})\n```diff\n{diff}\n```\n"
    if len(body.encode()) > MAX_PROMPT_BYTES:
        stat = git(["diff", "--stat", f"{base}...{branch}"], root)
        body = (f"{XREVIEW_ASK}{ctx}\n\n## Changed files ({base}...{branch})\n{stat}\n"
                f"The diff is too large to inline; read the files in your working directory.\n")
    prompt.write_text(body)
    if not workdir:  # the reviewer reads the change, not the base: a detached checkout at its head
        tree = ensure_store(cwd) / "wt" / ticket
        if not tree.exists():
            git(["worktree", "add", "--detach", str(tree), branch], root)
        workdir = str(tree)
    cmd = worker_cmd(settings, pick["worker"], "review", pick["model"], str(prompt), pick["effort"])
    rec = launch(cwd, ticket, workdir, cmd, f"{pick['worker']}:{pick['model']}", role="review")
    out.write_text(f"**Cross-family review:** running: {pick['worker']}:{pick['model']} "
                   f"(run {rec['run_dir']})\n")
    _xreview_record(out, "running", f"{pick['worker']}:{pick['model']}")
    return {"status": "running", "worker": f"{pick['worker']}:{pick['model']}", "report": str(out),
            "skipped": pick["skipped"]}


def _xreview_collect(cwd: str, branch: str, n: int, deadline: float) -> tuple[int, dict]:
    """Wait for reviewer n; when it produced nothing, start the next one, until the result is final."""
    out, _, s, ticket = _xreview_paths(cwd, branch, n)
    try:
        status = json.loads(out.with_suffix(".json").read_text()).get("status")
    except (OSError, ValueError):
        status = None
    if status in ("unavailable", "claude-agents", "disabled"):
        return 0, {"status": status, "report": str(out)}
    if not (store_path(cwd) / "runs" / ticket).is_dir():
        return 0, {"status": "not-started", "report": str(out)}
    while True:
        states, done = wait(cwd, [ticket], max(1.0, deadline - time.time()), 2.0)
        st = states[0]
        if not done:
            return 124, {"status": "running", "seconds": st["seconds"]}
        run = store_path(cwd) / "runs" / ticket / str(st["attempt"])
        worker = (run / "worker").read_text().strip() if (run / "worker").is_file() else "?"
        log = worker_output((run / "log").read_text(errors="replace"))
        review = log[log.find("REVIEW-START"):] if "REVIEW-START" in log else ""
        own_tree = ensure_store(cwd) / "wt" / ticket
        if own_tree.exists():
            remove_worktree(main_root(cwd), own_tree)
        params = json.loads(out.with_name(f"{out.stem}-start.json").read_text())
        files = git(["diff", "--name-only", f"{params['base']}...{branch}"], main_root(cwd)).split()
        missing = uncovered(review, files) if review else []
        if missing:  # a verdict that does not show it read the change is no review (2026-10-07: a bare READY)
            review, st = "", {**st, "state": "exited", "code": f"no coverage of {', '.join(missing[:5])}"}
        if st["state"] == "exited" and st["code"] == 0 and review:
            out.write_text(f"**Cross-family review:** {worker}, exit 0\n\n{review}\n")
            _xreview_record(out, "done", worker)
            return 0, {"status": "done", "worker": worker, "report": str(out)}
        tail = " | ".join(log.strip().splitlines()[-3:])[:300]
        cooled = cool_unavailable(worker, strip_ansi(log)) if worker != "?" else None
        if cooled:
            tail = f"cooling down: {cooled}"
        print(f"sysop: cross-family reviewer {worker} produced no review ({st['state']} "
              f"{st['code']}: {tail}); trying the next one", file=sys.stderr)
        if time.time() >= deadline:  # never start another reviewer past the caller's bound
            return 124, {"status": "running", "note": "deadline reached; collect again"}
        nxt = _xreview_start(cwd, branch, params["base"], params["author_family"], params["context"],
                             params["exclude"], params["workdir"], n, params.get("avoid"),
                             params.get("reviewers", 1))
        if nxt["status"] != "running":
            nxt["failed_before"] = tried_workers(cwd, ticket)
            return 0, nxt


def cmd_xreview(args: argparse.Namespace) -> int:
    out, _, s, _ = _xreview_paths(args.cwd, args.branch)
    if args.action == "start":
        settings = load_settings(args.cwd)
        reviewers = max(1, int(settings.get("xreview_risky") or 1)) if args.risky else 1
        first = _xreview_start(args.cwd, args.branch, args.base, args.author_family, args.context, args.exclude,
                               args.workdir, reviewers=reviewers)
        if reviewers > 1 and first["status"] == "running":  # a second vendor, neither the author's nor the first's
            first["second"] = _xreview_start(args.cwd, args.branch, args.base, args.author_family, args.context,
                                             args.exclude, args.workdir, 2, [family(first["worker"])], reviewers)
        print(json.dumps(first))
        return 0
    try:
        reviewers = json.loads(out.with_name(f"{out.stem}-start.json").read_text()).get("reviewers", 1)
    except (OSError, ValueError):
        reviewers = 1
    deadline = time.time() + args.timeout
    results = []
    for n in range(1, reviewers + 1):
        code, result = _xreview_collect(args.cwd, args.branch, n, deadline)
        if code:
            print(json.dumps(result))
            return code
        results.append(result)
    first = results[0]
    if reviewers > 1 and first["status"] == "done":  # one report, one section per reviewer
        second = results[1]
        out2 = _xreview_paths(args.cwd, args.branch, 2)[0]
        body = out2.read_text() if out2.is_file() else f"**Cross-family review:** {second['status']}\n"
        out.write_text(out.read_text() + f"\n## Second cross-family reviewer\n\n{body}")
        _xreview_record(out, "done", first["worker"] + (f"; {second['worker']}" if second.get("worker") else ""))
        first = {**first, "second": second}
    print(json.dumps(first))
    return 0


def offered(roster: dict, entry: str) -> bool | None:
    """Whether the entry's worker lists its model; None when the worker has no listing."""
    worker, _, model = entry.partition(":")
    model = model.split("@")[0]
    models = (roster["workers"].get(worker) or {}).get("models")
    if worker == "claude" and CLAUDE_ID.fullmatch(model):
        return True  # claude lists only its aliases; an exact release id is the CLI's to resolve
    return None if models is None else model in models


def entry_state(roster: dict, cooling: dict, entry: str) -> str:
    worker, _, model_effort = entry.partition(":")
    model = model_effort.split("@")[0]
    cool = cooling_for(cooling, entry)
    if not (roster["workers"].get(worker) or {}).get("available"):
        return "not installed"
    if cool:
        return f"cooling until {cool['until_iso']}"
    return "model not offered" if offered(roster, entry) is False else "ok"


def probe(settings: dict, entry: str, timeout: int = 90) -> tuple[bool, float, str]:
    """One tiny real request through the worker's build command."""
    worker, _, model_effort = entry.partition(":")
    model, _, effort = model_effort.partition("@")
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
        fh.write("Reply with exactly: OK")
    start = time.time()
    empty = tempfile.mkdtemp(prefix="sysop-probe-")  # a worker never starts inside a real repo
    try:
        cmd = worker_cmd(settings, worker, "build", model, fh.name, effort or None)
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, errors="replace",
                              timeout=timeout, stdin=subprocess.DEVNULL, env=worker_env(), cwd=empty)
        out = proc.stdout + proc.stderr
        ok = proc.returncode == 0 and "OK" in worker_output(proc.stdout)
        reason = "" if ok else (_tail(strip_ansi(worker_output(out)), 1) or f"exit {proc.returncode}")
    except subprocess.TimeoutExpired:
        ok, reason = False, f"no answer within {timeout}s"
    except SysopError as exc:
        ok, reason = False, str(exc)
    finally:
        os.unlink(fh.name)
        shutil.rmtree(empty, ignore_errors=True)
    return ok, round(time.time() - start, 1), reason


KIND_SKILL = {"writing": "writing", "docs": "writing", "research": "reasoning", "review": "review"}


SIZES = ("small", "large", "risky")




def cmd_models(args: argparse.Namespace) -> int:
    import advice  # noqa: PLC0415 - `models` is the opt-in layer's surface
    settings = load_settings(args.cwd)
    if args.action in ("set", "route", "agent"):
        path = settings_path(args.cwd, args.scope)
        data = _read_json(path)
        if args.action == "set":
            roster = load_roster(settings, cwd=args.cwd)
            for entry in args.entries:
                worker, _, model_effort = entry.partition(":")
                model, _, effort = model_effort.partition("@")
                if not model or (worker not in BUILTIN_CMDS and worker not in settings["workers"]):
                    raise SysopError(f"bad entry '{entry}': want worker:model[@effort], known worker")
                if effort and effort not in EFFORTS:
                    raise SysopError(f"bad effort in '{entry}': one of {', '.join(EFFORTS)}")
                if offered(roster, entry) is False:
                    print(f"sysop: warning: {worker} does not list '{model}' "
                          f"(see `models available {worker}`)", file=sys.stderr)
            data.setdefault("tiers", {})[args.tier] = args.entries
        elif args.action == "agent":
            if args.model not in ("inherit", *CLAUDE_MODELS):
                raise SysopError(f"agent model must be inherit or one of {', '.join(CLAUDE_MODELS)}")
            data.setdefault("agents", {})[args.name] = args.model
        else:
            if args.key in settings["tiers"]:  # an explicit tier name always wins, so this route would be dead
                raise SysopError(f"'{args.key}' is a tier name; route a size or task kind instead")
            if not args.target.startswith("auto:") and args.target not in settings["tiers"]:
                raise SysopError(f"unknown tier '{args.target}' (or use auto:<skill>)")
            data.setdefault("routes", {})[args.key] = args.target
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, data)
        print(f"wrote {path}")
        settings = load_settings(args.cwd)
    roster, cooling = load_roster(settings, cwd=args.cwd), active_cooldowns()
    if args.action == "available":
        catalog = advice.load_catalog(settings, roster)
        for worker, info in roster["workers"].items():
            if not info.get("available"):
                continue
            models = info.get("models") or []
            hits = [f"{worker}:{m}" for m in models
                    if not args.search or args.search.lower() in f"{worker}:{m}".lower()]
            if not hits:
                if info.get("error") and not args.search:
                    print(f"{worker}\n  ({info['error']})")
                continue
            print(worker)
            width = max(map(len, hits))
            for e in hits:
                skills = (catalog.get(e) or {}).get("skills")
                note = " ".join(f"{k}{v}" for k, v in skills.items()) if skills else ""
                print(f"  {e:<{width}}  {family(e.partition(':')[2]):<10} {note}")
        return 0
    if args.action == "learn":
        try:
            changes = advice.learn(args.cwd, settings, roster)
        except SysopError:  # outside git: tune injects this at load, and a failure aborts the skill
            print(json.dumps({"changes": [], "applied": False, "note": "no git repository"}))
            return 0
        applied = bool(changes) and (args.apply or settings.get("learn") is True)
        where = advice.apply_learned(args.cwd, changes) if applied else None
        print(json.dumps({"changes": changes, "applied": applied, **({"settings": str(where)} if where else {}),
                          **({} if changes else {"note": f"nothing to advice.learn yet (needs {advice.LEARN_MIN}+ tickets per "
                                                         "model and skill in this repo's ledger)"})}, indent=2))
        return 0
    if args.action == "advise":
        data = (advice.refresh_intel(settings, roster, args.cwd) if args.refresh
                else advice.current_intel(settings, roster, args.cwd))
        result = advice.advise(settings, roster, cooling, data, tier=args.target, cwd=args.cwd)
        if data.get("notice"):  # the operator is looking now: the session notice has done its job
            data["notice"] = None
            write_json(intel_path(), data)
        print(json.dumps(result, indent=2) if args.json else advice.render_advice(result))
        return 0
    if args.action == "check":
        tiers = settings["tiers"]
        names = list(tiers) if args.tier in (None, "all") else [args.tier]
        entries = sorted({e for n in names for e in tiers.get(n, [])
                          if entry_state(roster, cooling, e) == "ok"})
        from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = dict(zip(entries, pool.map(lambda e: probe(settings, e), entries)))
        width = max(map(len, results), default=0)
        for entry, (ok, secs, reason) in results.items():
            print(f"  {entry:<{width}}  {'ok' if ok else 'FAIL':<5} {secs:>5}s  {reason}")
            if ok:
                advice.record_latency(entry.partition("@")[0], secs)
            if not ok and not cool_signin(entry, reason) and not cool_refused(entry, reason):
                add_cooldown(entry, stated_reset(reason) or 300, f"preflight: {reason}")
        return 0 if all(ok for ok, _, _ in results.values()) else 1
    routes_to: dict = {}
    for key, target in settings.get("routes", {}).items():
        routes_to.setdefault(target, []).append(key)
    lines = []
    chains = {t: advice.auto_chain(settings, roster, t[5:]) for t in routes_to if t.startswith("auto:")}
    width = max(map(len, [e for es in [*settings["tiers"].values(), *chains.values()] for e in es]), default=0)
    for tier, entries in settings["tiers"].items():
        label = tier + (f"  <- {', '.join(routes_to[tier])}" if tier in routes_to else "")
        if tier == "review":
            label += "  (cross-family review)"
        lines.append(label)
        for entry in entries:
            model = entry.partition(":")[2].split("@")[0]
            lines.append(f"  {entry:<{width}}  {family(model):<10} {entry_state(roster, cooling, entry)}")
    for target, chain in chains.items():
        lines.append(f"{target}  <- {', '.join(routes_to[target])}  (prefer: {settings.get('prefer')})")
        lines += [f"  {e:<{width}}  {family(e.partition(':')[2]):<10} ranked" for e in chain] or ["  (nothing available)"]
    budget = float(settings.get("budget") or 0)
    spent = advice.spend_today()
    lines.append(f"Spend today (per-token only): ${spent:.2f}" + (f" of ${budget:.2f} budget" + (
        "  (near the limit: auto routes prefer free and plan models)" if spent >= 0.8 * budget else "")
        if budget else "  (no budget set)"))
    quota_file = roster_path().with_name("kaggle-quota.json")
    if quota_file.is_file():
        q = json.loads(quota_file.read_text())
        lines.append(f"Kaggle: {q.get('line')} (as of {q.get('at')})")
    lines.append("Claude agents: " + ", ".join(f"{k} = {v}" for k, v in settings.get("agents", {}).items())
                 + "  (ship/plan/build/review coordinate in the session's own model)")
    print("\n".join(lines))
    return 0


def billed(worker: str) -> str:
    """Whether a worker's stated cost is money spent (paid) or covered by a plan or free tier."""
    if worker == "claude":
        return "paid" if os.environ.get("ANTHROPIC_API_KEY") else "plan"
    return "plan" if worker in PLAN_WORKERS else "free" if worker == "pi" else "paid"


def spend_path() -> Path:
    return roster_path().with_name("spend.jsonl")


def ledger_summary(cwd: str) -> dict:
    path = store_path(cwd) / "ledger.jsonl"
    rows, bad = [], 0
    if path.is_file():
        for line in path.read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                bad += 1
    groups: dict = {}
    for r in rows:
        g = groups.setdefault(f"{r.get('worker')}:{r.get('model')}",
                              {"n": 0, "landed": 0, "check_pass": 0, "fallbacks": 0, "secs": []})
        g["n"] += 1
        g["landed"] += bool(r.get("landed"))
        g["check_pass"] += r.get("check") == "pass"
        g["fallbacks"] += int(r.get("fallbacks") or 0)
        if isinstance(r.get("seconds"), (int, float)):
            g["secs"].append(r["seconds"])
    for g in groups.values():
        secs = g.pop("secs")
        g["mean_seconds"] = round(sum(secs) / len(secs), 1) if secs else None
        g["enough_data"] = g["n"] >= 5
    return {"total": len(rows), "malformed": bad, "groups": groups, "landing": landing_waits(rows)}


def landing_waits(rows: list[dict]) -> dict:
    """R4's measurement: how long ready work waited to land (median), and the most tickets that were
    ready at the same moment. Batched landing is worth planning only when both are large."""
    spans = [(r["ready_at"], r["landed_at"]) for r in rows
             if isinstance(r.get("ready_at"), (int, float)) and isinstance(r.get("landed_at"), (int, float))]
    waits = sorted(b - a for a, b in spans)
    together = max((sum(1 for a, b in spans if a <= t < b) for t, _ in spans), default=0)
    median = (waits[len(waits) // 2] if len(waits) % 2 else (waits[len(waits) // 2 - 1] + waits[len(waits) // 2]) / 2
              ) if waits else None
    return {"n": len(waits), "median_seconds": round(median, 1) if median is not None else None,
            "most_ready_together": together}
