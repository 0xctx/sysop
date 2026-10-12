#!/usr/bin/env bash
# v1 e2e, finding D1: the coordinator is killed mid-fleet (a crash, a closed laptop, a context limit);
# a new session with --resume picks up from the records and finishes, without duplicating anything.
source "$(dirname "$0")/lib.sh"
REPO="$E2E_ROOT/resume"
make_calc_repo "$REPO" false
claude_only .claude/sysop.json && git add -A && git commit -qm "claude-only routing for the test"
# kill the coordinator a few seconds after its first worker starts: a crash mid-run, not a clean stop
PROMPT="/sysop:fleet --go --land Two independent tickets.
t1 (small): add calc/stats.py with mean(xs); ValueError on an empty list; tests in tests/test_stats.py.
t2 (small): add calc/geometry.py with area_square(s) = s*s; ValueError for negative s; tests in tests/test_geometry.py."
setsid bash -c 'source "$0/lib.sh"; cd "$1"; E2E_TIMEOUT=1800 run_claude v1_resume_1 "$2"' \
  "$(cd "$(dirname "$0")" && pwd)" "$REPO" "$PROMPT" > "$E2E_ROOT/v1_resume_1.out" 2>&1 &
LEADER=$!
for _ in $(seq 600); do ls "$REPO"/.sysop/runs/t*/1/pid >/dev/null 2>&1 && break; sleep 1; done
sleep 8
kill -TERM -- "-$LEADER" 2>/dev/null; wait "$LEADER" 2>/dev/null
echo "[v1_resume_1] coordinator killed after its first launch"
cd "$REPO" || exit 1
expect "the first coordinator was cut off before finishing" bash -c "python3 -c \"import json; t=json.load(open('.sysop/tickets.json'))['tickets']; assert any(v['status']!='landed' for v in t.values())\""
E2E_TIMEOUT=3600 run_claude v1_resume_2 "/sysop:fleet --resume --land"
expect "both tickets landed" bash -c "test -f calc/stats.py && test -f calc/geometry.py"
expect "tickets recorded landed, no duplicates" bash -c "python3 -c \"import json; t=json.load(open('.sysop/tickets.json'))['tickets']; assert sorted(t)==['t1','t2'] and all(v['status']=='landed' for v in t.values()), t\""
expect "each ticket launched no more than its attempts" bash -c "test \$(ls .sysop/runs/t1 | wc -l) -le 3 && test \$(ls .sysop/runs/t2 | wc -l) -le 3"
expect "the resumed session called --resume, not a new ticket list" bash -c "! grep -q 'ticket add' '$E2E_ROOT/v1_resume_2.jsonl'"
expect "gate green on main" python3 -m unittest discover -s tests -q
expect "no run left active" bash -c "! ls .sysop/active/*.json"
finish v1_resume
