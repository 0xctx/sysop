#!/usr/bin/env bash
# T14 e2e: one coordinator, one lane on free OpenRouter models, two repos, and a ticket in one repo
# that waits for the other repo's ticket to land. LANE_PRESET picks the lane's preset (default openrouter-free;
# cline-free when OpenRouter's free daily quota is spent).
source "$(dirname "$0")/lib.sh"
command -v pi > /dev/null || { echo "SKIP: pi not installed"; exit 0; }
lane() {  # a lane on the free preset that lands on its own, written into the repo's settings
  python3 - .claude/sysop.json <<'PY'
import json, sys
import os
d = json.load(open(sys.argv[1])); d["lanes"] = {"free": {"preset": os.environ.get("LANE_PRESET", "openrouter-free"), "land": True}}
json.dump(d, open(sys.argv[1], "w"), indent=2)
PY
  git add -A && git commit -qm "free lane"
}
API="$E2E_ROOT/api"; APP="$E2E_ROOT/app"
make_calc_repo "$API" true && lane
make_calc_repo "$APP" true && lane
S="python3 $SYSOP/scripts/sysop.py"
echo "Add calc.subtract(a, b) returning a - b, with a test." > "$E2E_ROOT/server.txt"
$S --cwd "$API" --lane free ticket add server --title "add subtract" --size small --kind feature \
   --request-file "$E2E_ROOT/server.txt" --base main --authority "standing land: true" > /dev/null
cd "$APP" || exit 1
E2E_TIMEOUT=3600 run_claude t14_lanes "/sysop:fleet --all --go --lane free One ticket in this repo: client (size small): add calc/version.py with VERSION = \"2.0\" and a test for it. It depends on api:server, the other repo's ticket, which is already queued; run both."
expect "api landed its ticket" grep -q "def subtract" "$API/calc/__init__.py"
expect "app landed its ticket" test -f "$APP/calc/version.py"
expect "the app ticket waited for api:server (it depends on it)" bash -c "grep -q 'api:server' '$APP/.sysop/tickets.json'"
expect "client started only after server landed" python3 - "$API" "$APP" <<'PY'
import subprocess, sys, pathlib
landed = int(subprocess.check_output(["git", "-C", sys.argv[1], "log", "-1", "--format=%ct", "--grep", "subtract", "-i"]).strip() or 0)
started = float((pathlib.Path(sys.argv[2]) / ".sysop/runs/client/1/started").read_text())
sys.exit(0 if landed and started >= landed - 5 else 1)
PY
expect "both built in the free lane by pi" bash -c "grep -h '^pi:' '$API'/.sysop/runs/server/*/worker && grep -h '^pi:' '$APP'/.sysop/runs/client/*/worker"
expect "status --all shows both landed" bash -c "$S --cwd '$APP' status --all | grep -A3 '^Landed' | grep -q 'api:free/server' && $S --cwd '$APP' status --all | grep -A3 '^Landed' | grep -q 'app:free/client'"
expect "no run left active" bash -c "! ls '$APP'/.sysop/active/*.json"
expect "no AI attribution" bash -c "! git -C '$API' log --format=%B | grep -qiE 'co-authored-by|generated with' && ! git -C '$APP' log --format=%B | grep -qiE 'co-authored-by|generated with'"
finish t14_lanes
