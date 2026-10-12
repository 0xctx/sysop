#!/usr/bin/env bash
# sysop demo: three small repos in a fresh folder, each with work queued in one lane, so a single
# `/sysop:fleet --all --yes` builds all of them at once. Safe to re-run: it starts the folder over.
#   /sysop demo [folder] [preset]   or   bash scripts/demo.sh [folder] [preset]
#   (defaults: ~/sysop-demo, and your active preset; free-max when none is set)
set -euo pipefail
DEMO="${1:-$HOME/sysop-demo}"
PRESET="${2:-}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
S="python3 $HERE/sysop.py"
if [ -z "$PRESET" ]; then  # the preset you switched to last (`/sysop preset <name>`), else free-max
  PRESET="$($S settings --key preset 2>/dev/null | tr -d '"' || true)"
  [ -n "$PRESET" ] && [ "$PRESET" != "null" ] || PRESET="free-max"
fi

rm -rf "$DEMO" && mkdir -p "$DEMO"
repo() {  # repo <name> <check>: a git repo whose sysop settings (the check) are committed
  mkdir -p "$DEMO/$1/.claude" && cd "$DEMO/$1"
  git init -q -b main
  printf '{"check": "%s", "cross_review": true}\n' "$2" > .claude/sysop.json
}
commit() { git add -A && git -c user.name=demo -c user.email=demo@example.com commit -qm "$1"; }

# 1. api: a tiny Python money library
repo api "python3 -m unittest discover -s tests -q"
mkdir -p money tests
printf '"""Money helpers."""\n\n\ndef cents(amount):\n    return round(amount * 100)\n' > money/__init__.py
printf 'import unittest\n\nfrom money import cents\n\n\nclass T(unittest.TestCase):\n    def test_cents(self):\n        self.assertEqual(cents(1.25), 125)\n' > tests/test_money.py
printf '# money\nHelpers in `money/__init__.py`; tests in `tests/` (unittest).\n' > AGENTS.md
commit "money helpers"

# 2. cli: a command line that will use the api (its ticket waits for api's)
repo cli "python3 -m unittest discover -s tests -q"
mkdir -p tests
printf '"""A tiny CLI."""\nimport sys\n\n\ndef main(argv):\n    print("usage: cli <command>")\n    return 0\n\n\nif __name__ == "__main__":\n    sys.exit(main(sys.argv[1:]))\n' > cli.py
printf 'import unittest\n\nimport cli\n\n\nclass T(unittest.TestCase):\n    def test_usage(self):\n        self.assertEqual(cli.main([]), 0)\n' > tests/test_cli.py
printf '# cli\nThe entry point is `cli.py`; tests in `tests/` (unittest).\n' > AGENTS.md
commit "cli skeleton"

# 3. book: prose, not code (the gate is sysop's prose check)
repo book "python3 $HERE/sysop.py prose-check chapters"
mkdir -p chapters
printf '# Chapter 1: The Lighthouse\n\nMara climbed the hundred steps every evening.\n' > chapters/01.md
printf '# book\nChapters live in `chapters/`, one file each, plain Markdown.\n' > AGENTS.md
commit "chapter one"

# One lane for all three, on the chosen preset, landing by itself (it's a demo).
$S lane add demo --preset "$PRESET" --land > /dev/null

ticket() {  # ticket <repo> <id> <size> <kind> <request> [depends]
  printf '%s\n' "$5" > "$DEMO/$1/.ticket-$2.txt"
  $S --cwd "$DEMO/$1" --lane demo ticket add "$2" --title "$5" --size "$3" --kind "$4" \
     --request-file "$DEMO/$1/.ticket-$2.txt" --base main --authority "standing land: true" \
     ${6:+--depends "$6"} > /dev/null
  rm "$DEMO/$1/.ticket-$2.txt"
}
ticket api  split   small feature "Add money.split(amount, parts) that splits an amount into parts that differ by at most one cent and sum exactly to it, with tests."
ticket api  format  small feature "Add money.format_usd(cents) returning strings like \$1,234.50, with tests."
ticket cli  split   small feature "Add a 'split <amount> <parts>' command to cli.py that prints each part using the api's money.split (copy the function in; this repo has no dependency on api). Tests in tests/test_cli.py." api:split
ticket book ch2     small docs    "Write chapter 2 (chapters/02.md, 300 to 500 words): the night the lighthouse lamp fails and Mara climbs anyway. Keep Mara's voice from chapter 1."

cat <<EOF

Demo ready in $DEMO: three repos, four tickets in lane 'demo', built with preset $PRESET.
(Another preset: /sysop demo $DEMO <preset>. kaggle-session needs /sysop serve up first.)
Next (watch the band above the prompt, and /sysop board):
  cd $DEMO/api && claude
  then type:  /sysop:fleet --all --yes
EOF
