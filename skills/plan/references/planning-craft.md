# Planning craft

The highest-leverage planning decision is usually what not to build. Separate the required outcome from any mechanism the request proposes.

## Invariant, acceptance, success
- **Invariant**: what every acceptable implementation keeps true. Name the behavior, not the storage or abstraction.
- **Acceptance**: what proves this implementation is complete.
- Keep both distinct from any proposed mechanism ("build a per-run allowlist" is a mechanism; "the agent is not told about undeclared skills" is the invariant).

## Foundations before logic
Get the data shape and its owner right first; a late shape change is a rewrite. Ask: if this requirement had existed from day one, what would the design be? Use that as a reference shape, then plan the smallest safe step toward it.

Search from cheapest to most structural, and stop at the first that satisfies the invariant cleanly:
1. existing configuration or supported behavior;
2. composition of existing commands, functions or components;
3. a small extension to a primitive the project owns;
4. a new abstraction with a clear owner;
5. a new subsystem with its own lifecycle.

Signs of a missing primitive: the same policy maintained at several entry points; state with no obvious owner; a new signal threaded through layers that do not own it; lifecycle or sync code dominating the outcome. When one is load-bearing, recommend building it first and say what it unlocks.

## The laziness test
For every proposed abstraction, store, background process, config key, fallback or scaffold, answer: which invariant requires it; what evidence rules out the smaller option; what new failure modes it brings; what disappears if it is removed. Prefer deletion, direct control flow, one source of truth per decision. DRY shared structure, not every repeated line. Before sharing state between actors, ask what happens if another actor changes it concurrently; if not "nothing", isolate ownership.

## Spike at the hinge
Spike only when the claim is uncertain, falsifiable, and changes the architecture or scope. The planner chooses the question; an agent runs the cheapest experiment at the real boundary (in a scratch directory, thrown away) and reports a verdict with evidence. Never spike settled details or grow a spike into a prototype.

## Decide, don't defer
Name each design decision the work contains, choose, and record why, with the strongest rejected alternative and the reason it lost. A plan that defers its central decision is not a plan. The one thing never decided by the planner is missing product intent: stop and ask.

## Steps that survive
Order steps by dependency; anchor each to stable text (a named function, a described test), never raw line numbers. Each step has its own focused verification. Validation uses commands that exist in this repository. Every requested outcome is covered; nothing required is left as optional follow-up.

## Delivery concerns (only those that apply)
Compatibility with existing behavior or stored data, migration, rollback, observability, documentation. Put the concrete work into steps; omit what does not apply.
