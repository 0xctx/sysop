---
name: batch
description: Runs big batch work on Kaggle's free GPUs or CPUs as one finite private notebook (batch LLM inference over many prompts, model evaluation on the operator's tasks, embeddings or other ML scripts), then brings the results back as files. Use via /sysop:batch <what to do over which inputs>.
argument-hint: "<task over inputs, e.g. summarise every chapter in book/>"
disable-model-invocation: true
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py *)
---

# Batch

Turn one batch-shaped request into one Kaggle job, run it, and bring back the results. sysop owns the mechanics (`sysop.py kaggle …`); you own the shape of the job and the honesty of the report.

Batch models: !`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py kaggle models`

**Request:** $ARGUMENTS

## Rules
- **ML and data-science work only.** Kaggle's Acceptable Use Policy forbids "activity unrelated to ML data science" and "server farming". Batch inference, model evaluation, embeddings, training and data processing qualify. Builds, test suites, crawling and general compute do not; say so and stop.
- One job per request. Never split work across sessions to get more compute.
- Prompts and outputs pass through Kaggle (Google). If the inputs are private or sensitive, say so and ask before uploading.
- A job is finite: `max_minutes` is its hard limit (Kaggle cannot stop a running session from outside, so the notebook stops itself).

## Workflow
1. **Shape it.** Decide `infer` (one prompt per item, same instructions) or `script` (the operator's own Python ML script). If neither fits, say why and stop.
2. **Write the inputs** in the store, under `.sysop/batch/<name>/`:
   - `infer`: `prompts.jsonl`, one `{"id": "<stable id, e.g. the file path>", "prompt": "<instructions + the item>"}` per line. Keep each prompt self-contained, and put shared instructions in `params.system`.
   - `script`: the script plus its data paths.
3. **Write the spec** next to them, as `job.json`:
   `{"name": "<kebab-case>", "kind": "infer", "model": "<one of the batch models>", "inputs": "prompts.jsonl", "max_minutes": <estimate × 1.5, at most 660>, "params": {"system": "...", "max_tokens": 512, "temperature": 0.3}}`.
   For a script job: `"kind": "script", "script": "train.py", "inputs": ["data/"], "accelerator": "cpu"|"gpu"`.
   Estimate the time from the model's measured tok/s (rows × max_tokens ÷ tok/s, plus about 10 minutes of start-up).
4. **Tell the operator before running:** rows, model, estimated minutes and GPU hours, and the quota line `run` prints. Wait for a go unless the request already gave one.
5. **Run:** `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sysop.py kaggle run .sysop/batch/<name>/job.json`.
6. **Wait:** `… kaggle wait <name>` (bounded at about 9 minutes; exit 124 means still running, so re-run it). Never end the turn while it runs and nothing waits.
7. **Pull:** `… kaggle pull <name>`. Report results versus expected rows, failed rows, aggregate tok/s, quota left, and where the files are (`.sysop/kaggle/<name>/<n>/out/`). On `error`, read the log in the out folder and say what failed.
8. **Use the results** as the request asked (for example, write the summaries into place) only on the operator's word if that changes tracked files.
