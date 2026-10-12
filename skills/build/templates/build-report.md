# Build report: <slug>

**Branch:** <branch> · **Head:** <sha> · **Plan:** <path, or "tiny: no plan"> · **Built by:** <worker:model>

## Contract
<tiny work only: problem, outcome, acceptance; otherwise "see plan">

## What changed and why
<behavior-level summary; files touched>

## Deviations from the plan
<each deviation and the evidence that forced it, or "None">

## Simplifier
<ran: what it found and what was folded in | skipped: tiny work>

## Validation
| Command | Exit | Decisive output |
|---|---|---|

**Verdict:** done=<true|false> green=<true|false> red_cause=<""|introduced|inherited|environment|incomplete>
<for any red: the failing check and the evidence for its cause>

## Commits
- <sha> <subject>

## Review dispositions
<correction passes only; one line per finding: R<n>: FIXED at <sha> | DECLINED: <reason> | NOT A FINDING: <evidence> | FOLLOW-UP: <where recorded>>

## Unrelated discoveries
<proved defects outside this change, each with file:line evidence, not fixed here; or "None">
