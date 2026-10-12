---
name: fleet
description: >-
  Runs a batch of agreed changes as parallel tickets: writes a ticket file, routes each to claude or agy by size with recorded fallbacks, runs workers detached in their own worktrees, reviews each across model families, and lands them one at a time. Use when asked to "run these as a fleet", "do these in parallel", "farm these out", or via /sysop:fleet.
argument-hint: "[--yes] [--land] [--lane <name>] [--add] [--all] [--resume] [--from <slices.json>] [--race N] [ticket notes]"
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py *)
---

# Fleet

Turn agreed work into tickets, build them in parallel, and land the reviewed results one at a time. **`sysop.py next` owns the state machine** (order, caps, stacking, attempts, halt, landing); this skill owns the judgement: tickets, briefs, failures, review.

Store: !`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py store --soft`
Settings: !`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py settings`
Roster: !`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py roster`
Session: !`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py session`

**Request:** $ARGUMENTS

`S` below means `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py`. **Skills run inline:** when the Skill tool returns, carry out its instructions at once, in this turn; a specialist finishing is never the end of the turn, so go back to the loop. Workers run only through `S launch` (never `&` or `nohup`), in the background.

## Workflow
`--from`, `--add`, `--resume`, `--all` and `--lane` change where this starts: `references/modes.md`. With `--race N`, follow `references/race.md` for each raced ticket instead of the loop.

1. **Tickets.** One ticket per independent outcome: an id (the operator's, else `t1`, … or kebab), size (small/large/risky), kind (one word: docs, tests, chore, bugfix, feature, refactor, perf, security, migration) and depends. For each, write to the store's `briefs/`: `<id>.request.txt`, the operator's words for that ticket **verbatim** (every path, name and number), and `<id>.contract.txt`, the six-element contract (`${CLAUDE_PLUGIN_ROOT}/skills/plan/references/contract.md`) without dropping a detail. Register each: `S ticket add <id> --title … --size … --kind … [--depends a,b] --request-file … --contract-file … --base <base> --authority "<a>"`, where `<a>` is `--land given` only when the literal `--land` is in the Request, `standing land: true` only when the settings (or the lane) say `"land": true`, otherwise `none`. Depend only on a true need (another ticket's output, shared state, a clashing migration), never on sharing a file.
2. **Confirm.** Show `<store>/tickets.md`. Unless the request says `--yes` (or the older `--go`), stop for the operator's go.
3. **Bracket the run:** `S run start fleet --session ${CLAUDE_SESSION_ID}`.
4. **Loop:** run `S next`, do every action it lists, in order, then run it again. Never invent or skip an action. An action that carries `repo` gets `--cwd <repo>` on every `S` command (and `git -C <repo>`).
   - **launch** `{ticket, branch, worktree, base_ref, stacked_on, fork}`: from the main checkout `git worktree add -b <branch> <worktree> <base_ref>`; `S report brief <ticket>` and fill it (absolute worktree path, branch, contract, `check`, report path `<store>/reports/sysop-<ticket>.build.md`, template `${CLAUDE_PLUGIN_ROOT}/skills/build/templates/build-report.md`); `S route <size> --kind <kind> --ticket <ticket> --prompt-file <brief>`; `S launch <ticket> <worktree> --worker <entry> --prompt-file <brief>` (it detaches); `S ticket set <ticket> --status running --worker <entry>` (plus `--stacked-on <stacked_on> --fork <fork>` when set); `S ticket log <ticket> "<route skips>"` when anything was skipped.
   - **wait** `{tickets, any}`: when Session says `"wakes": true`, **do not wait**: `S run pause fleet`, then end the turn with one status line ("3 tickets building; I'll pick up when one finishes"). The sysop mod wakes you (a prompt asking for `sysop:fleet --resume`) when a worker finishes or anything else needs you. Otherwise: `references/modes.md`.
   - **check** `{ticket}`: commits on its branch, `sysop:check` in its worktree, the build report with "Built by" set (from the route if missing). Green → `S ticket set <ticket> --status built`; else a work failure.
   - **correct** `{ticket, why}`: read `references/failures.md`. An availability failure: `S fallback …`, then launch its pick in the same worktree. A work failure, NEEDS FIXES or a landing conflict: one correction brief (the original plus the evidence `why` names) to the **same** worker in the same worktree, then `S ticket set <ticket> --status running`. With `escalate`: `references/modes.md`.
   - **review** `{ticket, branch, author}`: `sysop:review` on the branch: author family = the worker's model vendor; pass the ticket's request and contract. Its last step seals the verdict (`S review seal <branch> --sha <reviewed sha>`); `next` reads only the seal.
   - **verify** `{ticket, branch, author}` (only when settings `start` is set): `S verify run <branch> --author-family <the worker's model id>`.
   - **land** `{ticket, branch, base}`: from the main checkout, `S land <branch> --base <base> --land`.
   - **pr**, **pr-merge** (a PR lane): `references/modes.md` § PR lanes.
   - **blocked** `{ticket, why}`: nothing to do; it goes in the final message.
   - **done**: leave the loop.
   Record progress with `S run step fleet <n>`.
5. **Ledger**, one row per finished ticket: `S ledger add --ticket <id> --size <size> --kind <kind> --tier <tier> --check <pass|fail> --review "<verdict>" --landed <yes|no> --seconds <launch to land>`.
6. **Finish**: `S run end fleet --outcome <landed|ready|blocked>`. Two build reports with the same `repo fact:` line: run `sysop:rules drift` and offer its AGENTS.md line (applied on the operator's word). The final message stands alone: per ticket its outcome, builder (and any fallback, with why), verdict and reviewer family, landed commit; **the run's cost** (the ledger rows' `usd`; none means "on your plan" or "free", never a guess); any decision owed. Outcomes, not mechanics.

## Rules
- **Narration:** when Session says `"explain": true`, open each action with its line from `references/narration.md`, once per ticket.
- **The final message** is in plain words (`${CLAUDE_PLUGIN_ROOT}/skills/go/references/words.md`; no internal term unexplained) and ends with `Next: <the one thing that moves this forward>`, from `next`'s hint (for example `/sysop land`).
- **Ping before you stop for the operator:** `S notify "<what is needed>"` (`run end` pings by itself). `S halt` / `S unhalt` stop and resume new launches.
- **Keep this session light:** it coordinates; workers read and build. Open a log or report only when something failed, and only the part that says why. Background reviewer agents: `references/modes.md`.
- Settings change only inside a ticket's branch; never `config set` on the main checkout during a run (it blocks every landing).
- Never build a ticket yourself (the guard refuses it); one no worker can build is blocked for the operator.
- Fallback is for availability, never for bad code. Every switch is recorded on the ticket with its evidence.
- Landing, force and discard follow `sysop:ship`'s authority rules. A dependent ticket starts stacked on its dependency's reviewed branch and lands after it, only if its change replays unchanged.

## Resources
- `references/modes.md`: `--from`, `--add`, `--resume`, `--all`, lanes, PR lanes, GitHub issues as tickets, waiting without the mod, `/loop`, escalation.
- `references/narration.md`: the one plain line per action for someone learning sysop.
- `references/race.md`: `--race N`.
- `references/failures.md`: availability vs work failures, fallback and correction, stuck workers.
- `templates/brief.md`: the work order a worker receives.
