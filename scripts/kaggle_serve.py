"""Session-scoped serving on Kaggle: one model, one account, keyed, and it ends by itself.

`up` pushes a private notebook (scripts/kaggle/serve.py on common.py) that serves one model behind
a keyed proxy on the operator's ngrok static domain, waits until it answers, and writes pi's
`kaggle` provider. The notebook stops on `down`, after `idle_minutes` without use, or at
`max_hours`; nothing restarts it. Personal use under Kaggle's terms: never shared, never farmed.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import secrets
import shutil
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import kaggle_jobs as kj

KERNEL, TOKEN_SET = "sysop-serve", "sysop-tunnel"
FIT_GB = 24  # 2x T4 is 30 GB; leave room for the KV cache
SERVE_SLOTS, SERVE_CTX = 2, 32768  # agents need long contexts more than many slots
QUANTS = ("q4_k_m", "q4_0", "q4_k_s", "iq4_xs", "q5_k_m")  # preferred GGUF files for `add`, in order


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "sysop"


def state_path() -> Path:
    return cache_dir() / "serve.json"


def key_path() -> Path:
    return cache_dir() / "serve.key"  # guard-protected: an agent never reads it


def load_state() -> dict | None:
    try:
        return json.loads(state_path().read_text())
    except (OSError, json.JSONDecodeError):
        return None


def save_state(state: dict | None, key: str | None = None) -> None:
    cache_dir().mkdir(parents=True, exist_ok=True)
    if state is None:
        for p in (state_path(), key_path()):
            p.unlink(missing_ok=True)
        return
    state_path().write_text(json.dumps(state, indent=2))
    if key is not None:
        key_path().write_text(key)
        key_path().chmod(0o600)


def is_up(model: str) -> bool:
    """Offline check for routing: this model was healthy and its session is within the cap."""
    st = load_state()
    return bool(st and st.get("status") == "up" and st.get("model") == model and time.time() < st.get("cap_at", 0))


def entries(settings: dict) -> dict:
    return {**kj.models(), **(settings.get("serve", {}).get("models") or {})}


def base_url(domain: str) -> str:
    return os.environ.get("SYSOP_SERVE_URL") or f"https://{domain}"  # tests point it at a local server


def call(url: str, path: str, key: str | None = None, method: str = "GET", timeout: float = 15) -> tuple[int, dict]:
    headers = {"ngrok-skip-browser-warning": "1", **({"Authorization": f"Bearer {key}"} if key else {})}
    req = urllib.request.Request(url + path, method=method, headers=headers, data=b"{}" if method == "POST" else None)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.status, json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, {}
    except (OSError, ValueError):
        return 0, {}


def pi_models_path() -> Path:
    base = os.environ.get("PI_CODING_AGENT_DIR") or os.path.join(os.path.expanduser("~"), ".pi", "agent")
    return Path(base) / "models.json"


def read_pi_models() -> dict:
    path = pi_models_path()
    try:
        return json.loads(path.read_text()) if path.is_file() else {}
    except json.JSONDecodeError as exc:  # the operator's file: never overwrite what we cannot read
        raise kj.JobError(f"{path} is not valid JSON ({exc.msg}, line {exc.lineno}); fix it, then retry") from exc


def write_provider(url: str, key: str, mid: str, entry: dict) -> None:
    """pi's `kaggle` provider for this session; every other provider in the file is left as it was."""
    path = pi_models_path()
    data = read_pi_models()
    data.setdefault("providers", {})["kaggle"] = {
        "baseUrl": f"{url}/v1", "api": "openai-completions", "apiKey": key,
        "headers": {"ngrok-skip-browser-warning": "1"},
        "models": [{"id": mid, "name": f"{mid} (Kaggle 2xT4)", "contextWindow": SERVE_CTX, "maxTokens": 8192,
                    "reasoning": bool(entry.get("reasoning"))}]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))
    path.chmod(0o600)


def remove_provider() -> None:
    path = pi_models_path()
    if path.is_file():
        data = read_pi_models()
        if (data.get("providers") or {}).pop("kaggle", None) is not None:
            path.write_text(json.dumps(data, indent=2))


def token() -> dict:
    """Upload the ngrok token (NGROK_AUTHTOKEN) as the private dataset the notebook attaches."""
    value = os.environ.get("NGROK_AUTHTOKEN", "").strip()
    if not value:
        raise kj.JobError("NGROK_AUTHTOKEN is not set: copy it from dashboard.ngrok.com (Your Authtoken), then "
                          "in your own terminal: NGROK_AUTHTOKEN=… python3 scripts/sysop.py serve token")
    ref = f"{kj.user()}/{TOKEN_SET}"
    folder = Path(tempfile.mkdtemp(prefix="sysop-tunnel-"))
    try:
        (folder / "ngrok.token").write_text(value)
        (folder / "ngrok.token").chmod(0o600)
        kj._push_dataset(ref, folder, 1)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return {"dataset": ref, "private": True, "stored": "ngrok.token (never printed)"}


def kernel_state(ref: str) -> str:
    out = kj.cli("kernels", "status", ref, timeout=60)
    found = re.search(r"KernelWorkerStatus\.(\w+)", out.stdout)
    return kj.STATES.get(found.group(1), found.group(1).lower()) if found else "unknown"


def up(settings: dict, model: str | None = None, run: str | None = None) -> dict:
    cfg = settings.get("serve") or {}
    domain = cfg.get("domain") or ""
    if not domain:
        raise kj.JobError("serve.domain is not set: claim your free static domain at dashboard.ngrok.com, then "
                          "`sysop.py config set serve.domain <name>.ngrok-free.dev`")
    mid = model or cfg.get("model") or "qwen3.6-35b-a3b"
    entry = entries(settings).get(mid)
    if not entry:
        raise kj.JobError(f"model '{mid}' is not in the serve list ({', '.join(entries(settings))}); "
                          "`sysop.py serve add <hf repo>` adds one")
    if entry.get("engine", "llama.cpp") != "llama.cpp":
        raise kj.JobError(f"{mid}: engine {entry['engine']} is not enabled yet (llama.cpp only until measured)")
    st, url = load_state(), base_url(domain)
    if st and st.get("status") == "up" and key_path().is_file():
        code, live = call(url, "/sysop/status", key_path().read_text())
        if code == 200 and live.get("model") == st.get("model"):
            return {**st, "already_up": True, **live}
    who = kj.user()
    ref = f"{who}/{KERNEL}"
    if kernel_state(ref) in ("queued", "running"):
        raise kj.JobError(f"a previous session ({ref}) is still running; it stops by itself after "
                          f"{cfg.get('idle_minutes', 20)} idle minutes. `serve down` stops it now if it answers")
    if not kj._dataset_ready(f"{who}/{TOKEN_SET}"):
        raise kj.JobError("no ngrok token on Kaggle yet: run `sysop.py serve token` in your own terminal")
    read_pi_models()  # refuse now rather than after a 3-minute start
    key = secrets.token_urlsafe(24)
    spec = {**entry, "id": mid, "name": mid, "key": key, "domain": domain, "accelerator": "gpu", "engine": "auto",
            "parallel": SERVE_SLOTS, "ctx": SERVE_CTX, "idle_minutes": cfg.get("idle_minutes", 20),
            "max_hours": cfg.get("max_hours", 4)}
    sources = [f"{who}/{TOKEN_SET}"] + ([f"{who}/{kj.ENGINE_CACHE}"] if kj._dataset_ready(f"{who}/{kj.ENGINE_CACHE}")
                                        else [])
    kernel = Path(tempfile.mkdtemp(prefix="sysop-serve-"))
    try:  # the rendered notebook holds the session key: it lives only in Kaggle's private copy
        (kernel / "run.py").write_text(kj.notebook("serve.py", spec))
        (kernel / "kernel-metadata.json").write_text(json.dumps({
            "id": ref, "title": KERNEL, "code_file": "run.py", "language": "python", "kernel_type": "script",
            "is_private": True, "enable_gpu": True, "enable_internet": True, "machine_shape": "NvidiaTeslaT4",
            "dataset_sources": sources, "competition_sources": [], "kernel_sources": [], "model_sources": []}))
        proc = kj.cli("kernels", "push", "-p", str(kernel))
    finally:
        shutil.rmtree(kernel, ignore_errors=True)
    if proc.returncode != 0 or "successfully pushed" not in proc.stdout:
        raise kj.JobError(f"kernel push failed: {(proc.stderr or proc.stdout).strip()[-300:]}")
    now = time.time()
    st = {"model": mid, "kernel": ref, "domain": domain, "url": url, "status": "starting", "started": now,
          "cap_at": now + spec["max_hours"] * 3600, "idle_minutes": spec["idle_minutes"], "run": run,
          "cap_iso": dt.datetime.fromtimestamp(now + spec["max_hours"] * 3600).isoformat(timespec="minutes")}
    save_state(st, key)
    return wait_healthy(settings, st, key)


def wait_healthy(settings: dict, st: dict, key: str) -> dict:
    deadline = time.time() + (settings.get("serve") or {}).get("wait_minutes", 12) * 60
    poll = float(os.environ.get("SYSOP_SERVE_POLL", "10"))
    while time.time() < deadline:
        code, live = call(st["url"], "/sysop/status", key)
        if code == 200 and live.get("ready"):
            write_provider(st["url"], key, st["model"], entries(settings).get(st["model"], {}))
            st.update(status="up", ready_s=round(time.time() - st["started"]))
            save_state(st)
            return {**st, "pi": f"pi:kaggle/{st['model']}"}
        state = kernel_state(st["kernel"])
        if state in kj.TERMINAL:
            save_state(None)
            raise kj.JobError(f"the serving notebook ended ({state}) before it answered; "
                              f"`kaggle kernels output {st['kernel']} -p <dir>` has its metrics and server.log")
        time.sleep(poll)
    return {**st, "warning": "not answering yet; `serve status` keeps checking, and an unused session ends by itself"}


def status(settings: dict) -> dict:
    st = load_state()
    if not st:
        return {"status": "down"}
    code, live = call(st["url"], "/sysop/status", key_path().read_text() if key_path().is_file() else None)
    if code == 200:
        if live.get("ready") and st["status"] != "up":
            return wait_healthy(settings, st, key_path().read_text())
        return {**st, **live}
    state = kernel_state(st["kernel"])
    if state in kj.TERMINAL:  # ended by itself (idle, cap) or failed: clear what routes and pi rely on
        save_state(None)
        remove_provider()
        return {"status": "down", "ended": state, "model": st["model"]}
    return {**st, "kernel_state": state, "note": "not answering yet"}


def down() -> dict:
    st = load_state()
    if not st:
        return {"status": "down", "note": "nothing was up"}
    key = key_path().read_text() if key_path().is_file() else None
    code, _ = call(st["url"], "/sysop/down", key, method="POST")
    state = "unknown"
    for _ in range(int(os.environ.get("SYSOP_SERVE_DOWN_POLLS", "12"))):
        state = kernel_state(st["kernel"])
        if state in kj.TERMINAL:
            break
        time.sleep(float(os.environ.get("SYSOP_SERVE_POLL", "10")))
    save_state(None)
    remove_provider()
    out = {"status": "down", "model": st["model"], "kernel_state": state,
           "minutes": round((time.time() - st["started"]) / 60, 1)}
    if code != 200 and state not in kj.TERMINAL:
        out["note"] = f"the notebook did not answer; it stops by itself after {st.get('idle_minutes', 20)} idle minutes"
    return out


def listing(settings: dict) -> list[str]:
    st = load_state() or {}
    lines = []
    for mid, e in entries(settings).items():
        speed = f"{e['tok_s']} tok/s" if e.get("tok_s") else "not measured"
        live = "  [up]" if st.get("model") == mid and st.get("status") == "up" else ""
        lines.append(f"{mid:<22} {e['repo']}/{e['file']}  ({e.get('license', '?')}; {speed}){live}")
    return lines


def add(settings_file: Path, repo: str, mid: str | None = None) -> dict:
    """Pick a 4-bit GGUF from a Hugging Face repo that fits 2xT4 and record it as a user serve entry."""
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise kj.JobError("want a Hugging Face repo id like unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF")
    api = os.environ.get("SYSOP_HF_API", "https://huggingface.co/api")
    try:
        with urllib.request.urlopen(f"{api}/models/{repo}/tree/main", timeout=30) as res:
            tree = json.loads(res.read())
        with urllib.request.urlopen(f"{api}/models/{repo}/revision/main", timeout=30) as res:
            info = json.loads(res.read())
    except (OSError, ValueError) as exc:
        raise kj.JobError(f"cannot read {repo} from Hugging Face: {exc}") from exc
    ggufs = {f["path"]: f.get("size", 0) for f in tree if f.get("path", "").endswith(".gguf")
             and "mmproj" not in f["path"].lower() and not re.search(r"-\d{5}-of-\d{5}", f["path"])}
    pick = next((p for q in QUANTS for p in sorted(ggufs) if q in p.lower()), None)
    if not pick:
        raise kj.JobError(f"{repo}: no single-file 4-bit GGUF ({', '.join(QUANTS)}) found")
    gb = ggufs[pick] / 1e9
    if gb > FIT_GB:
        raise kj.JobError(f"{repo}/{pick} is {gb:.1f} GB; 2xT4 fits about {FIT_GB} GB with room for context")
    mid = mid or re.sub(r"[^a-z0-9.-]+", "-", repo.split("/")[1].lower().replace("-gguf", "")).strip("-")
    entry = {"repo": repo, "file": pick, "license": (info.get("cardData") or {}).get("license", "?"),
             "size_gb": round(gb, 1), "flags": kj.models()["gemma-4-26b-a4b"]["flags"],
             "source": f"serve add {dt.date.today().isoformat()}: not measured"}
    data = json.loads(settings_file.read_text()) if settings_file.is_file() else {}
    data.setdefault("serve", {}).setdefault("models", {})[mid] = entry
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_text(json.dumps(data, indent=2))
    return {"id": mid, **entry, "settings": str(settings_file)}
