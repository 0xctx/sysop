---
name: tune
description: Switches model presets on plain requests ("go free tonight", "use only Haiku", "I added OpenRouter credit"), and suggests routing and model changes from sysop's own outcomes (the ledger, fallbacks, cooldowns, review results) and from model intel (newer releases and better runnable models per tier and task kind, explicit or auto), and applies a change only on the operator's word. Use via /sysop:tune.
argument-hint: "[apply <suggestion number>] [a plain request: go free tonight, use only Haiku, …]"
disable-model-invocation: true
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py *)
---

# Tune

Turn the record of what actually happened into at most a few concrete routing suggestions. Evidence first; no data, no advice.

Ledger: !`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py ledger summary --json`
Current routing: !`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py models`
Cooldowns: !`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py cooldown list`
Model intel: !`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py models advise`
Learned scores: !`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py models learn`

**Arguments:** $ARGUMENTS

Presets: !`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py preset list`

## Workflow
0. **A plain request about which models to use** ("go free tonight", "use only Haiku", "I added OpenRouter credit", "switch back"): map it to the preset that fits (`preset show <name>` for its catch and the session model it suggests), or to a copy of one with an edit (`preset save <name>`, then edit the copy's JSON in `~/.claude/sysop-presets/`). Show `preset diff <name>`, and apply with `preset use <name>` on the operator's word. "Switch back" is `preset use previous`. Then stop; the steps below are for "how is it going" requests.
1. **Gate on data.** Use only groups with `enough_data: true` (five or more rows). For every group below that, say in one line what it would take ("2 more tickets on agy:gpt-oss-120b-medium") and give no advice about it.
2. **Read the signals** per worker:model, and per kind when the rows carry it: landed rate, check pass rate, fallbacks (how often this entry was skipped or failed at runtime), mean seconds, and, from `<store>/reports/*.review.md` and `*.xreview.md`, how many review findings were later declined as wrong (reviewer noise). Recurring cooldown reasons (a quota, capacity 503s) count as availability evidence.
3. **Learned scores.** `models learn` above proposes one-step catalog score changes from this repo's outcomes (5+ tickets per model and skill), each with its evidence. Present them; apply with `models learn --apply` on the operator's word, or note that `config set learn true` applies them automatically from now on.
4. **Add model intel.** The advice above lists newer releases of the same line (`newer:`), runnable models that score higher for the tier's skill (`better:`), and when an `auto:<skill>` route would beat an explicit chain (`switch:`), each with its command. Intel advice needs no ledger rows: it rests on public benchmarks and release dates, so say so and name the source line. Where the ledger contradicts a benchmark (a "better" model that fell back or failed review here), the ledger wins: the advice already marks such entries `(weak here)` and never proposes them, and it ranks tiers by the repo's own kind (its first line).
5. **Suggest at most three changes**, each as the exact command plus the numbers behind it. Examples:
   - reorder a tier: `models set fast claude:haiku agy:gemini-3.8-flash-high`, because agy fell back in 4 of 6 tickets for quota;
   - route a kind: `models route tests fast` (or `models route writing auto:writing`), because test tickets on standard landed 6/6 with no review findings;
   - re-score a model: add `"catalog": {"<entry>": {"skills": {"writing": 4}}}` to the user sysop.json, because its writing tickets kept failing review;
   - change an agent model: `models agent sysop:code-reviewer haiku`, because sonnet reviews found nothing haiku missed in 8 tickets.
   Prefer changes that save cost without lowering the landed rate, and never weaken cross-family review or the gate.
   When the operator wants upgrades to happen by themselves, name the grant once: `config set follow_latest true` (same line, same CLI only; everything else stays a suggestion). When they want to hear about new models at session start, `config set intel true`. When advise says "estimates only", say so: the scores are not benchmarks.
6. **Apply only on the operator's word.** With `apply <n>` in the arguments, run that one command, then show `models` again. Otherwise stop after the suggestions.

Never edit ledger or settings files directly; the script owns both.
