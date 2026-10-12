# Lineage: what came from where, and what was left out

Credits first: sysop stands on ideas from the projects below. Thank you to their authors.

sysop distils five agent-tooling repositories into one small plugin. Each idea below has one owner in sysop; the column on the right says where it lives now. Where sysop departs from its source, the reason is given.

## Taken

| Source | Idea | Where it lives in sysop |
|---|---|---|
| Cole Medin, `skills/` | The PIV loop (plan → implement → validate) | sysop's own loop: `plan` → `build` → `check` → `review` → `commit`, chained by `ship` and `fleet` |
| | The skill craft standard: trigger-rich descriptions, lean imperative bodies, detail in `references/` | every `skills/*/SKILL.md`; `scripts/lint.py` enforces budgets |
| | Rule vs hook: a rule asks, a hook guarantees; the six hook gotchas | `hooks/` (guard, context, stop gate); AGENTS.md ground rules |
| | Rules as a map plus conventions, commands and principles, checked for drift | `AGENTS.md`; `sysop:rules` |
| PRP (`prp/`) | Planning craft: invariants, existing primitives, the laziness test, a spike at a hinge | `sysop:plan` |
| | One owner of the end-to-end loop, a tiny-work route, review only in a fresh context, batched fixes | `sysop:ship` |
| | Risk-scaled review scopes, a detached checkout pinned to the head, enumerating a whole bug class, stable finding IDs, one canonical PR comment | `sysop:review`, `references/scopes.md`; `sysop.py pr comment` |
| | The reviewer agents | `agents/` (code-reviewer, failure-hunter, seam-reviewer, simplifier) |
| | Commit and PR discipline | `sysop:commit`, `sysop:pr` |
| Archon | "YAML coordinates, code computes, agents judge" | skills judge, `scripts/sysop.py` computes, hooks guarantee |
| | The six-element contract; "prose is a claim, code is the fact"; "decide, don't defer"; red-check cause labels | `skills/plan/references/contract.md`; `sysop:check` |
| | "Public writes belong to a script"; the guard test | `sysop.py pr …`, `land`; the guard refuses a raw `gh pr merge`; script-owned records |
| firstmate | Explicit authority; escalation in outcome language; parallel by default | `land`/`--land` gates; plain words (`skills/go/references/words.md`); `sysop:fleet` |
| | Diagnostic reasoning (trigger, masking condition, symptom; competing hypotheses) | `sysop:debug`, `agents/root-cause.md` |
| | Knowledge placement, one owner per fact | AGENTS.md; `sysop:rules` (session-knowledge stowing dropped in v1: Claude Code's memory covers it) |
| | The agy harness notes | the `agy` worker in `sysop.py` |
| ai-software-factory | A setup interview of at most four questions, each with a proposed answer | `sysop:setup` |
| | A "never build" list | `docs/prd.md` non-goals; the Kaggle policy in `docs/architecture-serve.md` |
| | Incident write-ups as failure lessons | "hard-won facts" in AGENTS.md |

## Left out, and why

| Left out | Why |
|---|---|
| Archon's workflow engine and YAML DAGs | sysop needs no server or engine: skills coordinate, one script computes. |
| The factory's scheduler and dark-factory triggers | Out of scope for a one-person, on-machine tool; fleet covers parallel work. |
| firstmate's tmux supervision | Detached workers with `launch`/`wait` and a stop gate replace a supervising terminal. |
| PRP's inline shell snippets in skills | Agents skip shell snippets they are told to run (verified), and copies drift. Mechanics are `sysop.py` subcommands with tests instead. |
| The ~160 skills and agents of the sources | The always-loaded budget is about 1.3k tokens (now about 940). Nineteen skills, seven agents. |
| External trackers (Jira, Linear) | Tickets live in the store, rendered by the script. |
| Syncing to Codex and other harnesses | Out of scope; the design stays open (custom workers in settings). |

## Grown beyond the original spec (operator decisions, 2026-10-06 and 2026-10-07)
- **pi as a third worker:** free and open models through one CLI, with pi-free optional (its Cline provider impersonates Cline's client, so sysop never depends on it).
- **Model intel and advice:** public benchmarks plus this repo's own outcomes.
- **Routes per model:** the same model reached through several CLIs or providers, with fallback to the same model first.
- **Kaggle, within its rules:** finite batch jobs, and opt-in session-scoped serving. Both run only ML work, personal use, and stop themselves. Out of scope in the first spec; the operator brought it in as an opt-in module.
- **The GitHub path:** PR, comment, CI and merge as tested commands.
- **Power features:** race mode, learned routing, a cost and quota ledger with a budget, notifications, a prose reviewer and a writing gate. The operator uses sysop for long-form writing too, not only code.

## Grown before the first release (operator decisions, 2026-10-11)
- **The mod:** Claude Code's in-process hooks module gives `/sysop` (zero tokens), the board, wake-ups so the coordinator sleeps while workers build, and the plan reserve.
- **Free sources settled:** Kilo's free gateway as a second door, Groq's GPT-OSS as the free OpenAI-vendor reviewer; fastrouter, Copilot and SambaNova left out.
- **Simpler surfaces:** one command per intent (`/sysop:go` to ask, `/sysop` to look and press), everyday commands in `--help`, skills cut to what is read every time.

## Where to read on
The design is [`architecture.md`](architecture.md); [`guide.md`](guide.md) is the reference for every skill, setting and command.
