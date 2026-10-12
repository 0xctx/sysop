# Architecture

How sysop works today. The reference for every skill, setting and command is [`guide.md`](guide.md); credits are in [`lineage.md`](lineage.md).

## Three layers, plus the mod

| Layer | What it holds | Rule |
|---|---|---|
| **Skills** (`skills/*/SKILL.md`) and **agents** (`agents/*.md`) | judgement: contracts, plans, briefs, failure classification, review | they never do mechanics by hand; every exact step is one `sysop.py` command |
| **Scripts** (`scripts/sysop.py` and the modules behind it) | mechanics: routing, launching, waiting, sealing, landing, the fleet's state machine | standard-library Python, unit-tested offline; they decide nothing a person or a skill should |
| **Hooks** (`hooks/*.py`) | guarantees: the guard, the session context, the Stop gate | a rule asks; a hook enforces, in every session where the plugin is installed |
| **The mod** (`hooks/sysop.mod.jsx`, Claude Code 2.1.287+) | sight and speed inside Claude Code: `/sysop`, the board, wake-ups, the plan reserve's reading | it shows, wakes and runs the exact command the operator pressed; it never decides, and everything works with it off |

Imports run one way: `store` ← `workers` ← `fleet`, `store` ← `land` ← `fleet`, `workers` ← `presets` ← `health`, `fleet` ← `health`, and `workers` ← `advice` (loaded only for `auto:` routes, doors and `models`). Hooks import only `store`.

## The flow of one change
1. **Contract.** The session agrees the outcome with the operator (`skills/plan/references/contract.md`).
2. **Route.** `sysop.py route` picks the first available `worker:model@effort` in the tier the size or kind maps to, skipping entries that rest (a cooldown, a missing login, the plan reserve) and trying the same model through another door first.
3. **Build** in an isolated worktree under `.sysop/wt/`, by a worker launched detached (`sysop.py launch`) or, with `/sysop:go --here`, in the session itself.
4. **Check.** The project's own gate (settings `check`).
5. **Review.** Fresh read-only agents plus a reviewer from another vendor, run by the script (`xreview`); a verdict that does not name every changed file is no review.
6. **Seal.** `review seal` binds the verdict to the exact commit it read.
7. **Land.** `sysop.py land` rebases, re-checks, fast-forwards and cleans up, only with the operator's authority (`--land` in the request, or a standing grant), and only under the land gate below.

## The fleet
`sysop.py next` is the state machine: dependency order, the parallel cap, stacking a dependent ticket on its dependency's reviewed branch, attempts, escalation a tier up, halt, and serial landing. The fleet skill does what each action needs and nothing else. Every fact `next` reads is on disk, so a coordinator can stop and resume at any point.

**Waiting.** With the mod, the coordinator launches, pauses its run and ends its turn; the mod asks `sysop.py wake` every 10 seconds and, when more than waiting is due, wakes it once. Without the mod, the coordinator waits in the background (interactive) or the foreground (headless), or a `/loop` tick resumes it.

## Lanes and presets
- **A preset** is a JSON file of tiers, routes, agents and limits, with what it needs and its catch (`presets/`, plus the operator's and the repo's own). `preset use` applies one and keeps the old settings as `previous`.
- **A lane** is a named queue on its own preset, parallel cap and landing authority. A ticket carries its lane; any command naming the ticket acts in it. `next --all` drives every lane in every registered repo under one machine-wide cap, and a ticket may depend on another repo's ticket.

## The land gate
A local land and a GitHub merge refuse unless all of these hold:
- exactly one `Verdict: READY`;
- a cross-vendor review result written by the script (or `cross_review: false`, stated);
- a seal binding that review to the branch's current commit;
- when `start` is set, a calibrated verifier's PASS at the same commit;
- the operator's authority.

## The records
| Where | What |
|---|---|
| `<repo>/.sysop/tickets.json` (+ `tickets.md`) | the fleet's tickets; only `sysop.py` writes them |
| `<repo>/.sysop/runs/<ticket>/<n>/` | one attempt: command, worker, log, pid, exit |
| `<repo>/.sysop/reports/` | build, review and verify reports; `*.seal.json` |
| `<repo>/.sysop/plans/`, `briefs/` | plans; each ticket's verbatim request and contract |
| `<repo>/.sysop/active/*.json`, `HALT` | active run markers (the Stop gate and wake-ups read them); the off switch |
| `<repo>/.sysop/ledger.jsonl`, `runlog.jsonl`, `discoveries.md` | outcomes per ticket and per run; out-of-scope defects |
| `~/.claude/sysop.json`, `<repo>/.claude/sysop.json` | settings, user then repo |
| `~/.cache/sysop/` | the roster, cooldowns, model facts, spend, Kaggle state, the plan's usage (`claude-usage.json`) and the mod's heartbeat (`mod-<session>.json`) |

## What is deliberately not here
No server, no database, no build step, no telemetry. Free routes that impersonate another client (pi-free's Cline pools) are opt-in and never a default. Kaggle runs only ML work, under its terms.
