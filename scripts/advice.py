"""Model intelligence, the opt-in layer: benchmark and price facts, catalog scoring, auto chains,
doors, latency, budget, follow_latest, advice and learning from the ledger.

Core routing (ordered tiers, cooldowns, recorded fallback, cross-vendor filtering) never imports this
module; `auto:<skill>` routes, the door fallback and `models` load it on demand. Hooks never load it.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

import intel  # scripts/intel.py: model facts and scoring (pure; this module owns the cache)
import kaggle_serve  # scripts/kaggle_serve.py: a served Kaggle model scores as free
import repo_profile  # scripts/repo_profile.py: what a repository is (pure; this module owns the cache)

from store import (
    _read_json, deep_merge, ensure_store, git, intel_path, load_intel, PLUGIN_ROOT, settings_path, store_path,
    SysopError, write_json,
)
from workers import (
    active_cooldowns, cooling_for, entry_state, family, kaggle_down, KIND_SKILL, ledger_summary, PLAN_WORKERS,
    ROSTER_TTL, runnable, SIZES, spend_path,
)


def current_intel(settings: dict, roster: dict, cwd: str | None = None) -> dict:
    """The cached facts, refreshed once they are a day old: the layer refreshes only when it is used."""
    data = load_intel()
    if time.time() - float(data.get("checked_at") or 0) < ROSTER_TTL:
        return data
    try:
        return refresh_intel(settings, roster, cwd)
    except Exception as exc:  # noqa: BLE001 - offline: score with what we had
        print(f"sysop: model intel not refreshed: {exc}", file=sys.stderr)
        return data


def refresh_intel(settings: dict, roster: dict, cwd: str | None = None) -> dict:
    old = load_intel()
    sources, now = dict(old["sources"]), int(time.time())
    try:
        orouter = intel.fetch_openrouter()
        sources["openrouter"] = {"ok": True, "updated": now}
    except Exception as exc:  # noqa: BLE001 - offline or changed API: keep the facts we had
        orouter = {}
        sources["openrouter"] = {**old["sources"].get("openrouter", {}), "ok": False, "error": str(exc)[:200]}
    aa: dict = {}
    try:
        aa, snapshot = intel.load_aa_bundled()
        sources["aa"] = {"ok": True, "mode": f"bundled pi-free snapshot {snapshot}", "updated": now}
    except Exception as exc:  # noqa: BLE001
        sources["aa"] = {"ok": False, "error": str(exc)[:200]}
    key = os.environ.get("ARTIFICIAL_ANALYSIS_API_KEY")
    if key:
        try:
            aa = {**aa, **intel.fetch_aa_live(key)}
            sources["aa"] = {"ok": True, "mode": "live", "updated": now}
        except Exception as exc:  # noqa: BLE001 - a bad key or limit: the snapshot still serves
            sources["aa_live"] = {"ok": False, "error": str(exc)[:200]}
    models = {}
    for entry in runnable(roster):
        worker, model = intel.split_entry(entry)
        meta = ((roster["workers"].get(worker) or {}).get("meta") or {}).get(model)
        facts = intel.facts_for(entry, orouter, aa, meta)
        if worker in PLAN_WORKERS and not facts.get("free"):
            facts["cost"] = "plan"
        complete = sources["openrouter"]["ok"] and sources.get("aa", {}).get("ok")
        models[entry] = facts if complete else {**old["models"].get(entry, {}), **facts}  # old only fills gaps
    data = {**old, "checked_at": now, "sources": sources, "models": models}
    notes = []
    if old["seen"]:  # the first refresh only learns what exists; afterwards, news is news
        fresh = [e for e in models if e not in set(old["seen"])]
        advice = advise(settings, roster, active_cooldowns(), data, only=fresh, cwd=cwd)
        hits = sorted({e for t in advice["tiers"].values() for e in [*t["better"], *t["newer"].values()]})
        if hits:
            notes.append(f"{len(hits)} new model(s) you can run beat or update your tiers "
                         f"(e.g. {hits[0]}): `sysop.py models advise`")
    if settings.get("follow_latest") is True and cwd:
        notes += follow_latest(settings, roster, data, cwd)
    data["seen"] = sorted(set(old["seen"]) | set(models))
    if notes:
        data["notice"] = "; ".join(notes)
    write_json(intel_path(), data)
    return data


def record_latency(entry: str, seconds: float) -> None:
    data = load_intel()
    cur = data["latency"].get(entry) or {"ema": seconds, "n": 0}
    data["latency"][entry] = {"ema": round(0.7 * cur["ema"] + 0.3 * seconds, 1) if cur["n"] else seconds,
                              "n": cur["n"] + 1}
    intel_path().parent.mkdir(parents=True, exist_ok=True)
    write_json(intel_path(), data)


def newest_in_line(entry: str, candidates: list[str], facts: dict) -> str | None:
    """The latest release of the entry's line; among its listed variants, the same one (-high stays -high)."""
    suffix = lambda e: (intel.EFFORT_SUFFIX.search(intel.split_entry(e)[1]) or [""])[0]  # noqa: E731
    newer = [c for c in candidates if intel.newer_than(entry, c, facts)]
    return max(newer, key=lambda c: (intel.line_and_version(intel.split_entry(c)[1].split("/")[-1])[1],
                                     suffix(c) == suffix(entry)), default=None)


def tier_file(cwd: str, tier: str) -> Path:
    """The settings file that defines a tier: the repo's when it overrides it, else the user's."""
    repo = settings_path(cwd, "repo")
    return repo if tier in (_read_json(repo).get("tiers") or {}) else settings_path(cwd, "user")


def follow_latest(settings: dict, roster: dict, data: dict, cwd: str) -> list[str]:
    """Under the operator's standing grant: same line, same CLI and provider, newer release."""
    done, pool = [], runnable(roster)
    for tier, entries in settings["tiers"].items():
        swapped = []
        for entry in entries:
            base, _, effort = entry.partition("@")
            new = newest_in_line(base, pool, data["models"])
            swapped.append(f"{new}@{effort}" if new and effort else new or entry)
        if swapped != entries:
            path = tier_file(cwd, tier)
            file_data = _read_json(path)
            file_data.setdefault("tiers", {})[tier] = swapped
            path.parent.mkdir(parents=True, exist_ok=True)
            write_json(path, file_data)
            settings["tiers"][tier] = swapped
            for a, b in zip(entries, swapped):
                if a != b:
                    done.append(f"sysop followed {a} -> {b} in {tier} (follow_latest)")
    data["applied"] = (data.get("applied") or []) + [{"at": int(time.time()), "change": d} for d in done]
    return done


COST_RANK = {"free": 0, "plan": 1, "paid": 2}


def load_catalog(settings: dict, roster: dict | None = None, data: dict | None = None) -> dict:
    """Every model sysop can score. Skill scores: benchmarks > the shipped estimates in
    scripts/catalog.json; cost and notes: the shipped catalog; the settings `catalog` beats both.
    A release not benchmarked yet carries its predecessor's benchmarks (scripts/intel.py)."""
    catalog = json.loads((PLUGIN_ROOT / "scripts" / "catalog.json").read_text())["models"]
    if roster is not None:
        data = data if data is not None else load_intel()
        inferred = {e: intel.infer_entry(data["models"].get(e) or {}, (data["latency"].get(e) or {}).get("ema"))
                    for e in runnable(roster)}
        for entry, inf in inferred.items():
            if entry not in catalog:
                catalog[entry] = {**inf, "cost": "free"} if free_by_name(entry) else inf
            elif inf["index"]:  # evidence replaces the estimate's skill scores, keeps its cost
                cur = catalog[entry]
                measured = {k: v for k, v in inf["skills"].items() if k not in PROXY_SKILLS}
                cur.update(skills={**inf["skills"], **cur.get("skills", {}), **measured}, index=inf["index"],
                           source=f"{cur.get('source', 'catalog')}; skills from benchmarks")
        for entry, lat in data["latency"].items():  # measured beats estimated, once steady
            if entry in catalog and lat.get("n", 0) >= 3:
                catalog[entry]["speed"] = intel.speed_score({}, lat["ema"])
        served = kaggle_serve.entries(settings)
        for entry in runnable(roster):  # a served Kaggle model: free, scored by its list entry unless benchmarked
            worker, _, model = entry.partition(":")
            sid = model[len("kaggle/"):] if worker == "pi" and model.startswith("kaggle/") else None
            if sid in served:
                strengths = served[sid].get("strengths") or {}
                info = catalog.setdefault(entry, {})
                info["skills"] = {**{**strengths, "review": strengths.get("reasoning", 0), "long": 3},
                                  **(info.get("skills") if info.get("index") else {})}
                info.update(cost="free", speed=3, source=f"Kaggle serve list ({served[sid].get('tok_s') or '?'} tok/s)")
    claude_cost = "paid" if os.environ.get("ANTHROPIC_API_KEY") else "plan"  # the login is a subscription
    for entry, info in catalog.items():
        if entry.startswith("claude:"):
            info["cost"] = claude_cost
    return deep_merge(catalog, settings.get("catalog") or {})


PROXY_SKILLS = ("writing",)  # no public benchmark: a reasoning proxy fills gaps, never overrides


TIER_MIN_SPEED = {"fast": 4}  # a fast tier is for quick answers: slower models are not upgrades


NATIVE = {"claude": "anthropic", "agy": "google"}  # the CLI a vendor ships for its own models


def rank_key(info: dict, skill: str, prefer: str, entry: str = "") -> tuple:
    score = (info.get("skills") or {}).get(skill, 0)
    raw = (info.get("index") or {}).get(skill) or score * 15  # an estimate's 5 sits near a benchmark 75
    cost, speed = COST_RANK.get(info.get("cost", "paid"), 2), info.get("speed", 3)
    worker, _, model = entry.partition(":")
    foreign = int(NATIVE.get(worker) != family(model)) if entry else 0  # on a tie, the vendor's own CLI
    return {"quality": (-score, -raw, -speed, foreign), "economy": (cost, -score, -raw, -speed, foreign)}.get(
        prefer, (-score, -raw, -cost, -speed, foreign))  # balanced: best first, paid ahead of free on ties


def door_rank(entry: str, catalog: dict) -> tuple:
    """How to reach one model: free first, then a plan, then per-token; at equal cost the vendor's own
    provider (pi:google for Gemini) before resold pools (cline, openrouter), then the vendor's own CLI."""
    worker, _, model = entry.partition(":")
    model = model.split("@")[0]
    vendor, provider = family(model), (model.split("/")[0] if "/" in model else "")
    cost = 0 if free_by_name(model) else COST_RANK.get((catalog.get(f"{worker}:{model}") or {}).get("cost", "paid"), 2)
    own_provider = family(provider) == vendor if provider else NATIVE.get(worker) == vendor
    pooled = int(entry.startswith(OPT_IN_POOLS))  # pi-free's pools after the legitimate gateways (Kilo, OpenRouter)
    return cost, pooled, int(not own_provider), int(NATIVE.get(worker) != vendor)


def free_by_name(model: str) -> bool:
    """A gateway's free variant says so in its id: `…:free`, `…-free`, `kilo-auto/free`."""
    return model.endswith((":free", "-free", "/free"))


OPT_IN_POOLS = ("pi:cline/",)  # pi-free's resold pools: used only where the operator names them in a tier


def opted_out(entry: str, settings: dict) -> bool:
    """An opt-in pool the operator has not put in any tier: never picked on sysop's own initiative."""
    if not entry.startswith(OPT_IN_POOLS):
        return False
    return intel.variant_base(entry) not in {intel.variant_base(e) for es in settings["tiers"].values() for e in es}


def doors(entry: str, roster: dict, cooling: dict, catalog: dict, settings: dict | None = None) -> list[str]:
    """Other ways to reach the same model that are installed, offered and not cooling, best first."""
    key, effort = intel.model_key(entry), entry.partition("@")[2]
    own = intel.variant_base(entry)
    byvariant: dict = {}
    for e in runnable(roster):
        if intel.model_key(e) != key or intel.variant_base(e) == own or cooling_for(cooling, e) or kaggle_down(e):
            continue
        if settings is not None and opted_out(e, settings):
            continue
        listed = intel.EFFORT_SUFFIX.search(e)  # agy lists one model per effort: keep the one asked for
        best = byvariant.get(intel.variant_base(e))
        if best is None or (listed and listed.group(1) == (effort or "high")):
            byvariant[intel.variant_base(e)] = e
    return [e + (f"@{effort}" if effort else "") for e in sorted(byvariant.values(), key=lambda e: door_rank(e, catalog))]


def superseded(entry: str, pool: list[str], facts: dict) -> bool:
    return any(intel.newer_than(entry, other, facts) for other in pool)


def best_unique(ranked: list[str], cap: int) -> list[str]:
    out, bases = [], set()
    for entry in ranked:
        if intel.variant_base(entry) not in bases:
            bases.add(intel.variant_base(entry))
            out.append(entry)
    return out[:cap]


def auto_chain(settings: dict, roster: dict, skill: str, cap: int = 4) -> list[str]:
    """Rank catalog entries for a skill among what is installed, offered and not cooling."""
    cooling, prefer = active_cooldowns(), settings.get("prefer", "balanced")
    budget = float(settings.get("budget") or 0)
    if budget > 0 and spend_today() >= 0.8 * budget:
        prefer = "economy"  # near the day's budget: free and plan models first
    data, pool = current_intel(settings, roster), runnable(roster)
    candidates = []
    for entry, info in load_catalog(settings, roster, data).items():
        worker, _, model = entry.partition(":")
        state = roster["workers"].get(worker) or {}
        if ((info.get("skills") or {}).get(skill, 0) < 1 or not state.get("available")
                or (state.get("models") is not None and model not in state["models"])
                or cooling_for(cooling, entry) or kaggle_down(entry) or superseded(entry, pool, data["models"])
                or opted_out(entry, settings)):
            continue
        candidates.append((rank_key(info, skill, prefer, entry), entry))
    catalog = load_catalog(settings, roster, data)
    groups: dict = {}  # one model, several doors: rank models by quality, then take each one's best door
    for _, entry in sorted(candidates):
        groups.setdefault(intel.model_key(entry), []).append(entry)
    return [min(doors_, key=lambda e: door_rank(e, catalog)) for doors_ in groups.values()][:cap]


def load_profile(cwd: str, refresh: bool = False) -> dict | None:
    """The repository's profile, cached per HEAD in the store when one exists. None outside git."""
    try:
        head = git(["rev-parse", "HEAD"], cwd)
        root = Path(git(["rev-parse", "--show-toplevel"], cwd))
    except Exception:  # noqa: BLE001 - outside git, or no commit yet
        return None
    cache = store_path(cwd) / "profile.json"  # safe here: inside git
    if not refresh and cache.is_file():
        cached = _read_json(cache)
        if cached.get("head") == head:
            return cached
    try:
        prof = {**repo_profile.profile(root), "head": head}
    except OSError:
        return None
    if cache.parent.is_dir():  # never creates the store just to cache this
        write_json(cache, prof)
    return prof


def cmd_profile(args: argparse.Namespace) -> int:
    prof = load_profile(args.cwd, args.refresh)
    if prof is None:
        raise SysopError("no profile: not inside a git repository with a commit")
    print(json.dumps(prof, indent=2) if args.json else profile_line(prof))
    return 0


def profile_line(prof: dict) -> str:
    langs = ", ".join(f"{l['name']} {round(l['share'] * 100)}%" for l in prof["languages"]) or "no code"
    share = f"{round(prof['prose_share'] * 100)}% prose; " if prof.get("prose_share") is not None else ""
    return f"repo: {prof['kind']} ({share}{langs}), about {_k(prof['tokens'])} tokens"


def _k(n: int) -> str:
    return f"{n / 1_000_000:.1f}M" if n >= 1_000_000 else f"{round(n / 1000)}K"


CONTEXT_TIERS = ("standard", "deep")  # tiers that take big tasks; fast and review stay small


def advise(settings: dict, roster: dict, cooling: dict, data: dict, tier: str | None = None,
           only: list[str] | None = None, cwd: str | None = None) -> dict:
    """For each tier: newer releases of its entries, better runnable models, and what auto would pick.

    With `cwd`, the advice fits that repository: tiers rank by the skill its work is judged by
    (writing for a prose repo), entries whose context is far below the repo's size are flagged,
    and this repo's own ledger (five or more tickets per model) outweighs public benchmarks.
    Suggestions only. `only` limits candidates (the refresh uses it for models that are new today).
    """
    catalog = load_catalog(settings, roster, data)
    prof = load_profile(cwd) if cwd else None
    repo_skill = repo_profile.skill_for(prof["kind"]) if prof else "code"
    groups = ledger_summary(cwd)["groups"] if prof and store_path(cwd).is_dir() else {}  # prof: inside git

    def here(entry: str) -> dict | None:
        g = groups.get(entry.partition("@")[0])
        return g if g and g.get("enough_data") else None

    def local_rate(entry: str) -> float | None:
        g = here(entry)
        return g["landed"] / g["n"] if g else None
    everything = runnable(roster)
    pool = [e for e in (only if only is not None else everything) if not cooling_for(cooling, e)]
    routes_to: dict = {}
    for key, target in settings.get("routes", {}).items():
        routes_to.setdefault(target, []).append(key)

    def score(entry: str, skill: str) -> int:
        base = (catalog.get(entry.partition("@")[0]) or {}).get("skills", {}).get(skill, 0)
        rate = local_rate(entry)  # evidence from this repo beats a benchmark
        return base + (1 if rate is not None and rate >= LOCAL_STRONG else 0)

    def speed(entry: str) -> int:
        return (catalog.get(entry.partition("@")[0]) or {}).get("speed", 3)

    def weak(entry: str) -> bool:
        rate = local_rate(entry)
        return rate is not None and rate < LOCAL_WEAK

    def context_note(entry: str, name: str) -> str | None:
        ctx = (catalog.get(entry.partition("@")[0]) or {}).get("ctx")
        if prof and name in CONTEXT_TIERS and ctx and prof["tokens"] > 100_000 and ctx < prof["tokens"]:
            return f"context {_k(ctx)} < repo about {_k(prof['tokens'])} tokens"
        return None

    out: dict = {"tiers": {}, "kinds": {}, "sources": data.get("sources", {}), "profile": prof}
    if prof and prof["kind"] == "mixed":
        out["writing_pick"] = auto_chain(settings, roster, "writing")[:1]
    for name, entries in settings["tiers"].items():
        if tier and name != tier:
            continue
        skill = "review" if name == "review" else repo_skill
        healthy = [e for e in entries if entry_state(roster, cooling, e) == "ok" and not weak(e)]
        best = max((score(e, skill) for e in healthy), default=0)
        newer = {e: n for e in entries if (n := newest_in_line(e.partition("@")[0], pool, data["models"]))}
        known = {intel.variant_base(e.partition("@")[0]) for e in entries}
        fit = [e for e in pool if intel.variant_base(e) not in known and score(e, skill) > best
               and not weak(e)
               and speed(e) >= TIER_MIN_SPEED.get(name, 0) and not superseded(e, everything, data["models"])]
        better = best_unique(sorted(fit, key=lambda e: (-score(e, skill), rank_key(  # local evidence first
            catalog.get(e) or {}, skill, settings.get("prefer", "balanced"), e))), 3)
        auto = auto_chain(settings, roster, skill)[:3]
        out["tiers"][name] = {
            "skill": skill, "routed_from": routes_to.get(name, []),
            "chain": [{"entry": e, "score": score(e, skill), "speed": speed(e),
                       "state": entry_state(roster, cooling, e), "here": here(e), "weak_here": weak(e),
                       "context": context_note(e, name)} for e in entries],
            "newer": newer,
            "better": better, "better_scores": {e: score(e, skill) for e in better},
            "auto": auto, "auto_beats": bool(auto) and score(auto[0], skill) > best}
    if not tier:
        for key, target in settings.get("routes", {}).items():
            if key in SIZES:
                continue
            skill = KIND_SKILL.get(key, "code")
            out["kinds"][key] = {"target": target, "skill": skill,
                                 "auto": auto_chain(settings, roster, skill)[:3]}
    return out


def render_advice(result: dict) -> str:
    lines = []
    prof = result.get("profile")
    if prof:
        lines.append(profile_line(prof))
        if result.get("writing_pick"):
            lines.append(f"  mixed repo: tiers rank by code; for prose tasks auto:writing starts with "
                         f"{result['writing_pick'][0]} (`models route docs auto:writing`)")
    for name, t in result["tiers"].items():
        frm = f"  <- {', '.join(t['routed_from'])}" if t["routed_from"] else ""
        why = f": {prof['kind']} repo" if prof and t["skill"] != "review" else ""
        lines.append(f"{name}{frm}  (ranked by {t['skill']}{why})")
        width = max([len(c["entry"]) for c in t["chain"]] + [len(e) for e in t["better"]] + [0])
        for c in t["chain"]:
            extra = []
            if c.get("here"):
                h = c["here"]
                extra.append(f"here: landed {h['landed']}/{h['n']}, check {h['check_pass']}/{h['n']}, "
                             f"{h['fallbacks']} fallback(s)" + (" (weak here)" if c.get("weak_here") else ""))
            if c.get("context"):
                extra.append(c["context"])
            lines.append(f"  {c['entry']:<{width}}  {t['skill']} {c['score'] or '-'}  speed {c['speed']}  {c['state']}"
                         + "".join(f"  [{x}]" for x in extra))
        entries = [c["entry"] for c in t["chain"]]
        for old, new in t["newer"].items():
            swapped = " ".join(new + ("@" + old.partition("@")[2] if "@" in old else "") if e == old else e
                               for e in entries)
            lines.append(f"  newer: {old} -> {new}\n    apply: sysop.py models set {name} {swapped}")
        for e in t["better"]:
            lines.append(f"  better: {e} ({t['skill']} {t['better_scores'][e]})"
                         f"\n    apply: sysop.py models set {name} {e} {' '.join(entries)}")
        if t["auto_beats"]:
            keys = t["routed_from"] or ["<size or kind>"]
            lines.append(f"  auto:{t['skill']} would start with {t['auto'][0]}"
                         f"\n    switch: sysop.py models route {keys[0]} auto:{t['skill']}")
        if not (t["newer"] or t["better"] or t["auto_beats"]):
            lines.append("  up to date")
    for key, k in result["kinds"].items():
        mode = "auto" if k["target"].startswith("auto:") else f"tier {k['target']}"
        pick = ", ".join(k["auto"]) or "nothing scored"
        lines.append(f"kind {key} -> {mode}; auto:{k['skill']} now: {pick}")
    aa = result["sources"].get("aa") or {}
    if aa.get("ok"):
        lines.append(f"quality data: {intel.AA_ATTRIBUTION} via {aa.get('mode')}; "
                     "facts: OpenRouter public model list")
    else:  # no benchmark source: say so, never pass estimates off as measurements
        lines.append("quality data: estimates only (scripts/catalog.json), no benchmark source reachable; "
                     "set ARTIFICIAL_ANALYSIS_API_KEY for live benchmarks; facts: OpenRouter public model list")
    return "\n".join(lines)


def spend_today() -> float:
    """Per-token money spent today across every repo (plan and free runs cost nothing here)."""
    today, total = dt.date.today().isoformat(), 0.0
    try:
        for line in spend_path().read_text().splitlines():
            row = json.loads(line)
            if row.get("day") == today and row.get("billed") == "paid":
                total += float(row.get("usd") or 0)
    except (OSError, ValueError):
        pass
    return round(total, 4)


LEARN_MIN = 5  # tickets per model and skill before outcomes count


def learn(cwd: str, settings: dict, roster: dict) -> list[dict]:
    """Score changes this repo's own outcomes support: +1 where a model lands nearly everything,
    -1 where it lands too little. One step at a time, each with its evidence."""
    path = store_path(cwd) / "ledger.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.is_file() else []
    catalog = load_catalog(settings, roster)
    groups: dict = {}
    for r in rows:
        if not r.get("worker") or not r.get("model"):
            continue
        skill = KIND_SKILL.get(r.get("kind") or "", "code")
        g = groups.setdefault((f"{r['worker']}:{r['model']}", skill), {"n": 0, "landed": 0, "ready": 0, "fallbacks": 0})
        g["n"] += 1
        g["landed"] += bool(r.get("landed"))
        g["ready"] += str(r.get("review") or "").strip().upper().startswith("READY")
        g["fallbacks"] += int(r.get("fallbacks") or 0)
    out = []
    for (entry, skill), g in sorted(groups.items()):
        if g["n"] < LEARN_MIN:
            continue
        now = (catalog.get(entry) or {}).get("skills", {}).get(skill)
        if now is None:
            continue
        rate, ready = g["landed"] / g["n"], g["ready"] / g["n"]
        step = 1 if rate >= 0.9 and ready >= 0.5 and now < 5 else -1 if rate < LOCAL_WEAK and now > 1 else 0
        if step:
            out.append({"entry": entry, "skill": skill, "from": now, "to": now + step,
                        "evidence": f"{g['landed']}/{g['n']} landed, {g['ready']} READY first time, "
                                    f"{g['fallbacks']} fallback(s) in this repo"})
    return out


def apply_learned(cwd: str, changes: list[dict]) -> Path:
    path = settings_path(cwd, "user")
    data = _read_json(path)
    for c in changes:
        data.setdefault("catalog", {}).setdefault(c["entry"], {}).setdefault("skills", {})[c["skill"]] = c["to"]
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, data)
    with (ensure_store(cwd) / "runlog.jsonl").open("a") as fh:  # every applied change is on record
        fh.write(json.dumps({"learned": changes, "ts": time.time()}) + "\n")
    return path


LOCAL_WEAK, LOCAL_STRONG = 0.6, 0.8  # landed share here, over at least five tickets
