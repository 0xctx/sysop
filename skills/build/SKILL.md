---
name: build
description: >-
  Implements a sysop plan or a tiny contract on the current branch: reproduces bugs first, writes focused tests, runs the simplifier early, passes the project's gate, commits per outcome, and writes a build report. Also applies review findings in one batched pass. Use when asked to "implement the plan", "build this", "apply the review findings", or via /sysop:build.
argument-hint: "<plan path | tiny change in words> | review <review report path>"
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py *)
---

# Build

Implement the work until it is complete and the project's own gate is green, and leave the code the way you would want to find it. Own the outcome, not the attempt.

Store: !`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py store --soft`
Agent models: !`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py settings --key agents` (dispatch agents with these, per `${CLAUDE_PLUGIN_ROOT}/skills/review/references/scopes.md` → Agent models)

**Input:** $ARGUMENTS

## Modes
- **Plan path:** read the whole plan, then implement its steps in order.
- **Tiny change:** the stated contract is the plan (see `${CLAUDE_PLUGIN_ROOT}/skills/plan/references/contract.md` for what makes work tiny). If it turns out not to be tiny, stop before committing and say so; the caller plans it.
- **`review <report>`:** correction pass, described below.

## Workflow
1. **Ground.** Read repository guidance, the files the work names, their direct callers and tests. Live code beats the plan's assumptions; when they conflict, follow the code and record the deviation. If the intended outcome becomes ambiguous or a missing primitive blocks a clean change, stop and say exactly that.
2. **Work in the given branch or worktree.** Never touch the operator's main checkout when a worktree was assigned. Make the project runnable with its own package manager in locked mode; never update a lockfile.
3. **Reproduce bugs first**, as a failing test where practical; that test becomes the regression proof.
4. **Implement** the smallest change that solves the actual problem. If the path keeps getting more complicated, stop and reconsider. On the path you touch, delete superseded machinery and fix drift between sites that must agree; never widen into unrelated cleanup. No speculative config, flags or abstractions.
5. **Test** behavior, not implementation: focused tests that prove each acceptance item; no coverage theater.
6. **Simplify early.** Once the first version works, dispatch the `sysop:simplifier` agent on the diff; apply what holds up with evidence. Record the result in the report's Simplifier section, so review does not repeat it. Skip only for tiny work, and work that has a plan file is never tiny. "The code already looks clean" is not a reason to skip: that judgement is the simplifier's.
7. **Gate.** Run the full project gate through the `sysop:check` skill (narrow runs are for iterating only). On red, fix `introduced` causes; claim `inherited` or `environment` only with the evidence `check` requires.
8. **Commit** each coherent outcome through the `sysop:commit` skill. Never commit store files or scratch files.
9. **Report.** Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py report build <slug>` (slug = branch with `/` → `-`): it creates `<store>/reports/<slug>.build.md` from `templates/build-report.md`, or returns the existing one. Fill every section in that file; replace each `<…>` placeholder and keep the headings. Before declaring green, re-read `git status`, `git diff` and the log since the start, and confirm every acceptance item maps to an observation.

## Correction pass (`review <report>`)
Read the complete review report, the plan and the build report. Judge every finding; reviewers can be wrong:
- fix what matters by its severity, including adjacent defects on the changed path;
- `DECLINED` only with a reason grounded in the contract or project guidance (taste, overengineering, wrong finding). When the reason is a fact about this repo that reviewers keep missing ("tabs are deliberate here"), say so in one line under Unrelated discoveries as `repo fact: <the fact>`, so `sysop:rules` can offer it to AGENTS.md;
- `NOT A FINDING` only with decisive evidence;
- a real but unrelated defect goes to Unrelated discoveries.
Never leave a finding without a disposition. Batch all fixes into one pass, re-run the gate, commit, and update the same build report's Review dispositions.

## Not this skill's job
Do not review your own work beyond validation, open PRs, push, merge or land.

**Called by another skill** (ship, fleet)? Return the result and hand control back: the caller continues with its next step. Do not end the turn here.
