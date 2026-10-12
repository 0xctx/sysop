#!/usr/bin/env bash
# R2 e2e: a labelled GitHub issue on the private sandbox (0xctx/sysop-sandbox) becomes a lane's ticket,
# is built on free models, lands (pushed), and the issue is commented and closed by sysop.
# Each run opens its own issue and adds its own file, so runs never collide.
source "$(dirname "$0")/lib.sh"
SANDBOX="${E2E_SANDBOX:-0xctx/sysop-sandbox}"
gh auth status > /dev/null 2>&1 || { echo "SKIP: gh not signed in"; exit 0; }
gh repo view "$SANDBOX" > /dev/null 2>&1 || { echo "SKIP: no access to $SANDBOX"; exit 0; }
command -v pi > /dev/null || { echo "SKIP: pi not installed"; exit 0; }
REPO="$E2E_ROOT/sandbox"
git clone -q "https://github.com/$SANDBOX" "$REPO" && cd "$REPO" || exit 1
git config user.name "e2e" && git config user.email "e2e@example.com"
RUN=$(date +%s)
gh label create "sysop:e2e" -R "$SANDBOX" --force --description "sysop e2e: issue intake" > /dev/null
URL=$(gh issue create -R "$SANDBOX" --title "Add sign_$RUN" --label "sysop:e2e" \
  --body "Add calc/sign_$RUN.py with sign(x) that returns -1 for a negative x, 0 for zero and 1 for a positive x; tests in tests/test_sign_$RUN.py.")
N="${URL##*/}"
echo "issue: $URL"
python3 - .claude/sysop.json <<'PY'
import json, os, sys
d = json.load(open(sys.argv[1]))
d["lanes"] = {"e2e": {"preset": os.environ.get("LANE_PRESET", "openrouter-free"), "land": True}}
json.dump(d, open(sys.argv[1], "w"), indent=2)
PY
git add -A && git commit -qm "e2e lane"
S="python3 $SYSOP/scripts/sysop.py"
$S lane intake e2e
E2E_TIMEOUT=3600 run_claude r2_intake "/sysop:fleet --go --lane e2e --resume Run the lane's queued ticket: GitHub issue #$N, already taken in as ticket issue-$N. Fill its contract from the issue's text first."
cd "$REPO" || exit 1
expect "the issue became ticket issue-$N in lane e2e" bash -c "python3 -c \"import json;t=json.load(open('.sysop/tickets.json'))['tickets']['issue-$N'];assert t['lane']=='e2e' and t['issue']['number']==$N\""
expect "the change is on origin main" bash -c "git fetch -q origin && git cat-file -e origin/main:calc/sign_$RUN.py"
expect "the issue is closed" bash -c "test \"\$(gh issue view $N -R $SANDBOX --json state -q .state)\" = CLOSED"
expect "sysop's comment names the landed commit" bash -c "gh issue view $N -R $SANDBOX --json comments -q '.comments[].body' | grep -q 'Landed by sysop as'"
expect "built in the free lane by pi" bash -c "grep -h '^pi:' .sysop/runs/issue-$N/*/worker"
expect "no run left active" bash -c "! ls .sysop/active/*.json"
expect "no AI attribution" bash -c "! git log origin/main --format=%B -5 | grep -qiE 'co-authored-by|generated with'"
finish r2_intake
