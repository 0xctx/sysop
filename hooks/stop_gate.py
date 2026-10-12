#!/usr/bin/env python3
"""Stop gate: refuses to end the turn while sysop work is unfinished.

1. Run gate (always on): an active run marker for this session (`sysop.py run start`,
   written by ship and fleet) holds the turn open. In headless mode any text-only
   message ends the turn, even when the skill has steps left. Anti-spin: a measured
   fact is that `stop_hook_active` stays true on every stop after the first block,
   progress or not, so it cannot tell a spin from progress. This gate stands down
   when no new tool call happened since its last block, and after MAX_RUN_BLOCKS. It also stands down
   while background agents or commands of this session have not reported back: their results arrive
   only after the turn ends.
2. Check gate (opt-in: `"stop_gate": true` plus `"check"`): refuses while the check
   is red; it stands down on `stop_hook_active`.
Fails open on internal errors.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

MAX_RUN_BLOCKS = 8
TOOL_USE = re.compile(r'"type":\s*"tool_use"')

# hooks.json gives this hook 900 s; past that Claude Code kills it and the gate would
# silently fail open, so the check itself is capped below that.
MAX_CHECK_SECONDS = 870


def project_env(root: Path) -> dict:
    """os.environ without any foreign virtualenv, with the project's own venv first.

    A hook is not the operator's shell: the project's .venv was never on PATH.
    """
    env = os.environ.copy()
    foreign = env.pop("VIRTUAL_ENV", None)
    parts = env.get("PATH", "").split(os.pathsep)
    if foreign:
        drop = {os.path.join(foreign, "bin"), os.path.join(foreign, "Scripts")}
        parts = [p for p in parts if p not in drop]
    for name in (".venv", "venv"):
        for bindir in ("bin", "Scripts"):
            venv_bin = root / name / bindir
            if venv_bin.is_dir():
                env["VIRTUAL_ENV"] = str(root / name)
                parts.insert(0, str(venv_bin))
                env["PATH"] = os.pathsep.join(parts)
                return env
    env["PATH"] = os.pathsep.join(parts)
    return env


LAUNCHED = re.compile(r"agentId: ([A-Za-z0-9]+)|running in background with ID: ([A-Za-z0-9]+)")
NOTIFIED = re.compile(r"<task-id>([A-Za-z0-9]+)</task-id>")


def pending_background(transcript: str | None) -> set[str]:
    """Background agents and commands this session started that have not reported back. Their results
    arrive only after the turn ends, so holding the turn open would wait forever (seen in the v1 e2e)."""
    try:
        text = Path(transcript).read_text(errors="replace")
    except (OSError, TypeError):
        return set()
    launched = {a or b for a, b in LAUNCHED.findall(text)}
    return launched - set(NOTIFIED.findall(text))


def tool_calls(transcript: str | None) -> int:
    try:
        return len(TOOL_USE.findall(Path(transcript).read_text(errors="replace")))
    except (OSError, TypeError):
        return -1


def run_gate(payload: dict, store) -> str | None:
    """The reason to keep the turn open, or None."""
    cwd = payload.get("cwd") or os.getcwd()
    runs = store.active_runs(cwd, payload.get("session_id"))
    if not runs:
        return None
    path, data = runs[0]
    if pending_background(payload.get("transcript_path")):
        return None  # ending the turn is how background results get delivered; the run resumes with them
    calls = tool_calls(payload.get("transcript_path"))
    no_progress = calls != -1 and calls <= data.get("last_block_tools", -1)
    if (payload.get("stop_hook_active") and no_progress) or data.get("blocks", 0) >= MAX_RUN_BLOCKS:
        return None  # stand down: the agent is stuck, not working
    data.update(blocks=data.get("blocks", 0) + 1, last_block_tools=calls)
    store.write_json(path, data)
    slug, step, steps = data["slug"], data.get("step", 0), data.get("steps", 10)
    end = (f"python3 {Path(__file__).resolve().parent.parent / 'scripts' / 'sysop.py'} run end "
           f"{slug} --outcome blocked --reason '<why>'")
    return (f"sysop: run '{slug}' is unfinished (last recorded step {step} of {steps}). Continue "
            f"with the next step now, using tools; the final summary comes only at the last step. "
            f"If you genuinely must stop for the operator, first run: {end}")


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        import store  # noqa: PLC0415
        try:
            reason = run_gate(payload, store)
        except Exception:  # noqa: BLE001 - fail open
            reason = None
        if reason:
            print(reason, file=sys.stderr)
            return 2
        if payload.get("stop_hook_active"):
            return 0

        cwd = payload.get("cwd") or os.getcwd()
        settings = store.load_settings(cwd)
        check = str(settings.get("check") or "").strip()
        if settings.get("stop_gate") is not True or not check:
            return 0
        root = Path(store.git(["rev-parse", "--show-toplevel"], cwd))
        timeout = min(int(settings.get("check_timeout") or 600), MAX_CHECK_SECONDS)
    except Exception:  # noqa: BLE001 - fail open
        return 0
    code, tail = store.run_check(check, root, timeout, env=project_env(root))
    if code == 0:
        return 0
    if code == 124 and tail.startswith("check timed out"):
        print(f"sysop stop gate: check `{check}` timed out after {timeout}s. "
              f"Make it pass (or faster) before finishing.", file=sys.stderr)
        return 2
    print(f"sysop stop gate: check `{check}` is red (exit {code}). "
          f"Fix the cause before finishing.\n{tail}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
