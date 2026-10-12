---
name: ship
description: >-
  Owns one change end to end: contract, plan (skipped for tiny work, never the checks), build in an isolated worktree, project gate, independent and cross-family review, one batched correction pass, then landing locally or through a PR under the operator's authority. Use when asked to "ship this", "fix and land", "take this to done", "implement X end to end", or via /sysop:ship.
argument-hint: "<what to change> [--land] [--pr] [--base <branch>]"
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py *)
---

# Ship

Carry one change from request to a landed or ready-to-land state, with every claim backed by an artifact in the store. Compose the specialist skills; do not redo their craft here. Keep this context as the owner through corrections; use fresh contexts only where independence is the point (review).

**You own all ten steps.** Invoke each specialist with the Skill tool by its full name (`sysop:plan`, `sysop:build`, `sysop:review`, …), never a built-in namesake, and never do its work inline: its artifacts are the evidence later steps need. **Skills run inline:** when the Skill tool returns, carry out its instructions at once, in this turn; there is nothing to wait for. A specialist finishing (a report, a commit, a verdict) is an intermediate result: say which step is next and continue. The turn ends only at step 10.

Store: !`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py store --soft`
Settings: !`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py settings`
Session: !`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py session`

**Request:** $ARGUMENTS

`S` below means `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py`.

## Workflow
1. **Contract.** Establish the six elements per `${CLAUDE_PLUGIN_ROOT}/skills/plan/references/contract.md`. Ask once, only for missing product intent. If the outcome already holds, say so and stop.
2. **Size and kind**: size is tiny / small / large / risky (risky = auth, persisted data, destructive paths, concurrency, public contracts; when unsure, not tiny). Kind is one word (docs, tests, chore, bugfix, feature, refactor, perf, security, migration). ship builds in this session; to hand one change to a routed worker instead (another model or CLI), run it as a one-ticket `sysop:fleet`.
3. **Isolate.** From the main checkout: base = `--base`, else settings `base`, else the current branch of the main checkout. `git worktree add -b sysop/<short-slug> <store>/wt/sysop-<short-slug> <base>`; work only there (`cd <worktree> &&` on every command, absolute paths for edits); the operator's checkout is never modified. slug = `sysop-<short-slug>`. Mark the run active: `S run start <slug> --session ${CLAUDE_SESSION_ID}` (the Stop hook keeps the turn open meanwhile); record each later step with `S run step <slug> <n>`.
4. **Plan** (skip for tiny): `sysop:plan` saves `<store>/plans/<slug>.md`. Stop only on a decision it escalates.
5. **Build**: `sysop:build` with the plan path (or the tiny contract, stated as tiny): simplifier, gate, commit, `<store>/reports/<slug>.build.md`. To continue: green, or a red cause other than `introduced`, with evidence.
6. **Review**: `sysop:review` on the branch (fresh reviewer agents plus the cross-family reviewer; `<store>/reports/<slug>.review.md`).
7. **Correct once.** For `NEEDS FIXES` or Suggestions worth taking: `sysop:build` with `review <report>` here, one batched pass giving every finding a disposition; then `sysop:review --re-review`, which checks the dispositions and the correction diff and re-seals on the new head (a review covers only the commits it sealed). After two rounds with no progress, report the blocker.
7b. **Verify** (only when settings `start` is set): `S verify run sysop/<short-slug> --author-family <your model id>` drives the running app through its journeys. FAIL: one correction pass from `<store>/reports/<slug>.verify.md`, a light re-review, verify again. Landing needs a calibrated verifier's PASS at the sealed commit.
8. **Land.**
   - **Remote path** when `origin` exists and is pushable (or `--pr` was asked): follow `references/remote.md`.
   - **Local path** otherwise: from the main checkout `S land sysop/<short-slug> --base <base>`, with `--land` only when the request said `--land` or settings grant `"land": true`. It rebases, re-checks, fast-forwards and cleans up, and refuses without a review sealed to the head; its JSON is the evidence. If the main checkout is not on the base, say so; never switch it yourself.
   - **Without authority**: stop at "ready to land" and print the exact command.
9. **Ledger.** `S ledger add --ticket <slug> --size <size> --kind <kind> --worker claude --model "<your model id>" --check <pass|fail> --review "<verdict>" --landed <yes|no> --seconds <elapsed>`.
10. **End the run**: `S run end <slug> --outcome <landed|ready|blocked>`, then the **final message**, standing alone: the outcome in a sentence or two, the landed commit or PR URL, the plan and report paths, the check result, **the cost** (the ledger row's `usd`; none means "on your plan" or "free", never a guess), and any decision owed. Outcomes, not mechanics.

## Rules
- **Narration:** when Session says `"explain": true`, open steps 3, 5, 6, 7b and 8 with their lines from `references/narration.md`.
- **The final message** is in plain words (`${CLAUDE_PLUGIN_ROOT}/skills/go/references/words.md`; no internal term unexplained) and ends with `Next: <the one thing that moves this forward>`, from the last command's `next` field.
- **Stopping early for the operator** (a product decision, missing authority, a blocker after two rounds): `S run end <slug> --outcome blocked --reason "<what is needed>"` (it pings them), then ask. Never stop silently mid-run.
- Authority: landing or merging needs `--land` or a standing `"land": true`. Force-push, `reset --hard`, discarding work and `git branch -D` always need the operator's own word (the guard blocks them).
- **Background reviewer agents:** end the turn with one status line and continue when their results arrive; never sleep or poll. Never end a turn with a worker or CI run going and nothing waiting on it.
- Evidence over assertion: "done" means `land` (or the PR merge) succeeded and its output is quoted.

## Resources
- `references/narration.md`: the one plain line per step for someone learning sysop.
- `templates/verifier-brief.md`: what the runtime verifier is told (`sysop.py verify` fills in the URL and the journeys).
- `references/remote.md`: the PR path: canonical review comment, bounded CI wait, red-CI loop, merge under authority.
