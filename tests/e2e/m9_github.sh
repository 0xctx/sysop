#!/usr/bin/env bash
# M9 e2e: the GitHub path on the private sandbox (0xctx/sysop-sandbox, real GitHub Actions CI).
# `/sysop:ship --pr --land` adds a unique module, opens a PR, posts one canonical review comment,
# waits for CI, and merges through `sysop.py pr merge`. Each run adds its own file, so runs never collide.
source "$(dirname "$0")/lib.sh"
SANDBOX="${E2E_SANDBOX:-0xctx/sysop-sandbox}"
gh auth status > /dev/null 2>&1 || { echo "SKIP: gh not signed in"; exit 0; }
gh repo view "$SANDBOX" > /dev/null 2>&1 || { echo "SKIP: no access to $SANDBOX"; exit 0; }
REPO="$E2E_ROOT/sandbox"
git clone -q "https://github.com/$SANDBOX" "$REPO" && cd "$REPO" || exit 1
git config user.name "e2e" && git config user.email "e2e@example.com"
RUN=$(date +%s)
E2E_TIMEOUT=2700 run_claude m9_github "/sysop:ship --pr --land Add calc/clamp_$RUN.py with clamp(x, lo, hi) that returns x limited to the closed range [lo, hi] and raises ValueError when lo > hi; tests in tests/test_clamp_$RUN.py."
cd "$REPO" || exit 1
PR=$(gh pr list -R "$SANDBOX" --state all --limit 20 --json number,files \
  --jq "[.[] | select(any(.files[]; .path == \"calc/clamp_$RUN.py\"))][0].number")
echo "PR: ${PR:-none}"
expect "a PR was opened for the change" test -n "$PR"
expect "the PR is merged" bash -c "test \"\$(gh pr view $PR -R $SANDBOX --json state -q .state)\" = MERGED"
expect "exactly one canonical review comment" bash -c "test \"\$(gh api repos/$SANDBOX/issues/$PR/comments --jq '[.[] | select(.body | startswith(\"<!-- sysop-review\"))] | length')\" = 1"
expect "CI was green on the PR" gh pr checks "$PR" -R "$SANDBOX"
expect "origin main has the change" bash -c "git fetch -q origin && git cat-file -e origin/main:calc/clamp_$RUN.py"
expect "the local main checkout was fast-forwarded" bash -c "test \"\$(git rev-parse HEAD)\" = \"\$(git rev-parse origin/main)\" && test -f calc/clamp_$RUN.py"
expect "the remote branch was deleted" bash -c "! git ls-remote --heads origin | grep -q clamp"
expect "no worktree left" test "$(git worktree list | wc -l)" -eq 1
expect "the merge went through sysop (pr merge in the transcript)" grep -q 'pr merge' "$E2E_ROOT/m9_github.jsonl"
expect "no AI attribution in the PR or commits" bash -c "! (gh pr view $PR -R $SANDBOX --json title,body,commits -q '.title + .body + ([.commits[].messageBody] | join(\" \"))' | grep -qiE 'co-authored-by|generated with')"
expect "no run left active" bash -c "! ls .sysop/active/*.json"
finish m9_github
