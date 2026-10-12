#!/usr/bin/env bash
# M3 e2e: a planned feature goes through /sysop:ship: plan, build (simplifier ran), review, land.
source "$(dirname "$0")/lib.sh"
REPO="$E2E_ROOT/feature"
make_calc_repo "$REPO" false
# fix the seeded bug first so this run is only about the feature
sed -i 's/return a - b/return a + b/' "$REPO/calc/__init__.py" && git -C "$REPO" commit -qam "fix add"
run_claude m3_feature "/sysop:ship add calc.divide(a, b) that returns a / b and raises ValueError with a clear message when b is 0, with unit tests. This is a small feature; plan it first. --land"
cd "$REPO" || exit 1
expect "plan exists" bash -c 'ls .sysop/plans/*.md'
# the invariant: planned work is simplified before it lands (build runs it; review runs it when build did not)
expect "the simplifier ran before landing" bash -c 'sed -n "/^## Simplifier/,/^## /p" .sysop/reports/*build.md | grep -qiE "^\**ran" || grep -q "sysop:simplifier" "$E2E_ROOT/m3_feature.jsonl"'
expect "review report says READY" bash -c 'grep -qE "Verdict:\**\s*READY" .sysop/reports/*review.md'
expect "divide landed on main" grep -q "def divide" calc/__init__.py
expect "gate green on main" python3 -m unittest discover -s tests -q
expect "divide is tested" grep -rq "divide" tests/
expect "ValueError raised for zero" python3 -c "import calc,sys
try: calc.divide(1, 0)
except ValueError: sys.exit(0)
sys.exit(1)"
expect "ledger has one row" test "$(wc -l < .sysop/ledger.jsonl)" -eq 1
expect "no run left active" bash -c "! ls .sysop/active/*.json"
expect "no worktree left" test "$(git worktree list | wc -l)" -eq 1
finish m3_planned_feature
