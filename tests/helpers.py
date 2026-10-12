"""Shared test fixtures: throwaway git repos, a throwaway HOME, and runners."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SYSOP = ROOT / "scripts" / "sysop.py"
HOOKS = ROOT / "hooks"
FAKEBIN = ROOT / "tests" / "fakebin"


def run(cmd: list[str], cwd: Path, env: dict | None = None, stdin: str | None = None,
        timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), env=env, input=stdin, capture_output=True,
                          text=True, timeout=timeout)


def git(cwd: Path, *args: str) -> str:
    proc = run(["git", *args], cwd)
    if proc.returncode != 0:
        raise AssertionError(f"git {args} failed: {proc.stderr}")
    return proc.stdout.strip()


class TempRepoCase(unittest.TestCase):
    """Each test gets a fresh repo with one commit and an isolated HOME."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.env = self.make_env()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "t@example.com")
        git(self.repo, "config", "user.name", "t")
        (self.repo / "README.md").write_text("hi\n")
        git(self.repo, "add", "README.md")
        git(self.repo, "commit", "-q", "-m", "init")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def serve_up(self, model: str, **state) -> None:
        """Mark a Kaggle serving session up for routing (what `serve up` records once healthy)."""
        path = self.home / ".cache" / "sysop" / "serve.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"model": model, "status": "up", "cap_at": time.time() + 3600,
                                    "kernel": "tester/sysop-serve", "url": "http://127.0.0.1:9", "started": time.time(),
                                    **state}))

    def make_env(self, **extra: str) -> dict:
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("GIT_", "CLAUDE", "XDG_", "FAKE_", "ARTIFICIAL_ANALYSIS", "PI_CODING_AGENT_DIR",
                                    "SYSOP_SERVE", "SYSOP_HF", "NGROK", "ANTHROPIC_API_KEY"))}
        env["HOME"] = str(self.home)
        env["SYSOP_INTEL_FIXTURES"] = str(self.tmp / "no-intel")  # offline: every intel source fails fast
        env["GIT_AUTHOR_NAME"] = env["GIT_COMMITTER_NAME"] = "t"
        env["GIT_AUTHOR_EMAIL"] = env["GIT_COMMITTER_EMAIL"] = "t@example.com"
        env.update(extra)
        return env

    def sysop(self, *args: str, cwd: Path | None = None, timeout: int = 60) -> subprocess.CompletedProcess:
        return run([sys.executable, str(SYSOP), *args], cwd or self.repo, env=self.env,
                   timeout=timeout)

    def run_hook(self, name: str, payload: dict) -> str:
        proc = subprocess.run([sys.executable, str(ROOT / "hooks" / name)], input=json.dumps(payload),
                              capture_output=True, text=True, env=self.env, timeout=60)
        return proc.stdout

    def sysop_json(self, *args: str, code: int = 0, **kw):
        proc = self.sysop(*args, **kw)
        self.assertEqual(proc.returncode, code, proc.stderr + proc.stdout)
        return json.loads(proc.stdout)

    # The chains the routing tests exercise, fixed here so they never move with sysop's defaults.
    ROUTING_TIERS = {
        "fast": ["pi:google/gemini-3.8-flash", "agy:gemini-3.8-flash-high", "claude:haiku", "pi:google/gemini-3.5-flash-lite"],
        "standard": ["claude:sonnet", "pi:google/gemini-3.8-flash@high", "agy:gemini-3.8-flash-high"],
        "deep": ["claude:opus", "pi:google/gemini-3.8-flash@high", "agy:gemini-3.1-pro-high"],
    }

    def pin_tiers(self) -> None:
        """User-scope settings holding ROUTING_TIERS (the repo's settings still win per tier)."""
        path = self.home / ".claude" / "sysop.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"tiers": self.ROUTING_TIERS}))

    def use_fakebin(self, only: tuple[str, ...] = ("agy", "claude")) -> Path:
        """PATH = a dir holding only the chosen fakes + the system dirs (no real agent CLIs)."""
        bindir = self.tmp / "bin"
        bindir.mkdir(exist_ok=True)
        for name in only:
            target = bindir / name
            if not target.exists():
                target.symlink_to(FAKEBIN / name)
        self.env["PATH"] = os.pathsep.join([str(bindir), "/usr/bin", "/bin"])
        return bindir

    def commit(self, name: str, text: str, msg: str, cwd: Path | None = None) -> None:
        where = cwd or self.repo
        (where / name).write_text(text)
        git(where, "add", name)
        git(where, "commit", "-q", "-m", msg)

    def hook(self, name: str, payload: dict | str, cwd: Path | None = None,
             env: dict | None = None) -> subprocess.CompletedProcess:
        data = payload if isinstance(payload, str) else json.dumps(payload)
        return run([sys.executable, str(HOOKS / name)], cwd or self.repo, env=env or self.env,
                   stdin=data)

    def worktree(self, name: str = "wt", branch: str = "feature") -> Path:
        path = self.tmp / name
        git(self.repo, "worktree", "add", "-q", "-b", branch, str(path))
        return path
