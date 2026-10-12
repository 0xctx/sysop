---
name: pr
description: Pushes the current branch and opens (or reuses) one GitHub pull request with a problem/solution/validation body and no AI attribution; reports local-only when there is no origin or push access. Use when asked to "open a PR", "create a pull request", "push this for review", when ship reaches its PR step, or via /sysop:pr.
argument-hint: "[--base <branch>] [--draft]"
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py *)
---

# Pull request

Open one clear pull request for the committed work on the current branch. The PR is the artifact.

Store: !`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py store --soft`

## Workflow
1. **Commit first.** If intended changes are uncommitted, use the `sysop:commit` skill for exactly those; never sweep unrelated work in.
2. **Check scope.** Read the full diff and log against the base; stop if the branch holds unexplained scope.
3. **Write the body** in the store (never in the worktree, where it would be untracked work): `<store>/reports/<slug>.pr.md`. Use the repository's PR template when one exists (`.github/pull_request_template.md` and similar); otherwise:
   ```
   ## Problem
   <what was wrong or missing, for whom>
   ## Solution
   <the behavior change, not a file inventory>
   ## Validation
   - `<command>`: passed (from the check report)
   - Review: <verdict>, report at <path>
   Fixes #<n> (when there is an issue)
   ```
   Title: a concise outcome, in the repository's enforced syntax.
4. **Open it** from the branch's checkout, in one command:
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py pr open --title "<title>" --body-file <store>/reports/<slug>.pr.md [--base <branch>] [--draft]`.
   - It checks the route: no origin, gh not signed in, or no push access gives `local_only: <reason>`. Report that, and ship lands locally.
   - It resolves the base (never assumes `main`) and reuses an existing PR for the branch (pushing any new commits to it).
   - It pushes without force, creates the PR, refuses AI attribution, and verifies the PR head equals the local HEAD.
5. **Return** the number, URL and whether it was reused.

## Rules
- No AI attribution in the title or body.
- Never merge here; merging belongs to ship under the operator's authority.
- Pull from the store's check and review reports when they exist (`<store>/reports/<slug>.*.md`); never claim validation that did not run.

**Called by another skill** (ship, fleet)? Return the result and hand control back: the caller continues with its next step. Do not end the turn here.
