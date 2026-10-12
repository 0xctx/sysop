"""The writing gate: mechanical checks for prose that is the product (books, articles, docs).

Errors (exit 1): an unclosed code fence, a relative link or image that points nowhere.
Warnings: heading levels that jump (H1 to H3), leftover TODO/TK/FIXME markers, a doubled word
("the the"). Word count, with progress against an optional target. Pure and stdlib-only.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

PROSE_EXT = (".md", ".markdown", ".mdx", ".txt", ".rst")
FENCE = re.compile(r"^ {0,3}(```|~~~)")
LINK = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
HEADING = re.compile(r"^(#{1,6})\s+\S")
MARKER = re.compile(r"\b(TODO|TK|FIXME|XXX)\b")
DOUBLED = re.compile(r"\b([A-Za-z]{2,})\s+\1\b", re.IGNORECASE)
DOUBLE_OK = {"had", "that", "is", "bye", "no", "so"}  # "had had", "that that": English allows these
WORD = re.compile(r"[A-Za-z0-9’']+")
CODE_SPAN = re.compile(r"`[^`]*`")  # inline code is not prose: no marker or doubled-word findings inside it


def tracked(root: Path) -> list[Path]:
    out = subprocess.run(["git", "ls-files"], cwd=str(root), capture_output=True, text=True)
    names = out.stdout.splitlines() if out.returncode == 0 else [str(p.relative_to(root)) for p in root.rglob("*")]
    return [root / n for n in names if n.lower().endswith(PROSE_EXT) and (root / n).is_file()]


def check_file(path: Path, root: Path) -> tuple[list[dict], int]:
    findings, words, fence_open, last_level = [], 0, None, 0
    rel = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
    lines = path.read_text(errors="replace").splitlines()
    markdown = path.suffix.lower() in (".md", ".markdown", ".mdx")
    for n, line in enumerate(lines, 1):
        if markdown and FENCE.match(line):
            fence_open = None if fence_open else n
            continue
        if fence_open:
            continue  # code is not prose
        words += len(WORD.findall(line))
        if markdown:
            heading = HEADING.match(line)
            if heading:
                level = len(heading.group(1))
                if last_level and level > last_level + 1:
                    findings.append({"file": rel, "line": n, "level": "warning",
                                     "issue": f"heading jumps from H{last_level} to H{level}"})
                last_level = level
            for target in LINK.findall(line):
                if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.IGNORECASE) or target.startswith("#"):
                    continue  # a URL, mailto or in-page anchor
                local = target.split("#")[0].split("?")[0]
                if local and not (path.parent / local).exists():
                    findings.append({"file": rel, "line": n, "level": "error", "issue": f"broken link: {target}"})
        prose = CODE_SPAN.sub(" ", line) if markdown else line
        marker = MARKER.search(prose)
        if marker:
            findings.append({"file": rel, "line": n, "level": "warning", "issue": f"leftover {marker.group(1)} marker"})
        for doubled in DOUBLED.finditer(prose):
            if doubled.group(1).lower() not in DOUBLE_OK:
                findings.append({"file": rel, "line": n, "level": "warning",
                                 "issue": f'doubled word: "{doubled.group(0)}"'})
    if fence_open:
        findings.append({"file": rel, "line": fence_open, "level": "error", "issue": "code fence never closed"})
    return findings, words


def check(root: Path, paths: list[str] | None = None, target: int | None = None) -> dict:
    files = [Path(root, p) for p in paths] if paths else tracked(root)
    findings, words = [], 0
    for f in files:
        if f.is_dir():
            subs = [p for p in f.rglob("*") if p.suffix.lower() in PROSE_EXT]
        else:
            subs = [f]
        for s in subs:
            found, n = check_file(s, root)
            findings += found
            words += n
    out = {"files": len(files), "words": words, "errors": sum(f["level"] == "error" for f in findings),
           "warnings": sum(f["level"] == "warning" for f in findings), "findings": findings}
    if target:
        out["target"] = target
        out["progress"] = f"{words:,} of {target:,} words ({round(100 * words / target)}%)"
    return out


def render(result: dict) -> str:
    lines = [f"{f['file']}:{f['line']}: {f['level']}: {f['issue']}" for f in result["findings"]]
    summary = (f"prose-check: {result['errors']} error(s), {result['warnings']} warning(s), "
               f"{result['words']:,} words in {result['files']} file(s)")
    if result.get("progress"):
        summary += f"; {result['progress']}"
    return "\n".join([*lines, summary])
