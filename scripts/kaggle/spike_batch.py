"""Spike 2: llama.cpp batch inference on Kaggle 2xT4. Finite by construction.

Builds llama-server with CUDA, downloads one GGUF, serves it on 127.0.0.1 inside this session
only, sends a concurrent batch, writes metrics.json and the built binary, then exits.
"""
import json
import os
import subprocess
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

MAX_SECONDS = 90 * 60  # this session ends by itself whatever happens
OUT = "/kaggle/working"
REPO, FILE = "google/gemma-4-26B-A4B-it-qat-q4_0-gguf", "gemma-4-26B_q4_0-it.gguf"
PORT, PARALLEL, PROMPTS, MAX_TOKENS = 8080, 8, 64, 256
T0 = time.time()
metrics: dict = {"model": f"{REPO}/{FILE}", "parallel": PARALLEL, "prompts": PROMPTS}


def save() -> None:
    with open(f"{OUT}/metrics.json", "w") as fh:
        json.dump(metrics, fh, indent=2)


def watchdog() -> None:
    time.sleep(MAX_SECONDS)
    metrics["error"] = f"wall-clock limit {MAX_SECONDS}s"
    save()
    os._exit(3)


def sh(cmd: str) -> None:
    print("+", cmd, flush=True)
    subprocess.run(cmd, shell=True, check=True)


def mark(name: str) -> None:
    metrics[name] = round(time.time() - T0, 1)
    print(f"[{name}] {metrics[name]}s", flush=True)
    save()


threading.Thread(target=watchdog, daemon=True).start()
try:
    sh("nvidia-smi --query-gpu=name,memory.total --format=csv")
    sh("git clone --depth 1 https://github.com/ggml-org/llama.cpp /tmp/llama.cpp")
    # Kaggle's container has no libcuda for the linker: no VMM pools (the only driver-API user), stubs on the path
    sh("cmake -S /tmp/llama.cpp -B /tmp/llama.cpp/build -DGGML_CUDA=ON -DGGML_CUDA_NO_VMM=ON "
       "-DCMAKE_CUDA_ARCHITECTURES=75 -DLLAMA_CURL=OFF -DCMAKE_BUILD_TYPE=Release "
       "-DCMAKE_LIBRARY_PATH=/usr/local/cuda/lib64/stubs 2>&1 | tail -40")
    sh("test -f /tmp/llama.cpp/build/Makefile -o -f /tmp/llama.cpp/build/build.ninja")
    sh("cmake --build /tmp/llama.cpp/build --target llama-server -j 4 2>&1 | tail -20")
    sh("test -x /tmp/llama.cpp/build/bin/llama-server")
    mark("built_s")
    sh(f"tar czf {OUT}/llama-cuda.tar.gz -C /tmp/llama.cpp/build bin")
    from huggingface_hub import hf_hub_download
    path = hf_hub_download(REPO, FILE, local_dir="/tmp/model")
    mark("downloaded_s")
    server = subprocess.Popen(
        ["/tmp/llama.cpp/build/bin/llama-server", "-m", path, "--host", "127.0.0.1", "--port", str(PORT),
         "-ngl", "99", "--split-mode", "layer", "--flash-attn", "on", "-ctk", "q8_0", "-ctv", "q8_0",
         "--parallel", str(PARALLEL), "-c", str(4096 * PARALLEL)],
        stdout=open("/tmp/server.log", "w"), stderr=subprocess.STDOUT)
    while True:
        try:
            if urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=5).status == 200:
                break
        except Exception:  # noqa: BLE001 - still loading
            if server.poll() is not None:
                raise RuntimeError("llama-server exited: " + open("/tmp/server.log").read()[-2000:])
            time.sleep(3)
    mark("ready_s")
    topics = ["the printing press", "photosynthesis", "the Roman road network", "TCP congestion control",
              "the water cycle", "compound interest", "plate tectonics", "the history of chess"]

    def ask(i: int) -> dict:
        body = json.dumps({"messages": [{"role": "user", "content":
                           f"In about 150 words, explain {topics[i % len(topics)]} to a curious teenager. (#{i})"}],
                           "max_tokens": MAX_TOKENS, "temperature": 0.3}).encode()
        start = time.time()
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}/v1/chat/completions", data=body,
                                     headers={"Content-Type": "application/json"})
        res = json.loads(urllib.request.urlopen(req, timeout=600).read())
        return {"tokens": res["usage"]["completion_tokens"], "seconds": time.time() - start}

    start = time.time()
    with ThreadPoolExecutor(PARALLEL) as pool:
        results = list(pool.map(ask, range(PROMPTS)))
    wall = time.time() - start
    tokens = sum(r["tokens"] for r in results)
    metrics.update(batch_wall_s=round(wall, 1), completion_tokens=tokens,
                   aggregate_tok_s=round(tokens / wall, 1),
                   per_request_tok_s=round(sum(r["tokens"] / r["seconds"] for r in results) / len(results), 1))
    mark("done_s")
    server.terminate()
except Exception as exc:  # noqa: BLE001 - the metrics say what failed
    metrics["error"] = str(exc)[-2000:]
    save()
    raise
