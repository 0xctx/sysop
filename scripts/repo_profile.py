"""What a repository is, measured mechanically: code, prose or mixed; its languages; its size.

Pure: reads tracked files through git and returns a dict; sysop.py owns the cache. It never
claims per-language model quality: public benchmarks are not broken down that way.
"""
from __future__ import annotations

import random
import subprocess
import time
from pathlib import Path

PROSE_EXT = {".md", ".mdx", ".markdown", ".txt", ".rst", ".adoc", ".tex", ".org", ".fountain"}
LANG_EXT = {
    ".py": "python", ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".go": "go", ".rs": "rust", ".java": "java",
    ".kt": "kotlin", ".kts": "kotlin", ".c": "c", ".h": "c", ".cc": "c++", ".cpp": "c++", ".hpp": "c++",
    ".cs": "c#", ".rb": "ruby", ".php": "php", ".swift": "swift", ".scala": "scala", ".sh": "shell",
    ".bash": "shell", ".sql": "sql", ".html": "html", ".css": "css", ".scss": "css", ".vue": "vue",
    ".svelte": "svelte", ".lua": "lua", ".dart": "dart", ".ex": "elixir", ".exs": "elixir",
    ".hs": "haskell", ".ml": "ocaml", ".r": "r", ".jl": "julia", ".zig": "zig", ".nim": "nim",
}
BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz", ".tar", ".jar",
              ".so", ".dll", ".exe", ".bin", ".woff", ".woff2", ".ttf", ".otf", ".mp3", ".mp4", ".mov",
              ".wav", ".ogg", ".psd", ".sqlite", ".db", ".pyc", ".class", ".o", ".a", ".lock"}
MAX_FILE = 2_000_000  # generated or vendored blobs say little about the repo's kind
BUDGET = 10.0  # seconds; a huge monorepo gets a partial (still representative) profile
PROSE_KIND, CODE_KIND = 0.6, 0.25  # prose share at or above -> prose; at or below -> code


def tracked_files(root: Path) -> list[Path]:
    out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, timeout=60)
    if out.returncode != 0:
        raise OSError(out.stderr.decode(errors="replace").strip() or "git ls-files failed")
    return [root / p for p in out.stdout.decode(errors="replace").split("\0") if p]


def is_text(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return b"\0" not in fh.read(8192)
    except OSError:
        return False


def profile(root: Path, budget: float = BUDGET) -> dict:
    prose = code = other = 0
    langs: dict[str, int] = {}
    files, deadline, seen = tracked_files(root), time.monotonic() + budget, 0
    random.Random(0).shuffle(files)  # a partial profile must sample the whole tree, not docs/ first
    for path in files:
        if time.monotonic() > deadline:
            break
        seen += 1
        ext = path.suffix.lower()
        try:
            size = path.stat().st_size
        except OSError:  # deleted but still tracked
            continue
        known = ext in PROSE_EXT or ext in LANG_EXT  # known text: no need to read it
        if ext in BINARY_EXT or size > MAX_FILE or (not known and not is_text(path)):
            continue
        if ext in PROSE_EXT:
            prose += size
        elif ext in LANG_EXT:
            code += size
            langs[LANG_EXT[ext]] = langs.get(LANG_EXT[ext], 0) + size
        else:
            other += size
    if prose + code == 0:
        kind = "empty" if other == 0 else "other"
    else:
        share = prose / (prose + code)
        kind = "prose" if share >= PROSE_KIND else "code" if share <= CODE_KIND else "mixed"
    top = sorted(langs.items(), key=lambda kv: -kv[1])[:3]
    return {"kind": kind,
            "prose_share": round(prose / (prose + code), 2) if prose + code else None,
            "languages": [{"name": n, "share": round(b / code, 2)} for n, b in top] if code else [],
            "tokens": (prose + code + other) * len(files) // max(seen, 1) // 4,  # ~4 bytes/token; scaled if partial
            "partial": seen < len(files)}


def skill_for(kind: str) -> str:
    """The skill a repo's work is mostly judged by."""
    return "writing" if kind == "prose" else "code"
