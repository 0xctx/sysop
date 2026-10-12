#!/usr/bin/env python3
"""Lint a sysop-style plugin: skill craft, agent tool limits, hook wiring, context budget.

  python3 scripts/lint.py [plugin_root]

Exit 0 when clean (warnings allowed), 1 on any failure. The token budget counts the
description + when_to_use of every model-invocable skill (chars / 4); user-only skills
(`disable-model-invocation: true`) cost no context and are not counted.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

DESC_CAP = 1536          # description + when_to_use, per Claude Code's skill listing
BODY_MAX_LINES = 500
BUDGET_WARN = 1300       # tokens, always-loaded skill descriptions
BUDGET_FAIL = 1500
AGENT_TOOLS = {"Read", "Grep", "Glob", "Bash"}
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
INJECT_RE = re.compile(r"(?:^|[ \t])!`([^`]+)`", re.MULTILINE)  # dynamic context, run at load
REF_RE = re.compile(r"`((?:references|templates|scripts|assets)/[^`\s]+)`")


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Minimal YAML subset: scalars, quoted strings, `>-`/`|` blocks, inline and dash lists."""
    if not text.startswith("---\n"):
        raise ValueError("missing opening --- frontmatter line")
    end = text.find("\n---", 4)
    if end == -1:
        raise ValueError("missing closing --- frontmatter line")
    lines = text[4:end].splitlines()
    body = text[end + 4:].lstrip("\n")
    try:  # the real parser, when it is installed (CI installs it); the subset parser below still always runs
        import yaml  # noqa: PLC0415
    except ImportError:
        yaml = None
    if yaml is not None:
        try:
            yaml.safe_load(text[4:end])
        except yaml.YAMLError as exc:
            raise ValueError(f"frontmatter is not valid YAML: {str(exc).splitlines()[0]}") from None
    data: dict = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("\t") or re.match(r" *\t", line):
            raise ValueError(f"tab indentation in frontmatter (YAML forbids it): {line!r}")
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if not m:
            raise ValueError(f"unparseable frontmatter line: {line!r}")
        key, value = m.group(1), m.group(2).strip()
        if key in data:
            raise ValueError(f"duplicate frontmatter key '{key}'")
        if value in (">", ">-", "|", "|-", ""):
            block = []
            while i < len(lines) and (lines[i].startswith((" ", "\t")) or not lines[i].strip()):
                block.append(lines[i].strip())
                i += 1
            if value == "" and block and all(b.startswith("- ") or not b for b in block):
                data[key] = [b[2:].strip().strip("'\"") for b in block if b]
            elif value.startswith("|"):
                data[key] = "\n".join(block).strip()
            else:
                data[key] = " ".join(b for b in block if b).strip()
            continue
        if value.startswith("[") and value.endswith("]"):
            data[key] = [v.strip().strip("'\"") for v in value[1:-1].split(",") if v.strip()]
        elif value.startswith("["):
            raise ValueError(f"{key}: unbalanced [ ] in a flow list: {value!r}")
        elif len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            data[key] = value[1:-1]
        elif value[0] in "'\"":
            raise ValueError(f"{key}: opens a {value[0]} quote it never closes: {value[:40]!r}")
        elif value.lower() in ("true", "yes", "on"):
            data[key] = True
        elif value.lower() in ("false", "no", "off"):
            data[key] = False
        else:
            # Claude Code parses frontmatter with a real YAML parser; this subset parser is more lenient,
            # so reject what only the real one rejects: a plain scalar may not hold ": " or " #".
            if re.search(r":(\s|$)|\s#", value) or value[0] in "&*!%@`":
                raise ValueError(f"{key}: is not valid YAML as a plain value (it holds ': ' or ' #'); "
                                 "quote it or use a '>-' block")
            data[key] = value
    return data, body


def as_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value]
    return [v.strip() for v in re.split(r"[,\s]+", str(value or "")) if v.strip()]


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


def lint_skills(root: Path, rep: Report) -> list[tuple[str, int, bool]]:
    rows = []
    for skill_md in sorted((root / "skills").glob("*/SKILL.md")):
        skill_dir = skill_md.parent
        where = f"skills/{skill_dir.name}"
        try:
            fm, body = parse_frontmatter(skill_md.read_text())
        except ValueError as exc:
            rep.error(f"{where}: {exc}")
            continue
        name = str(fm.get("name", ""))
        if name != skill_dir.name:
            rep.error(f"{where}: name '{name}' must equal the directory name")
        if not NAME_RE.match(skill_dir.name) or len(skill_dir.name) > 64:
            rep.error(f"{where}: directory name must be lowercase-hyphen, at most 64 chars")
        desc = str(fm.get("description", "")).strip()
        when = str(fm.get("when_to_use", "")).strip()
        if not desc:
            rep.error(f"{where}: description is empty")
        listing = len(desc) + len(when)
        if listing > DESC_CAP:
            rep.error(f"{where}: description + when_to_use is {listing} chars (cap {DESC_CAP})")
        body_lines = body.count("\n") + 1
        if body_lines >= BODY_MAX_LINES:
            rep.error(f"{where}: body is {body_lines} lines (must be under {BODY_MAX_LINES})")
        allowed = [m.rstrip("*").strip() for m in
                   re.findall(r"Bash\(([^)]*)\)", " ".join(as_list(fm.get("allowed-tools"))))]
        for cmd in INJECT_RE.findall(body):
            if not any(cmd.strip().startswith(a) for a in allowed if a):
                rep.error(f"{where}: dynamic context `{cmd}` is not covered by allowed-tools "
                          f"(Claude Code runs it at load; a headless run silently aborts the skill)")
        for ref in sorted(set(re.findall(r"\$\{CLAUDE_PLUGIN_ROOT\}/([^`\s)]+)", body))):
            if not (root / ref).exists():
                rep.error(f"{where}: points at missing plugin file `{ref}`")
        for ref in sorted(set(REF_RE.findall(body))):
            if not (skill_dir / ref).exists():
                rep.error(f"{where}: references missing file `{ref}`")
        for sub in ("references", "templates", "scripts", "assets"):
            for f in sorted((skill_dir / sub).glob("**/*")) if (skill_dir / sub).is_dir() else []:
                if f.is_file() and f"{sub}/{f.relative_to(skill_dir / sub).as_posix()}" not in body:
                    rep.error(f"{where}: bundled file {sub}/{f.name} is not linked from SKILL.md")
        user_only = fm.get("disable-model-invocation") is True
        rows.append((skill_dir.name, (listing + 3) // 4, user_only))
    return rows


def lint_agents(root: Path, rep: Report) -> int:
    tokens = 0
    for agent in sorted((root / "agents").glob("*.md")):
        where = f"agents/{agent.name}"
        try:
            fm, _ = parse_frontmatter(agent.read_text())
        except ValueError as exc:
            rep.error(f"{where}: {exc}")
            continue
        for key in ("name", "description"):
            if not str(fm.get(key, "")).strip():
                rep.error(f"{where}: missing {key}")
        tools = as_list(fm.get("tools"))
        if not tools:
            rep.error(f"{where}: tools must be listed (a subset of {sorted(AGENT_TOOLS)})")
        extra = set(tools) - AGENT_TOOLS
        if extra:
            rep.error(f"{where}: tools {sorted(extra)} exceed {sorted(AGENT_TOOLS)}")
        tokens += (len(str(fm.get("description", ""))) + 3) // 4
    return tokens


def lint_hooks(root: Path, rep: Report) -> None:
    path = root / "hooks" / "hooks.json"
    if not path.exists():
        return
    try:
        config = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        rep.error(f"hooks/hooks.json: invalid JSON: {exc}")
        return
    for event, groups in (config.get("hooks") or {}).items():
        for group in groups:
            for hook in group.get("hooks", []):
                cmd = hook.get("command", "")
                for ref in re.findall(r"\$\{CLAUDE_PLUGIN_ROOT\}/([^\"'\s]+)", cmd):
                    if not (root / ref).is_file():
                        rep.error(f"hooks/hooks.json: {event} hook points at missing {ref}")
    for module in config.get("modules") or []:
        if not (path.parent / module).is_file():
            rep.error(f"hooks/hooks.json: module {module} is missing")


def collect(root: Path) -> tuple[Report, list[tuple[str, int, bool]], int]:
    """Every check, silently: the report, the per-skill token rows and the agent description tokens."""
    rep = Report()
    rows = lint_skills(root, rep)
    for stray in sorted((root / ".claude-plugin").glob("**/SKILL.md")):  # Claude Code never loads these
        rep.error(f"{stray.relative_to(root)}: skills live at the plugin root (skills/<name>/SKILL.md), "
                  "not inside .claude-plugin/")
    agent_tokens = lint_agents(root, rep)
    lint_hooks(root, rep)
    budget = sum(t for _, t, user_only in rows if not user_only)
    if budget > BUDGET_FAIL:
        rep.error(f"skill description budget ~{budget} tokens exceeds {BUDGET_FAIL}")
    elif budget > BUDGET_WARN:
        rep.warn(f"skill description budget ~{budget} tokens exceeds target {BUDGET_WARN}")
    return rep, rows, agent_tokens


def main(argv: list[str]) -> int:
    root = Path(argv[1] if len(argv) > 1 else ".").resolve()
    rep, rows, agent_tokens = collect(root)
    budget = sum(t for _, t, user_only in rows if not user_only)
    if rows:
        print(f"{'skill':<16}{'tokens':>7}  invocation")
        for name, tok, user_only in rows:
            print(f"{name:<16}{tok:>7}  {'user-only (0 context)' if user_only else 'model'}")
    print(f"always-loaded skill descriptions: ~{budget} tokens (warn > {BUDGET_WARN}, "
          f"fail > {BUDGET_FAIL}); agent descriptions: ~{agent_tokens} tokens")
    for w in rep.warnings:
        print(f"WARN  {w}")
    for e in rep.errors:
        print(f"FAIL  {e}")
    print("lint: FAIL" if rep.errors else "lint: OK")
    return 1 if rep.errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
