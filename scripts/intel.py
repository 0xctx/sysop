"""Model intel: what each runnable model is, from public facts and benchmarks.

Pure functions plus three fetchers; sysop.py owns the cache file and every write.
Sources: OpenRouter's public model list (release date, context, reasoning, price; no key),
Artificial Analysis benchmarks (pi-free's bundled snapshot, or live with
ARTIFICIAL_ANALYSIS_API_KEY; attribution: https://artificialanalysis.ai/), and sysop's
own probe latency. Every fetch is bounded and may fail; callers keep what they had.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path

OPENROUTER_URL = "https://openrouter.ai/api/v1/models"
AA_URL = "https://artificialanalysis.ai/api/v2/data/llms/models"
AA_ATTRIBUTION = "Artificial Analysis (artificialanalysis.ai)"
TIMEOUT = 10
FIXTURES = "SYSOP_INTEL_FIXTURES"  # tests only: read <dir>/<source>.json instead of the network
# words AA appends for effort/reasoning variants of one model (anything else is another model)
VARIANT_WORDS = {"high", "low", "medium", "minimal", "xhigh", "max", "reasoning", "non", "adaptive",
                 "effort", "thinking", "preview"}
EFFORT_SUFFIX = re.compile(r"-(high|low|medium|minimal|thinking)$")
FREE_PROVIDERS = {"google"}  # pi's google provider with an AI Studio key is the free tier


# --- fetching ------------------------------------------------------------------

def _get_json(url: str, headers: dict | None = None):
    req = urllib.request.Request(url, headers={"User-Agent": "sysop", **(headers or {})})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 - fixed https URLs
        return json.loads(resp.read().decode())


def _fixture(name: str):
    base = os.environ.get(FIXTURES)
    if not base:
        return None
    path = Path(base) / f"{name}.json"
    if not path.is_file():
        raise OSError(f"no fixture {path}")
    return json.loads(path.read_text())


def fetch_openrouter() -> dict:
    """{normalized id: {id, released, ctx, reasoning, free}}."""
    data = _fixture("openrouter")
    if data is None:
        data = _get_json(OPENROUTER_URL)
    out = {}
    for m in data.get("data", []):
        mid = str(m.get("id", ""))
        price = str((m.get("pricing") or {}).get("completion", ""))
        out[norm(mid)] = {"id": mid.removesuffix(":free"), "released": m.get("created"),
                          "ctx": m.get("context_length"),
                          "reasoning": "reasoning" in (m.get("supported_parameters") or []),
                          "free": mid.endswith(":free") or price in ("0", "0.0")}
    return out


def pi_free_dir() -> Path | None:
    if not shutil.which("pi"):
        return None
    try:
        out = subprocess.run(["pi", "list"], capture_output=True, text=True, timeout=30,
                             stdin=subprocess.DEVNULL, env={**os.environ, "AUTO_FALLBACK": "false"}).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = re.sub(r"\x1b\][^\x07\x1b]*(\x07|\x1b\\)|\x1b\[[0-9;?]*[A-Za-z]", "", out).splitlines()  # colour, links
    for i, line in enumerate(lines):
        if line.strip() == "npm:pi-free" and i + 1 < len(lines):
            return Path(lines[i + 1].strip())
    return None


def load_aa_bundled() -> tuple[dict, str]:
    """pi-free's Artificial Analysis snapshot: ({key: scores}, snapshot date)."""
    data = _fixture("benchmarks")
    if data is None:
        base = pi_free_dir()
        path = base / "dist" / "provider-failover" / "benchmarks.json" if base else None
        if not path or not path.is_file():
            raise OSError("pi-free's benchmark snapshot not found (pi-free not installed)")
        data = json.loads(path.read_text())
    out, newest = {}, ""
    for key, v in data.items():
        out[key.lower()] = {"coding": v.get("codingIndex"), "gpqa": v.get("gpqa"), "hle": v.get("hle")}
        newest = max(newest, str(v.get("lastUpdated") or ""))
    return out, newest


def fetch_aa_live(key: str) -> dict:
    data = _fixture("aa_live")
    if data is None:
        data = _get_json(AA_URL, {"x-api-key": key})
    out = {}
    for m in data.get("data", []):
        ev = m.get("evaluations") or {}
        out[str(m.get("slug", "")).lower()] = {
            "coding": ev.get("artificial_analysis_coding_index"),
            "intelligence": ev.get("artificial_analysis_intelligence_index"),
            "gpqa": ev.get("gpqa"), "hle": ev.get("hle"),
            "tps": m.get("median_output_tokens_per_second"), "aa_released": m.get("release_date")}
    return out


# --- matching ------------------------------------------------------------------

def norm(model_id: str) -> str:
    return model_id.lower().removesuffix(":free").replace(".", "-")


def split_entry(entry: str) -> tuple[str, str]:
    worker, _, model = entry.partition(":")
    return worker, model.split("@")[0]


def openrouter_match(entry: str, orouter: dict) -> dict | None:
    worker, model = split_entry(entry)
    candidates = []
    if worker == "pi":
        provider, _, rest = model.partition("/")
        candidates = [model] if provider in FREE_PROVIDERS else [rest]
    elif worker == "agy":
        base = EFFORT_SUFFIX.sub("", model)
        vendor = {"google": "google", "anthropic": "anthropic", "openai": "openai"}
        prefix = next((v for k, v in vendor.items() if k in vendor_hint(base)), None)
        candidates = [f"{prefix}/{base}"] if prefix else []
    elif worker == "claude":
        pat = re.compile(rf"^anthropic/claude-{re.escape(model)}-[\d-]+$")
        hits = [v for k, v in orouter.items() if pat.match(k)]
        return max(hits, key=lambda v: v.get("released") or 0) if hits else None
    for c in candidates:
        if norm(c) in orouter:
            return orouter[norm(c)]
    return None


def vendor_hint(model: str) -> str:
    m = model.lower()
    return ("anthropic" if "claude" in m else "google" if ("gemini" in m or "gemma" in m)
            else "openai" if "gpt" in m else "")


def basename(entry: str, orouter_hit: dict | None) -> str:
    """The model's own name, the way benchmark tables spell it (dots kept)."""
    if orouter_hit:
        return orouter_hit["id"].split("/")[-1].lower()
    _, model = split_entry(entry)
    return EFFORT_SUFFIX.sub("", model.split("/")[-1].lower().removesuffix(":free"))


def aa_match(name: str, aa: dict) -> dict | None:
    """The best effort/reasoning variant of exactly this model (never a sibling like -lite)."""
    if (aa.get(name) or {}).get("coding") is not None:  # an exact benchmark always wins over a variant's
        return aa[name]
    hits = []
    for key, v in aa.items():
        if key == name or (key.startswith(name + "-")
                           and set(key[len(name) + 1:].split("-")) <= VARIANT_WORDS):
            hits.append(v)
    scored = [h for h in hits if h.get("coding") is not None] or hits
    return max(scored, key=lambda h: h.get("coding") or -1) if scored else None


def aa_base(key: str) -> str:
    """An AA key without its effort/reasoning variant words."""
    parts = key.split("-")
    while len(parts) > 1 and parts[-1] in VARIANT_WORDS:
        parts.pop()
    return "-".join(parts)


def aa_predecessor(name: str, aa: dict) -> str | None:
    """The newest benchmarked release of the same line older than `name` (snapshots lag releases)."""
    line, version = line_and_version(name)
    # same line, older, and the same major version: a generation back is a different model
    older = {b for b in map(aa_base, aa) if line_and_version(b)[0] == line and () < line_and_version(b)[1] < version
             and line_and_version(b)[1][:1] == version[:1]}
    return max(older, key=lambda b: line_and_version(b)[1], default=None)


def facts_for(entry: str, orouter: dict, aa: dict, meta: dict | None = None) -> dict:
    hit = openrouter_match(entry, orouter)
    worker, model = split_entry(entry)
    facts: dict = {}
    if hit:
        facts.update({k: hit[k] for k in ("released", "ctx", "reasoning", "free") if hit.get(k) is not None})
    if meta:  # pi's own listing: context and thinking support as this CLI serves the model
        facts.setdefault("ctx", meta.get("ctx"))
        if meta.get("thinking") is not None:
            facts["reasoning"] = meta["thinking"]
    if model.endswith(":free") or (worker == "pi" and model.split("/")[0] in FREE_PROVIDERS):
        facts["free"] = True
    name = basename(entry, hit)
    scores = aa_match(name, aa)
    if not scores and (pred := aa_predecessor(name, aa)):
        scores, facts["benchmarks_of"] = aa_match(pred, aa), pred  # a newer release is presumed no worse
    if scores:
        facts.update({k: v for k, v in scores.items() if v is not None})
    return facts


# --- scoring -------------------------------------------------------------------

def _step(value, cuts) -> int | None:
    if value is None:
        return None
    for score, cut in zip((5, 4, 3, 2), cuts):
        if value >= cut:
            return score
    return 1


def speed_score(facts: dict, latency: float | None) -> int | None:
    if facts.get("tps"):
        return _step(facts["tps"], (200, 120, 60, 30))
    if latency is not None:  # seconds for a one-line answer, CLI start-up included
        return 5 if latency <= 10 else 4 if latency <= 20 else 3 if latency <= 40 else 2 if latency <= 80 else 1
    return None


def infer_entry(facts: dict, latency: float | None = None) -> dict:
    """A catalog entry from facts; a skill with no evidence is left out (not a candidate)."""
    code = _step(facts.get("coding"), (70, 60, 45, 30))
    reasoning = _step(facts.get("intelligence"), (70, 60, 45, 30)) or _step(facts.get("gpqa"), (.90, .85, .75, .60))
    skills = {"code": code, "reasoning": reasoning,
              "review": round((code + reasoning) / 2) if code and reasoning else None,
              "writing": reasoning,  # no public writing benchmark: reasoning as a proxy
              "long": _step(facts.get("ctx"), (1_000_000, 256_000, 128_000, 32_000))}
    index = {"code": facts.get("coding"),
             "reasoning": facts.get("intelligence") or (facts["gpqa"] * 100 if facts.get("gpqa") else None)}
    index["review"] = (index["code"] + index["reasoning"]) / 2 if index["code"] and index["reasoning"] else None
    entry = {"cost": "free" if facts.get("free") else facts.get("cost", "paid"),
             "skills": {k: v for k, v in skills.items() if v},
             "index": {k: round(v, 1) for k, v in index.items() if v},  # raw benchmark, breaks score ties
             "source": (f"inferred; benchmarks of {facts['benchmarks_of']} (not benchmarked yet)"
                        if facts.get("benchmarks_of") else "inferred"),
             "notes": "writing is a reasoning proxy"}
    speed = speed_score(facts, latency)
    if speed:
        entry["speed"] = speed
    if facts.get("ctx"):
        entry["ctx"] = facts["ctx"]
    return entry


# --- model lines ---------------------------------------------------------------

# a version is a number not glued to a following letter: 3.8 in qwen3.8 or gemini-3.8, never 31b or a12b
VERSION_RE = re.compile(r"(?<![\d.])\d+(?:\.\d+)*(?![\d.]*[a-z])")


def line_and_version(model: str) -> tuple[str, tuple]:
    """`gemini-3.5-flash` -> ('gemini-#-flash', (3, 5)); sizes such as 31b stay part of the line."""
    name = EFFORT_SUFFIX.sub("", model.lower().removesuffix(":free"))
    versions = [tuple(int(p) for p in m.group().split(".")) for m in VERSION_RE.finditer(name)]
    line = VERSION_RE.sub("#", name)
    return line, tuple(n for v in versions for n in v)


def model_key(entry: str) -> str:
    """The model behind an entry, whichever CLI or provider is the door to it:
    pi:google/gemini-3.8-flash, agy:gemini-3.8-flash-high and pi:openrouter/google/gemini-3.8-flash
    are one model. Names are compared exactly once normalized, never fuzzily."""
    _, model = split_entry(entry)
    name = model.lower().split("/")[-1].removesuffix(":free")
    return re.sub(r"[._]", "-", EFFORT_SUFFIX.sub("", name))


def variant_base(entry: str) -> str:
    """One model whatever its listed effort variant (agy lists -high, -medium and -low)."""
    worker, model = split_entry(entry)
    return f"{worker}:{EFFORT_SUFFIX.sub('', model)}"


def newer_than(old: str, new: str, intel: dict) -> bool:
    """`new` is a later release of the same model line, same CLI and provider."""
    (ow, om), (nw, nm) = split_entry(old), split_entry(new)
    provider = lambda m: m.rsplit("/", 1)[0] if "/" in m else ""  # noqa: E731
    if ow != nw or provider(om) != provider(nm):
        return False
    oline, over = line_and_version(om.split("/")[-1])
    nline, nver = line_and_version(nm.split("/")[-1])
    if oline != nline or not over or nver <= over:
        return False
    odate = (intel.get(old) or {}).get("released")
    ndate = (intel.get(new) or {}).get("released")
    return not (odate and ndate and ndate <= odate)
