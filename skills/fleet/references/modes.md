# Fleet modes and details

The fleet skill's rarer paths. `S` means `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py`.

## Starting points
- **`--from <file>`:** instead of step 1, `S ticket import <file> --base <base> --authority "…"` (slices from `sysop:slice`, or a saved tickets file); then step 2.
- **`--add`** (a run is already active in this session): do step 1 for the new work, skip steps 2–3, and go back into the loop. One loop serves every lane.
- **`--resume`:** skip steps 1–2; run `S run resume fleet --session ${CLAUDE_SESSION_ID}` (it starts the run when none is active) and go straight to the loop. The tickets and every run are on disk, so `next` picks up exactly where the last coordinator stopped. A wake-up from the sysop mod arrives as this.
- **`--all`:** the loop runs `S next --all`, which covers every lane in every repo sysop knows under one machine-wide cap; each action carries its `repo`. Start the run with `S run start fleet --all --session ${CLAUDE_SESSION_ID}`, so wake-ups follow every repo.

## Lanes
With `--lane <name>`, tickets join that lane (`S --lane <name> ticket add …`); its preset builds and reviews them, under its own cap and landing authority. Without it, tickets join the default lane (this repo's settings). A ticket's lane follows it by itself: commands that name the ticket or its `sysop/<ticket>` branch act in its lane, and `route` takes `--ticket <id>` for the same. A dependency on another repo's ticket is `<repo folder>:<ticket>`; it starts only once that ticket has landed.

## PR lanes
A lane added with `--pr` lands through pull requests, and what people say on a PR goes back to its builder.
- **pr** `{ticket, branch, base, worktree, number?}`: from the worktree, `S --cwd <worktree> pr open --base <base> --title "<ticket title>" --body-file <store>/reports/sysop-<ticket>.pr.md` (problem, solution, validation; write it first), then `S pr comment <n> --report <store>/reports/sysop-<ticket>.review.md --slug sysop-<ticket>`, then `S ticket set <ticket> --pr <n>`. With `number`, the PR exists and lags the re-reviewed head: the same `pr open` pushes to it and `pr comment` edits the one comment.
- Review comments and requests for changes on the PR come back from `next` as **correct**, quoted with their links: one correction brief to the same worker, then re-review as usual.
- **pr-merge** `{ticket, number, branch}`: `S pr checks <number>` (repeat while it exits 124), then `S pr merge <number> --branch <branch> --land`. It refuses while the PR has feedback the ticket has not taken in; run `next` again.

## Issues as tickets
`S lane intake <lane> [--label <label>] [--every <minutes>]` takes the open GitHub issues labelled `sysop:<lane>` (or the label given) into the lane as tickets `issue-<n>`, once each: the request is the issue body verbatim, the contract a skeleton. **Fill the contract** from the request (`S ticket show issue-<n>`) before its launch, the way step 1 writes one. With `--every`, the sysop mod takes issues in on its own and the wake-up brings you to them. Landing an issue ticket pushes the base by itself, then comments the landed commit on the issue and closes it (with no origin to push to, the issue stays open and the ticket says why). Never close or comment on such an issue yourself (the guard refuses it during a run).

## Waiting without the mod
When Session says `"wakes": false`, `S wait <tickets…>` (add `--any` when the action says `any`; bounded at about 9 minutes; it shows tails only for failures). Interactive (`"interactive": true`): run it as a background command (the Bash tool's background mode, never `&`) and end the turn with one status line; the result wakes the loop. Headless: in the foreground. Re-run `next` after it returns, whatever it returned.

**A tick under `/loop`** (`/loop /sysop:fleet --resume`, for headless or remote sessions; an open interactive session with the mod needs no loop): when `next` lists only `wait` and the bounded wait returned 124, run `S run pause fleet` and end the turn; the next tick resumes.

## Escalation
A `correct` action that carries `escalate` means the author has had its tries: route the correction with `S route <escalate>` (a stronger model), launch that pick in the same worktree, and log the switch on the ticket with `S ticket log`. If that try fails too, `next` blocks the ticket for the operator.

## Background reviewer agents
When reviewer agents run in the background, end the turn with one status line and continue when their results arrive; the Stop hook lets that turn end. Never sleep or poll for them.
