#!/usr/bin/env bash
# Shared helpers for sysop end-to-end runs. They spend real tokens: run deliberately.
#   E2E_ROOT  scratch directory for repos and transcripts (default: mktemp -d)
#   MODEL     claude model for the driving session (default: haiku)
set -uo pipefail
SYSOP="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export E2E_ROOT="${E2E_ROOT:-$(mktemp -d -t sysop-e2e.XXXXXX)}"
MODEL="${MODEL:-haiku}"
FAILS=0

# The operator's real state that e2e sessions (which share HOME for auth) must never touch:
# other projects' memory, the global CLAUDE.md, and the user-level sysop settings.
operator_state() {
  { find "$HOME/.claude/projects" -path '*/memory/*' -type f -not -path '*-tmp-*' -print0 2>/dev/null \
      | sort -z | xargs -0 -r sha256sum
    sha256sum "$HOME/.claude/CLAUDE.md" "$HOME/.claude/sysop.json" 2>/dev/null; } | sha256sum
}
OPERATOR_STATE_BEFORE="$(operator_state)"
# sysop's caches (cooldowns, spend, roster, intel, serve state) go to a scratch directory, so an e2e
# run never cools down or bills the operator's real routes. Agent CLI sign-ins live under HOME, not here.
export XDG_CACHE_HOME="$E2E_ROOT/cache"
mkdir -p "$XDG_CACHE_HOME"

# make_calc_repo <dir> [cross_review(true|false)]: a tiny Python project with a seeded bug
make_calc_repo() {
  local dir="$1" cross="${2:-false}"
  rm -rf "$dir" && mkdir -p "$dir/calc" "$dir/tests" "$dir/.claude"
  cd "$dir" || return 1
  git init -q -b main
  git config user.name "e2e" && git config user.email "e2e@example.com"
  cat > calc/__init__.py <<'PY'
"""Tiny arithmetic helpers."""


def add(a, b):
    return a - b


def multiply(a, b):
    return a * b
PY
  cat > tests/test_calc.py <<'PY'
import unittest

from calc import multiply


class CalcTest(unittest.TestCase):
    def test_multiply(self):
        self.assertEqual(multiply(3, 4), 12)


if __name__ == "__main__":
    unittest.main()
PY
  cat > AGENTS.md <<'MD'
# calc
Tiny arithmetic helpers in `calc/__init__.py`; tests in `tests/` with stdlib unittest.
Check: `python3 -m unittest discover -s tests -q`.
MD
  # E2E_CLAUDE_RESERVE=0 lets a run spend the last of the plan (the operator's call, e.g. just before a reset)
  printf '{"check": "python3 -m unittest discover -s tests -q", "cross_review": %s%s}\n' "$cross" \
    "${E2E_CLAUDE_RESERVE:+, \"claude_reserve\": $E2E_CLAUDE_RESERVE}" > .claude/sysop.json
  git add -A && git commit -q -m "initial calc package"
}

# run_claude <name> <prompt>: headless session with the plugin; transcript in $E2E_ROOT/<name>.jsonl
run_claude() {
  local name="$1" prompt="$2" start
  start=$(date +%s)
  timeout "${E2E_TIMEOUT:-2400}" claude -p "$prompt" --plugin-dir "$SYSOP" --model "$MODEL" \
    --dangerously-skip-permissions --output-format stream-json --verbose \
    < /dev/null > "$E2E_ROOT/$name.jsonl" 2> "$E2E_ROOT/$name.stderr"
  local code=$?
  echo "[$name] session exit $code after $(( $(date +%s) - start ))s; transcript $E2E_ROOT/$name.jsonl"
  python3 - "$E2E_ROOT/$name.jsonl" <<'PY'
import json, sys
skills, agents, cost, result = [], [], None, ""
for line in open(sys.argv[1]):
    try:
        d = json.loads(line)
    except ValueError:
        continue
    if d.get("type") == "assistant":
        for c in d["message"].get("content", []):
            if c.get("type") == "tool_use" and c["name"] == "Skill":
                skills.append(c["input"].get("skill"))
            if c.get("type") == "tool_use" and c["name"] in ("Agent", "Task"):
                agents.append(c["input"].get("subagent_type"))
    if d.get("type") == "result":
        cost, result = d.get("total_cost_usd"), d.get("result", "")
print(f"  skills used: {skills}\n  agents used: {agents}\n  cost: ${cost}")
print("  final message:", (result or "")[:1200].replace("\n", "\n    "))
PY
  return $code
}

# claude_only: every tier on claude:haiku, Claude reviewers only, fleet cap 2: cheap, predictable, no free-key quota
claude_only() {
  python3 - "${1:-.claude/sysop.json}" <<'PY'
import json, os, sys
path = sys.argv[1]
data = json.load(open(path)) if os.path.exists(path) else {}
data.update(tiers={t: ["claude:haiku"] for t in ("fast", "standard", "deep", "review", "verify")},
            routes={"small": "fast", "large": "standard", "risky": "deep"}, cross_review=False, max_parallel=2)
os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
json.dump(data, open(path, "w"), indent=2)
PY
}
result_of() { python3 -c "import json,sys; print([json.loads(l) for l in open(sys.argv[1]) if '\"type\":\"result\"' in l][-1].get('result',''))" "$E2E_ROOT/$1.jsonl"; }
pass() { echo "  PASS: $*"; }
fail() { echo "  FAIL: $*"; FAILS=$((FAILS + 1)); }
expect() { local desc="$1"; shift; if "$@" >/dev/null 2>&1; then pass "$desc"; else fail "$desc"; fi; }
finish() {
  if [ "$(operator_state)" != "$OPERATOR_STATE_BEFORE" ]; then
    fail "the run changed the operator's real memory or settings (check ~/.claude/projects/*/memory)"
  fi
  echo "== $1: $FAILS failure(s)"; [ "$FAILS" -eq 0 ]
}
