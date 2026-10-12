# Changelog

## 1.0.0 (2026-10-12)

The first public release.

- **Three ways in:** `/sysop:go` routes one change to `ship`, a batch to `fleet`, and an idea through `prd` → `slice` → `fleet`, with a guided, narrated first run.
- **Off your session:** `/sysop:go` hands a change to the preset's models and frees the session while they build (`--here` builds in the session instead). **Lanes** are named queues, each on its own preset, cap and landing authority, across repos, with dependencies between repos and one coordinator (`/sysop:fleet --all`).
- **Unstoppable, honestly:** a stronger model takes one last try before a ticket waits for you; a cross-vendor verdict must show which files it checked or it does not count; repo facts that reviewers keep missing become AGENTS.md lines on your word.
- **Delivery you can check:** contract-first plans, builds in isolated worktrees, your project's own gate, a review from another vendor's model, and a seal that binds each READY review to the commit it read. Nothing lands without your authority.
- **The fleet engine:** `sysop.py next` owns dependency order, the parallel cap, stacking, attempts, halt and landing order; `--resume` picks up after a crash or `/clear`, and `/loop` runs it unattended.
- **Done means it runs (opt-in):** journeys checked against the running app by a calibrated verifier from another vendor.
- **Presets:** fourteen readable model setups chosen by what you have (a Claude plan, a Google plan, OpenRouter, free keys, Kaggle), each checked against what is installed; `preset use previous` undoes a switch. Exact Claude releases can be pinned, and the release a worker actually ran is recorded.
- **Three worker CLIs:** `claude`, `agy` and `pi` (free and open models included), with recorded fallbacks, cost tracking and a daily budget.
- **Free GPUs (opt-in):** Kaggle batch jobs, and models served from Kaggle as a pi provider.
- **One screen each:** `/sysop` (runs, tickets, ready to land, preset, resting models, spend, next step) and `/sysop doctor` (each problem with its one fix; setup runs it). Final messages state what the run cost.
- **Hooks:** a guard against credential reads, destructive commands, forged records and AI attribution; a stop gate; a short session-start context.

**Inside Claude Code (2.1.287+):**

- **The mod (Claude Code 2.1.287+):** `/sysop` shows the board and lands, halts, switches presets and lanes **at zero tokens**, even mid-turn. A band above the prompt shows what is working, what needs you and what is ready (`1` the board, `2` land ready); a board pane has Land, Why and Halt buttons; a toast marks each landing or block.
- **Claude sleeps while workers build:** the coordinator launches, pauses and ends its turn; the mod wakes it only when a worker finishes or something needs judgement.
- **Your plan is kept for you:** past 80% of the 5-hour or weekly limit (`claude_reserve`), delegated work goes to your other models.
- **Approve in place:** a destructive git command the guard stopped can be run once from a dialog you press, and is logged; credential reads, forged records and AI attribution are never offered.
- **Free sources:** Kilo's free gateway is a second route to the same free models; Groq's GPT-OSS is a fast free reviewer from another vendor; a refused model or key rests with its fix; `doctor --probe` checks each source.
- **Reviews:** risky work gets two reviewers from two other vendors; a sysop reviewer agent gets its configured model without a round trip.
- **GitHub:** issues labelled for a lane become its tickets and are closed when they land; PR lanes land through pull requests, and review comments go back to the builder before anything merges.
- **One word for merging:** `/sysop land` merges everything ready; `/sysop autoland on` lets a repo merge by itself; `/sysop demo` builds three small repos at once.
