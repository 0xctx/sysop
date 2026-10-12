---
name: prd
description: >-
  Writes a problem-first product requirements doc: evidence, thesis, a falsifiable hypothesis with right and wrong conditions, MVP, outcome metrics, a "never build" list, non-goals and open questions. Product intent only, never engineering. Use when asked to "write a PRD", "spec this product", "what should we build and why", or via /sysop:prd.
argument-hint: "[idea or path to notes]"
---

# PRD

Capture intent a team can challenge before building and judge after shipping. A PRD is the *what* and *why*; engineering choices (libraries, data models, security boundaries, test architecture, error handling, layout) belong to the plan, never here.

**Input:** $ARGUMENTS (when empty, use the conversation).

## Workflow
1. **Read first.** Read whatever was passed and the repository (README, AGENTS.md, existing docs) so questions cover only real gaps.
2. **Interview in clusters**, each question carrying a proposed answer, stopping after each cluster for the operator:
   - foundation: who has the problem, the observable pain, how they cope today, why now, why they would switch;
   - users and the job to be done; non-users;
   - the hypothesis: co-write it, insisting on the **wrong** condition;
   - MVP and doors: the thinnest end-to-end slice; which choices are one-way doors (recommend a spike for those).
   - slices: the MVP and what follows, cut into thin end-to-end pieces, each with one user-visible check (what a person does and sees).
   If the operator declines the interview ("just write it"), name what you are guessing and write it as `TBD: needs validation`, never as a requirement.
3. **Propose the never-build list.** Draft seven entries yourself from what you read: plausible, well-argued requests that would still be drift. The operator strikes the wrong ones. "Never" means never; anything merely deferred goes to Non-goals.
4. **Write** `docs/<slug>.prd.md` (create `docs/` if needed; never a bare `PRD.md`). Before writing, read `templates/prd.md` and follow its shape. Mark unknowns `TBD: needs validation`; never invent personas, metrics or evidence.
5. **Check** the five tests before reporting: problem grounded in evidence; separate right and wrong conditions; outcome-shaped metrics; explicit non-goals and never-build entries; open questions named. Also check that every slice names a user-visible check. Report the path, the thesis and hypothesis in three lines, and the count of open questions. Next step: `sysop:slice <path>` turns the slices into tickets and builds them (`sysop:plan` for a single change).

## Resources
- `templates/prd.md`: the PRD's shape.
