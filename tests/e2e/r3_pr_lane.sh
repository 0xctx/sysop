#!/usr/bin/env bash
# R3 e2e: a PR lane on the private sandbox (0xctx/sysop-sandbox). The ticket is built on free models and
# opened as a PR; a scripted reviewer comments on the PR as soon as it appears; the fleet must send that
# comment back to the builder, re-review, push to the same PR, and only then merge.
source "$(dirname "$0")/lib.sh"
SANDBOX="${E2E_SANDBOX:-0xctx/sysop-sandbox}"
gh auth status > /dev/null 2>&1 || { echo "SKIP: gh not signed in"; exit 0; }
gh repo view "$SANDBOX" > /dev/null 2>&1 || { echo "SKIP: no access to $SANDBOX"; exit 0; }
command -v pi > /dev/null || { echo "SKIP: pi not installed"; exit 0; }
REPO="$E2E_ROOT/sandbox"
git clone -q "https://github.com/$SANDBOX" "$REPO" && cd "$REPO" || exit 1
git config user.name "e2e" && git config user.email "e2e@example.com"
RUN=$(date +%s)
T="abs$RUN"
python3 - .claude/sysop.json <<'PY'
import json, os, sys
d = json.load(open(sys.argv[1]))
d.setdefault("lanes", {})["prl"] = {"preset": os.environ.get("LANE_PRESET", "openrouter-free"), "land": True, "pr": True}
json.dump(d, open(sys.argv[1], "w"), indent=2)
PY
if ! git diff --quiet; then git commit -qam "e2e: a PR lane" && git push -q origin main; fi
S="python3 $SYSOP/scripts/sysop.py"
echo "Add calc/abs_$RUN.py with absolute(x) returning the absolute value of x; tests in tests/test_abs_$RUN.py." > "$E2E_ROOT/req.txt"
$S --lane prl ticket add "$T" --title "absolute value" --size small --kind feature \
   --request-file "$E2E_ROOT/req.txt" --base main --authority "standing land: true" > /dev/null
ASK="Please also make absolute() raise TypeError when x is not an int or a float, with a test for it."
( for _ in $(seq 1 360); do  # the scripted reviewer: one comment on the PR, as soon as it exists
    N=$(gh pr list -R "$SANDBOX" --head "sysop/$T" --state open --json number -q '.[0].number' 2>/dev/null)
    if [ -n "$N" ]; then gh pr comment "$N" -R "$SANDBOX" --body "$ASK" > /dev/null && echo "$N" > "$E2E_ROOT/commented"; exit 0; fi
    sleep 5
  done ) &
WATCHER=$!
E2E_TIMEOUT=3600 run_claude r3_pr_lane "/sysop:fleet --go --lane prl --resume Run the lane's queued ticket $T."
kill "$WATCHER" 2> /dev/null
cd "$REPO" || exit 1
N=$(cat "$E2E_ROOT/commented" 2> /dev/null)
echo "PR: ${N:-none}"
expect "the scripted reviewer commented on the PR" test -n "$N"
expect "the PR is merged" bash -c "test \"\$(gh pr view $N -R $SANDBOX --json state -q .state)\" = MERGED"
expect "the comment went back to the builder (ticket log)" bash -c "grep -q 'review item(s) on PR #$N' .sysop/tickets.json"
expect "the builder ran again for it" test -d ".sysop/runs/$T/2"
expect "the merged code raises TypeError as asked" bash -c "git fetch -q origin && git show origin/main:calc/abs_$RUN.py | grep -q TypeError"
expect "one canonical review comment" bash -c "test \"\$(gh api repos/$SANDBOX/issues/$N/comments --jq '[.[] | select(.body | startswith(\"<!-- sysop-review\"))] | length')\" = 1"
expect "no run left active" bash -c "! ls .sysop/active/*.json"
expect "no AI attribution" bash -c "! (gh pr view $N -R $SANDBOX --json title,body,commits -q '.title + .body + ([.commits[].messageBody] | join(\" \"))' | grep -qiE 'co-authored-by|generated with')"
finish r3_pr_lane
