"""Named model presets: readable files that set tiers, routes and limits for what the operator has.

Shipped presets live in `presets/` at the plugin root; the operator's own in `~/.claude/sysop-presets/`
and the team's in `<repo>/.claude/sysop-presets/` (committed). Same name: repo beats user beats shipped.
A preset's `settings` are applied as-is; `summary`, `needs`, `capacity`, `caveats` and the rest are shown,
never applied. `preset use` first saves what it replaces as the preset `previous`, so one command undoes it.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from store import (
    BUILTIN_CMDS, PLUGIN_ROOT, SysopError, _read_json, git, load_settings, settings_files, settings_path,
    write_json,
)
from workers import EFFORTS, active_cooldowns, entry_state, family, load_roster

SHIPPED = PLUGIN_ROOT / "presets"
PRESET_KEYS = ("tiers", "routes", "prefer", "agents", "cross_review", "max_parallel", "budget")
META_KEYS = ("name", "summary", "session", "needs", "optional", "capacity", "caveats", "settings")
BUILD_TIERS = ("fast", "standard", "deep")
# What each `needs` word means, checked against the cached roster (never the network).
PI_SOURCES = {"openrouter": "openrouter/", "pi-free": "cline/", "gemini-key": "google/", "kaggle": "kaggle/",
              "kilo": "kilo/", "groq": "groq/"}
NEED_FIX = {
    "claude": "install Claude Code", "agy": "install agy and sign in", "pi": "install pi (npm i -g pi-coding-agent)",
    "openrouter": "export OPENROUTER_API_KEY (openrouter.ai/keys), then `sysop.py roster --refresh`",
    "pi-free": "install pi-free (optional; read its caveat first)",
    "gemini-key": "export GEMINI_API_KEY (free at aistudio.google.com), then `sysop.py roster --refresh`",
    "kaggle": "`sysop.py serve up` (it writes pi's kaggle provider)",
    "kilo": "sign in to Kilo's free gateway in pi (`pi`, then `/login kilo`), then `sysop.py roster --refresh`",
    "groq": "export GROQ_API_KEY (free at console.groq.com), then `sysop.py roster --refresh`",
}


def preset_dirs(cwd: str) -> list[tuple[str, Path]]:
    dirs = [("shipped", SHIPPED), ("user", Path(os.path.expanduser("~")) / ".claude" / "sysop-presets")]
    try:
        dirs.append(("repo", Path(git(["rev-parse", "--show-toplevel"], cwd)) / ".claude" / "sysop-presets"))
    except SysopError:
        pass  # outside git: shipped and user presets only
    return dirs


def load_all(cwd: str) -> dict[str, dict]:
    """name -> preset, with `_source` and `_path` added; later directories win."""
    found: dict[str, dict] = {}
    for source, folder in preset_dirs(cwd):
        for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
            try:
                data = _read_json(path)
                shape_ok(data)
            except SysopError as exc:  # one broken file of yours must not hide the others
                print(f"sysop: skipping preset {path}: {exc}", file=sys.stderr)
                continue
            data.setdefault("name", path.stem)
            found[path.stem] = {**data, "_source": source, "_path": str(path)}
    return found


def shape_ok(data: dict) -> None:
    settings = data.get("settings")
    tiers = settings.get("tiers", {}) if isinstance(settings, dict) else None
    if not isinstance(tiers, dict) or not all(
            isinstance(chain, list) and all(isinstance(e, str) for e in chain) for chain in tiers.values()):
        raise SysopError("want \"settings\": {\"tiers\": {\"<tier>\": [\"worker:model\", …]}, …}")
    for key in ("needs", "optional", "caveats"):
        if not isinstance(data.get(key, []), list):
            raise SysopError(f"{key} must be a list")


def get(cwd: str, name: str) -> dict:
    presets = load_all(cwd)
    if name not in presets:
        raise SysopError(f"no preset '{name}' (`sysop.py preset list` shows them)")
    return presets[name]


def entries(preset: dict) -> list[str]:
    return [e for chain in (preset.get("settings") or {}).get("tiers", {}).values() for e in chain]


def main_vendor(settings: dict) -> str | None:
    """The vendor whose models lead the build tiers (the one a reviewer must differ from)."""
    leads = [family(chain[0].partition(":")[2]) for t in BUILD_TIERS if (chain := settings.get("tiers", {}).get(t))]
    return Counter(leads).most_common(1)[0][0] if leads else None


def problems(preset: dict) -> list[str]:
    """The safety rules every preset must pass; [] when it does."""
    out = []
    settings = preset.get("settings") or {}
    for key in ("summary", "capacity"):
        if not str(preset.get(key) or "").strip():
            out.append(f"{key} is empty: every preset states it")
    if not preset.get("caveats"):
        out.append("caveats is empty: every preset states its limits")
    if unknown := sorted(set(settings) - set(PRESET_KEYS)):
        out.append(f"settings may only hold {', '.join(PRESET_KEYS)}; not {', '.join(unknown)}")
    tiers = settings.get("tiers") or {}
    for entry in entries(preset):
        worker, _, model_effort = entry.partition(":")
        model, _, effort = model_effort.partition("@")
        if worker not in BUILTIN_CMDS or not model:
            out.append(f"{entry}: want worker:model[@effort] with a known worker")
        elif effort and effort not in EFFORTS:
            out.append(f"{entry}: unknown effort '{effort}'")
    for entry in tiers.get("verify", []):
        if "verify" not in BUILTIN_CMDS.get(entry.partition(":")[0], {}):
            out.append(f"{entry}: its CLI cannot verify (claude or pi only)")
    words = [m.group(0) for w in (preset.get("needs") or []) + (preset.get("optional") or [])
             if (m := re.match(r"[\w-]+", str(w)))]  # an optional line leads with its word, then says why
    if unknown_words := sorted(set(words) - set(NEED_FIX)):
        out.append(f"needs/optional name what readiness can check ({', '.join(NEED_FIX)}); "
                   f"not {', '.join(unknown_words)}")
    named = " ".join(map(str, (preset.get("needs") or []) + (preset.get("optional") or [])))
    if any(e.startswith("pi:cline/") for e in entries(preset)) and "pi-free" not in named:
        out.append("pi:cline entries need pi-free named in needs or optional, so its caveat travels with them")
    lead = main_vendor(settings)
    if settings.get("cross_review", True) is False:
        if not any("cross_review" in c for c in preset.get("caveats") or []):
            out.append("cross_review: false must be stated in caveats")
    elif lead and "review" in tiers and not any(family(e.partition(":")[2]) != lead for e in tiers.get("review", [])):
        out.append(f"the review tier has no vendor other than the builders' ({lead})")
    return out


def need_met(need: str, roster: dict) -> bool:
    workers = roster.get("workers") or {}
    if need in PI_SOURCES:
        pi = workers.get("pi") or {}
        return bool(pi.get("available")) and any(m.startswith(PI_SOURCES[need]) for m in pi.get("models") or [])
    return bool((workers.get(need) or {}).get("available"))


def readiness(preset: dict, roster: dict, cooling: dict | None = None) -> dict:
    """ready (every entry can run), partly (needs met, some entries cannot), missing (a need is not met)."""
    cooling = active_cooldowns() if cooling is None else cooling
    missing = [n for n in preset.get("needs") or [] if not need_met(n, roster)]
    states = {e: entry_state(roster, cooling, e) for e in dict.fromkeys(entries(preset))}
    bad = {e: s for e, s in states.items() if s != "ok"}
    tiers = (preset.get("settings") or {}).get("tiers") or {}
    empty = [t for t, chain in tiers.items() if not any(states[e] == "ok" for e in chain)]
    status = "missing" if missing or empty else "partly" if bad else "ready"
    return {"status": status, "missing": missing, "fixes": [NEED_FIX.get(n, n) for n in missing],
            "empty_tiers": empty, "entries": states}


MARK = {"ready": "✓", "partly": "◐", "missing": "✗"}


def readiness_line(name: str, ready: dict) -> str:
    line = f"{MARK[ready['status']]} {name}"
    if ready["missing"]:
        return line + ": missing " + ", ".join(ready["missing"])
    if ready["empty_tiers"]:
        return line + ": nothing can run in " + ", ".join(ready["empty_tiers"])
    unready = sum(1 for s in ready["entries"].values() if s != "ok")
    return line + (f": {unready} model(s) unavailable, the rest stand in" if unready else ": ready")


def shadowed(cwd: str, scope: str, keys) -> list[str]:
    """Keys a preset written at user scope would not change, because the repo's settings set them."""
    if scope != "user":
        return []
    files = settings_files(cwd)
    repo = _read_json(files[1]) if len(files) > 1 else {}
    return [k for k in keys if k in repo]


def diff_lines(current: dict, settings: dict) -> list[str]:
    lines = []
    for key, new in settings.items():
        old = current.get(key)
        if isinstance(new, dict):
            for sub, value in new.items():
                was = (old or {}).get(sub) if isinstance(old, dict) else None
                if was != value:
                    lines.append(f"  {key}.{sub}: {json.dumps(was)} -> {json.dumps(value)}")
        elif old != new:
            lines.append(f"  {key}: {json.dumps(old)} -> {json.dumps(new)}")
    return lines


def use(cwd: str, name: str, scope: str) -> dict:
    preset = get(cwd, name)
    if name != "previous" and (issues := problems(preset)):  # undo restores your own settings as they were
        raise SysopError(f"preset '{name}' fails its safety rules: " + "; ".join(issues))
    path = settings_path(cwd, scope)
    data = _read_json(path)
    settings = preset["settings"]
    keys = [*settings, "preset"]
    if name != "previous":
        before = {k: data[k] for k in keys if k in data}
        user_dir().mkdir(parents=True, exist_ok=True)
        write_json(user_dir() / "previous.json", {
            "name": "previous",
            "summary": f"your {scope} settings before `preset use {name}` "
                       f"({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC)",
            "needs": [], "optional": [], "capacity": "as before", "caveats": ["`preset use previous` restores it."],
            "settings": {k: v for k, v in before.items() if k != "preset"},
            "unset": [k for k in keys if k not in data], "restores": before.get("preset"), "scope": scope})
    for key in preset.get("unset") or []:
        data.pop(key, None)
    data.update(settings)
    restored = preset.get("restores") if name == "previous" else name
    if restored:
        data["preset"] = restored
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, data)
    return {"preset": name, "settings": str(path), "shadowed": shadowed(cwd, scope, settings)}


def status_line(cwd: str) -> str | None:
    """One line for the session hook: the active preset, whether settings were edited since, and readiness.
    Reads only cached facts (the roster cache), never runs a CLI."""
    from store import roster_path  # noqa: PLC0415
    settings = load_settings(cwd)
    name = settings.get("preset")
    if not name:
        return None
    try:
        preset = get(cwd, name)
    except SysopError:
        return f"sysop preset: {name} (no such preset file any more)"
    edited = " (edited since)" if diff_lines(settings, preset.get("settings") or {}) else ""
    try:
        roster = json.loads(roster_path().read_text())
    except (OSError, ValueError):
        return f"sysop preset: {name}{edited}"
    return f"sysop preset: {readiness_line(name, readiness(preset, roster))}{edited}"


def user_dir() -> Path:
    return Path(os.path.expanduser("~")) / ".claude" / "sysop-presets"


def cmd_preset(args: argparse.Namespace) -> int:
    if args.action == "use":
        scope = args.scope or (get(args.cwd, args.name).get("scope") if args.name == "previous" else None) or "user"
        result = use(args.cwd, args.name, scope)
        settings = load_settings(args.cwd)
        ready = readiness(get(args.cwd, args.name), load_roster(settings, cwd=args.cwd))
        print(f"preset {args.name} written to {result['settings']}"
              + ("" if args.name == "previous" else "; `sysop.py preset use previous` undoes it"))
        if result["shadowed"]:
            print(f"note: this repo's own settings still decide {', '.join(result['shadowed'])} "
                  f"(use --scope repo to change them here)")
        print(readiness_line(args.name, ready))
        for fix in ready["fixes"]:
            print(f"  fix: {fix}")
        return 0
    if args.action == "save":
        settings = load_settings(args.cwd)
        target = user_dir() / f"{args.name}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not args.force:
            raise SysopError(f"{target} exists (--force replaces it)")
        write_json(target, {"name": args.name, "summary": "my own preset: edit this line", "session": "sonnet",
                            "needs": [], "optional": [], "capacity": "edit: what this preset can carry",
                            "caveats": ["edit: its limits"],
                            "settings": {k: settings[k] for k in PRESET_KEYS if k in settings}})
        print(f"saved {target}: edit it, then `sysop.py preset use {args.name}`")
        return 0
    settings = load_settings(args.cwd)
    roster = load_roster(settings, cwd=args.cwd)
    cooling = active_cooldowns()
    if args.action == "list":
        presets = load_all(args.cwd)
        active = settings.get("preset")
        rows = []
        for name, preset in presets.items():
            ready = readiness(preset, roster, cooling)
            rows.append({"name": name, "active": name == active, "source": preset["_source"],
                         "summary": preset.get("summary", ""), **ready})
        if args.json:
            print(json.dumps(rows, indent=2))
            return 0
        width = max(map(len, presets), default=0)
        for row in rows:
            star = "*" if row["active"] else " "
            print(f"{star}{MARK[row['status']]} {row['name']:<{width}}  {row['summary']}")
            if row["missing"]:
                print(f"     missing: {', '.join(row['missing'])}")
        print("\n* active  ✓ ready  ◐ partly (some models unavailable)  ✗ missing what it needs\n"
              "`sysop.py preset show <name>` explains one; `preset use <name>` applies it.")
        return 0
    preset = get(args.cwd, args.name)
    if args.action == "diff":
        lines = diff_lines(settings, preset.get("settings") or {})
        print("\n".join(lines) if lines else f"{args.name}: your settings already match")
        return 0
    ready = readiness(preset, roster, cooling)
    print(f"{preset['name']}  ({preset['_source']}: {preset['_path']})\n{preset.get('summary', '')}")
    if preset.get("session"):
        print(f"Run your session on: {preset['session']} (the session coordinates; it spends your plan too)")
    print(f"Needs: {', '.join(preset.get('needs') or []) or 'nothing'}"
          + (f"   Optional: {'; '.join(preset['optional'])}" if preset.get("optional") else ""))
    print(f"Capacity: {preset.get('capacity', '')}")
    for caveat in preset.get("caveats") or []:
        print(f"Caveat: {caveat}")
    tiers = (preset.get("settings") or {}).get("tiers") or {}
    width = max(map(len, ready["entries"]), default=0)
    for tier, chain in tiers.items():
        print(tier)
        for entry in chain:
            print(f"  {entry:<{width}}  {family(entry.partition(':')[2]):<10} {ready['entries'][entry]}")
    print(readiness_line(preset["name"], ready))
    for fix in ready["fixes"]:
        print(f"  fix: {fix}")
    for issue in problems(preset):
        print(f"  rule: {issue}")
    return 0


def cmd_lane(args: argparse.Namespace) -> int:
    """Lanes are named queues, each on its own preset; they live in the user settings (`lanes`)."""
    path = settings_path(args.cwd, "user")
    data = _read_json(path)
    lanes = data.setdefault("lanes", {})
    if args.action == "add":
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", args.name):
            raise SysopError("a lane name is lowercase letters, digits and dashes")
        preset = get(args.cwd, args.preset)
        if issues := problems(preset):
            raise SysopError(f"preset '{args.preset}' fails its safety rules: " + "; ".join(issues))
        conf = {"preset": args.preset, **({"max_parallel": args.max_parallel} if args.max_parallel else {}),
                **({"land": True} if args.land else {}), **({"pr": True} if args.pr else {})}
        lanes[args.name] = conf
    elif args.action == "remove":
        if lanes.pop(args.name, None) is None:
            raise SysopError(f"no lane '{args.name}'")
    elif args.action == "intake":
        from fleet import intake  # noqa: PLC0415 - fleet imports presets' neighbours; loaded only here
        if args.every is not None and args.every < 1:
            raise SysopError("--every is a number of minutes, 1 or more")
        if args.label or args.every:  # remember how this lane takes issues in, for the mod's timer
            if args.name not in lanes:
                raise SysopError(f"no lane '{args.name}'")
            lanes[args.name]["intake"] = {"label": args.label or f"sysop:{args.name}",
                                          "every_minutes": args.every or 0}
            path.parent.mkdir(parents=True, exist_ok=True)
            write_json(path, data)
        print(json.dumps(intake(args.repo or args.cwd, args.name, args.label)))
        return 0
    if args.action != "list":
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, data)
    settings = load_settings(args.cwd)
    roster = load_roster(settings, cwd=args.cwd)
    rows = []
    for name, conf in (settings.get("lanes") or {}).items():
        try:
            ready = readiness_line(conf.get("preset", "?"), readiness(get(args.cwd, conf["preset"]), roster))
        except (SysopError, KeyError):
            ready = f"✗ {conf.get('preset')}: no such preset"
        rows.append(f"{name:<12} {ready}  (at most {conf.get('max_parallel', settings.get('max_parallel'))} at once; "
                    f"{'lands on its own' if conf.get('land') else 'landing waits for your word'}"
                    f"{'; through pull requests' if conf.get('pr') else ''})")
    print("\n".join(rows) or "no lanes yet: `sysop.py lane add <name> --preset <preset>`")
    return 0


def add_lane_parser(sub) -> None:
    p = sub.add_parser("lane", help="named queues, each on its own preset (`--lane <name>` acts for one)")
    lsub = p.add_subparsers(dest="action", required=True)
    k = lsub.add_parser("add", help="create or change a lane")
    k.add_argument("name")
    k.add_argument("--preset", required=True)
    k.add_argument("--max-parallel", type=int)
    k.add_argument("--land", action="store_true", help="a standing grant: this lane lands without asking")
    k.add_argument("--pr", action="store_true", help="land through pull requests; review comments go back to the builder")
    lsub.add_parser("remove").add_argument("name")
    lsub.add_parser("list")
    k = lsub.add_parser("intake", help="open GitHub issues with the lane's label become its tickets (issue-<n>)")
    k.add_argument("name")
    k.add_argument("--label", help="default: sysop:<lane> (or the lane's own `intake.label`)")
    k.add_argument("--repo", help="the repo the issues belong to (default: this one)")
    k.add_argument("--every", type=int, help="minutes: the sysop mod takes issues in on its own this often")
    p.set_defaults(func=cmd_lane)


def add_parser(sub) -> None:
    p = sub.add_parser("preset", help="named model setups for what you have (Claude plan, OpenRouter, free…)")
    psub = p.add_subparsers(dest="action", required=True)
    k = psub.add_parser("list", help="every preset, and whether you can run it now")
    k.add_argument("--json", action="store_true")
    for action, text in (("show", "one preset: what it needs, its limits, and a live check of each model"),
                         ("diff", "what `use` would change in your current settings")):
        psub.add_parser(action, help=text).add_argument("name")
    k = psub.add_parser("use", help="apply a preset (saves the old settings as preset `previous`)")
    k.add_argument("name")
    k.add_argument("--scope", choices=["user", "repo"], help="default: user (previous: where it came from)")
    k = psub.add_parser("save", help="your current settings as a new preset to edit")
    k.add_argument("name")
    k.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_preset)
