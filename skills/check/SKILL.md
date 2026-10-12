---
name: check
description: Runs the project's authoritative gate (tests, types, lint) and reports GREEN or RED with an evidenced cause (introduced, inherited, environment, incomplete), saving a check report. Use when asked to "run the checks", "is it green", "validate this", before landing, or via /sysop:check.
argument-hint: "[branch or path] [--base <branch>]"
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py *)
---

# Check

Answer one question with evidence: is this checkout green by the project's own gate? Fix nothing.

Store: !`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py store --soft`
Configured check: !`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py settings --key check`

## Workflow
1. **Find the gate.** Use the configured check above when it is non-empty. Otherwise discover it from the repository: package scripts, Makefile/justfile, pyproject/tox, CI workflow files, contributor docs. Prefer the project's aggregate command. Never invent a generic command the project does not define; a class with no command (say, no type checker) is reported as "none defined", not skipped silently.
2. **Run it** in the checkout under review (the worktree, never the operator's checkout by default). Record each command, its exit code and the decisive output lines.
3. **On red, classify the cause** of each failing check:
   - `introduced`: this change caused it. The default when evidence is missing.
   - `inherited`: the same check already fails at the base. Prove it by running only the narrowest failing piece at the base revision in a temporary worktree (`git worktree add --detach <store>/wt/check-base-<slug> <base>`), then remove it with `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py cleanup <store>/wt/check-base-<slug>`. Never switch the checkout under review.
   - `environment`: the machine, not code (a port held by another process, a missing credential, a network fault). Name the resource.
   - `incomplete`: nothing that ran failed, but not every check ran (timeout, killed process).
   `inherited` and `environment` let work continue, so they demand evidence; without it the cause is `introduced`. Never relabel red to get past a gate.
4. **Write the report** to `<store>/reports/<slug>.check.md` (slug = branch name with `/` → `-`): verdict, commands with exit codes, failing output tails, cause and evidence. Skip writing when the store line above says there is no repository.
5. **Answer** in one line first: `GREEN`, or `RED (<cause>): <failing check>`, then the report path.

## Gotchas
- A passing narrow test run is not the gate. Iterate with narrow tests; report only the full gate.
- Install missing dependencies with the project's own package manager in locked mode; never update a lockfile.
- If the discovered gate is long-running, say so and still run it; do not substitute a faster partial check.
- Suggest recording a discovered gate in `.claude/sysop.json` as `"check"` so later runs and the stop gate use it; do not write that file unasked.

**Called by another skill** (ship, fleet)? Return the result and hand control back: the caller continues with its next step. Do not end the turn here.
