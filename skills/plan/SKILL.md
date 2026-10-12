---
name: plan
description: Turns a request, issue or PRD phase into a decided, evidence-backed implementation plan (contract, invariants, approach, ordered steps, validation) saved to the sysop store. Plans only. Use when asked to "plan this", "how should we build", "make an implementation plan", when ship needs a plan, or via /sysop:plan.
argument-hint: "<request | issue URL | path/to/prd.md>"
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py *)
---

# Plan

Produce a plan an implementer can execute without rediscovering the design: the invariant identified, existing primitives found, the smallest evidence-backed solution chosen. Do not implement, commit or open PRs.

Store: !`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py store --soft`
Agent models: !`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py settings --key agents` (dispatch agents with these, per `${CLAUDE_PLUGIN_ROOT}/skills/review/references/scopes.md` → Agent models)

**Input:** $ARGUMENTS (when empty, use the conversation).

## Workflow
1. **Contract.** Read `references/contract.md` and establish the six elements from the input. For an issue, read its comments and linked items too, stopping once they no longer change the plan. Stop and ask only for missing product intent.
2. **Evidence.** Read repository guidance (AGENTS.md, CLAUDE.md) and the files the change will touch, with their callers and tests. For non-trivial work, dispatch the `sysop:explorer` agent for an evidence map; for broken behavior, dispatch `sysop:root-cause` with the symptom and no preferred fix, and plan from its verified causal chain, not the report's assumed cause. Inspect the decisive files yourself.
3. **Design.** Read `references/planning-craft.md`. Find the invariant, the closest existing primitive, and the smallest coherent shape; apply the laziness test; spike (through an agent, in a scratch dir) only at a falsifiable hinge.
4. **Simplify early.** For anything beyond small, dispatch the `sysop:simplifier` agent on the draft approach and fold in what holds up.
5. **Gate.** Stop and ask the operator only when a missing primitive should come first, the evidence contradicts the request, or a choice would change the product. Everything else: decide, record why.
6. **Write.** Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py report plan <slug>` (slug = branch name with `/` → `-`, or a short kebab name when there is no branch yet). It creates `<store>/plans/<slug>.md` from `templates/plan.md`; fill every section in place. Plain, concrete language; the repository's own terms; no placeholders left.
7. **Verify, then report** the approach in two or three sentences, the plan path, and any decision needed. Check that every named file and function exists (or is marked new), every validation command exists in this repo, and every requested outcome is covered.

## Resources
- `references/contract.md`: the six-element contract and when work is tiny (single owner; ship and fleet point here).
- `references/planning-craft.md`: invariants, primitives, laziness test, spikes, decide-don't-defer.
- `templates/plan.md`: the plan's required shape.

**Called by another skill** (ship, fleet)? Return the result and hand control back: the caller continues with its next step. Do not end the turn here.
