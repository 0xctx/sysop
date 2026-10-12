---
name: rules
description: >-
  Creates or checks the repository's rules file (AGENTS.md, or CLAUDE.md where that is the convention): a lean map, ground rules traceable to code, the real commands, and working principles; or reports drift after a change as minimal edits. Use when asked to "write AGENTS.md", "set up rules for this repo", "are the rules still true", before a merge, or via /sysop:rules.
argument-hint: "[create | drift [range]]"
---

# Rules

Keep the always-loaded steering file true and lean. Its only failure that matters is being wrong; a longer file is worse than a lean one.

**Mode:** $ARGUMENTS (`create` by default when no rules file exists; otherwise `drift`).

## Pick the file
`AGENTS.md` by default, including when neither file exists (Claude Code reads AGENTS.md, and so do other agents). `CLAUDE.md` only when it already exists and AGENTS.md does not. If both exist and CLAUDE.md is just `@AGENTS.md`, edit AGENTS.md. Back up an existing file before a full rewrite (`<file>.bak`), and read it first as input.

## create
1. **Derive what is**, from the code: read the README, package manifests, task runners, CI, the directory layout and a few representative files. Every ground rule must point at the file that proves it; drop any rule you cannot trace.
2. **Elicit working principles** from the operator in one question with a proposal (plan first? clarify before coding? the engineering stances they hold). These cannot be derived from code. Keep only the ones that change behavior.
3. **Write four parts**: what this is (one paragraph plus the stack), a map (the directories and files that matter, one line each), ground rules (specific choices, not slogans: "derive types with z.infer", not "type safety matters"), commands (the real test, lint, type-check and run commands with flags), and principles.
4. **Push detail out**: recurring, task-specific patterns go to an on-demand reference the file points to; task-specific "what to build next" belongs in a plan; delete generic advice.
5. **Prune**: for every line, ask "would removing this cause a mistake?" If not, cut it.

## drift [range]
Default range: uncommitted changes, else `main...HEAD`. Read the diff and the rules file, and flag only:
1. a stated rule or fact that is now false, with the one-line fix;
2. a map entry that drifted (fix the wrong path; do not add every new file);
3. a new durable invariant the change establishes, as one line.
4. a repo fact reviewers keep missing: a `repo fact:` line found in two or more build reports in the store (`<store>/reports/*.build.md`) that the rules file does not state yet, as one ground-rule line (every reviewer and worker reads this file, so it stops the same wrong finding from returning).
Never suggest recording that a feature was added, restating what code makes obvious, or adding rationale. If nothing drifted, say exactly: "The rules file is still accurate for these changes; no edits needed." Report first; apply the edits when the caller asked for them.
