#!/usr/bin/env bash
# M8b e2e: a real Kaggle serving session. `serve up` → pi answers through pi:kaggle/<model> →
# a fleet ticket built on the served model lands → `serve down` ends the notebook and routing
# skips the entry again. Needs the one-time setup (serve.domain, `serve token`). Uses GPU quota.
source "$(dirname "$0")/lib.sh"
command -v pi > /dev/null || { echo "SKIP: pi not installed"; exit 0; }
S0="python3 $SYSOP/scripts/sysop.py"
DOMAIN=$($S0 config get serve.domain | tr -d '"')
[ -n "$DOMAIN" ] || { echo "SKIP: serve.domain not set (sysop:setup free-models step)"; exit 0; }
SERVED="${E2E_SERVE_MODEL:-$($S0 config get serve.model | tr -d '"')}"
REPO="$E2E_ROOT/serve"
make_calc_repo "$REPO" false
python3 - "$REPO/.claude/sysop.json" "$SERVED" <<'PY'
import json, sys
p = sys.argv[1]; d = json.load(open(p))
d["tiers"] = {"fast": [f"pi:kaggle/{sys.argv[2]}"], "standard": ["claude:haiku"], "deep": ["claude:sonnet"],
              "review": ["claude:sonnet"]}
json.dump(d, open(p, "w"), indent=2)
PY
git -C "$REPO" add .claude/sysop.json && git -C "$REPO" commit -qm "route small work to the served model"
S="$S0 --cwd $REPO"
expect "route skips the served model while down" bash -c "$S route small | grep -q 'serve up'"
$S serve up "$SERVED" > "$E2E_ROOT/up.json" 2> "$E2E_ROOT/up.err"
cat "$E2E_ROOT/up.json" "$E2E_ROOT/up.err"
expect "serve up reports the session up" grep -q '"status": "up"' "$E2E_ROOT/up.json"
expect "the endpoint refuses requests without the key" \
  bash -c "test \"\$(curl -s -o /dev/null -w '%{http_code}' -H 'ngrok-skip-browser-warning: 1' https://$DOMAIN/v1/models)\" = 401"
$S roster --refresh > /dev/null
expect "pi lists the served model" bash -c "pi --list-models --offline 2>/dev/null | grep -q 'kaggle.*$SERVED'"
(cd "$REPO" && timeout 600 pi -p "Reply with exactly the word PONG and nothing else." --model "kaggle/$SERVED" \
  --no-session > "$E2E_ROOT/pong.txt" 2>&1)
cat "$E2E_ROOT/pong.txt"
expect "pi answers through the served model" grep -q PONG "$E2E_ROOT/pong.txt"
expect "route picks the served model while up" bash -c "$S route small | grep -q '\"worker\": \"pi\"'"
E2E_TIMEOUT=2400 run_claude m8b_serve "/sysop:fleet --go --land One ticket.
t1 (size small): add calc/stats.py with mean(xs) returning the arithmetic mean of a non-empty list of numbers, raising ValueError on an empty list; tests in tests/test_stats.py."
cd "$REPO" || exit 1
expect "stats landed" test -f calc/stats.py
expect "gate green on main" python3 -m unittest discover -s tests -q
expect "built on the served model (launch records)" grep -q "^pi:kaggle/$SERVED" .sysop/runs/t1/1/worker
expect "review READY" grep -qE 'Verdict:\**\s*READY' .sysop/reports/sysop-t1.review.md
$S serve status > "$E2E_ROOT/status.json"; cat "$E2E_ROOT/status.json"
$S serve down > "$E2E_ROOT/down.json"; cat "$E2E_ROOT/down.json"
expect "serve down ends the notebook" grep -q '"kernel_state": "complete"' "$E2E_ROOT/down.json"
expect "pi's kaggle provider is removed" bash -c "! grep -q '\"kaggle\"' ~/.pi/agent/models.json"
expect "route skips the served model again" bash -c "$S route small | grep -q 'serve up'"
expect "no AI attribution" bash -c '! git log --format=%B | grep -qiE "co-authored-by|generated with"'
finish m8b_serve
