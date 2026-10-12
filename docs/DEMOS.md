# 🎬 Demos: the everyday workflows, step by step

Each demo starts in Claude Code with sysop installed (`claude` in a git repo). Every step says what you should see; times and costs are what sysop's own test runs measured, with Haiku coordinating.

> **Tip:** free models reset their daily quotas at different times (OpenRouter at 00:00 UTC). Run `/sysop doctor --probe` first: it checks every model source with one tiny request.

## 1. 🔧 One fix, end to end · 1–2 min · ~$0.05

The everyday case: one small change, from request to merged.

1. In a repo with a bug (for a test, make `add()` return `a - b`), type:
   ```
   /sysop:go fix add() so it adds --land
   ```
2. You'll see one plain line per step: the isolated copy, your tests, the second opinion from another vendor's model, the merge.
3. The final message says what landed and what it cost. `git log --oneline` shows the fix and its new test.

Without `--land` it stops at **ready**; `/sysop land` merges it.

## 2. ⚙️ Three repos at once · 10–20 min · cents

One coordinator, several repos, a dependency between them.

1. `/sysop demo` creates `~/sysop-demo`: a money library (`api`), a CLI that needs it (`cli`), and a short story (`book`), with four tickets queued in a lane called `demo`. The lane uses your active preset (free-max when none is set); `/sysop demo ~/sysop-demo <preset>` picks another.
2. `cd ~/sysop-demo/api`, start `claude`, and type:
   ```
   /sysop:fleet --all --yes
   ```
3. You'll see three builds start at once, the band counting them above the prompt, and the session going quiet: the sysop mod wakes it when a worker finishes. `cli` waits until `api`'s ticket lands, then starts.
4. Open `/sysop board` mid-run: Needs you · Working · Ready · Landed.
5. Afterwards: `git log --oneline` in each repo, and `book/chapters/02.md`.

Clean up: `/sysop lane remove demo`, then `rm -rf ~/sysop-demo`.

## 3. 🔥 Your own model on Kaggle's free GPUs · ~25 min (mostly waiting)

An open model you serve builds the change.

1. One-time setup is in [the guide](guide.md#free-models): a Kaggle account, a free ngrok static domain, a token uploaded once from your own terminal.
2. Then:
   ```
   /sysop serve list                  what fits, with measured speed
   /sysop serve up                    3–10 minutes to ready
   /sysop preset kaggle-session
   /sysop:go add calc.multiply with a test --land
   ```
3. You'll see the ticket built by `pi:kaggle/<model>` on the board and in the final message.
   For the three-repo demo on your Kaggle model: after `serve up`, run `/sysop demo ~/sysop-demo kaggle-session`, then the fleet as in demo 2.
4. `/sysop serve down` when you're done (it also stops after 20 idle minutes, at the end of the run that started it, or after 4 hours).

Kaggle gives about 30 GPU hours a week and allows personal ML use only. The coordinating session still runs on Claude (cents with Haiku), and `kaggle-session` keeps Claude for risky work and reviews.

## 4. 🆓 A free-only afternoon · $0 per-token spend

Your Claude plan stays almost untouched.

1. `/sysop preset free-max`
2. `/sysop demo ~/free-demo`, then the fleet as in demo 2.
3. `/sysop` shows `Spend today (per-token): $0.00` and your plan's usage barely moving. When OpenRouter's free requests run out, the same models answer through Kilo.

`/sysop preset back` restores your previous setup.

## 5. 💡 Idea to working app · 30–60 min

1. In an empty git repo:
   ```
   /sysop:go build me a command-line reading tracker: add books, mark pages read, show progress --land
   ```
2. You'll see a product doc with a never-build list, tickets with a walking skeleton first, and a fleet building them.
3. At the end the app runs, for example `python3 reader.py add "Dune"`.

## 6. 🐙 GitHub on autopilot

**Issues become merged commits:**
1. `/sysop lane add gh --preset free-max --land`
2. Label an issue `sysop:gh`, then `/sysop lane intake gh` and `/sysop:fleet --lane gh --resume --yes`.
3. The issue is closed by sysop with a comment naming the landed commit.

**Pull requests with review comments:**
1. `/sysop lane add team --preset claude-plan --pr --land`
2. Queue a ticket in that lane and run the fleet; sysop opens a PR with one review comment.
3. Comment "please also handle X" on the PR: the builder fixes it, the review is redone, and only then does the PR merge.

## 7. ✍️ Not only code

1. In `~/sysop-demo/book` (from demo 2):
   ```
   /sysop:go write chapter 3: Mara finds the old logbook --land
   ```
2. The gate is the prose check, and the prose reviewer checks names and facts against chapters 1 and 2.

## 8. 🛡️ The safety net · 1 min

Ask Claude, in any repo with sysop installed:
- "read ~/.ssh/id_rsa": blocked.
- "run git reset --hard HEAD": blocked, then a dialog offers **Run once / Refuse**; Run once runs it and logs it to `.sysop/approvals.jsonl`.
- "commit with a Co-Authored-By: Claude line": blocked.

## 9. 🕹️ Watching costs nothing · 15 s

While any run is going (even mid-turn), type `/sysop` or `/sysop board`. They answer at once, without a model turn.

## 🎥 Recording tips
- Make the terminal about 140 columns wide, with a large font.
- `asciinema rec --idle-time-limit 2 demo.cast` squeezes the waiting; `agg demo.cast demo.gif` makes a GIF.
- End each clip on proof: the cost line, `git log`, or the app running.
