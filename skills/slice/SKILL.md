---
name: slice
description: Cuts a PRD or plan into thin, end-to-end slices that become fleet tickets, walking skeleton first, each checked against the never-build list. Use when asked to "slice this PRD", "turn the plan into tickets", "build this app from the PRD", or via /sysop:slice.
argument-hint: "<path to the PRD or plan> [--yes] [--land]"
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py *)
---

# Slice

Turn product intent into the tickets that build it: thin, end-to-end, independently shippable slices, in an order where the product runs from the first landing. The script owns the record (`ticket import`); this skill owns the cut.

Store: !`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py store --soft`
Check command: !`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py config get check`

**Input:** $ARGUMENTS

## Workflow
1. **Read** the document passed (a PRD from `sysop:prd`, or a plan), the repository's README and AGENTS.md, and what already exists in the code. Its `## Slices` section, when present, is the starting point; its `## Never build` list is a hard boundary.
2. **Cut slices.** Each slice is one user-visible capability, built end to end (interface, logic, storage, tests), small enough for one worker:
   - `id` (short kebab or `s1`, `s2`, …), `title`, `size` (small/large/risky), `kind` (feature, docs, …);
   - `depends`: only real ones (this slice needs that one's output); never "because both touch the same file";
   - `request`: the document's own words for this slice, **verbatim** (the reviewers judge against them);
   - `contract`: the six elements (`${CLAUDE_PLUGIN_ROOT}/skills/plan/references/contract.md`);
   - `acceptance`: checkable statements; `check`: **one user-visible check**, what a person does and sees ("open /, add 'milk', see it listed").
   The first slice is the MVP's thinnest path. Prefer more, thinner slices over a few thick ones.
3. **Hold the line.** Compare every slice with the Never-build list. A slice that would build a never-build item is dropped, and you name it and the entry it hits; a slice that only brushes one says so in its contract ("must not become …").
4. **Write** the slices as JSON to `<store>/slices.json`: `{"slices": [ … ]}`. When the check command above is empty, do not write a skeleton slice yourself: the import adds ticket `t0-skeleton` (start, health check, tests, CI, AGENTS.md) and hangs every root slice off it.
5. **Import and show:** `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py ticket import <store>/slices.json --base <base> --authority "<--land given | standing land: true | none>" (`--land given` only when the literal `--land` is in the Request above; `standing land: true` only when the settings say `"land": true`; otherwise `none`)`. It refuses a cycle or an unknown dependency (fix the JSON and rerun; a rerun updates, never duplicates). Show its tree, the dropped slices with the entry each hit, and the count.
6. **Go.** Unless the request says `--yes` (or `--go`), stop and wait for the operator's go. Then use `sysop:fleet` with `--resume --yes` (plus `--land` when given): the tickets are already registered, so fleet goes straight to its loop.

**Called by another skill** (go)? Carry on into fleet in the same turn after the go; do not end the turn between them.
