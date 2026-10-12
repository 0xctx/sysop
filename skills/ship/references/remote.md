# Remote path (origin exists and is pushable)

sysop owns the mechanics; run each step as one command and act on its JSON. Never call `gh pr merge` yourself: the guard refuses it, because a direct merge skips the gates.

1. **Open the PR** with the `sysop:pr` skill from the worktree (`sysop.py pr open`). `local_only: <reason>` means ship lands locally instead; say the reason in one line. Record the PR number and URL in the build report.
2. **Publish the review** as the one canonical PR comment:
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py pr comment <n> --report <store>/reports/<slug>.review.md --slug <slug>`.
   It creates the comment once, then edits that same comment in later rounds, and reads it back. Never post a second report. Record the returned comment URL at the top of the review report.
3. **Wait for CI**, bounded: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py pr checks <n>`.
   - Exit 0 means green, or `none` (no CI; the local gate result stands, so say so).
   - Exit 1 means red, with `failing` jobs and run ids.
   - Exit 124 means still pending: run it again. A pending check is never green.
4. **Red CI**: read the failing job log (`gh run view <run> --log-failed`) and classify it as `check` does. Fix `introduced` failures with `sysop:build`, commit, and run `pr open` again (it pushes to the same PR). Every fix is new code: re-review it (`sysop:review --re-review`, light unless the fix is risky), update the comment (step 2), and wait again (step 3).
5. **Merge** with the operator's authority only:
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py pr merge <n> --branch <branch> [--land]`.
   - Pass `--land` only when the request said `--land` or settings grant `"land": true`.
   - It refuses without a READY review and a cross-family result.
   - It also refuses on red or pending checks, a conflict, a required human review on GitHub, or a PR head that is not the commit the review sealed.
   - It merges with the repository's allowed method (never `--admin`), deletes the remote branch, fast-forwards the main checkout, and removes sysop's worktree and the local branch.
6. Without authority it returns `ready: true`: stop at "ready to merge" with the PR URL and the exact `pr merge … --land` line.
