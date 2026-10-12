#!/usr/bin/env bash
# M7 e2e: one fleet ticket built by pi on a free model, reviewed by another vendor, landed.
# Cline's free pool comes first when pi-free is installed (route skips it otherwise), then Gemini
# through pi's built-in provider. Free pools refuse at times (429/503): a non-pi build is
# accepted only after every pi attempt failed for availability, with each switch recorded.
# Free pools rate-limit (429) outside our control: a recorded fallback to the next pi entry
# is accepted; a silent switch or a non-pi build is not.
source "$(dirname "$0")/lib.sh"
command -v pi > /dev/null || { echo "SKIP: pi not installed"; exit 0; }
REPO="$E2E_ROOT/pi"
make_calc_repo "$REPO" true
sed -i 's/return a - b/return a + b/' "$REPO/calc/__init__.py" && git -C "$REPO" commit -qam "fix add"
python3 - "$REPO/.claude/sysop.json" <<'PY'
import json, sys
p = sys.argv[1]; d = json.load(open(p))
d["tiers"] = {"fast": ["pi:cline/nvidia/nemotron-3-super-120b-a12b:free", "pi:cline/poolside/laguna-s-2.1:free",
                       "pi:google/gemini-3.8-flash", "pi:google/gemini-3.5-flash-lite"],
              "standard": ["claude:haiku"], "deep": ["claude:sonnet"],
              "review": ["agy:gpt-oss-120b-medium", "claude:sonnet"]}
json.dump(d, open(p, "w"), indent=2)
PY
git -C "$REPO" add .claude/sysop.json && git -C "$REPO" commit -qm "pi test routing"
S="python3 $SYSOP/scripts/sysop.py --cwd $REPO"
$S roster --refresh > /dev/null
for c in pi pi:cline/ pi:google/ pi:cline/nvidia/nemotron-3-super-120b-a12b:free pi:cline/poolside/laguna-s-2.1:free \
         pi:google/gemini-3.8-flash pi:google/gemini-3.5-flash-lite agy agy:gpt-oss-120b-medium; do $S cooldown clear "$c" > /dev/null; done
E2E_TIMEOUT=2400 run_claude m7_pi "/sysop:fleet --go --land One ticket.
t1 (size small): add calc/stats.py with mean(xs) returning the arithmetic mean of a non-empty list of numbers, raising ValueError on an empty list; tests in tests/test_stats.py."
cd "$REPO" || exit 1
expect "stats landed" test -f calc/stats.py
expect "gate green on main" python3 -m unittest discover -s tests -q
expect "ledger has one row" test "$(wc -l < .sysop/ledger.jsonl)" -eq 1
expect "the first build attempt was pi (launch records)" grep -q '^pi:' .sysop/runs/t1/1/worker
expect "built by pi, or every pi attempt failed for availability (logs)" bash -c "grep '\"ticket\": \"t1\"' .sysop/ledger.jsonl | grep -q '\"worker\": \"pi\"' || for d in .sysop/runs/t1/*/; do grep -q '^pi:' \$d/worker || continue; grep -qiE '429|503|quota|rate|unavailable|no api key' \$d/log || exit 1; done"
expect "any switch between pi entries is recorded on the ticket" bash -c "n=\$(ls -d .sysop/runs/t1/*/ | wc -l); [ \$n -eq 1 ] || grep -qi 'fallback' .sysop/tickets.md"
expect "cross-family result is script-written and final" bash -c "grep -qE '\"status\": \"(done|unavailable|claude-agents)\"' .sysop/reports/sysop-t1.xreview.json"
expect "a done cross-review came from a vendor other than the author's" bash -c "! grep -q '\"status\": \"done\"' .sysop/reports/sysop-t1.xreview.json || python3 -c \"import sys; sys.path.insert(0, '$SYSOP/scripts'); import sysop, glob; a = open(sorted(glob.glob('.sysop/runs/t1/*/worker'))[-1]).read().strip(); r = open(sorted(glob.glob('.sysop/runs/xreview-sysop-t1/*/worker'))[-1]).read().strip(); sys.exit(sysop.family(a.split(':', 1)[1]) == sysop.family(r.split(':', 1)[1]))\""
expect "review READY" grep -qE 'Verdict:\**\s*READY' .sysop/reports/sysop-t1.review.md
expect "no run left active" bash -c "! ls .sysop/active/*.json"
expect "no worktree left" test "$(git worktree list | wc -l)" -eq 1
expect "no AI attribution" bash -c '! git log --format=%B | grep -qiE "co-authored-by|generated with"'
finish m7_pi
