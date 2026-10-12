#!/usr/bin/env bash
# M3 e2e: a tiny bug fix goes through /sysop:ship and lands locally with evidence, no plan.
source "$(dirname "$0")/lib.sh"
REPO="$E2E_ROOT/tiny"
make_calc_repo "$REPO" false
run_claude m3_tiny "/sysop:ship fix: calc.add(a, b) returns a - b but must return a + b. Add a regression test. --land"
cd "$REPO" || exit 1
expect "main has the fix" grep -q "return a + b" calc/__init__.py
expect "gate green on main" python3 -m unittest discover -s tests -q
expect "a regression test for add exists" grep -rq "add(" tests/
expect "review report says READY" bash -c 'grep -qE "Verdict:\**\s*READY" .sysop/reports/*review.md'
expect "the review was sealed to a commit" bash -c 'ls .sysop/reports/*.seal.json'
expect "no plan (tiny route)" bash -c '! ls .sysop/plans/*.md'
expect "ledger has one row" test "$(wc -l < .sysop/ledger.jsonl)" -eq 1
expect "no run left active" bash -c "! ls .sysop/active/*.json"
expect "no worktree left" test "$(git worktree list | wc -l)" -eq 1
expect "branch deleted" bash -c '! git branch | grep -q sysop/'
expect "operator checkout still on main" test "$(git rev-parse --abbrev-ref HEAD)" = main
expect "no AI attribution" bash -c '! git log --format=%B | grep -qiE "co-authored-by|generated with"'
finish m3_tiny_fix
