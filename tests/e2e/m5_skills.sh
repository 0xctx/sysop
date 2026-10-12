#!/usr/bin/env bash
# M5 e2e: each remaining skill exercised once, headless, in parallel sessions; assertions after.
source "$(dirname "$0")/lib.sh"
mk() { make_calc_repo "$E2E_ROOT/$1" false >/dev/null; cd "$E2E_ROOT/$1" || exit 1; }

mk prd;   ( run_claude m5_prd "/sysop:prd a tiny command-line tool that converts temperatures between Celsius and Fahrenheit for students. I decline the interview: just write it, marking guesses." >/dev/null ) &
mk rules; rm AGENTS.md; git commit -qam "no rules"
          ( run_claude m5_rules "/sysop:rules create. My working principles: plan before non-trivial work; tests prove behavior, not lines. Do not ask me anything else." >/dev/null ) &
mk go;    ( run_claude m5_go "/sysop:go why does calc.add(2, 3) return -1? Explain only; do not change any code." >/dev/null ) &
mk setup; rm .claude/sysop.json; git commit -qam "no settings"
          ( run_claude m5_setup "/sysop:setup --yes" >/dev/null ) &
mk tune;  python3 "$SYSOP/scripts/sysop.py" store >/dev/null
          for i in 1 2 3 4 5 6; do python3 "$SYSOP/scripts/sysop.py" ledger add --ticket a$i --worker agy --model gemini-3.8-flash-high --check pass --landed yes --seconds 60 --fallbacks 1 >/dev/null; done
          for i in 1 2; do python3 "$SYSOP/scripts/sysop.py" ledger add --ticket c$i --worker claude --model haiku --check pass --landed yes --seconds 40 >/dev/null; done
          cp .claude/sysop.json "$E2E_ROOT/tune.settings.before"
          ( run_claude m5_tune "/sysop:tune" >/dev/null ) &
wait

final() { python3 - "$E2E_ROOT/$1.jsonl" <<'PY'
import json, sys
for line in open(sys.argv[1]):
    try:
        d = json.loads(line)
    except ValueError:
        continue
    if d.get("type") == "result":
        print(d.get("result", ""))
PY
}
skills_used() { grep -o '"name":"Skill","input":{"skill":"[^"]*"' "$E2E_ROOT/$1.jsonl" | sed 's/.*"skill":"//;s/"$//'; }

cd "$E2E_ROOT/prd"
expect "prd: file written in docs/" bash -c 'ls docs/*.prd.md'
expect "prd: hypothesis has a wrong condition" bash -c 'grep -qi "wrong if" docs/*.prd.md'
expect "prd: never-build list" bash -c 'grep -qi "never build" docs/*.prd.md'
expect "prd: guesses marked TBD" bash -c 'grep -q "TBD" docs/*.prd.md'
cd "$E2E_ROOT/rules"
expect "rules: AGENTS.md created" test -f AGENTS.md
expect "rules: real test command recorded" grep -q "unittest" AGENTS.md
expect "rules: principles captured" grep -qi "plan before" AGENTS.md
expect "rules: no root CLAUDE.md created" test ! -f CLAUDE.md
cd "$E2E_ROOT/go"
expect "go: routed to sysop:debug" bash -c "skills_used(){ grep -o '\"skill\":\"[^\"]*\"' '$E2E_ROOT/m5_go.jsonl'; }; skills_used | grep -q 'sysop:debug'"
expect "go: code unchanged" bash -c 'git diff --quiet && grep -q "return a - b" calc/__init__.py'
expect "go: debug report written" bash -c 'ls .sysop/reports/*.debug.md'
cd "$E2E_ROOT/setup"
expect "setup: check recorded" bash -c "python3 '$SYSOP/scripts/sysop.py' config get check | grep -q unittest"
expect "setup: cross_review settled (kept default or set)" bash -c "python3 '$SYSOP/scripts/sysop.py' config get cross_review | grep -qE '^(true|false)$'"
cd "$E2E_ROOT/tune"
expect "tune: names the worker with enough data" bash -c "final(){ :; }; python3 -c \"import json;[print(json.loads(l).get('result','')) for l in open('$E2E_ROOT/m5_tune.jsonl') if '\\\"type\\\":\\\"result\\\"' in l]\" | grep -qi 'agy'"
expect "tune: no advice for claude:haiku (2 rows)" bash -c "python3 -c \"import json;[print(json.loads(l).get('result','')) for l in open('$E2E_ROOT/m5_tune.jsonl') if '\\\"type\\\":\\\"result\\\"' in l]\" | grep -qiE '(3|three) more|fewer than|not enough|insufficient|only 2'"
expect "tune: settings unchanged" cmp -s .claude/sysop.json "$E2E_ROOT/tune.settings.before"
finish m5_skills
