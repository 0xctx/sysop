---
name: debug
description: >-
  Diagnoses a reported bug or unexplained behavior: reproduces it on the user's path, separates trigger from masking condition from symptom, tests competing causes, and writes a debug report with the fix boundary and regression test. Diagnosis only. Use when asked to "debug this", "why does X happen", "find the root cause", or via /sysop:debug.
argument-hint: "<symptom, error, or issue URL>"
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py *)
---

# Debug

Find what actually causes the reported behavior, with evidence a skeptic would accept. A diagnosis is evidence, not authorization: change no code unless the request explicitly asks for the fix, and then hand over to `sysop:ship` or `sysop:build` with the reproduction as the regression test.

Store: !`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py store --soft`
Agent models: !`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py settings --key agents` (dispatch agents with these, per `${CLAUDE_PLUGIN_ROOT}/skills/review/references/scopes.md` → Agent models)

**Report:** $ARGUMENTS

## Workflow
1. **Observed behavior first.** Start from what the user experiences, not an internal error string or a guessed mechanism. Write down expected vs actual, setup, inputs and how often it happens.
2. **Reproduce** end to end on the real user path when feasible and safe; otherwise use the closest representative path and state the gap. Turn it into the smallest command or failing test you can.
3. **Separate three facts:** the *trigger* (the input or transition that starts the fault), the *masking condition* (state, timing, cache, config or path that hides or exposes it), and the *symptom* (what is seen). Do not collapse them.
4. **Compare paths.** Find a proven path where the behavior works and locate the earliest meaningful divergence (inputs, state, dependencies, timing, control flow). Read history (`git log -S`, blame) only when timing or intent matters; the most recent nearby change is not causal without evidence.
5. **Compete hypotheses.** For each candidate, name the observation that would distinguish it, and test the cheapest discriminating one first. Run the smallest counterfactual that should flip the outcome if the leading cause is true, changing one condition at a time. Name what would falsify the leading explanation, run that check, and keep contradictory results.
6. **Go deeper when needed.** For a non-trivial chain, dispatch the `sysop:root-cause` agent with the symptom and reproduction (and no preferred fix), then verify its causal chain against your reproduction and the proven path.
7. **Fix boundary.** Name the violated invariant and the smallest owner of the fix: where the requirement should have lived, not a guard at the failure site. Define the regression check that fails before the fix and passes after.
8. **Report.** Write `<store>/reports/<slug>.debug.md` (slug = a short kebab name of the symptom) with: status (PROVEN / CONDITIONAL on one named condition / UNRESOLVED), the reproduction command and output, trigger/mask/symptom, causal chain with `file:line`, alternatives ruled out, fix boundary, regression check, remaining uncertainty. Answer with the status and root cause in two sentences plus the report path.

## Rules
- Scratch experiments live under the store or `/tmp` and are removed; never leave edits in the repository.
- If a load-bearing element is missing (no repro, untested masking condition), say so and recommend the focused follow-up; confidence is not proof.
