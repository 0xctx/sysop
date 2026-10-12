"""sysop batch inference: appended to common.py in a private Kaggle notebook. Finite by construction.

Serves the model on 127.0.0.1 inside this session only, answers every row of prompts.jsonl
concurrently, appends each result as it finishes, and exits. A watchdog ends the session at the
job's wall-clock limit; results written so far are kept.
"""
import threading
from concurrent.futures import ThreadPoolExecutor

PORT = JOB.get("port", 8080)
lock = threading.Lock()


def watchdog() -> None:
    time.sleep(max(60, JOB.get("max_minutes", 60) * 60 - 90))
    metrics["error"] = "wall-clock limit reached; results so far are kept"
    save()
    stop_server()
    os._exit(3)


def main() -> None:
    threading.Thread(target=watchdog, daemon=True).start()
    [prompts] = glob.glob(f"{KAGGLE}/input/**/prompts.jsonl", recursive=True) or [None]
    if not prompts:
        raise RuntimeError("no prompts.jsonl in the attached inputs")
    rows = [json.loads(line) for line in open(prompts) if line.strip()]
    metrics["rows"] = len(rows)
    parallel = JOB.get("parallel", 8)
    thinking = [] if JOB.get("params", {}).get("reasoning") else ["--reasoning-budget", "0"]  # batch: answers, not thoughts
    start_model(PORT, thinking)
    params = JOB.get("params", {})
    results = open(f"{OUT}/results.jsonl", "a")
    stats = {"ok": 0, "failed": 0, "tokens": 0}

    def ask(row: dict) -> None:
        messages = ([{"role": "system", "content": params["system"]}] if params.get("system") else []) + [
            {"role": "user", "content": row["prompt"]}]
        body = json.dumps({"messages": messages, "max_tokens": params.get("max_tokens", 512),
                           "temperature": params.get("temperature", 0.3)}).encode()
        start = time.time()
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{PORT}/v1/chat/completions", data=body,
                                         headers={"Content-Type": "application/json"})
            res = json.loads(urllib.request.urlopen(req, timeout=900).read())
            msg = res["choices"][0]["message"]
            out = {"id": row["id"], "output": msg.get("content") or "",
                   **({"reasoning": msg["reasoning_content"]} if msg.get("reasoning_content") else {}),
                   "tokens": res["usage"]["completion_tokens"], "seconds": round(time.time() - start, 2)}
            key = "ok"
        except Exception as exc:  # noqa: BLE001 - one bad row never sinks the batch
            out, key = {"id": row["id"], "error": str(exc)[:500]}, "failed"
        with lock:
            results.write(json.dumps(out) + "\n")
            results.flush()
            stats[key] += 1
            stats["tokens"] += out.get("tokens", 0)

    start = time.time()
    with ThreadPoolExecutor(parallel) as pool:
        list(pool.map(ask, rows))
    wall = time.time() - start
    metrics.update(stats, batch_wall_s=round(wall, 1), aggregate_tok_s=round(stats["tokens"] / max(wall, 0.1), 1))
    mark("done_s")
    evidence()  # the final log tail too


try:
    main()
except Exception as exc:  # noqa: BLE001 - the metrics say what failed
    metrics["error"] = str(exc)[-2000:]
    evidence()
    raise
finally:
    stop_server()
