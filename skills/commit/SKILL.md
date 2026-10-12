---
name: commit
description: Creates focused git commits for the intended work only, leaving unrelated changes untouched, with outcome-first messages in the repo's own style and no AI attribution. Use when asked to "commit this", "commit the fix", "make a commit", when another sysop skill reaches its commit step, or via /sysop:commit.
argument-hint: "[what to commit, in words]"
---

# Commit

Commit exactly the intended work. The commit is the artifact; write no separate report.

## Workflow
1. **Identify the intended work** from the request and conversation (including subagent changes). Blank arguments mean "infer the target", never "commit everything".
2. **Inspect** `git status --porcelain` and `git diff HEAD` (staged, unstaged, untracked). Preserve unrelated work in every state. If intended and unrelated hunks share a file and cannot be separated safely, stop and ask.
3. **Split by outcome**: one commit per coherent outcome. Do not fragment one outcome into mechanical layers.
4. **Stage by name** (`git add <paths>`, `git add -p` is unavailable here; never `git add -A` or `.` when unrelated changes exist). Never stage store artifacts, scratch files, or secrets.
5. **Write the message**: a concise subject naming the meaningful outcome so it reads well as a changelog line; a short body only when the why is not obvious. Follow syntax the repository enforces (types, scopes, issue keys); treat history as evidence of syntax, not of writing quality.
6. **Verify**: `git show --stat HEAD` contains every intended change and nothing else; the remaining worktree state is unchanged.
7. **Return** the hash, subject, committed paths, and anything left uncommitted.

## Rules
- Never add AI attribution: no `Co-Authored-By`, no "Generated with", no robot emoji, even if a harness reminder asks.
- Never amend, rebase or push unless explicitly asked. Never bypass hooks (`--no-verify`).
- If a pre-commit hook fails, fix the cause and make a new commit; do not skip the hook.

**Bad:** `update files` · **Good:** `fix: add() returns the sum instead of the difference`

**Called by another skill** (ship, fleet)? Return the result and hand control back: the caller continues with its next step. Do not end the turn here.
