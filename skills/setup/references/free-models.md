# Free models (optional)

Offer this only when the operator asks about free models, or when no paid worker is available. Each route is opt-in; recommend them in this order.

## 1. A free API key through pi (two minutes)
- Install pi: `npm install -g @earendil-works/pi-coding-agent` (pi ≥ 0.86).
- Get a free Gemini key at aistudio.google.com and `export GEMINI_API_KEY=…` in the shell profile. Then `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py roster --refresh` and `… models check`. The default preset (claude-plan) already uses it as its backup and second-vendor reviewer.

## 1b. OpenRouter (a key; free models, or credit for cheap paid ones)
- Create a key at openrouter.ai/keys and `export OPENROUTER_API_KEY=…` in the shell profile, then `… roster --refresh`.
- Free models (`:free`): 50 requests a day, or 1,000 once the account has bought $10 of credit in its lifetime (the unlock stays when the balance is spent). A small ticket takes 20–60 requests, so 1,000 is roughly 15–30 small tickets. Free routes may log prompts: private code belongs on paid routes.
- Then `… preset show openrouter-free` (no spend) or `openrouter-value` (needs credit; a $2 a day budget guard). Without credit, paid models answer 402; sysop rests them for six hours and falls back.

## 1c. Kilo's free gateway and Groq's free tier (through pi)
- **Kilo** serves free models (Nemotron 3 Super and Ultra, Laguna S 2.1, Step 5 preview, `kilo-auto/free`) with no credit: sign in once in pi (`pi`, then `/login kilo`), then `… roster --refresh`. It is the same models OpenRouter's free tier has, through another door: when OpenRouter's 50 a day run out, sysop reaches the same model through Kilo before it switches models. It ranks ahead of pi-free's Cline pools.
- **Groq** (`export GROQ_API_KEY=…`, free at console.groq.com): only `openai/gpt-oss-120b` and `gpt-oss-20b` answer on the free tier (the Llama and Qwen models it lists refuse; sysop rests them for a day with "not enabled for your account"). GPT-OSS is the free reviewer from a different vendor (OpenAI) for Nemotron, Laguna and Gemini builders, and it is fast. The catch: the free tier takes at most 8,000 tokens a minute, so a request larger than that (a review of a big diff) is refused at once with 413, and sysop moves on to the next reviewer. `free-max` and `overnight-fleet` already use it.

## 1d. More free keys, if you want them (offered, never installed)
pi supports these built in; add the key and `… roster --refresh`, then `… models advise` suggests their models.
- **Mistral** "Experiment" (`MISTRAL_API_KEY`): about 1B tokens a month across the lineup, Codestral included; a new vendor for review. The catch: prompts may be used for training, so non-private work only.
- **NVIDIA NIM** (`NVIDIA_API_KEY`, build.nvidia.com): about 40 requests a minute. Nemotron (another door), DeepSeek, MiniMax, Gemma.
- **Z.ai** (`ZAI_API_KEY`): GLM Flash is free; read the terms first.
- Not recommended: Cerebras (no lasting free tier since July 2026); Cloudflare Workers AI (2–8K context is too small for an agent).

`… doctor` lists each pi source as listed, resting (and why) or absent; `… doctor --probe` sends each one a 1-token request.

## 2. Your own model on Kaggle's free GPUs (opt-in, personal use)
`serve up` runs one open model on Kaggle's 2×T4 for the length of a work session, behind a key, on the operator's ngrok static domain. It is reached as `pi:kaggle/<model>`. The session ends on `serve down`, at the end of the run that started it, after 20 idle minutes, or after 4 hours; nothing restarts it.

Say plainly: this uses Kaggle's free GPU quota (about 30 h a week) under Kaggle's terms, for personal ML use only, at the operator's own risk. Never share the endpoint, and never run sessions back to back to stay up.

One-time steps (the operator does 1–3; give them exactly these lines):
1. A Kaggle account with phone verification, and the CLI signed in: `pip install kaggle`, then `kaggle auth login`.
2. An ngrok account (free). Claim the free static domain at dashboard.ngrok.com → Domains, then `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py config set serve.domain <name>.ngrok-free.dev --scope user`.
3. In **their own terminal** (never paste the token into chat): `NGROK_AUTHTOKEN=<token from dashboard.ngrok.com> python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py serve token`. It is stored only as a private Kaggle dataset (`sysop-tunnel`) and never printed.
4. Run `… serve list` to see the models with their measured speed, then `… serve up` (3–10 min to ready), then `… models set <tier> pi:kaggle/<model> …` or `models route <kind> <tier>` to use it.

Run `serve add <huggingface GGUF repo>` to add another model: it checks the model fits 2×T4 (a 4-bit file of at most 24 GB) and records it as "not measured" until a run measures it.
