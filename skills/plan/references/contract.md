# The work contract (single owner; other skills point here)

A run cannot recover from a premise that was never stated. Before planning or building, the work must communicate six things, semantically rather than by heading:

1. **Problem**: what is wrong or missing today, observably.
2. **Why**: why it is worth solving: who pays, and how.
3. **Why now**: what changed, or what this unblocks.
4. **Outcome**: what should become observably true when done.
5. **Invariants**: what must stay true for every acceptable implementation (behavior, data, compatibility, security).
6. **Acceptance**: how completed behavior is recognized: checks a third party could run.

Solution steering (reuse this primitive, no new dependency) is optional and comes last. Its absence is a decision: the planner chooses.

## Judging a contract
- **Infer, don't interrogate.** Fill the elements from the request, the conversation, the issue and the code. Ask only for what cannot be inferred and would change the result (product intent, an unstated constraint); ask once, all at once, each question with a proposed answer.
- **Never invent product intent** (personas, business value, metrics). Missing intent is a stop, not a guess.
- **Prose is a claim; code is the fact.** Verify load-bearing claims in the request against the source. When they disagree, the code decides and the plan records the conflict.
- **Already true?** If the outcome already holds on the current base, say so and stop.
- **Tiny work** (a few lines, docs-only or test-only, touching no wire format, schema, persisted state, data-loss path or security surface) needs only problem, outcome and acceptance. When unsure, it is not tiny.

Record the contract at the top of the plan (or, for tiny work, in the build report).
