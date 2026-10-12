# Classifying a finished worker

Read the attempt's log (`<store>/runs/<id>/<attempt>/log`), the exit code from `wait`, and the worktree (`git log <base>..HEAD`, `git status`). Judge from that evidence, not from a keyword match.

## Availability failure → fall back
The worker never got to do the work. Typical evidence: the CLI is missing or crashed at startup; sign-in or credential errors; rate limit or quota (429, "quota exceeded", "rate limit"); "unknown model" or "model not available"; `died` within seconds with nothing in the worktree.
- One command does the whole mechanic: `sysop.py fallback <id> --from <worker:model> --reason "<the error, short>" --size <size> --kind <kind> --prompt-file <brief> [--cooldown-seconds <until reset>] [--whole-worker]`. It records the cooldown (use `--whole-worker` for an account-wide quota; on pi it rests only that provider, so the same model through another provider still serves), logs the switch on the ticket, and routes past every worker this ticket already tried. Launch its pick with `--worker <its entry> --prompt-file <brief>`. A pi provider without a login ("No API key for provider: X") is read from the log and cools every model of that provider. Exit 3 means nothing is left: the ticket is blocked, and the reason is already logged.
- Never discard what a failed attempt left (stash, reset and checkout all discard). Tell the next worker, in its brief, what is already in the worktree and that it continues from there.
- Record on the ticket: `fallback <from> → <to>: <one-line reason>, evidence runs/<id>/<n>/log`. Never fall back silently.

## Work failure → not a fallback
The worker did work but the result is not acceptable: red gate, no commits, acceptance not met, a wrong change, or `died` after working (a timeout). Another model is not the fix.
- Send one correction brief to the **same** worker: the original brief plus the evidence (failing command and output, or the review report path), as a new attempt.
- `next` counts attempts: past the `max_attempts` setting it blocks the ticket with the evidence, and the operator decides.

## Running too long
`wait` returning 124 is not a failure; re-arm it. A worker is stuck only when its log has not changed for a long stretch and the process is alive; report it and ask before stopping anything (never kill by name; ownership stays explicit).
