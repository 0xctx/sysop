#!/usr/bin/env python3
"""PreToolUse guard: refuses routes to credentials and destructive git/shell commands.

Exit 2 blocks (the reason on stderr goes back to the agent); exit 0 allows.
Any internal error exits 0, so a bug here never bricks a session.

Coverage is honest, not total: `@file` mentions bypass PreToolUse, and a script the
agent writes and then runs is not inspected. The block message never tells the agent
how to get around it: an earlier hint ("run it with `! cmd`") was read by the agent as
shell syntax, and `! git reset --hard` (bash negation) walked straight through.
"""
from __future__ import annotations

import fnmatch
import glob
import json
import os
import re
import shlex
import sys

TEMPLATE_ENV = (".env.example", ".env.sample", ".env.template", ".env.dist")
SECRET_NAMES = ("id_rsa*", "id_ed25519*", "id_ecdsa*", "id_dsa*", "*.pem", "*.p12", "*.pfx",
                "*.key", ".netrc", ".npmrc", ".pypirc", "credentials.json", ".git-credentials")
SECRET_DIRS = (".ssh", ".aws", ".gnupg", ".kaggle", ".azure")
SECRET_DIR_PATHS = (".config/gcloud",)  # directories named by more than one path part
SECRET_SUFFIXES = (".docker/config.json", ".config/gh/hosts.yml", ".kube/config",
                   ".pi/agent/auth.json", ".pi/free.json", ".config/kaggle/kaggle.json",
                   ".pi/agent/models.json", ".cache/sysop/serve.key", ".claude/.credentials.json")

FILE_TOOLS = ("Read", "Edit", "Write", "MultiEdit", "NotebookEdit")
SEARCH_TOOLS = ("Grep", "Glob")

# One representative file name per SECRET_NAMES pattern, so a glob can be probed against
# them; derived from the pattern list so the two cannot drift apart.
SECRET_SAMPLES = (".env", ".env.local") + tuple(
    p.replace("*", "x", 1).replace("*", "") if p.startswith("*") else p.replace("*", "")
    for p in SECRET_NAMES)

WRAPPERS = {"!", "sudo", "command", "builtin", "time", "nice", "nohup", "exec", "then", "do",
            "else", "if", "elif", "while", "until", "doas", "xargs"}
WRAPPER_VALUE_OPTS = {"-u", "-g", "-n", "-p"}  # sudo -u USER, nice -n N, ...
GIT_VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix",
                  "--config-env", "--exec-path", "--list-cmds"}


GLOB_PROBE_CAP = 20000  # entries; a bigger tree costs too much to look at, so assume the worst
SKIP_DIRS = {".git", "node_modules", "__pycache__"}


def glob_reaches_secret(pattern: str, cwd: str) -> bool:
    """A glob reaches a secret only when something it matches right now is one (or links to one).
    A bare name pattern (`*.key`) is also looked for at any depth, since `grep -r --include`,
    `rg -g` and Grep's own `glob` filter apply it there."""
    pattern = os.path.expanduser(pattern)
    full = os.path.join(cwd, pattern)
    for n, match in enumerate(glob.iglob(full, recursive=True, include_hidden=True)):
        if n >= GLOB_PROBE_CAP:
            return is_secret_path(pattern)
        if is_secret_path(match) or is_secret_path(os.path.realpath(match)):
            return True
    if "/" in pattern:
        return False
    seen = 0
    for root, dirs, files in os.walk(cwd):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in dirs + files:
            seen += 1
            if seen > GLOB_PROBE_CAP:
                return is_secret_path(pattern)
            if fnmatch.fnmatch(name, pattern):
                path = os.path.join(root, name)
                if is_secret_path(path) or is_secret_path(os.path.realpath(path)):
                    return True
    return False


def is_secret_path(path: str, cwd: str | None = None) -> bool:
    """`cwd` given: a glob is expanded there and judged by what it matches. Without it, a glob
    is judged by whether it could match a typical secret file name (the cautious answer)."""
    path = path.strip().strip("'\"").replace("\\", "/")
    if not path:
        return False
    norm = os.path.normpath(path)
    if re.search(r"/proc/.+/environ$", norm):
        return True
    parts = [p for p in norm.split("/") if p and p != "."]
    if not parts:
        return False
    base = parts[-1]
    if any(c in base for c in "*?[") and base not in ("*", "**"):
        if is_secret_path("/".join(parts[:-1] + ["x"])):
            return True  # anything inside a credential directory
        could = any(fnmatch.fnmatch(sample, base) and is_secret_path(sample) for sample in SECRET_SAMPLES)
        return could and (cwd is None or glob_reaches_secret(path, cwd))
    if base == ".env" or (base.startswith(".env.") and base not in TEMPLATE_ENV):
        return True
    if any(fnmatch.fnmatch(base, pat) for pat in SECRET_NAMES):
        return True
    if any(part in SECRET_DIRS for part in parts[:-1]) or base in SECRET_DIRS:
        return True
    if any(f"/{d}/" in "/" + "/".join(parts) + "/" for d in SECRET_DIR_PATHS):
        return True
    return any(norm.endswith(suffix) for suffix in SECRET_SUFFIXES)


def _is_assignment(tok: str) -> bool:
    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tok))


def strip_prefixes(tokens: list[str]) -> list[str] | None:
    """Drop what does not change the command that runs. None means a bare `env` (a dump)."""
    while tokens:
        head = tokens[0]
        if _is_assignment(head):
            tokens = tokens[1:]
        elif head == "env":
            rest = tokens[1:]
            while rest and (rest[0].startswith("-") or _is_assignment(rest[0])):
                rest = rest[2:] if rest[0] in ("-u", "--unset", "-C", "--chdir") else rest[1:]
            if not rest or rest[0].startswith(">"):
                return None
            tokens = rest
        elif head in WRAPPERS:
            tokens = tokens[1:]
            while tokens and tokens[0].startswith("-"):
                tokens = tokens[2:] if tokens[0] in WRAPPER_VALUE_OPTS else tokens[1:]
        else:
            break
    if tokens and tokens[0].startswith("\\"):
        tokens = [tokens[0][1:], *tokens[1:]]
    return tokens


def substitutions(command: str) -> list[str]:
    """The bodies of `$( … )`, `<( … )`, `>( … )` and backticks: commands that run wherever they sit,
    even inside an echo's arguments or double quotes."""
    out, i = [], 0
    while i < len(command):
        if command[i] == "`":
            end = command.find("`", i + 1)
            if end == -1:
                break
            out.append(command[i + 1:end])
            i = end + 1
        elif command[i] in "$<>" and command[i + 1:i + 2] == "(":
            depth, j = 1, i + 2
            while j < len(command) and depth:
                depth += {"(": 1, ")": -1}.get(command[j], 0)
                j += 1
            out.append(command[i + 2:j - 1])
            i = j
        else:
            i += 1
    return out


def segments(command: str) -> list[list[str]]:
    """Split a shell command into simple commands, each tokenized. Best effort."""
    out = [seg for body in substitutions(command) for seg in segments(body)]
    for chunk in re.split(r"\|\||&&|[;|&\n]", command):
        chunk = chunk.strip().lstrip("({").rstrip(")}").strip()
        if not chunk:
            continue
        try:
            tokens = shlex.split(chunk)
        except ValueError:
            tokens = chunk.split()
        tokens = strip_prefixes(tokens)
        if tokens is None:
            out.append(["env"])
            continue
        # unwrap `bash -c "<cmd>"`, `sh -c`, `eval "<cmd>"`: the inner command is what runs
        if tokens and os.path.basename(tokens[0]) in ("bash", "sh", "zsh", "dash") and "-c" in tokens:
            i = tokens.index("-c")
            if i + 1 < len(tokens):
                out.extend(segments(tokens[i + 1]))
                continue
        if tokens and tokens[0] == "eval" and len(tokens) > 1:
            out.extend(segments(" ".join(tokens[1:])))
            continue
        if tokens:
            out.append(tokens)
    return out


def env_dump_reason(tokens: list[str]) -> str | None:
    name = os.path.basename(tokens[0])
    args = tokens[1:]
    # drop redirections (`> f`, `>f`, `2>&1`): they do not change what is printed
    rest = [t for i, t in enumerate(args)
            if not re.match(r"^\d*>", t) and not (i and re.match(r"^\d*>>?$", args[i - 1]))]
    # Spelled in fragments so this file does not trip other guards that grep for the word.
    if name == "print" + "env":
        return "dumping the process environment"
    if name == "env" and not rest:
        return "dumping the process environment"
    if name == "set" and not rest:
        return "dumping shell variables"
    if name in ("export", "declare") and rest and rest[0] in ("-p", "-x", "-px", "-xp") and len(rest) == 1:
        return "dumping exported variables"
    return gh_or_printenv_reason(tokens)


# sysop's workflow rules: they bind sysop's own branches and active runs, never the operator's other work
MERGE_REASON = "a direct merge skips sysop's merge gates (sealed READY review, cross-family result, green checks, authority)"
REMOTE_DELETE_REASON = "deleting a remote branch"
ISSUE_REASON = ("closing or commenting on an issue a sysop ticket tracks is the script's job: `land` comments the "
                "landed commit and closes it")
SYSOP_BRANCH = re.compile(r"(^|[:/]|refs/heads/)(sysop|race)/")


def workflow_applies(tokens: list[str], cwd: str, session: str | None) -> bool:
    """A merge or remote delete is sysop's business when it names a sysop branch, when a bare
    `gh pr merge` runs on one, or while a sysop run is active."""
    named = [t for t in tokens[1:] if not t.startswith("-")]
    if any(SYSOP_BRANCH.search(t) for t in named):
        return True
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
        import store  # noqa: PLC0415
        if store.active_runs(cwd, session):
            return True
        bare_merge = os.path.basename(tokens[0]) == "gh" and len(named) == 2
        if bare_merge:
            branch = store.git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
            return bool(SYSOP_BRANCH.search(branch))
    except Exception:  # noqa: BLE001 - cannot tell: keep the rule
        return True
    return False


def gh_or_printenv_reason(tokens: list[str]) -> str | None:
    name, rest = os.path.basename(tokens[0]), tokens[1:]
    if name == "print" + "env":
        return "dumping the process environment"
    if name == "gh" and rest[:2] == ["auth", "token"]:
        return "printing the GitHub token"
    if name == "gh" and rest[:2] == ["auth", "status"] and ({"-t", "--show-token"} & set(rest)):
        return "printing the GitHub token"
    if name == "gh" and (rest[:2] == ["pr", "merge"]
                         or (rest[:1] == ["api"] and any(re.search(r"pulls/\d+/merge", t) for t in rest))):
        return MERGE_REASON
    if name == "gh" and rest[:1] == ["issue"] and rest[1:2] in (["close"], ["comment"], ["reopen"]):
        return ISSUE_REASON
    if name == "kaggle" and rest[:1] == ["auth"] and rest[1:2] != ["login"]:
        return "printing or revoking the Kaggle credential"
    if name == "pi" and "auth" in rest:
        # allowlist: only a readiness check without its credential output; pi's other auth commands print secrets
        words = [t for t in rest[rest.index("auth") + 1:] if not t.startswith("-")]
        if words[:1] != ["check"] or any(t.startswith("--credentials") for t in rest):
            return "printing a pi provider credential"
    return None


def _flag_set(tokens: list[str]) -> set[str]:
    """Short flags exploded (`-rf` -> r, f) plus long flags verbatim."""
    flags: set[str] = set()
    for tok in tokens:
        if tok.startswith("--"):
            flags.add(tok)
        elif tok.startswith("-") and len(tok) > 1:
            flags.update(tok[1:])
    return flags


def repo_root(cwd: str) -> str | None:
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
        import store  # noqa: PLC0415
        return os.path.normpath(store.git(["rev-parse", "--show-toplevel"], cwd))
    except Exception:  # noqa: BLE001 - not a repo: absolute paths stay absolute
        return None


def rm_reason(tokens: list[str], cwd: str = ".") -> str | None:
    if os.path.basename(tokens[0]) != "rm":
        return None
    args = tokens[1:]
    flags = _flag_set([a for a in args if a.startswith("-")])
    recursive = bool({"r", "R", "--recursive"} & flags)
    force = bool({"f", "--force"} & flags)
    if not (recursive and force):
        return None
    tmp_roots = ["/tmp/", "/var/tmp/"]
    if os.environ.get("TMPDIR"):
        tmp_roots.append(os.environ["TMPDIR"].rstrip("/") + "/")
    root = None
    for target in (a for a in args if not a.startswith("-")):
        if target.endswith("/") and resolved(target, cwd):
            target = resolved(target, cwd)  # `rm -rf link/` deletes what the link points at
        t = target.rstrip("/") or "/"
        if t.startswith("/"):
            t = os.path.normpath(t)
            root = root or repo_root(cwd) or "\0"
            if t.startswith(root + "/"):
                t = os.path.relpath(t, root)  # inside this repo: judged like the relative path it is
        if "/.sysop/" in t + "/" and not t.endswith("/.sysop"):
            continue  # sysop's own scratch and worktrees inside the store, never the store itself
        danger = (
            t in ("/", "~", ".", "..", "*", ".*", "/*", "$HOME", "${HOME}", "~/*")
            or t.startswith(("../", "~/", "$HOME", "${HOME}"))
            or (t.startswith("/") and not any((t + "/").startswith(r) and t + "/" != r
                                              for r in tmp_roots))
        )
        if danger:
            return f"recursive force delete of '{target}'"
    return None


def git_reason(tokens: list[str]) -> str | None:
    if os.path.basename(tokens[0]) != "git":
        return None
    i = 1
    while i < len(tokens) and tokens[i].startswith("-"):
        i += 2 if tokens[i] in GIT_VALUE_OPTS else 1
    if i >= len(tokens):
        return None
    sub, args = tokens[i], tokens[i + 1:]
    flags = _flag_set([a for a in args if a.startswith("-")])
    positional = [a for a in args if not a.startswith("-")]

    if sub == "push":
        if {"f", "--force", "--force-with-lease", "--force-if-includes"} & flags or any(
                a.startswith("--force-with-lease") for a in args):
            return "force push (needs the operator's word)"
        if any(p.startswith("+") for p in positional):
            return "force push via +refspec (needs the operator's word)"
        if "d" in flags or "--delete" in flags or any(p.startswith(":") for p in positional):
            return REMOTE_DELETE_REASON
    if sub == "reset" and "--hard" in flags:
        return "git reset --hard discards work"
    if sub == "clean" and {"f", "--force"} & flags:
        return "git clean -f deletes untracked files"
    deleting = bool({"d", "--delete"} & flags)
    forcing = bool({"f", "--force"} & flags)
    if sub == "branch" and ("D" in flags or (deleting and forcing)):
        return "forced branch deletion loses unmerged work (use -d)"
    if sub == "checkout" and ("--" in args or "." in positional or forcing):
        return "git checkout -- <path> / -f discards uncommitted changes"
    if sub == "switch" and (forcing or "--discard-changes" in flags):
        return "git switch --discard-changes / -f discards uncommitted changes"
    if sub == "restore" and "--staged" not in flags and "S" not in flags:
        return "git restore discards uncommitted changes"
    if sub == "stash" and positional[:1] in (["drop"], ["clear"]):
        return "git stash drop/clear discards stashed work"
    if sub == "worktree" and positional[:1] == ["remove"] and ({"f", "--force"} & flags):
        return "forced worktree removal discards uncommitted work"
    return None


ATTRIBUTION = re.compile(
    r"co-authored-by:[^\n]*\b(claude|anthropic|copilot|gemini|chatgpt|openai|gpt-?\d|ai assistant)\b"
    r"|generated (with|by) \[?(claude|anthropic|copilot|gemini|chatgpt|openai)"
    r"|\U0001F916",  # robot face
    re.IGNORECASE)


def attribution_reason(command: str, cwd: str) -> str | None:
    """A git commit whose message credits an AI. The message may sit in -m text, a heredoc
    (both inside the command string) or a -F file."""
    commits = [t for t in segments(command) if os.path.basename(t[0]) == "git" and "commit" in t]
    if not commits:
        return None
    texts = [command]
    for tokens in commits:
        for i, tok in enumerate(tokens):
            path = tokens[i + 1] if tok in ("-F", "--file") and i + 1 < len(tokens) else (
                tok.split("=", 1)[1] if tok.startswith("--file=") else None)
            if path and path != "-":
                try:
                    texts.append(open(os.path.join(cwd, path), errors="replace").read())
                except OSError:
                    pass
    if not any(ATTRIBUTION.search(t) for t in texts):
        return None
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
        import store  # noqa: PLC0415
        if store.load_settings(cwd).get("allow_attribution") is True:
            return None
    except Exception:  # noqa: BLE001 - settings unreadable: keep the default (refuse)
        pass
    return ("commit message credits an AI (Co-Authored-By / Generated with). This operator's "
            "commits carry no AI attribution: remove those lines and commit again")


GUARDED_COMMANDS = {"rm", "git", "gh"}


def resolved(piece: str, cwd: str) -> str:
    """Where an existing symlink leads (a link to a secret is the secret); otherwise ''."""
    path = os.path.join(cwd, os.path.expanduser(piece.strip("'\"")))
    try:
        return os.path.realpath(path) if piece and os.path.islink(path.rstrip("/")) else ""
    except (OSError, ValueError):
        return ""


def bash_reason(command: str, cwd: str = ".", session: str | None = None) -> str | None:
    for tokens in segments(command):
        # Check from every position where a guarded command appears, not only the head:
        # wrapper syntax nobody enumerated (`sudo --user root rm -rf /`) must not hide it.
        starts = [0] + [i for i, t in enumerate(tokens[1:], 1)
                        if os.path.basename(t) in GUARDED_COMMANDS or t == "print" + "env"]
        scan = tokens
        if os.path.basename(tokens[0]) in ("echo", "printf"):
            # their arguments are text (e.g. `echo .env >> .gitignore`); only redirect targets count
            scan = [t for i, t in enumerate(tokens) if i and tokens[i - 1] in (">", ">>")]
            scan += [t.split(">")[-1] for t in tokens if ">" in t and not t.endswith(">")]
        for tok in scan:
            for piece in re.split(r"[=<>]", tok):
                if is_secret_path(piece, cwd) or is_secret_path(resolved(piece, cwd)):
                    return f"reading a credential path ({piece})"
        for i in starts:
            checks = (env_dump_reason, rm_reason, git_reason) if i == 0 else (
                rm_reason, git_reason, gh_or_printenv_reason)
            for check in checks:
                reason = check(tokens[i:], cwd) if check is rm_reason else check(tokens[i:])
                if reason in (MERGE_REASON, REMOTE_DELETE_REASON, ISSUE_REASON) and not workflow_applies(
                        tokens[i:], cwd, session):
                    continue
                if reason:
                    return reason
    return None


SCRIPT_OWNED = re.compile(
    r"/\.sysop/(ledger\.jsonl|tickets\.(json|md)|runlog\.jsonl|runs/|active/"
    r"|reports/[^/]+\.(xreview(-\d+)?\.(md|json)|seal\.json|verify\.(md|json))$|verify/calibration\.json$|kaggle/[^/]+/\d+/job\.json$|races/)"
    r"|/\.cache/sysop/")


def script_owned_reason(path: str, cwd: str) -> str | None:
    """Records sysop.py writes (ledger, tickets, runs, cross-family results) are facts, not prose:
    an agent writing them directly could forge them, so only the script may."""
    absolute = os.path.normpath(os.path.join(cwd, os.path.expanduser(path)))
    if SCRIPT_OWNED.search(absolute):
        return (f"{path} is a record that only sysop.py writes. Use the matching `sysop.py` "
                f"command (ledger add, ticket, launch, run, xreview, review seal, cooldown) instead")
    return None


BACKGROUNDED_WORKER = re.compile(
    r"sysop\.py\b[^\n;|]*?\b(launch|wait)\b[^\n]*?(?<![&>])&(?![&>])")


def fleet_worktree_reason(path: str, cwd: str, session: str | None) -> str | None:
    """During a fleet run the supervisor never edits a ticket's worktree: workers build."""
    absolute = os.path.normpath(os.path.join(cwd, path))
    if "/.sysop/wt/" not in absolute + "/":
        return None
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
        import store  # noqa: PLC0415
        runs = store.active_runs(cwd, session)
    except Exception:  # noqa: BLE001 - cannot tell: fail open
        return None
    if any(data.get("slug", "").startswith("fleet") for _, data in runs):
        return ("editing a ticket's worktree during a fleet run. Workers build, the supervisor "
                "never does: relaunch or re-brief the ticket's worker instead")
    return None


def agent_model_reason(tool_input: dict, cwd: str) -> str | None:
    """A sysop agent runs on the model the operator configured (settings `agents`)."""
    agent = str(tool_input.get("subagent_type") or "")
    if not agent.startswith("sysop:"):
        return None
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
        import store  # noqa: PLC0415
        agents = store.load_settings(cwd).get("agents") or {}
    except Exception:  # noqa: BLE001 - cannot tell: fail open
        return None
    want = agents.get(agent) or agents.get("default") or "inherit"
    if want == "inherit" or tool_input.get("model") == want:
        return None
    return (f"dispatch {agent} with model \"{want}\" (the operator's setting; "
            f"`sysop.py models` shows it). Repeat the same Agent call with model: \"{want}\"")


def reason_for(tool: str, tool_input: dict, cwd: str = ".", session: str | None = None) -> str | None:
    if tool in ("Agent", "Task"):
        return agent_model_reason(tool_input, cwd)
    if tool in FILE_TOOLS:
        path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        if not path:
            return None
        if is_secret_path(path) or is_secret_path(os.path.realpath(os.path.join(cwd, os.path.expanduser(path)))):
            return f"credential path ({path})"  # a link to a secret is the secret
        if tool == "Read":
            return None
        return script_owned_reason(path, cwd) or fleet_worktree_reason(path, cwd, session)
    if tool in SEARCH_TOOLS:
        # The search pattern is text to look for, not a file to read; only where it looks counts.
        where = str(tool_input.get("path") or "")
        if is_secret_path(where, cwd):
            return f"credential path ({where})"
        pattern = str(tool_input.get("glob") or "")
        if pattern and is_secret_path(pattern, os.path.join(cwd, where)):
            return f"credential path ({pattern})"
        return None
    if tool == "Bash":
        command = str(tool_input.get("command") or "")
        for tokens in segments(command):
            targets = [t for i, t in enumerate(tokens) if i and tokens[i - 1] in (">", ">>")]
            targets += [t.split(">")[-1] for t in tokens if ">" in t and not t.endswith(">")]
            if os.path.basename(tokens[0]) == "tee":
                targets += [t for t in tokens[1:] if not t.startswith("-")]
            if os.path.basename(tokens[0]) in ("mv", "cp", "ln") and len(tokens) > 2:
                targets.append(tokens[-1])
            for target in targets:
                reason = script_owned_reason(target, cwd)
                if reason:
                    return reason
        if BACKGROUNDED_WORKER.search(command):
            return ("backgrounding sysop.py launch/wait with '&'. launch already detaches the worker; "
                    "run it in the foreground and wait with a bounded `sysop.py wait`")
        return bash_reason(command, cwd, session) or attribution_reason(command, cwd)
    return None


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        reason = reason_for(str(payload.get("tool_name") or ""), payload.get("tool_input") or {},
                            str(payload.get("cwd") or os.getcwd()), payload.get("session_id"))
    except Exception:  # noqa: BLE001 - fail open
        return 0
    if reason:
        if reason.startswith(("commit message credits an AI", "editing a ticket's worktree",
                              "backgrounding sysop.py", "dispatch sysop:")) or "only sysop.py writes" in reason:
            print(f"sysop guard blocked: {reason}.", file=sys.stderr)  # fixable by the agent itself
        else:
            print(f"sysop guard blocked: {reason}. Do not retry this in another form. Stop and ask "
                  f"the operator; only they can run it, from their own prompt.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
