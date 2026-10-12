"""Shared head of every sysop Kaggle notebook: sysop renders JOB, then appends one template (infer.py, serve.py).

Engine (a cached static llama-server, or a fresh build), model download, a local server with a load
deadline, and proof that the model reached the GPU. Everything runs inside the private session.
"""
import glob
import json
import os
import subprocess
import time
import urllib.request

JOB: dict = {}  # sysop: rendered job
KAGGLE, TMP = os.environ.get("SYSOP_KAGGLE_ROOT", "/kaggle"), os.environ.get("SYSOP_TMP", "/tmp")  # tests move them
OUT, T0 = f"{KAGGLE}/working", time.time()
metrics: dict = {"job": JOB.get("name"), "model": f"{JOB.get('repo')}/{JOB.get('file')}"}
servers: list = []  # the llama-server child, stopped on every way out
LOAD_LIMIT = JOB.get("load_limit", 600)  # seconds from launch to healthy before the job gives up


def save() -> None:
    with open(f"{OUT}/metrics.json", "w") as fh:
        json.dump(metrics, fh, indent=2)


def mark(name: str) -> None:
    metrics[name] = round(time.time() - T0, 1)
    print(f"[{name}] {metrics[name]}s", flush=True)
    save()


def stop_server() -> None:
    for server in servers:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()


def sh(cmd: str) -> None:
    print("+", cmd, flush=True)
    subprocess.run(cmd, shell=True, check=True)


def llama_server() -> str:
    """A cached static llama-server from the attached engine dataset, or a fresh static build."""
    cached = [p for p in glob.glob(f"{KAGGLE}/input/**/llama-server", recursive=True) if os.path.getsize(p) > 0]
    if cached and JOB.get("engine") != "build":
        sh(f"mkdir -p {TMP}/llama/bin && cp {cached[0]} {TMP}/llama/bin/llama-server && chmod +x {TMP}/llama/bin/llama-server")
        metrics["engine"] = "cached"
        return f"{TMP}/llama/bin/llama-server"
    sh("git clone --depth 1 https://github.com/ggml-org/llama.cpp /tmp/llama.cpp")
    # Kaggle's image has no libcuda for the linker: no VMM pools (the only driver-API user), stubs on the path
    sh("cmake -S /tmp/llama.cpp -B /tmp/llama.cpp/build -DGGML_CUDA=ON -DGGML_CUDA_NO_VMM=ON -DBUILD_SHARED_LIBS=OFF "
       "-DCMAKE_CUDA_ARCHITECTURES=75 -DLLAMA_CURL=OFF -DCMAKE_BUILD_TYPE=Release "
       "-DCMAKE_LIBRARY_PATH=/usr/local/cuda/lib64/stubs 2>&1 | tail -20")
    sh("cmake --build /tmp/llama.cpp/build --target llama-server -j 4 2>&1 | tail -5")
    sh("ldd /tmp/llama.cpp/build/bin/llama-server | head -20")
    sh(f"cp /tmp/llama.cpp/build/bin/llama-server {OUT}/llama-server")  # one static file: sysop caches it on pull
    metrics["engine"] = "built"
    return "/tmp/llama.cpp/build/bin/llama-server"


def gpu_warning(gpus: list, log: str) -> str | None:
    """GPU memory in use is the proof: recent llama.cpp prints no offload line at the default verbosity."""
    used = sum(int(g.rsplit(",", 1)[-1].split()[0]) for g in gpus if g.strip().endswith("MiB"))
    failed = [line.strip() for line in log.splitlines() if "failed to initialize CUDA" in line]
    if failed or used < 1024:  # a bare CUDA context is a few hundred MiB; a loaded model is gigabytes
        return "the model did not reach the GPU: " + (failed[0] if failed else f"{used} MiB GPU memory in use")
    return None


def evidence() -> None:
    """What the run actually used: GPU memory, any CUDA failure, the server log (kept with the outputs)."""
    try:
        metrics["gpus"] = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.used", "--format=csv,noheader"],
                                         capture_output=True, text=True, timeout=30).stdout.strip().splitlines()
    except Exception:  # noqa: BLE001 - no driver: that is the evidence
        metrics["gpus"] = []
    log = open(f"{TMP}/server.log", errors="replace").read() if os.path.exists(f"{TMP}/server.log") else ""
    if JOB.get("key"):
        log = log.replace(JOB["key"], "<session key>")  # outputs never carry the key, whatever the engine logs
    warning = gpu_warning(metrics["gpus"], log) if JOB.get("accelerator") == "gpu" else None
    if warning:
        metrics["warning"] = warning
    with open(f"{OUT}/server.log", "w") as fh:  # the head holds load lines; the tail, timings
        fh.write(log if len(log) <= 60000 else log[:30000] + "\n...\n" + log[-30000:])
    save()


def start_model(port: int, extra: list) -> subprocess.Popen:
    """Engine + model + llama-server on 127.0.0.1:port, healthy within LOAD_LIMIT and proven on the GPU."""
    binary = llama_server()
    mark("server_ready_to_load_s")
    from huggingface_hub import hf_hub_download
    model = hf_hub_download(JOB["repo"], JOB["file"], local_dir=f"{TMP}/model")
    mark("downloaded_s")
    parallel = JOB.get("parallel", 8)
    # the inherited environment carries Kaggle's driver path (libcuda.so.1); replacing it hides the GPU
    server = subprocess.Popen([binary, "-m", model, "--host", "127.0.0.1", "--port", str(port),
                               "--parallel", str(parallel), "-c", str(JOB.get("ctx", 4096) * parallel),
                               *extra, *JOB.get("flags", [])],
                              stdout=open(f"{TMP}/server.log", "w"), stderr=subprocess.STDOUT)
    servers.append(server)
    loading_until = time.time() + LOAD_LIMIT
    while True:
        try:
            if urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5).status == 200:
                break
        except Exception:  # noqa: BLE001 - still loading
            if server.poll() is not None:
                raise RuntimeError("llama-server exited: " + open(f"{TMP}/server.log").read()[-2000:])
            if time.time() > loading_until:
                raise RuntimeError(f"llama-server not healthy after {LOAD_LIMIT} s; see server.log")
            time.sleep(3)
    mark("ready_s")
    evidence()
    if metrics.get("warning"):
        # a CPU fallback burns GPU quota at a hundredth of the speed: stop before any work
        raise RuntimeError("the model did not reach the GPU; see server.log")
    return server
