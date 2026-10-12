# ❓ FAQ

## 🤔 What does sysop add over plain Claude Code?
A delivery process around every change: a written contract, an isolated copy of your repo, your own tests as a gate, a review by **another AI vendor's model** sealed to the exact commit it read, and a merge that needs your word. It also runs many changes in parallel on other models (free ones first) while your session sleeps.

## 💸 Will it burn my Claude plan?
It is built not to. The coordinating session is light (Haiku or Sonnet is enough), workers can run on free models, waiting costs nothing, and at 80% of your 5-hour or weekly limit sysop stops sending work to Claude (`claude_reserve` changes the share). Every run ends by stating its cost.

## 🆓 Which free models work?
A free Gemini key (AI Studio), OpenRouter's free models, Kilo's free gateway, Groq's free tier (GPT-OSS), and your own model on Kaggle's free GPUs. `/sysop doctor --probe` checks each one. pi-free's Cline pools work too, but they present themselves as Cline's VS Code client, so they are opt-in and never a default.

## 🔒 Is my code sent anywhere?
Only to the model providers your preset uses. Free providers may log prompts, so keep private code on providers you trust (your Claude plan, or a paid key with a no-training policy).

## ✋ Can it merge or delete things without asking?
No. Merging needs `--land`, a standing grant you set, or you pressing Land. A guard hook blocks destructive git commands, credential reads and AI attribution in every session; in an interactive session you can approve a blocked git command once, from a dialog.

## 🧩 What are "mods", and do I need them?
Mods run inside Claude Code (2.1.287+). sysop's mod gives you `/sysop`, the live board, wake-ups and the plan reserve. Without it, everything else still works; you just wait the old way.

## 🛣️ What is a lane?
A named queue with its own models, parallel limit and merge rule, for example a `free` lane that merges by itself and a `careful` lane that waits for you. Lanes can span repos, take GitHub issues in, and land through pull requests.

## 🐙 Does it work with GitHub?
Yes: it opens PRs with one review comment, waits for CI, merges under the same gates, feeds PR review comments back to the builder, and can turn labelled issues into tickets and close them when they land.

## ✍️ Is it only for code?
No. For books and articles, the gate is a prose check and a prose reviewer traces names, dates and facts across the manuscript. Use the `writing` preset.

## 🍎 Does it run on macOS / Windows?
Linux is tested. macOS is expected to work (no GNU-only tools). Windows is untested; WSL is the safe bet.

## 🐞 Something went wrong. Where do I look?
`/sysop doctor` for the machine, `/sysop` for the work, and the `.sysop/` folder in your repo for every plan, log and report. Then open an issue with what `doctor` printed.
