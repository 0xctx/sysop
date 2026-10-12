# The sysop guide

> **Typing commands:** inside Claude Code, every everyday command below works as `/sysop <command>` (for example `/sysop preset free-max`, `/sysop serve up`, `/sysop land`), at no token cost. `sysop.py` is the same command line for scripts and shells; it lives in the plugin's `scripts/` folder.

Everything the README leaves out: every skill, preset, model setting and free source, and where things live. Start with the [README](../README.md); the design is in [architecture.md](architecture.md).

## Merging: what `--land` means

sysop never merges without your word. Without `--land`, a change goes all the way (contract, build, your tests, a review sealed to the commit) and stops at **ready**. Then any one of these merges it, through the same gates:

| You want | Do |
|---|---|
| merge everything ready | `/sysop land`, the board's **Land all ready**, or **2** in the band |
| merge one ticket | `/sysop land t3`, or **Land** on its row |
| merge as soon as it's ready, this time | add `--land` to the request |
| this repo merges by itself from now on | `/sysop autoland on` (`off` to stop); saved on this machine only, never in your commits |
| a lane that merges by itself | `/sysop lane add <name> --preset <p> --land` |

Saying "land it" in the chat works too; `/sysop land` does the same without a model turn. Destructive git commands are a separate matter: the guard blocks them, and in an interactive session a dialog lets you run one once.

## Skills, agents and hooks

| Skill | What it does |
|---|---|
| `go` | The front door: routes any request to exactly one workflow. |
| `ship` | One change, end to end: contract, plan, build, gate, independent review, correction pass, landing (locally or through a PR). |
| `fleet` | A batch of changes as parallel tickets on routed workers, driven by `sysop.py next`: dependency order, a parallel cap, stacking, recorded fallbacks, cross-vendor review, serial landing, and `--resume` after a crash or `/clear`. `--race N`: several vendors build the same ticket, and the first green, READY result lands. |
| `plan` / `prd` / `slice` | Evidence-backed implementation plans; problem-first product docs with a hypothesis that can be proved wrong; a PRD cut into thin, shippable tickets, skeleton first. |
| `build` / `check` / `review` | Implement with tests first; run the gate and name a red result's cause; review with fresh read-only agents scaled to risk, plus a reviewer from another vendor. |
| `debug` | Reproduce, test competing hypotheses, prove the root cause. |
| `commit` / `pr` | Focused commits in your repo's style; one pull request. Never AI attribution. |
| `batch` | Opt-in, with the Kaggle module: big ML batch work (summarise, classify, translate, evaluate, embed) on Kaggle's free GPUs, with the results back as files. |
| `setup` / `tune` | First-run setup; routing and model suggestions from your own outcomes and current benchmarks. |
| `rules` | Keep AGENTS.md true and lean. |

Reviewer agents: code-reviewer, failure-hunter (errors that look like success), seam-reviewer (missing types at boundaries), simplifier, root-cause, explorer, and prose-reviewer (continuity, voice and structure, for writing that is the product).

**Guarantees, as hooks:**
- **guard:** blocks reading credentials, destructive commands, forged sysop records, direct merges of sysop's branches that skip the gates, and AI attribution in commits. It runs in **every** Claude Code session once the plugin is installed, not only in sysop's own runs. Its rules about discarding work (`reset --hard`, `branch -D`, `clean -f`, `restore`) apply everywhere.
- **stop gate:** keeps a multi-step run from ending its turn early.
- **context:** at session start, a few lines of what is true now: branch, open work, live workers, and newer models worth a look.

**The mod** (Claude Code 2.1.287+): `/sysop` and the board at zero tokens; wake-ups, so the coordinator sleeps while workers build; the plan reserve's reading of your Claude limits; and a sysop reviewer agent dispatched without its model gets the configured one. When the guard stops a destructive git command (reset --hard, branch -D, a force push, clean -f), an interactive session shows a dialog with the full command: **Run once** runs that single call and logs it to `.sysop/approvals.jsonl`; credential reads, forged records and AI attribution are never offered. With mods off, everything else works as before.

## Presets

A preset is a readable JSON file that sets the models for what you have. `sysop.py preset list` marks each one ✓ ready, ◐ partly ready or ✗ missing something; `preset show <name>` gives its limits; `preset use <name>` applies it, and `preset use previous` undoes that.

| Preset | You have | Builds with | Catch |
|---|---|---|---|
| **claude-plan** (default) | a Claude plan | Haiku, Sonnet, Opus | your plan's limits; a reviewer from another vendor needs agy or a free Gemini key |
| haiku-workhorse | a Claude plan, and want many tickets | Haiku nearly everywhere | more review rounds on large changes |
| claude-only | Claude, nothing else | Haiku, Sonnet, Opus | no second opinion from another vendor (stated in every review) |
| openrouter-value | OpenRouter credit | DeepSeek V4 Flash and Pro; GLM 5.3 reviews | per-token spend, with a $2 a day guard |
| openrouter-free | an OpenRouter key | Laguna, Nemotron; Gemma reviews | 50 free requests a day (1,000 after $10 of lifetime credit); prompts may be logged |
| free-max | pi, plus any free source | OpenRouter free, then Kilo, then Cline, then Gemini; Groq's GPT-OSS reviews | quality varies most |
| cline-free | pi-free | Cline's free pools | pi-free presents itself as Cline's VS Code client |
| gemini-key | a free AI Studio key | Gemini 3.8 Flash | about one ticket a day |
| kaggle-session | Kaggle and ngrok | a model you serve; Claude for risky work | `serve up` first; 4 hours at most |
| overnight-fleet | Claude and pi | Claude plus free models in every tier | set `notify` to hear from it |
| quality | a Claude plan (+ agy or OpenRouter credit) | Sonnet and Opus | spends a plan fastest |
| writing | a Claude plan | Sonnet, Opus, `auto:writing` | for prose repos |
| google-plan | a Google AI plan (agy) | Gemini 3.8 Flash, Gemini 3.1 Pro; GPT-OSS reviews | agy's quota; Gemini falls back to a free key |
| two-plans | a Claude plan and a Google plan | Gemini for small and standard work, Claude for risky; each reviews the other | spends both plans a little per ticket |

**Your session's model:** the session coordinates, so Haiku or Sonnet is enough for everything except `--here` builds; `preset show` names the one each preset suggests. sysop's own reviewer agents use the preset's choice too (`agents`), never an expensive session model by accident.

Capacities are estimates: a small ticket costs a worker 20 to 60 requests, and a review 5 to 20.

### Make your own preset

**The easy way: ask.** `/sysop:tune use Gemini through agy for building and Claude only for reviews` picks the closest preset or writes a copy with your change, shows you the difference, and applies it on your word.

**By hand, in three steps:**
1. `/sysop preset save mine` writes your current settings to `~/.claude/sysop-presets/mine.json` (or put it in the repo's `.claude/sysop-presets/` to share it with your team). Yours win over a shipped preset with the same name.
2. Edit the file. A preset is plain JSON:
   ```json
   {
     "name": "mine",
     "summary": "One line: who it is for.",
     "session": "haiku",
     "needs": ["agy"],
     "optional": ["claude: risky work"],
     "capacity": "what limits it",
     "caveats": ["what to know before using it"],
     "settings": {
       "tiers": {
         "fast":     ["agy:gemini-3.8-flash-high", "pi:google/gemini-3.8-flash"],
         "standard": ["agy:gemini-3.1-pro-high", "claude:sonnet"],
         "deep":     ["claude:opus"],
         "review":   ["agy:gpt-oss-120b-medium", "claude:sonnet"],
         "verify":   ["claude:haiku"]
       },
       "routes": {"small": "fast", "large": "standard", "risky": "deep"}
     }
   }
   ```
   - Each tier is an ordered fallback chain of `worker:model[@effort]`: the first one that is installed, signed in and not resting does the work.
   - `routes` sends ticket sizes (and task kinds such as `writing`) to tiers.
   - `review` needs at least one model from **another vendor** than your builders; `verify` must be `claude` or `pi`.
   - `needs` and `optional` name what readiness checks: `claude`, `agy`, `pi`, `openrouter`, `kilo`, `groq`, `gemini-key`, `pi-free`, `kaggle`.
   - `/sysop models available` lists every model each CLI offers you.
3. `/sysop preset show mine` checks it (the safety rules and a live check of each model), then `/sysop preset mine` applies it; `/sysop preset back` undoes.

## Models

Every delegation is one entry, `worker:model@effort`, in ordered fallback chains called tiers. A size or task kind routes to a tier, or to `auto:<skill>`, a chain built live from the models you can run, ranked by benchmarks and by this repo's own results.

Model intelligence is an opt-in layer: named tiers route without it, and it loads only for `auto:` routes, the door fallback and `models`. Its benchmarks come from Artificial Analysis: live with `ARTIFICIAL_ANALYSIS_API_KEY`, else from a bundled snapshot when one is installed. Without either, scores are the shipped estimates in `scripts/catalog.json`, and `models advise` says "estimates only". Prices and context sizes come from OpenRouter's public model list. `config set intel true` refreshes these facts with every roster rebuild and announces new models at session start.

```
sysop.py models                              # tiers, routes, what is available, today's spend
sysop.py models set fast pi:google/gemini-3.8-flash agy:gemini-3.8-flash-high claude:haiku
sysop.py models route writing auto:writing
sysop.py models advise                       # newer and better models you can run, with evidence
sysop.py models learn [--apply]              # score changes from this repo's own outcomes
```

**How sysop picks and falls back:**
- **The same model through other routes first:** when a worker is rate-limited or signed out, sysop tries the same model through another CLI or provider before switching models. Free comes first, then your plans, then per-token billing.
- **Recorded switches:** every switch goes into the ticket log and the ledger.
- **Cooldowns honour quota resets:** a quota error that states its reset keeps the entry cooled down until then.
- **A different vendor reviews, and must show its work:** cross-vendor review always uses another company's model than the author's. A verdict that does not name each changed file it checked counts as no review, and the next reviewer takes over.
- **A stronger model before you:** a ticket whose author fails all its tries gets one last try a tier up (fast → standard → deep) before it waits for you.
- **Repo facts stick:** when the same wrong finding is declined twice for a fact about your repo, `sysop:rules` offers that fact as one AGENTS.md line, which every reviewer reads.

## Free models

1. **A free key through pi** (two minutes): install pi, export `GEMINI_API_KEY` (AI Studio) or `OPENROUTER_API_KEY`, then run `sysop.py roster --refresh`. The default preset uses the Gemini key as its backup and second-vendor reviewer; the openrouter-free and free-max presets use OpenRouter's free models. Free tiers are small (Gemini 3.8 Flash allows about 20 requests a day).
2. **Kilo and Groq** (through pi): Kilo's free gateway serves the same free models as OpenRouter (Nemotron, Laguna) through another door, so when OpenRouter's 50 a day run out sysop reaches the same model through Kilo first. Groq's free tier answers GPT-OSS 120B and 20B: a fast free reviewer from another vendor. `skills/setup/references/free-models.md` has the steps, plus optional free keys (Mistral, NVIDIA NIM, Z.ai). `sysop.py doctor --probe` sends each source a 1-token request.
3. **pi-free** (optional) adds more free pools. Its Cline provider presents itself as Cline's own VS Code client; sysop never depends on it, and this guide does not recommend it. Use it only if you accept that.
4. **Kaggle batch jobs:** `sysop.py kaggle run <job.json>` runs a finite, private notebook on 2×T4 and brings the outputs back. Measured: Gemma 4 26B at 134 tok/s, Qwen3.6-35B-A3B at 111 tok/s, both aggregate.
5. **Your own model for a work session:** `sysop.py serve up` serves one open model on Kaggle behind a per-session key, on your ngrok static domain, and pi reaches it as `pi:kaggle/<model>`. It stops on `serve down`, at the end of the run that started it, after 20 idle minutes, or after 4 hours. Nothing restarts it.

**Kaggle terms:** personal, non-commercial ML use only. Kaggle's Acceptable Use Policy forbids server farming and non-ML compute; sysop runs only ML work there (batch inference, evaluation, embeddings), never builds or test suites, never shares an endpoint, and never chains sessions to stay up. You use it at your own risk and under your own account.


## More ways to run
- **Overnight:** leave the session open: the mod wakes the coordinator whenever workers need it. Headless or remote, `/loop /sysop:fleet --resume` does the same from the records. `/sysop halt` stops new work, and `unhalt` resumes it.
- **Optional, "done means it runs":** set `start` (the command that serves your app) and write `JOURNEYS.md`. Before anything lands, a fresh agent from another vendor uses the running app the way a person would. That agent is trusted only after it has caught a deliberate fault (`sysop.py verify calibrate`).
- **PR lanes:** `/sysop lane add team --preset claude-plan --pr` lands that lane's tickets through pull requests (one canonical review comment, CI, a guarded merge). Review comments and requests for changes on a PR go back to the ticket's builder, quoted, and nothing merges over feedback it has not taken in.
- **GitHub issues as tickets:** `/sysop lane intake <lane> --every 15` takes the open issues labelled `sysop:<lane>` into that lane, once each, with the issue body as the request; the mod repeats it every 15 minutes. A ticket from an issue lands with a push, and then sysop comments the landed commit on the issue and closes it.
- **Notifications:** `config set notify notify-send` (desktop) or `notify ntfy:<topic>` (phone). Messages hold only the run name and its outcome.
- **Status:** `/sysop` shows runs, tickets, your preset, resting models, today's spend and the plan's usage on one screen; `status --all` covers every lane in every repo.
- **Commands:** `/sysop help` lists what you can type; in a shell, `sysop.py --help` lists the everyday commands and `--help-all` every mechanic the skills use.
- **Demo:** `/sysop demo` sets up three small repos with work queued; see [the demos](DEMOS.md).

## Writing, not only code

For a book or articles, `setup` proposes `sysop:prose-check` as the gate: broken links, unclosed fences, leftover `TK` markers, doubled words, and word count against a target. Reviews add the prose-reviewer, which traces names, dates and facts across the whole manuscript.

## Layout
- `skills/`: 16 skills, one per folder.
- `agents/`: 7 read-only reviewer and explorer agents.
- `hooks/`: guard, context and stop gate, plus the mod (`sysop.mod.jsx`) and its state contract (`types/`).
- `presets/`: 14 shipped model presets (JSON).
- `scripts/`: 15 standard-library Python files, behind one CLI, `scripts/sysop.py` (`--help` lists the everyday commands, `--help-all` every mechanic):
  - `store`, `workers`, `land`, `fleet`, `verify`, `presets`, `health` (status and doctor), and the opt-in `advice` (model intelligence);
  - `intel`, `repo_profile` and `prose_check` (pure helpers);
  - `kaggle_jobs` and `kaggle_serve` (the opt-in Kaggle module);
  - `lint` (the craft gate).
- `tests/`: offline unit tests, plus headless end-to-end scripts in `tests/e2e/`.

Design: [architecture.md](architecture.md). Credits: [lineage.md](lineage.md).
