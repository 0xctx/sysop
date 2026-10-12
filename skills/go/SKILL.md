---
name: go
description: The sysop front door. Routes a request to exactly one workflow (fleet, ship, debug, plan, prd, slice, review, setup, status) and runs it; changes are built off this session on the preset's models. Use via /sysop:go <anything>.
argument-hint: "<what you want, in your own words> [--land] [--here] [--lane <name>]"
disable-model-invocation: true
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py *)
---

# Go

Pick the one workflow that fits the request, say which in a single line, and run it with the request passed through verbatim.

Repo settings: !`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py config get check`

**Request:** $ARGUMENTS

## First, someone new
With no request, or a request like "help", "what can you do", "I'm new", "how does this work": do not route and do not paste the README. Ask what they want to do, in a few lines:
- one change: "fix the login redirect", "add a dark mode toggle";
- a batch: "these three bugs, in parallel";
- an idea: "build me an app that tracks my reading".

Add one line on what happens next: sysop builds in an isolated copy, runs the tests, has a model from another AI vendor review the change, and merges only on your say-so. If the check above is `""`, say that a one-minute setup comes first, and run `sysop:setup` when they answer. Then wait for their answer.

## Route
| The request is… | Run |
|---|---|
| no settings yet (the check above is `""`) and the request is about setting up, or nothing else is asked | `sysop:setup` |
| one change to make (fix, feature, refactor, docs), whether tiny or large | `sysop:fleet` with the change as one ticket and `--yes`: the preset's models build it, off this session |
| one change, with `--here` or "do it here", "in this session", "let me watch" | `sysop:ship` (builds in this session, on its model) |
| several independent changes, "in parallel", "as a fleet", or a backlog | `sysop:fleet` |
| "add this to the <name> lane", `--lane <name>` | `sysop:fleet --lane <name>` (with `--yes` for one change) |
| why something happens, a bug to understand, an error to explain | `sysop:debug` |
| "how should we build", a plan without building | `sysop:plan` |
| an idea or an app to build, with no PRD yet ("build me…", "I want an app that…") | `sysop:prd`, then `sysop:slice` on the PRD it wrote, which builds the slices as a fleet |
| a PRD or plan that exists, to build ("build this PRD", "slice this") | `sysop:slice` |
| a new product or epic to think through, without building yet | `sysop:prd` |
| "review this", "is it ready" | `sysop:review` |
| the same ML task over many items (summarise, classify, translate or extract per file; evaluate models; embeddings) | `sysop:batch` |
| "status", "where are we", "what's running", "what's left" | run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py status` and relay it in plain words, ending with its Next line |
| which models to use: "switch models", "use only X", "go free", a preset |  `sysop:tune` |
| "serve a model", free GPUs, run my own model | `sysop:setup` (its free-models step; `serve up` once set up) |

**Work already running:** when `sysop.py status` shows a fleet run active in this session, new work joins it: route to `sysop:fleet --add` with the request, which adds the tickets and keeps the one loop going. Never start a second loop.

Decide from the meaning, not from keywords. When two routes fit, prefer the one that changes less (debug before ship; plan before fleet) and say what the other would do. When the request is genuinely ambiguous between doing and diagnosing, ask one question with a proposed answer. Flags in the request (`--land`, `--yes` or `--go`, `--pr`, `--lane <name>`, `--here`) pass through unchanged.

Invoke the chosen skill with the Skill tool by its full `sysop:` name; never a built-in skill of a similar name. When no settings exist yet and the operator asked for real work, note once that `/sysop:setup` takes a minute, then do the work anyway.

## Resources
- `references/words.md`: plain words for every message to the operator; no unexplained internal term reaches them.
