#!/usr/bin/env bash
# T13 e2e: /sysop:go hands one change to the preset's models (free OpenRouter ones here) instead of
# building it in the session; the session only coordinates. Compare its cost with m3_tiny_fix (in-session).
source "$(dirname "$0")/lib.sh"
command -v pi > /dev/null || { echo "SKIP: pi not installed"; exit 0; }
REPO="$E2E_ROOT/go"
make_calc_repo "$REPO" true
python3 - "$SYSOP/presets/openrouter-free.json" .claude/sysop.json <<'PY'
import json, sys
preset, path = json.load(open(sys.argv[1])), sys.argv[2]
d = json.load(open(path)); d.update(preset["settings"]); json.dump(d, open(path, "w"), indent=2)
PY
git add -A && git commit -qm "free preset for this repo"
run_claude t13_go "/sysop:go fix: calc.add(a, b) returns a - b but must return a + b. Add a regression test. --land"
cd "$REPO" || exit 1
expect "main has the fix" grep -q "return a + b" calc/__init__.py
expect "gate green on main" python3 -m unittest discover -s tests -q
expect "built by a pi worker, not the session (launch records)" bash -c 'grep -q "^pi:openrouter/" .sysop/runs/*/1/worker'
expect "the ledger says pi built it" bash -c 'grep -q "\"worker\": \"pi\"" .sysop/ledger.jsonl'
expect "cross-vendor review ran and showed coverage" bash -c 'grep -q "COVERAGE" .sysop/reports/*.xreview.md'
expect "review sealed READY" bash -c 'ls .sysop/reports/*.seal.json && grep -qE "Verdict:\**\s*READY" .sysop/reports/*review.md'
expect "no run left active" bash -c "! ls .sysop/active/*.json"
expect "no worktree left" test "$(git worktree list | wc -l)" -eq 1
expect "no AI attribution" bash -c '! git log --format=%B | grep -qiE "co-authored-by|generated with"'
finish t13_go
