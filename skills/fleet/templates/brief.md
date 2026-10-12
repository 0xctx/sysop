# Work order: <slug>

You are working alone and unattended in this git worktree: `<worktree>` (branch `<branch>`). Work only inside it. Nobody will answer questions; when the order is impossible or contradictory, write that in the report (verdict: blocked) and stop, without speculative edits.

## The operator's request (verbatim; this is what you are judged against)
<the ticket's request text, unedited>

## Contract
<problem · why · outcome · invariants · acceptance, copied from the ticket>

## How to work
1. Read the repository guidance (AGENTS.md, CLAUDE.md, README) and the files involved, with their tests. The code is the fact; if it contradicts this order, follow the code and say so in the report.
2. **Plan first when the ticket is large or risky:** before editing, write a short plan at the top of the build report: the approach and the strongest alternative you rejected (and why), the files and existing helpers you will use, the tests that prove each acceptance item, and the risks. Then build to it. A small ticket skips the plan, never the tests.
3. Reproduce a bug as a failing test before fixing it. Make the smallest change that meets the contract, with focused tests that prove each acceptance item.
4. Run the project gate until it is green: `<check command>`. Never edit a lockfile; never weaken or delete a test to get green.
5. Commit on the current branch, staging files by name. The message names the outcome. No AI attribution (no Co-Authored-By, no "Generated with"). Never push, merge, rebase or switch branches.
6. Write the build report to `<build report path>`, following the structure of `<build report template path>`. Fill "Built by" with your CLI and model, the Validation table with the commands you ran and their exit codes, and the verdict line.
7. Stop. Your last message is a one-line summary: done / blocked, and why.
