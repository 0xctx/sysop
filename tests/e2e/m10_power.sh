#!/usr/bin/env bash
# M10 e2e: (1) a fleet race between two vendors on the calc repo, with notify to a file;
# (2) a prose review that must catch a planted continuity error; (3) the new mechanics
# (prose-check, models learn, spend line) run against the real repos.
source "$(dirname "$0")/lib.sh"
command -v pi > /dev/null || { echo "SKIP: pi not installed (a race needs two vendors)"; exit 0; }
REPO="$E2E_ROOT/race"
make_calc_repo "$REPO" true
sed -i 's/return a - b/return a + b/' "$REPO/calc/__init__.py" && git -C "$REPO" commit -qam "fix add"
NOTIFIED="$E2E_ROOT/notified"
python3 - "$REPO/.claude/sysop.json" "$NOTIFIED" <<'PY'
import json, sys
p = sys.argv[1]; d = json.load(open(p))
d["tiers"] = {"fast": ["claude:haiku", "pi:cline/nvidia/nemotron-3-super-120b-a12b:free"],
              "standard": ["claude:haiku"], "deep": ["claude:sonnet"],
              "review": ["pi:cline/nvidia/nemotron-3-ultra-550b-a55b:free", "pi:cline/nvidia/nemotron-3-super-120b-a12b:free",
                         "claude:sonnet"]}
d["notify"] = f"sh -c 'printf \"%s|%s\\\\n\" \"$0\" \"$1\" >> {sys.argv[2]}'"
json.dump(d, open(p, "w"), indent=2)
PY
git -C "$REPO" add .claude/sysop.json && git -C "$REPO" commit -qm "race routing"
S="python3 $SYSOP/scripts/sysop.py --cwd $REPO"
for c in pi pi:cline/ claude; do $S cooldown clear "$c" > /dev/null; done
E2E_TIMEOUT=3000 run_claude m10_race "/sysop:fleet --go --land --race 2 One ticket.
t1 (size small): add calc/stats.py with mean(xs) returning the arithmetic mean of a non-empty list of numbers, raising ValueError on an empty list; tests in tests/test_stats.py."
cd "$REPO" || exit 1
expect "a race was recorded with two racers" python3 -c "import json; d = json.load(open('.sysop/races/t1.json')); assert len(d['racers']) == 2"
expect "the racers are different vendors" python3 -c "import json; d = json.load(open('.sysop/races/t1.json')); assert len({r['family'] for r in d['racers']}) == 2"
expect "the race was settled" python3 -c "import json; assert json.load(open('.sysop/races/t1.json')).get('winner')"
expect "the loser's work is archived" bash -c "git for-each-ref refs/sysop/race/t1 | grep -q race"
expect "stats landed on main" test -f calc/stats.py
expect "gate green on main" python3 -m unittest discover -s tests -q
expect "the ledger has the loser and the winner" bash -c "test \$(grep -c '\"ticket\": \"t1-r' .sysop/ledger.jsonl) -ge 2"
expect "the operator was notified" test -s "$NOTIFIED"
expect "no worktree left" test "$(git worktree list | wc -l)" -eq 1
expect "no AI attribution" bash -c '! git log --format=%B | grep -qiE "co-authored-by|generated with"'

BOOK="$E2E_ROOT/book"
rm -rf "$BOOK" && mkdir -p "$BOOK/chapters" "$BOOK/.claude" && cd "$BOOK" && git init -q -b main
git config user.name e2e && git config user.email e2e@example.com
cat > chapters/01.md <<'MD'
# Chapter 1

Mara Venn was thirty-one the winter the lighthouse went dark. She kept her grandmother's brass key on a cord around her neck, and her eyes, grey as the harbour, missed nothing.
MD
printf '{"check": "sysop:prose-check", "cross_review": false}\n' > .claude/sysop.json
git add -A && git commit -qm "chapter 1"
git checkout -q -b ch2
cat > chapters/02.md <<'MD'
# Chapter 2

At nineteen, Mara had never trusted the sea. She touched the silver key at her throat and turned her green eyes toward the dark tower.
MD
git add -A && git commit -qm "chapter 2" && git checkout -q main
E2E_TIMEOUT=1500 run_claude m10_prose "/sysop:review ch2 (base main). This is a novel; review chapter 2 as prose against chapter 1."
expect "the prose reviewer ran" grep -q 'prose-reviewer' "$E2E_ROOT/m10_prose.jsonl"
expect "the continuity error was found (age, key or eyes)" bash -c "grep -qiE 'thirty-one|nineteen|brass|silver|grey|green' .sysop/reports/ch2.review.md"
expect "prose-check passes the manuscript" python3 "$SYSOP/scripts/sysop.py" prose-check
expect "models learn runs" python3 "$SYSOP/scripts/sysop.py" --cwd "$REPO" models learn
expect "models shows today's spend" bash -c "python3 $SYSOP/scripts/sysop.py --cwd $REPO models | grep -q 'Spend today'"
finish m10_power
