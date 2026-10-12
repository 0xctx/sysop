<div align="center">

# 🛰️ sysop

## Tell Claude what you want. Get it back **built, tested, reviewed by another AI, and merged.** ✨

*A Claude Code plugin that runs a whole delivery team on the models you already have, free ones first.*

[🚀 Quickstart](docs/QUICKSTART.md) · [🎬 Demos](docs/DEMOS.md) · [📖 Guide](docs/guide.md) · [❓ FAQ](docs/FAQ.md) · [🏗️ How it works](docs/architecture.md) · [🤝 Contributing](CONTRIBUTING.md)

</div>

---

## ⚡ In one minute

```
/plugin install sysop --marketplace 0xctx/sysop
/sysop:go fix the off-by-one in pagination --land
```

Want to see it first? **`/sysop demo`** sets up three small repos with work queued; `/sysop:fleet --all --yes` builds them all at once. 🎬

That's it. sysop:

1. 📝 **writes the contract**: what "done" means, before any code;
2. 🧪 **builds in an isolated copy** of your repo and runs **your** tests;
3. 🔍 has a model **from a different AI company** review it, and seals that review to the exact commit it read;
4. ✅ **merges only with your word**, and tells you what it cost.

New here? Just type **`/sysop:go`**. It sets your repo up in a minute and walks you through a first real change, explaining each step as it happens. 🧭

## 🌟 Why people use it

| | |
|---|---|
| 🤝 **A second opinion, always** | Every change is reviewed by another vendor's model (two for risky work), and the review must name every file it read, or it doesn't count. |
| 🆓 **Free models first** | Gemini keys, OpenRouter, Kilo and Groq free tiers, even your own model on Kaggle's free GPUs. Out of quota? sysop reaches **the same model through another route** before it switches. |
| 🛡️ **Your Claude plan, protected** | sysop stops sending work to Claude at 80% of your 5-hour or weekly limit. The last 20% stays yours. |
| 😴 **Claude sleeps while workers build** | The session hands work off and goes quiet; sysop wakes it only when something needs judgement. Waiting costs **zero tokens**. |
| 👀 **See everything, for free** | `/sysop` and the live board run inside Claude Code **without a model turn**: land, halt, switch models, all at zero tokens. |
| 🧾 **Evidence, not vibes** | Plans, build reports, reviews and seals live on disk next to your code. |

## 🎮 Three ways in

```
/sysop:go fix the login redirect                       🔧 one change
/sysop:fleet --yes --land add CSV export; cache stats  ⚙️  a batch, built in parallel
/sysop:go build me an app that tracks my reading       💡 an idea → product doc → tickets → app
```

## 🕹️ The board: `/sysop`

Runs inside Claude Code at **zero tokens**, even mid-turn:

| Command | Does |
|---|---|
| `/sysop` | 📋 every lane in every repo, at a glance |
| `/sysop board` | 🗂️ a live pane: **Needs you · Working · Ready · Landed**, with Land / Why / Halt buttons |
| `/sysop land` | 🛬 merge everything that's ready (or `/sysop land t3` for one); the safety gates still apply |
| `/sysop autoland on` · `off` | 🤖 this repo merges ready work by itself, or waits for you |
| `/sysop halt` · `unhalt` | ⏸️ stop / resume new work |
| `/sysop preset free-max` · `preset back` | 🔀 switch models, or undo |
| `/sysop doctor` | 🩺 is this machine ready? one fix per problem |
| `/sysop models` · `serve` · `config` · `lane` | ⚙️ every everyday command, typed right here |

While work runs, a band above your prompt says it all:
`sysop · 2 working · 1 needs you · 1 ready · plan 41% · $0.12 today`. Press **1** for the board, **2** to land everything ready. 🔔 A toast pops when something lands or needs you.

## 🎛️ Pick your models in one command

```
/sysop preset             ✓ ready   ◐ partly   ✗ missing something
/sysop preset free-max    🆓 every free source you have
/sysop preset back        ↩️ undo
```

| Preset | For you if you have… |
|---|---|
| ⭐ **claude-plan** (default) | a Claude plan |
| 🐎 haiku-workhorse | a Claude plan and lots of tickets |
| 🆓 free-max | `pi` and any free key |
| 🌐 openrouter-free | an OpenRouter key |
| 🌙 overnight-fleet | Claude + free models, running all night |
| ✍️ writing | books and articles, not code |
| 🔷 google-plan | a Google AI plan (Gemini through `agy`) |
| 🤝 two-plans | both a Claude plan and a Google plan |

Fourteen presets ship; [the guide](docs/guide.md#presets) lists them all. 🛠️ **Make your own** in plain words, `/sysop:tune use Gemini for building and Claude for reviews`, or by hand: [the guide](docs/guide.md#make-your-own-preset) shows the format.

## 🔥 Your own model on Kaggle's free GPUs

Serve an open model on Kaggle's free 2×T4 for a work session, and let it build:

```
/sysop serve list                    🧠 what fits, with measured speed (Gemma 4 26B ~134 tok/s)
/sysop serve up                      ⏳ 3–10 min to ready
/sysop preset kaggle-session         🏗️ your model builds; Claude takes risky work and reviews
```

It shuts itself down when idle (20 min), when the run ends, or after 4 hours. Setup is in [the guide](docs/guide.md#free-models). ⚖️ Kaggle's terms allow personal ML use only: sysop never runs builds or tests there, never shares the endpoint, never chains sessions.

## 🛣️ Lanes: many queues, many repos, one coordinator

```
/sysop lane add free --preset openrouter-free --land
/sysop:go --lane free add CSV export
/sysop:fleet --all                    🌍 every lane in every repo
```

- 🐙 **GitHub issues → tickets:** label an issue `sysop:<lane>`; sysop builds it, merges it and closes the issue.
- 💬 **PR lanes:** review comments on a PR go straight back to the builder, and nothing merges until they're handled.

## 💸 What it costs

Measured in sysop's own end-to-end tests (Haiku coordinating), whole session:

| Run | Cost |
|---|---|
| 🎓 setup + a narrated first change | $0.03–0.06 |
| 🔧 a tiny fix, planned, reviewed, landed | $0.03–0.43 |
| ⚙️ a two-ticket fleet | $0.10–0.62 |
| 🛣️ two lanes across two repos on free models | $0.09–0.12 |

On a Claude plan these come out of your plan, not your card. Free presets: $0. Every run ends by telling you its cost.

## 🛡️ Safe by design

- ✋ **Nothing merges without your word.** Without `--land`, sysop does everything up to the merge and stops at **ready**; then `/sysop land`, the board's Land button, or `/sysop autoland on` for a repo you trust.
- 🔏 **One gate for every merge**: a READY review, another vendor's review, a seal to the exact commit, and your tests green.
- 🚧 **A guard in every session** blocks credential reads, destructive git, forged records and AI attribution in commits. Need that `reset --hard` after all? A dialog lets you **run it once**, logged.
- 🗂️ **Work is never deleted**; it's archived.
- 🔐 Free providers may log prompts: keep private code on providers you trust.

## 📦 Install

```
/plugin install sysop --marketplace 0xctx/sysop
```

Answer `y`, pick a scope, then run `/sysop:go`. **Needs:** Python ≥ 3.11, git, Claude Code (2.1.287+ for the board and wake-ups). Optional: `agy`, `pi` ≥ 0.86, `gh`, `kaggle`. 🐧 Linux tested · 🍎 macOS expected.

## 🧭 Learn more

- 🚀 [Quickstart](docs/QUICKSTART.md): your first five minutes, step by step
- 🎬 [Demos](docs/DEMOS.md): nine short demos of the everyday workflows, with what you'll see
- 📖 [Guide](docs/guide.md): every skill, preset, setting and command
- ❓ [FAQ](docs/FAQ.md): the questions everyone asks
- 🏗️ [Architecture](docs/architecture.md): how it works inside
- 🙏 [Lineage](docs/lineage.md): the projects sysop learned from
- 🤝 [Contributing](CONTRIBUTING.md) · 🔒 [Security](SECURITY.md)

<div align="center">

**If sysop saved you an afternoon, a ⭐ helps others find it.**

MIT licensed · no server · no database · no build step · standard-library Python

</div>
