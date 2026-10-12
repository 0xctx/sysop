#!/usr/bin/env bash
# v1 e2e, the idea path: a PRD with three slices, one of which hits the never-build list, in a repo with
# no check. slice drops the never-build one, puts a walking skeleton first, and fleet builds the two
# independent slices in parallel (max_parallel 2) after the skeleton lands.
source "$(dirname "$0")/lib.sh"
REPO="$E2E_ROOT/idea"
rm -rf "$REPO" && mkdir -p "$REPO/docs" && cd "$REPO" && git init -q -b main
git config user.name e2e && git config user.email e2e@example.com
claude_only .claude/sysop.json
cat > docs/reading.prd.md <<'MD'
# Reading tracker: PRD

## Problem
Readers forget what they meant to read next.

## MVP
A tiny local web API (Python standard library only) that keeps a reading list in memory.

## Slices
| id | Outcome (a user can…) | Size | Depends | User-visible check |
|---|---|---|---|---|
| books | add a book with `POST /books` (JSON `{"title": ...}`) and list them with `GET /books` | small | - | post "Dune", then GET /books shows Dune |
| quote | get a reading quote with `GET /quote` (one of three fixed quotes, JSON `{"quote": ...}`) | small | - | GET /quote returns one of the three |
| sync | sign in and sync the list to a cloud account | large | books | log in, see the list on another device |

## Never build
- Accounts, sign-in or cloud sync: the list stays local.

## Non-goals
- Persistence across restarts.
MD
git add -A && git commit -qm "the reading tracker PRD"
E2E_TIMEOUT=5400 run_claude v1_idea "/sysop:slice docs/reading.prd.md --go --land"
cd "$REPO" || exit 1
T=.sysop/tickets.json
expect "the never-build slice was dropped" bash -c "! python3 -c \"import json; assert 'sync' in json.load(open('$T'))['tickets']\""
expect "a walking skeleton came first" bash -c "python3 -c \"import json; t=json.load(open('$T'))['tickets']; assert list(t)[0]=='t0-skeleton'; assert t['books']['depends']==['t0-skeleton']\""
# A slice may end blocked when review keeps finding defects past its attempts: that is sysop refusing
# to land weak code, not a failure of the path. The skeleton and at least one slice must land.
expect "the skeleton and at least one slice landed; the rest blocked with a reason" python3 - <<'PY'
import json
t = json.load(open(".sysop/tickets.json"))["tickets"]
assert len(t) == 3 and t["t0-skeleton"]["status"] == "landed", {k: v["status"] for k, v in t.items()}
assert any(t[s]["status"] == "landed" for s in ("books", "quote")), {k: v["status"] for k, v in t.items()}
for v in t.values():
    assert v["status"] == "landed" or (v["status"] == "blocked" and "blocked" in v["log"][-1]), v["log"][-2:]
PY
expect "the skeleton set the check" bash -c "python3 '$SYSOP/scripts/sysop.py' config get check | grep -qv '^\"\"$'"
expect "the check is green on main" bash -c "eval \"\$(python3 '$SYSOP/scripts/sysop.py' config get check | python3 -c 'import json,sys; print(json.load(sys.stdin))')\""
expect "books and quote ran in parallel" python3 - <<'PY'
from pathlib import Path
runs = Path(".sysop/runs")
spans = {t: (float((runs / t / "1" / "started").read_text()), (runs / t / "1" / "exit").stat().st_mtime) for t in ("books", "quote")}
(a0, a1), (b0, b1) = spans.values()
assert a0 < b1 and b0 < a1, spans  # the two first attempts overlapped in time
PY
expect "every landed slice was built on top of the landed skeleton" python3 - <<'PY'
import json, re, subprocess
t = json.load(open(".sysop/tickets.json"))["tickets"]
sha = {k: m.group(1) for k, v in t.items() if (m := re.search(r"landed at ([0-9a-f]{7,})", " ".join(v["log"])))}
sha = {k: v for k, v in sha.items() if t[k]["status"] == "landed"}
for child in set(sha) - {"t0-skeleton"}:
    assert subprocess.run(["git", "merge-base", "--is-ancestor", sha["t0-skeleton"], sha[child]]).returncode == 0, child
PY
expect "a start command was written down" bash -c "grep -rqiE 'PORT|start' AGENTS.md README.md 2>/dev/null"
expect "no run left active" bash -c "! ls .sysop/active/*.json"
expect "no AI attribution" bash -c '! git log --format=%B | grep -qiE "co-authored-by|generated with"'
finish v1_idea
