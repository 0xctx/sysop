#!/usr/bin/env bash
# v1 e2e, finding A6: a dependent pair without landing authority. t2 starts stacked on t1's reviewed
# branch, both end reviewed, main is untouched, and the final message says how to land them.
source "$(dirname "$0")/lib.sh"
REPO="$E2E_ROOT/stacked"
make_calc_repo "$REPO" false
claude_only .claude/sysop.json && git add -A && git commit -qm "claude-only routing for the test"
BEFORE=$(git rev-parse main)
E2E_TIMEOUT=3600 run_claude v1_stacked "/sysop:fleet --go Two tickets.
t1 (small): add calc/stats.py with mean(xs), the arithmetic mean of a non-empty list; ValueError on an empty one; tests in tests/test_stats.py.
t2 (small, depends on t1): add variance(xs) to calc/stats.py, the population variance, computed with mean() from t1; ValueError on an empty list; tests in tests/test_stats.py."
cd "$REPO" || exit 1
T=.sysop/tickets.json
expect "both tickets reviewed, neither landed" bash -c "python3 -c \"import json; t=json.load(open('$T'))['tickets']; assert {v['status'] for v in t.values()} <= {'reviewed', 'verified'}, t\""
expect "t2 was stacked on t1's sealed commit" bash -c "python3 -c \"import json; t=json.load(open('$T'))['tickets']['t2']; assert t['stacked_on']=='t1' and t['fork'], t\""
expect "t2's branch contains t1's reviewed commit" bash -c "git merge-base --is-ancestor \$(python3 -c \"import json; print(json.load(open('$T'))['tickets']['t2']['fork'])\") sysop/t2"
expect "main is untouched" test "$(git rev-parse main)" = "$BEFORE"
expect "both reviews sealed" bash -c "ls .sysop/reports/sysop-t1.seal.json .sysop/reports/sysop-t2.seal.json"
expect "the final message says how to land" bash -c "grep -qiE 'next:.*land' <<<\"\$0\"" "$(result_of v1_stacked)"
expect "no AI attribution" bash -c '! git log --all --format=%B | grep -qiE "co-authored-by|generated with"'
finish v1_stacked
