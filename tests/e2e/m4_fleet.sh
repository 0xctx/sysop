#!/usr/bin/env bash
# M4 e2e: two tickets as a fleet. t1 (small) is routed first to a deliberately broken worker
# that fails with a rate-limit error and must fall back to agy; t2 (large) is built by a
# claude worker and reviewed by agy. Both land; the ledger has two rows.
source "$(dirname "$0")/lib.sh"
REPO="$E2E_ROOT/fleet"
make_calc_repo "$REPO" true
sed -i 's/return a - b/return a + b/' "$REPO/calc/__init__.py" && git -C "$REPO" commit -qam "fix add"
BIN="$E2E_ROOT/bin"; mkdir -p "$BIN"
cat > "$BIN/flaky-agent" <<'SH'
#!/usr/bin/env bash
echo "flaky-agent v0.1 starting"
echo "Error: 429 Too Many Requests: rate limit exceeded for model m1; retry after 3600s" >&2
exit 1
SH
chmod +x "$BIN/flaky-agent"
export PATH="$BIN:$PATH"
python3 - "$REPO/.claude/sysop.json" <<'PY'
import json, sys
p = sys.argv[1]; d = json.load(open(p))
d["workers"] = {"flaky": {"bin": "flaky-agent", "build": "flaky-agent {prompt_file}", "review": "flaky-agent {prompt_file}"}}
d["tiers"] = {"fast": ["flaky:m1", "agy:gemini-3.8-flash-high", "agy:gpt-oss-120b-medium", "claude:haiku"],
              "standard": ["claude:haiku", "agy:gemini-3.1-pro-high", "agy:gpt-oss-120b-medium"],
              "deep": ["claude:sonnet", "agy:gemini-3.1-pro-high"],
              "review": ["agy:gpt-oss-120b-medium", "agy:gemini-3.1-pro-high", "claude:sonnet"]}
d["agents"] = {"default": "inherit", "sysop:code-reviewer": "sonnet"}
json.dump(d, open(p, "w"), indent=2)
PY
git -C "$REPO" add .claude/sysop.json && git -C "$REPO" commit -qm "fleet test routing"
python3 "$SYSOP/scripts/sysop.py" --cwd "$REPO" roster --refresh > /dev/null
for c in agy agy:gemini-3.8-flash-high agy:gemini-3.1-pro-high agy:gpt-oss-120b-medium flaky flaky:m1 claude claude:haiku; do
  python3 "$SYSOP/scripts/sysop.py" --cwd "$REPO" cooldown clear "$c" > /dev/null; done
probe=$(timeout 90 agy -p "Reply with exactly: OK" --model gemini-3.8-flash-low < /dev/null 2>&1)  # no pipe: pipefail + grep -q would mask it
if grep -q "quota" <<<"$probe"; then
  GEMINI=0; echo "  NOTE: agy Gemini quota exhausted; asserting the recorded-fallback path instead"
else GEMINI=1; fi
E2E_TIMEOUT=3600 run_claude m4_fleet "/sysop:fleet --go --land Two tickets.
t1 (size small): add calc/stats.py with mean(xs) returning the arithmetic mean of a non-empty list of numbers, raising ValueError on an empty list; tests in tests/test_stats.py.
t2 (size large): add calc/geometry.py with area_circle(r) returning pi*r*r using math.pi, raising ValueError for negative r; tests in tests/test_geometry.py.
They are independent."
cd "$REPO" || exit 1
expect "stats landed" test -f calc/stats.py
expect "geometry landed" test -f calc/geometry.py
expect "gate green on main" python3 -m unittest discover -s tests -q
expect "ledger has two rows" test "$(wc -l < .sysop/ledger.jsonl)" -eq 2
expect "mean is in calc/stats.py as asked" grep -q "def mean" calc/stats.py
expect "t2 built by claude" bash -c "grep '\"ticket\": \"t2\"' .sysop/ledger.jsonl | grep -q '\"worker\": \"claude\"'"
expect "t1 records a fallback in the ledger" bash -c "grep '\"ticket\": \"t1\"' .sysop/ledger.jsonl | grep -qv '\"fallbacks\": 0'"
expect "fallback from flaky recorded on the ticket" bash -c "sed -n '/^## t1/,/^## t2/p' .sysop/tickets.md | grep -qiE 'flaky.*(429|rate)|(429|rate).*flaky'"
expect "flaky attempt evidence kept" bash -c "grep -q '429' .sysop/runs/t1/1/log"
expect "t1 fell back from flaky to agy (launch records)" bash -c "cat .sysop/runs/t1/1/worker | grep -q flaky && cat .sysop/runs/t1/*/worker | grep -q '^agy:'"
# agy capacity is outside our control: accept a real review by another vendor (agy, or the same model
# through another door, such as Groq's GPT-OSS while agy rests), or an honest script-written
# 'unavailable' that a real attempt stands behind
expect "t2 cross-family: another vendor's review, or unavailable after a real attempt (script-written)" bash -c "(grep -qE 'Cross-family review:\*\* (agy|pi):' .sysop/reports/sysop-t2.xreview.md && ! grep -qE 'Cross-family review:\*\* [a-z]+:[^ ]*(claude|haiku|sonnet|opus)' .sysop/reports/sysop-t2.xreview.md && grep -q '\"status\": \"done\"' .sysop/reports/sysop-t2.xreview.json) || (grep -q '\"status\": \"unavailable\"' .sysop/reports/sysop-t2.xreview.json && ls .sysop/runs/xreview-sysop-t2/1/worker)"
expect "t1 cross-family result is script-written and final" bash -c "grep -qE '\"status\": \"(done|unavailable|claude-agents)\"' .sysop/reports/sysop-t1.xreview.json"
expect "t2 review does not claim a Gemini review" bash -c "! grep -qiE 'gemini (reviewer )?(confirmed|reviewed|approved)' .sysop/reports/sysop-t2.review.md"
expect "both reviews READY" bash -c "grep -qE 'Verdict:\**\s*READY' .sysop/reports/sysop-t1.review.md && grep -qE 'Verdict:\**\s*READY' .sysop/reports/sysop-t2.review.md"
expect "tickets classified by kind" bash -c "grep -c '\*\*Kind:\*\* [a-z]' .sysop/tickets.md | grep -q 2"
expect "code-reviewer dispatched on its configured model (sonnet)" bash -c "grep -o '\"subagent_type\":\"sysop:code-reviewer\"[^}]*' '$E2E_ROOT/m4_fleet.jsonl' | grep -q '\"model\":\"sonnet\"' || grep -o '\"model\":\"sonnet\"[^}]*\"subagent_type\":\"sysop:code-reviewer\"' '$E2E_ROOT/m4_fleet.jsonl' | grep -q ."
expect "the coordinator was driven by sysop.py next" bash -c "grep -q 'sysop.py next' '$E2E_ROOT/m4_fleet.jsonl'"
expect "both tickets recorded landed (by land, not by hand)" bash -c "python3 -c \"import json; t=json.load(open('.sysop/tickets.json'))['tickets']; assert all(v['status']=='landed' for v in t.values()), t\""
expect "both reviews sealed" bash -c "ls .sysop/reports/sysop-t1.seal.json .sysop/reports/sysop-t2.seal.json"
expect "no run left active" bash -c "! ls .sysop/active/*.json"
expect "no worktree left" test "$(git worktree list | wc -l)" -eq 1
expect "no sysop branches left" bash -c "! git branch | grep -q sysop/"
expect "no AI attribution" bash -c '! git log --format=%B | grep -qiE "co-authored-by|generated with"'
finish m4_fleet
