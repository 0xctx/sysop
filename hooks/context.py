#!/usr/bin/env python3
"""SessionStart context: a few lines of what is true right now.

Branch, uncommitted file count, and open sysop artifacts (store, ticket file, live
workers, the newest plans and reports). Read-only: never creates the store.
Prints nothing outside git or on any error.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


def lines_for(cwd: str) -> list[str]:
    import store  # noqa: PLC0415 - path set above

    branch = store.git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    dirty = [l for l in store.git(["status", "--porcelain"], cwd).splitlines() if l.strip()]
    out = [f"sysop: branch {branch} · {len(dirty)} uncommitted file(s)"]
    try:
        import presets  # noqa: PLC0415
        line = presets.status_line(cwd)
    except Exception:  # noqa: BLE001 - a broken preset file must not silence the rest
        line = None
    if line:
        out.append(line)
    notice = store.load_intel().get("notice")  # cache only: a hook never touches the network
    if notice:
        out.append(f"sysop models: {notice}")
    where = store.store_path(cwd)
    if not where.is_dir():
        return out
    out.append(f"sysop store: {where}")
    if (where / "tickets.md").is_file():
        out.append("sysop: fleet ticket file present (.sysop/tickets.md)")
    live = sum(1 for pid_file in (where / "runs").glob("*/*/pid")  # runs/<ticket>/<attempt>/
               if not (pid_file.parent / "exit").exists() and store.run_alive(pid_file.parent))
    if live:
        out.append(f"sysop: {live} worker(s) still running; wait on them before landing")
    def mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:  # deleted between glob and stat
            return 0.0

    recent = sorted((p for d in ("plans", "reports") for p in (where / d).glob("*.md")),
                    key=mtime, reverse=True)[:3]
    if recent:
        out.append("sysop recent: " + ", ".join(f"{p.parent.name}/{p.name}" for p in recent))
    return out[:7]


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        cwd = payload.get("cwd") or os.getcwd()
        lines = lines_for(cwd)
    except Exception:  # noqa: BLE001 - outside git or broken: say nothing
        return 0
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": "\n".join(lines),
    }}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
