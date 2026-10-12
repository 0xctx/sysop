#!/usr/bin/env bash
# T11 smoke: a preset applied by script, then a real 1-token probe of its models. No agent session.
# openrouter-free costs nothing (free models). A scratch HOME keeps the operator's settings and their
# `previous` preset untouched; pi keeps its real sign-in through PI_CODING_AGENT_DIR.
source "$(dirname "$0")/lib.sh"
command -v pi > /dev/null || { echo "SKIP: pi not installed"; exit 0; }
export PI_CODING_AGENT_DIR="${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}"
REPO="$E2E_ROOT/presets"
make_calc_repo "$REPO" true
REAL_HOME="$HOME"; export HOME="$E2E_ROOT/home" && mkdir -p "$HOME"
S="python3 $SYSOP/scripts/sysop.py --cwd $REPO"
$S roster --refresh > /dev/null
expect "openrouter-free is ready (OpenRouter key seen by pi)" bash -c "$S preset list --json | python3 -c 'import json,sys; r={x[\"name\"]:x for x in json.load(sys.stdin)}; sys.exit(r[\"openrouter-free\"][\"status\"]!=\"ready\")'"
expect "preset use writes the repo settings" bash -c "$S preset use openrouter-free --scope repo | grep -q 'preset use previous'"
expect "routing follows the preset" bash -c "$S route small | grep -q 'laguna-s-2.1:free'"
expect "review goes to another vendor than the builder" bash -c "$S route review --role review --not-family laguna | grep -q gemma"
$S models check fast > "$E2E_ROOT/presets-check.txt" 2>&1; cat "$E2E_ROOT/presets-check.txt"
expect "the fast tier answers through OpenRouter's free models" grep -qE 'openrouter/.*\bok\b' "$E2E_ROOT/presets-check.txt"
expect "undo restores the repo settings" bash -c "$S preset use previous && ! grep -q laguna '$REPO/.claude/sysop.json'"
export HOME="$REAL_HOME"
finish t11_presets
