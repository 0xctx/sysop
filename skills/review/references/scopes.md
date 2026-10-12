# Review scopes, severity and states

## Choosing scopes by risk (record the call in the report's Signal)
| Scope | Agent | Add when |
|---|---|---|
| code | `sysop:code-reviewer` | always |
| seams | `sysop:seam-reviewer` | the change touches types, contracts, payloads, wire or persisted formats, serialization, resume paths, concurrency, isolation or security; also whenever the risk is unclear |
| errors | `sysop:failure-hunter` | error handling, fallbacks, retries, exit codes or cleanup paths change |
| simplify | `sysop:simplifier` | only when the build report does not say `Simplifier: ran` |
| prose | `sysop:prose-reviewer` | the work is writing: the repo profile is prose, the ticket kind is `writing`, or most of the diff is prose that is the product (chapters, articles, documentation) |
| cross-family | external reviewer through `xreview` (the `review` tier); two, from two other vendors, with `--risky` (`xreview_risky`) | `cross_review` is on and a reviewer from another vendor is available |

A tiny change gets `code` alone (plus cross-family when enabled).

**Cross-family unavailable** (`xreview start` reports `unavailable`: every other-family entry is missing, excluded or cooling down): quote its line in the Signal and decide the verdict from the reviewers that ran. It is not `INCOMPLETE`; an outage of one vendor must not freeze landing. `INCOMPLETE` is for a selected Claude reviewer or the gate failing to run. Incidental prose (comments, a README touch-up in a code repo) skips review; the gate is its check. Prose that is the product gets the `prose` scope, and `code` only for any code in the diff. A documented snippet that runs is code.

## Agent models (single owner of this rule; other skills point here)
Settings `agents` holds the model for each sysop agent: its own entry (`sysop:code-reviewer`), else `default`. `inherit` means omit the Agent tool's `model` parameter (the session's model); otherwise pass the model (`haiku`, `sonnet`, `opus`). The guard refuses a sysop agent dispatched on any other model. If that model is unavailable, the reviewer did not run: report `INCOMPLETE`, saying so, and the operator changes it with `sysop.py models agent <name> <model>`.

## Shared instruction for every reviewer (pass verbatim, filled in)
> Review branch `<branch>` at exact head `<sha>` against base `<base>`. Work only in `<review worktree>`; never run a command that moves any other tree. The contract: `<plan path or tiny contract>`. Project guidance: `<AGENTS.md/CLAUDE.md paths>`. Suggest Critical, Important or Suggestion from the actual consequence. When one finding proves that a member of a finite class violates an invariant, enumerate the class with a deterministic search and report it once with every affected member and every member checked clean. Do not modify files, commit, post, or spawn agents.

## Cross-family reviewer
It routes through the `review` tier (`sysop.py models` shows it; the operator changes it with `sysop.py models set review …`). `sysop.py xreview start|collect` owns it end to end: routing to another family, the prompt (the instruction, the context files and the diff; a file list when the diff is too large), the launch, and the result file. The coordinator never writes the cross-family result itself.

## Severity (the coordinator decides; reviewer labels are advice)
- **Critical** (blocking): plausible security compromise, data loss or corruption, widespread outage, unrecoverable contract break.
- **Important** (blocking): wrong behavior on a reachable path; an isolation or security hole; a contract with no type at its seam; a false comment or doc; dead or duplicated machinery the change adds; a test that proves nothing; a change-caused gate failure.
- **Suggestion** (fixed in the same loop, never blocking): a simplification, a clearer name, a missing type for an invariant, stale docs next to the change.
A real defect unrelated to the change is a follow-up, not a finding against this change. Taste with no basis in the project's guidance is not a finding.

## Finding states (every finding ends in exactly one)
`OPEN` · `FIXED` (the new head proves it) · `NOT A FINDING` (decisive evidence) · `FOLLOW-UP` (real, unrelated, recorded) · `DECLINED` (reason recorded).
IDs are stable: `R1`, `R2`, … on the first review; on re-review keep IDs for the same causal finding, add new ones after the max, and never drop an earlier finding.

## Verdict
- `READY`: no OPEN Critical or Important finding, and the gate is green (check report or a run in the review worktree).
- `NEEDS FIXES`: any OPEN Critical or Important finding, or a change-caused red gate.
- `INCOMPLETE`: a required reviewer or check could not run. Say which; never pass silently.

## Re-review after a correction pass
Verify only the prior findings, their dispositions and the correction diff (`<previous head>..<new head>`), plus defects the correction itself caused. Run a full review again only if the correction changed the outcome, architecture or scope. The light form ends like a full one: the updated report, then `review seal <branch> --sha <new head> --re-review`, which refuses if the branch was rewritten rather than corrected on top.
