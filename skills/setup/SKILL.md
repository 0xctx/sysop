---
name: setup
description: First-run setup for sysop in a repo. Reads the repo, then asks a few questions with proposed answers (check command, landing authority, model preset (from what you have), cross-family review, and for an app its start command and journeys), writes the settings, and offers a narrated first lap on one tiny change. Use via /sysop:setup [--yes].
argument-hint: "[--yes to accept every proposed answer] [--first-lap \"<a tiny change>\"] [--land]"
disable-model-invocation: true
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py *)
---

# Setup

Fit sysop to this repository in one short exchange. Every question the repository already answers is a question not to ask.

Current settings: !`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py settings`
Workers on this machine: !`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py roster`

**Arguments:** $ARGUMENTS

## Workflow
0. **Doctor first:** run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py doctor`. Fix nothing yourself; carry each FAIL's one fix into the final message (a missing check command is what this setup answers).
1. **Read the repository first**: README, AGENTS.md or CLAUDE.md, package manifests, task runners, CI workflows. Find the authoritative gate (the aggregate test, lint and type-check command), the default branch, and whether `origin` exists and is pushable.
2. **Ask at most five questions in one message**, each with your proposed answer filled in so the cheapest reply is "yes":
   1. **Check command:** the gate you found (run it once now and report whether it is green). For a prose repo (the profile says prose: a book, articles, docs as the product) without one, propose `sysop:prose-check` (links, fences, markers, word count; add `--words <target>` for a book).
   2. **Landing authority:** propose "ask me each time" (`land: false`), unless the operator already stated a standing grant.
   3. **Model preset, from what they have:** ask which of these they have: a Claude plan or API key; agy; a free Gemini key; OpenRouter (with credit, or free only); pi-free; Kaggle. Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py preset list` and recommend one or two presets that fit, each with its capacity, its catch, and the session model it suggests (`preset show <name>`). Propose claude-plan (the default) when they have nothing else. Never recommend cline-free or free-max's Cline entries unless they bring up pi-free themselves.
   4. **Cross-family review:** on (propose on when agy or pi is installed), off otherwise.
   5. **Runtime verification** (only when the repo is an app that serves something): the start command that serves it on `$PORT`, and journeys you draft from the README or PRD: two to four plain-English things a person does and sees, each naming the value. The operator strikes or edits them. With `--yes`, skip this question.
   With `--yes` in the arguments, take every proposed answer without asking.
   Then **the model pick**: run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py models advise --refresh` and, for each tier with `newer:`, `better:` or `switch:` lines, propose the change in one line with its evidence (or "up to date"). The first line of the advice is the repo profile (code, prose or mixed); it explains why tiers rank by code or by writing, so name it. With `--yes`, take only the `newer:` upgrades. Mention `config set follow_latest true` for operators who want same-line upgrades applied automatically. Offer `config set notify notify-send` (desktop) or `notify ntfy:<topic>` (phone, via ntfy.sh) so long runs ping when they finish or need a decision.
3. **Write** with the script, never by hand (the accepted model commands from `models advise` included):
   - `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py config set check "<command>"` (repo scope);
   - `config set land <true|false>` and `config set cross_review <true|false>` (repo scope);
   - the preset: show `preset diff <name>`, then `preset use <name>` (user scope; it saves the old settings as preset `previous`, so `preset use previous` undoes it). For claude-plan with no earlier settings, write nothing: it is the default;
   - `config set base <branch>` only when the default branch is not what the main checkout is on.
   - with verification accepted: write the journeys to `JOURNEYS.md` and commit them; `config set start "<command>"`; then calibrate. Ask the operator for one deliberate fault ("what would a believable regression be?") or propose one. Build it with `sysop:build` on a scratch branch `sysop/verify-fault` (never on the base), and run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py verify calibrate --fault-branch sysop/verify-fault`. It records the calibration only when the base passes and the fault fails. Report which, and delete the scratch branch with `git branch -d` afterwards (or say it is kept).
4. **Show the result**: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py models` and the repo's `.claude/sysop.json`. If the roster shows pi installed with no models, say the one-time fix: export a free `GEMINI_API_KEY` (aistudio.google.com) or an `OPENROUTER_API_KEY`; then `sysop.py roster --refresh` and `sysop.py models check`. Remind the operator that `.claude/sysop.json` is meant to be committed (the team shares the check), and that `/sysop:go <anything>` is the front door.
5. **The first lap.** Offer to carry one tiny real change end to end, so they see how sysop works on their own code: propose one you noticed while reading (a typo, a missing test, a stale README line), or take the one in the arguments (`--first-lap "<change>"`; with `--yes`, accept the proposal). Run it with `sysop:ship`, passing `--land` only when they said so. Narration is on for a new repo (`sysop.py session` says `"explain": true`), so ship says each step in one plain line as it happens: the isolated copy, the check, the second opinion from another vendor's model, and why merging waits for their word.
6. **Free models, only when asked** (or when no paid worker is installed): follow `references/free-models.md`, a free API key through pi first, then optional Kaggle serving.

## The final message
Plain words (`${CLAUDE_PLUGIN_ROOT}/skills/go/references/words.md`): what is set up and what it means for them, not the setting names. Say "a second opinion from another AI vendor's model", not "cross-family review"; say "merging waits for your word", not `land: false`. Keep a path or a command only when they will act on it, and show it exactly. End with one line `Next: <the one thing to do now>`.

## Resources
- `${CLAUDE_PLUGIN_ROOT}/presets/`: the shipped presets, one JSON file each (`sysop.py preset list`, `show`, `diff`, `use`, `save`).
- `references/free-models.md`: free keys through pi, and serving your own model on Kaggle (`serve up`).
