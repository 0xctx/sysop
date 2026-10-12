---
name: review
description: Independently reviews a branch at a pinned head with fresh read-only reviewer agents scaled to risk, plus a cross-family reviewer (agy reviews Claude's work and the reverse), then writes one review report with stable finding IDs and a READY/NEEDS FIXES verdict. Use when asked to "review this", "is it ready to land", "re-review the fixes", or via /sysop:review.
argument-hint: "[branch] [--base <branch>] [--re-review]"
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py *)
---

# Review

Coordinate an evidence-based review in which independence is the point: every judgement comes from a fresh context that did not write the code. The coordinator aggregates and decides severity; it never adds findings of its own and never edits code.

Store: !`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py store --soft`
Agent models: !`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py settings --key agents` (dispatch agents with these, per `${CLAUDE_PLUGIN_ROOT}/skills/review/references/scopes.md` → Agent models)
Cross-family review enabled: !`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py settings --key cross_review`

## Workflow
1. **Pin the target.** Branch = argument or current branch; base = `--base`, else settings `base`, else the default branch. Record `sha = git rev-parse <branch>`. slug = branch with `/` → `-`. Create a detached checkout pinned to it: `git worktree add --detach <store>/wt/review-<slug> <sha>`. Never review in a working tree someone is editing.
2. **Gather the contract**: for fleet tickets, the operator's verbatim request first (`<store>/briefs/<id>.request.txt`; acceptance is judged against those words, not a summary of them), then the plan (`<store>/plans/<slug>.md`) or the tiny contract in `<store>/reports/<slug>.build.md`, the build report, project guidance, the full diff `git diff <base>...<sha>`, and the previous review report when re-reviewing.
3. **Gate evidence.** Use `<store>/reports/<slug>.check.md` when it was made at this sha; otherwise run the `sysop:check` skill in the review worktree.
4. **Pick scopes.** Read `references/scopes.md` and choose by risk.
5. **Cross-family**: the author's family is the vendor of the model in the build report's "Built by" (this session's model when you built it); passing the model id or alias (`claude`, `gemini-3.1-pro-high`) is enough, and the script derives the vendor. Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py xreview start <branch> --base <base> --author-family <family> --context <plan or request/contract files>`, adding `--risky` for a risky change (auth, persisted data, destructive paths, concurrency, public contracts): it asks two reviewers from two other vendors, and collect writes both into the one report, the second under its own heading. The script routes to another family, launches the external reviewer, or writes down exactly why none is available. If the status is `running`, run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py xreview collect <branch>` (it blocks for at most about 9 minutes; repeat while it exits 124) after dispatching the agents in step 6. Collect moves on to the next reviewer by itself when one produces nothing, until the result is final. The script writes `<store>/reports/<slug>.xreview.md`. Treat its findings like any reviewer's (its OUT-OF-SCOPE items are discoveries, filed as in step 8, never findings), and quote its first line in the Signal verbatim; never describe cross-family review in your own words. `land` refuses without this file.
6. **Dispatch** the selected agents in parallel with the Agent tool (`subagent_type: "sysop:code-reviewer"` and so on; these are agents, not skills, and Claude Code's built-in `code-review` skill is a different thing), each given the shared instruction from the reference. Wait for all of them, and for the external reviewer, before aggregating; never act on partial results. When the Agent tool reports that they run in the background, end your turn with one status line: their results arrive as the next message, and you continue at step 7 then. Never sleep, poll or start timers to wait for them.
7. **Aggregate.** Merge duplicates into one causal finding crediting every source; with two cross-family reviewers, label each of their findings `both` (the same file and an overlapping line range, or the same defect named) or `one`; a `one` finding gets the normal disposition and is never dismissed for being single; keep real disagreement; judge final severity from the consequence; close any proved class by running its enumeration yourself if no reviewer did; assign stable IDs and states; decide the verdict by the reference's rules.
8. **Write** the report: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py report review <slug> --fresh` creates `<store>/reports/<slug>.review.md` from `templates/review-report.md`. Fill every section in place. The `**Verdict:**` line must name exactly one of READY, NEEDS FIXES or INCOMPLETE: `land` reads it and refuses anything but READY. Keep a copy as `<store>/reports/<slug>.review.r<round>.md`. A real defect you found outside this change's scope is not a finding here: file it once with `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py review discovery <file> "<cause>" "<one line>" --ticket <slug>` (the script skips a file and cause already on file).
9. **Seal** the finished report to the commit you reviewed: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py review seal <branch> --sha <sha from step 1>`, adding `--re-review` in re-review mode. Landing refuses a branch whose head is not the sealed commit, or whose report changed after sealing, so seal last and never edit the report afterwards.
10. **Clean up**: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py cleanup <store>/wt/review-<slug>`, including when stopping early.
11. **Answer** with the verdict, the counts by severity, and the report path.

## Resources
- `references/scopes.md`: risk-scaled scopes, the shared reviewer instruction, the cross-family prompt rule, severity, states, verdict rules, re-review.
- `templates/review-report.md`: the review report's required shape.

**Called by another skill** (ship, fleet)? Return the result and hand control back: the caller continues with its next step. Do not end the turn here.
