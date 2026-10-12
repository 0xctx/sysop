#!/usr/bin/env bash
# T10 e2e: someone new, in an unconfigured repo. `/sysop:go` with nothing asks what they want (with
# examples, never the README); setup with a first lap carries one tiny change to main, narrated, and the
# final message is plain words ending in a "Next:" line.
source "$(dirname "$0")/lib.sh"
REPO="$E2E_ROOT/first"
make_calc_repo "$REPO" false
rm .claude/sysop.json && git rm -q --cached .claude/sysop.json && git commit -qm "an unconfigured repo"
run_claude first_go "/sysop:go"
GO_REPLY=$(python3 -c "import json,sys; print([json.loads(l) for l in open(sys.argv[1]) if '\"type\":\"result\"' in l][-1].get('result',''))" "$E2E_ROOT/first_go.jsonl")
expect "go asks what they want, with an idea example" bash -c "grep -qiE 'idea|app' <<<\"\$0\" && grep -qiE 'change|fix' <<<\"\$0\"" "$GO_REPLY"
expect "go does not paste the README" bash -c "! grep -q 'How you work with sysop\|## Models: one table' <<<\"\$0\"" "$GO_REPLY"
expect "go made no change yet" test "$(git rev-list --count HEAD)" -eq 2
run_claude first_lap '/sysop:setup --yes --first-lap "fix: calc.add(a, b) returns a - b but must return a + b; add a regression test" --land'
cd "$REPO" || exit 1
FINAL=$(python3 -c "import json,sys; print([json.loads(l) for l in open(sys.argv[1]) if '\"type\":\"result\"' in l][-1].get('result',''))" "$E2E_ROOT/first_lap.jsonl")
TEXT=$(python3 -c "
import json, sys
out = []
for line in open(sys.argv[1]):
    d = json.loads(line)
    if d.get('type') == 'assistant':
        out += [c['text'] for c in d['message']['content'] if c.get('type') == 'text']
print('\n'.join(out))" "$E2E_ROOT/first_lap.jsonl")
expect "setup wrote the check" grep -q '"check"' .claude/sysop.json
expect "the first lap landed the fix on main" grep -q "return a + b" calc/__init__.py
expect "gate green on main" python3 -m unittest discover -s tests -q
expect "the steps were narrated (isolated copy, second opinion)" bash -c "grep -qi 'isolated copy' <<<\"\$0\" && grep -qiE 'another (AI )?vendor|second opinion' <<<\"\$0\"" "$TEXT"
expect "the final message ends with a Next: line" bash -c "grep -q '^\**Next:' <<<\"\$0\"" "$FINAL"
# file paths may name internals (the reader may open them); the prose may not
PROSE=$(sed -E 's#`[^`]*/[^`]*`##g; s#[^ ]*\.sysop/[^ ]*##g' <<<"$FINAL")
expect "no unexplained internal terms in the final message" bash -c "! grep -qiE 'xreview|cross-family|red_cause|worktree|cooldown|run marker|ledger row|fast-forward' <<<\"\$0\"" "$PROSE"
expect "narration is off after the first landing" bash -c "python3 '$SYSOP/scripts/sysop.py' explain | grep -q '\"explain\": false'"
expect "no AI attribution" bash -c '! git log --format=%B | grep -qiE "co-authored-by|generated with"'
finish first_run
