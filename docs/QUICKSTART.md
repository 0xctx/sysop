# 🚀 Quickstart: your first five minutes

## 1. Install (30 seconds) 📦

In Claude Code:

```
/plugin install sysop --marketplace 0xctx/sysop
```

Answer `y` to add the marketplace, then pick **user** scope.

## 2. Let sysop meet your repo (1 minute) 🤝

Open Claude Code in any git repository and type:

```
/sysop:go
```

It reads the repo and asks a few questions, each with its answer already proposed:
- 🧪 the command that runs your tests;
- ✋ whether it may merge on its own (default: no, it asks you);
- 🧠 which models you have (it picks a preset for you);
- 🔍 whether another AI vendor should review the work (yes, when you have one).

## 3. Your first real change (3 minutes) ✨

`/sysop:go` offers a tiny real change it noticed (a typo, a missing test). Say yes, or ask for your own:

```
/sysop:go add a test for the empty-input case in parse()
```

Watch it explain each step in one plain line: the isolated copy, your tests, the second opinion, and why merging waits for your word. When it's ready, type **`/sysop land`** (or just say "land it"). 🛬 Want it to merge by itself next time? Add `--land` to the request, or turn on `/sysop autoland on` for this repo.

## 4. Look around (free) 👀

```
/sysop            📋 what's working, ready, landed
/sysop doctor     🩺 anything missing on this machine, with the fix
/sysop board      🗂️ the live board
```

None of these use a model turn.

## 5. See it build three repos at once 🎬

```
/sysop demo
```

It makes `~/sysop-demo` with three small repos (a money library, a CLI that waits for it, and a book) and queues four tickets in one lane on free models. Then open Claude Code in `~/sysop-demo/api` and type `/sysop:fleet --all --yes`: watch the band, open `/sysop board`, and see each repo land on its own. Clean up with `/sysop lane remove demo` and `rm -rf ~/sysop-demo`.

## 6. Next steps 🌱

- 🆓 Add a free model: a Gemini key from aistudio.google.com is the fastest start. Then `/sysop preset`.
- ⚙️ Run a batch: `/sysop:fleet --yes add X; fix Y; tidy Z`.
- 🌙 Leave it running: open a session, start a fleet, and walk away; the mod wakes Claude when workers need it.
- 🎬 More to try: [the demos](DEMOS.md).
- 📖 Everything else: [the guide](guide.md).

**Stuck?** `/sysop doctor` first, then the [FAQ](FAQ.md).
