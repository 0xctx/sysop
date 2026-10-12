"""Kaggle batch jobs: finite private notebooks for batch inference and ML scripts.

Inputs go up as a private dataset, a rendered script kernel runs on Kaggle, outputs come back as
files. Every job ends by itself (a wall-clock watchdog inside the notebook): the Kaggle CLI has no
command to stop a running session. No tunnel, no endpoint. Kaggle's Acceptable Use Policy limits
its compute to ML / data-science work, personal use; sysop's batch skill holds that line.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent / "kaggle"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}$")
STATES = {"QUEUED": "queued", "NEW_SCRIPT": "queued", "RUNNING": "running", "COMPLETE": "complete",
          "ERROR": "error", "CANCEL_REQUESTED": "cancelled", "CANCEL_ACKNOWLEDGED": "cancelled"}
TERMINAL = ("complete", "error", "cancelled")
MAX_MINUTES = 11 * 60  # Kaggle ends a session at 12 h; the watchdog ends it first
ENGINE_CACHE = "sysop-llama-cuda"  # private dataset holding a built llama-server


class JobError(Exception):
    pass


def cli(*args: str, timeout: int = 600) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["kaggle", *args], capture_output=True, text=True, errors="replace",
                              timeout=timeout, stdin=subprocess.DEVNULL)
    except FileNotFoundError as exc:
        raise JobError("the kaggle CLI is not installed (pip install kaggle; then `kaggle auth login`)") from exc


def user() -> str:
    out = cli("config", "view").stdout
    found = re.search(r"username:\s*(\S+)", out)
    if not found or found.group(1) == "None":
        raise JobError("no Kaggle account configured: run `kaggle auth login`")
    return found.group(1)


def models() -> dict:
    return json.loads((HERE / "models.json").read_text())["models"]


def quota() -> str:
    out = cli("quota", timeout=60)
    line = next((l for l in out.stdout.splitlines() if l.startswith("GPU")), "")
    parts = line.split()
    text = f"GPU {parts[1]} used, {parts[2]} left (resets {parts[4][:10]})" if len(parts) >= 5 else "quota unknown"
    if len(parts) >= 5:  # cached for `sysop.py models`, which never calls Kaggle itself
        base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
        cache = Path(base) / "sysop" / "kaggle-quota.json"
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"line": text, "at": time.strftime("%Y-%m-%d %H:%M")}))
    return text


def validate(spec: dict, base: Path) -> dict:
    name = spec.get("name", "")
    if not NAME_RE.match(name):
        raise JobError(f"job name '{name}': lowercase letters, digits and dashes, 2-39 characters")
    kind = spec.get("kind", "infer")
    if kind not in ("infer", "script"):
        raise JobError("kind is infer (batch prompts) or script (your own ML script)")
    minutes = spec.get("max_minutes", 120)
    if not isinstance(minutes, int) or not 5 <= minutes <= MAX_MINUTES:
        raise JobError(f"max_minutes is an integer from 5 to {MAX_MINUTES}")
    accel = spec.get("accelerator", "gpu" if kind == "infer" else "cpu")
    if accel not in ("gpu", "cpu"):
        raise JobError("accelerator is gpu or cpu")
    if spec.get("engine", "auto") not in ("auto", "build"):
        raise JobError("engine is auto (use the cached build when present) or build (build it fresh)")
    out = {**spec, "name": name, "kind": kind, "max_minutes": minutes, "accelerator": accel}
    if kind == "infer":
        if spec.get("model") not in models():
            raise JobError(f"model '{spec.get('model')}' is not in the batch catalog ({', '.join(models())})")
        path = base / spec.get("inputs", "")
        if not path.is_file():
            raise JobError(f"inputs must be a JSONL file of {{\"id\", \"prompt\"}} rows: {path}")
        ids = []
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise JobError(f"{path.name} line {n}: not JSON ({exc.msg})") from exc
            if not isinstance(row, dict) or "id" not in row or not isinstance(row.get("prompt"), str):
                raise JobError(f"{path.name} line {n}: want {{\"id\": ..., \"prompt\": \"...\"}}")
            ids.append(row["id"])
        if not ids or len(set(map(str, ids))) != len(ids):
            raise JobError(f"{path.name}: needs at least one row and unique ids")
        out.update(inputs=str(path), rows=len(ids))
    else:
        script = base / spec.get("script", "")
        if not script.is_file():
            raise JobError(f"script not found: {script}")
        extra = spec.get("inputs") or []
        paths = [base / p for p in ([extra] if isinstance(extra, str) else extra)]
        missing = [str(p) for p in paths if not p.exists()]
        if missing:
            raise JobError(f"inputs not found: {', '.join(missing)}")
        out.update(script=str(script), inputs=[str(p) for p in paths])
    return out


def _dataset_ready(ref: str) -> bool:
    proc = cli("datasets", "status", ref, timeout=60)
    return proc.returncode == 0 and "ready" in proc.stdout.lower()


def _push_dataset(ref: str, folder: Path, attempt: int) -> None:
    (folder / "dataset-metadata.json").write_text(json.dumps(
        {"title": ref.split("/")[1], "id": ref, "licenses": [{"name": "CC0-1.0"}]}))
    exists = cli("datasets", "status", ref, timeout=60).returncode == 0
    args = (["datasets", "version", "-p", str(folder), "-m", f"sysop run {attempt}"] if exists
            else ["datasets", "create", "-p", str(folder)])  # private unless --public
    proc = cli(*args, "-r", "zip", "-q")
    if proc.returncode != 0:
        raise JobError(f"dataset upload failed: {(proc.stderr or proc.stdout).strip()[-300:]}")
    for _ in range(36):  # Kaggle processes uploads asynchronously; a kernel needs them ready
        if _dataset_ready(ref):
            return
        time.sleep(5)
    raise JobError(f"dataset {ref} was not ready within 3 minutes")


def notebook(template: str, spec: dict) -> str:
    """One notebook file: common.py with JOB rendered, then the template (Kaggle runs a single file)."""
    common, marker = (HERE / "common.py").read_text(), "JOB: dict = {}  # sysop: rendered job"
    if marker not in common:
        raise JobError("common.py lost its JOB marker")
    rendered = f"JOB: dict = json.loads({json.dumps(spec)!r})"  # JSON literals (true, null) are not Python
    return (common.replace(marker, rendered) + f"\n\n# --- {template} ---\n"
            + (HERE / template).read_text())


def render(job: dict, kernel: Path) -> None:
    if job["kind"] == "infer":
        spec = {**models()[job["model"]], "name": job["name"], "max_minutes": job["max_minutes"],
                "params": job.get("params", {}), "engine": job.get("engine", "auto"),
                "accelerator": job["accelerator"]}
        (kernel / "run.py").write_text(notebook("infer.py", spec))
    else:
        shutil.copy(job["script"], kernel / "user_script.py")
        (kernel / "run.py").write_text(
            "import os, runpy, threading, time\n"
            f"LIMIT = {job['max_minutes'] * 60 - 90}\n"
            "threading.Thread(target=lambda: (time.sleep(LIMIT), os._exit(3)), daemon=True).start()\n"
            "os.chdir('/kaggle/working')\n"
            "runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'user_script.py'),"
            " run_name='__main__')\n")


def run(spec_path: Path, store: Path) -> dict:
    spec = json.loads(spec_path.read_text())
    job = validate(spec, spec_path.parent)
    base = store / "kaggle" / job["name"]
    if any((p / "job.json").is_file() for p in base.glob("*")):
        st = status(store, job["name"])
        if st["state"] in ("queued", "running"):  # a new version would start a second session beside it
            raise JobError(f"job '{job['name']}' attempt {st['attempt']} is still {st['state']}: "
                           f"wait for it (`kaggle wait {job['name']}`) or use another name")
    who = user()
    attempt = 1 + max((int(p.name) for p in base.glob("*") if p.name.isdigit()), default=0)
    work = base / str(attempt)
    data, kernel = work / "data", work / "kernel"
    data.mkdir(parents=True)
    kernel.mkdir()
    if job["kind"] == "infer":
        shutil.copy(job["inputs"], data / "prompts.jsonl")
    else:
        for p in map(Path, job["inputs"]):
            (shutil.copytree if p.is_dir() else shutil.copy)(p, data / p.name)
    (data / "job.json").write_text(json.dumps({k: job[k] for k in ("name", "kind", "max_minutes")}))
    dataset = f"{who}/sysop-in-{job['name']}"
    _push_dataset(dataset, data, attempt)
    render(job, kernel)
    sources = [dataset]
    if job["kind"] == "infer" and job.get("engine") != "build" and _dataset_ready(f"{who}/{ENGINE_CACHE}"):
        sources.append(f"{who}/{ENGINE_CACHE}")  # skip building llama.cpp
    meta = {"id": f"{who}/sysop-job-{job['name']}", "title": f"sysop-job-{job['name']}", "code_file": "run.py",
            "language": "python", "kernel_type": "script", "is_private": True,
            "enable_gpu": job["accelerator"] == "gpu", "enable_internet": True, "dataset_sources": sources,
            "competition_sources": [], "kernel_sources": [], "model_sources": []}
    if job["accelerator"] == "gpu":
        meta["machine_shape"] = "NvidiaTeslaT4"
    (kernel / "kernel-metadata.json").write_text(json.dumps(meta, indent=2))
    proc = cli("kernels", "push", "-p", str(kernel))
    if proc.returncode != 0 or "successfully pushed" not in proc.stdout:
        raise JobError(f"kernel push failed: {(proc.stderr or proc.stdout).strip()[-300:]}")
    record = {"name": job["name"], "attempt": attempt, "kernel": meta["id"], "dataset": dataset,
              "kind": job["kind"], "model": job.get("model"), "accelerator": job["accelerator"],
              "max_minutes": job["max_minutes"], "rows": job.get("rows"), "engine_cached": len(sources) > 1,
              "pushed_at": int(time.time()), "url": f"https://www.kaggle.com/code/{meta['id']}"}
    (work / "job.json").write_text(json.dumps(record, indent=2))
    return record


def latest(store: Path, name: str) -> tuple[Path, dict]:
    base = store / "kaggle" / name
    attempts = sorted((int(p.name) for p in base.glob("*") if p.name.isdigit() and (p / "job.json").is_file()))
    if not attempts:
        raise JobError(f"no job named '{name}' (run `sysop.py kaggle run <spec>` first)")
    work = base / str(attempts[-1])
    return work, json.loads((work / "job.json").read_text())


def status(store: Path, name: str) -> dict:
    work, rec = latest(store, name)
    out = cli("kernels", "status", rec["kernel"], timeout=60)
    found = re.search(r"KernelWorkerStatus\.(\w+)", out.stdout)
    state = STATES.get(found.group(1), found.group(1).lower()) if found else "unknown"
    return {"name": name, "attempt": rec["attempt"], "state": state, "kernel": rec["kernel"],
            "minutes": round((time.time() - rec["pushed_at"]) / 60, 1),
            **({"detail": (out.stderr or out.stdout).strip()[-200:]} if state == "unknown" else {})}


def wait(store: Path, name: str, timeout: float, poll: float) -> dict:
    deadline = time.time() + timeout
    while True:
        st = status(store, name)
        if st["state"] in TERMINAL or time.time() >= deadline:
            return st
        time.sleep(min(poll, max(0.0, deadline - time.time())))


def pull(store: Path, name: str) -> dict:
    work, rec = latest(store, name)
    out = work / "out"
    out.mkdir(exist_ok=True)
    proc = cli("kernels", "output", rec["kernel"], "-p", str(out))
    if proc.returncode != 0:
        raise JobError(f"output download failed: {(proc.stderr or proc.stdout).strip()[-300:]}")
    summary = {"name": name, "attempt": rec["attempt"], "out": str(out),
               "files": sorted(p.name for p in out.iterdir())}
    results = out / "results.jsonl"
    if results.is_file():
        rows = [json.loads(l) for l in results.read_text().splitlines() if l.strip()]
        summary.update(results=len(rows), failed=sum(1 for r in rows if "error" in r), expected=rec.get("rows"))
    if (out / "metrics.json").is_file():
        summary["metrics"] = json.loads((out / "metrics.json").read_text())
    engine, m = out / "llama-server", summary.get("metrics", {})
    if engine.is_file() and engine.stat().st_size > 0 and "ready_s" in m and m.get("gpus") and "warning" not in m:
        # only a build proven on the GPU (served, GPU memory in use) seeds the cache that later jobs attach
        folder = out.parent / "engine"
        folder.mkdir(exist_ok=True)
        shutil.copy(engine, folder / "llama-server")
        ref = f"{rec['kernel'].split('/')[0]}/{ENGINE_CACHE}"
        _push_dataset(ref, folder, rec["attempt"])
        summary["engine_cached"] = ref
    return summary
