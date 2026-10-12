#!/usr/bin/env bash
# M8a e2e: a real Kaggle batch job. 50 short texts summarised by Gemma 4 26B-A4B on 2x T4 through
# `sysop.py kaggle run|wait|pull`; asserts every row came back and nothing is left running.
# Uses about 10 minutes of the operator's weekly Kaggle GPU quota (the cached engine skips the build).
source "$(dirname "$0")/lib.sh"
command -v kaggle > /dev/null || { echo "SKIP: kaggle CLI not installed"; exit 0; }
REPO="$E2E_ROOT/batch"
mkdir -p "$REPO" && git -C "$REPO" init -q -b main && git -C "$REPO" -c user.name=e2e -c user.email=e2e@example.com commit -q --allow-empty -m init
S="python3 $SYSOP/scripts/sysop.py --cwd $REPO"
mkdir -p "$REPO/.sysop/batch/e2e"
ENGINE="${E2E_ENGINE:-auto}"  # build: rebuild and re-seed the cached engine (about 27 GPU-minutes)
python3 - "$REPO/.sysop/batch/e2e" "$ENGINE" <<'PY'
import json, sys
topics = ["lighthouses", "honeybees", "glaciers", "the telegraph", "sourdough", "volcanoes", "chess openings",
          "coral reefs", "printing presses", "solar eclipses"]
with open(f"{sys.argv[1]}/prompts.jsonl", "w") as fh:
    for i in range(50):
        t = topics[i % len(topics)]
        text = f"Note {i}: {t} have a long history. " * 6
        fh.write(json.dumps({"id": f"note-{i:02}", "prompt": f"Summarise in one sentence:\n{text}"}) + "\n")
json.dump({"name": "sysop-e2e-batch", "kind": "infer", "model": "gemma-4-26b-a4b", "inputs": "prompts.jsonl",
           "max_minutes": 75 if sys.argv[2] == "build" else 45, "engine": sys.argv[2],
           "params": {"max_tokens": 80, "temperature": 0.2}},
          open(f"{sys.argv[1]}/job.json", "w"))
PY
$S kaggle run .sysop/batch/e2e/job.json > "$E2E_ROOT/run.json" || { cat "$E2E_ROOT/run.json"; fail "kaggle run"; finish m8a_batch; }
if [ "$ENGINE" = build ]; then
  expect "engine build requested (cache skipped)" grep -q '"engine_cached": false' "$E2E_ROOT/run.json"
else
  expect "engine cache attached (no 27-minute build)" grep -q '"engine_cached": true' "$E2E_ROOT/run.json"
fi
for _ in 1 2 3 4 5 6 7 8 9 10; do $S kaggle wait sysop-e2e-batch --timeout 540 > "$E2E_ROOT/wait.json" && break; done
expect "job completed" grep -q '"state": "complete"' "$E2E_ROOT/wait.json"
$S kaggle pull sysop-e2e-batch > "$E2E_ROOT/pull.json"
cat "$E2E_ROOT/pull.json"
expect "50 results back" python3 -c "import json; d = json.load(open('$E2E_ROOT/pull.json')); assert d['results'] == 50 == d['expected']"
expect "no failed rows" python3 -c "import json; assert json.load(open('$E2E_ROOT/pull.json'))['failed'] == 0"
expect "outputs are summaries, not empty" python3 -c "
import json; rows = [json.loads(l) for l in open('$REPO/.sysop/kaggle/sysop-e2e-batch/1/out/results.jsonl')]
assert all(len(r.get('output', '').split()) >= 4 for r in rows)"
expect "the model ran on the GPU" python3 -c "import json; m = json.load(open('$E2E_ROOT/pull.json'))['metrics']; assert m['gpus'] and 'warning' not in m, m.get('warning')"
expect "GPU-class throughput (>= 20 tok/s)" python3 -c "import json; assert json.load(open('$E2E_ROOT/pull.json'))['metrics']['aggregate_tok_s'] >= 20"
expect "quota reported" grep -q '"quota": "GPU' "$E2E_ROOT/pull.json"
expect "nothing left running on Kaggle" bash -c "$S kaggle status sysop-e2e-batch | grep -qE '\"state\": \"(complete|error|cancelled)\"'"
finish m8a_batch
