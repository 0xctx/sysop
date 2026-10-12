import json
from pathlib import Path

from helpers import TempRepoCase, git

PRINT_ENV = "print" + "env"  # fragments, so this file never trips a guard that greps for it

BASH_BLOCK = [
    "cat .env",
    "cat ./config/.env.production",
    "grep KEY .env",
    "head -n 3 ~/.ssh/id_ed25519",
    "cp ~/.aws/credentials /tmp/x",
    "cat ~/.netrc",
    "openssl x509 -in server.pem",
    "cat /proc/self/environ",
    PRINT_ENV,
    "env",
    "env | grep TOKEN",
    "export -p",
    "gh auth token",
    "rm -rf /",
    "rm -rf ~",
    "rm -fr .",
    "rm -r -f ..",
    "rm -rf *",
    "rm --recursive --force /home/u/project",
    "rm -rf $HOME",
    "git push --force origin main",
    "git push -f",
    "git push --force-with-lease origin feat",
    "git push origin +main",
    "git push origin --delete sysop/feat",
    "git push origin :race/t1/r2",
    "git reset --hard HEAD~1",
    "git -C /x reset --hard",
    "git clean -fd",
    "git clean -xdf",
    "git branch -D feature",
    "git checkout -- .",
    "git checkout .",
    "git restore src/app.py",
    "git stash drop",
    "git worktree remove --force ../wt",
    "ls && git reset --hard",
    "FOO=1 git reset --hard",
    "echo SECRET=1 > .env",
    "! git reset --hard HEAD .",
    "bash -c 'git reset --hard'",
    "sh -c \"rm -rf /\"",
    "eval git clean -fd",
    "env git push --force",
    "command git branch -D x",
    "\\git reset --hard",
    "(cd sub && git reset --hard)",
    "if true; then git reset --hard; fi",
    "echo x>.env",
    "env FOO=bar",
    "env -i",
    "git --namespace foo reset --hard",
    "rm -rf /tmp/../etc",
    "rm -rf .*",
    "rm -rf ~/projects/app",
    "cat ~/.ssh/id_e*",
    "cat /proc/1//environ",
    "cat /proc/self/fd/../environ",
    "set > out.txt",
    "export -p > out.txt",
    "declare -x > f",
    "git branch -d -f old",
    "git checkout -f main",
    "git switch --discard-changes main",
    "sudo -u root git reset --hard",
    "env -i git reset --hard",
    "nice -n 5 git clean -fdx",
    "sudo --user root rm -rf /",
    "sudo --preserve-env=HOME --user=root git reset --hard",
    "timeout 30 git push --force",
    "xargs -I{} rm -rf /home/u/{}",
    "gh auth status -t",
    "gh auth status --show-token",
    "pi auth print-api-key --provider google",
    "pi auth print-bearer-token --provider cline",
    "pi auth check --provider cline --json --credentials",
    "pi auth check --credentials=true --provider cline",
    "pi auth --provider cline print-api-key",
    "pi auth export",
    "kaggle auth print-access-token",
    "gh pr merge sysop/t1 --merge --delete-branch",
    "cd /x && gh pr merge race/t1/r2 --admin --squash",
    "echo $(cat .env)",
    'echo "$(< .env)"',
    "echo `cat ~/.ssh/id_rsa`",
    "diff <(cat .env) /dev/null",
    "x=$(echo $(cat ~/.aws/credentials))",
    "cat ~/.claude/.credentials.json",
    "cat ~/.config/gcloud/application_default_credentials.json",
    "ls ~/.config/gcloud",
    "cat ~/.azure/accessTokens.json",
    "cat ~/.kaggle/credentials.json",
    "cat ~/.pi/agent/auth.json",
    "grep key /home/u/.pi/free.json",
    "cat ~/.cache/sysop/serve.key",
    "jq .providers.kaggle.apiKey ~/.pi/agent/models.json",
    "rm -rf /home/u/proj/.sysop",
]

BASH_ALLOW = [
    # sysop's workflow rules leave the operator's own branches alone outside a sysop run (finding A5)
    "git push origin --delete feat",
    "gh pr merge 3 --merge --delete-branch",
    "gh api -X PUT repos/o/r/pulls/3/merge",
    "echo $(date) $(git rev-parse HEAD)",
    "echo `whoami`",
    "echo $((1 + 2))",
    "cat ~/.config/gcloud-notes.txt",
    "cat .env.example",
    "ls -la",
    "git status",
    "git branch -d feature",
    "git worktree remove ../wt",
    "git push origin feature",
    "git push -u origin feature",
    "git reset --soft HEAD~1",
    "git restore --staged file.py",
    "git checkout -b new-branch",
    "git checkout main",
    "rm -rf build/",
    "rm -rf node_modules dist",
    "rm -rf /tmp/scratch-dir",
    "rm -f notes.txt",
    "rm -r olddir",
    "env FOO=1 python3 app.py",
    "grep -rn 'getenv' src/",
    "python3 -m unittest discover -s tests",
    "echo hello > out.txt",
    "echo .env >> .gitignore",
    "git switch main",
    "env -i python3 app.py",
    "sudo -u www ls",
    "rm -rf /tmp/a/b",
    "export PATH=$PATH:/x",
    "ls set",
    "rm -rf /home/u/proj/.sysop/wt/review-x",
    "rm -rf /home/u/proj/.sysop/scratch/exp1",
    "gh auth status",
    "pi auth check --provider cline --json",
    "cat ~/.pi/agent/settings.json",
    "kaggle quota",
    "gh pr view 3 --json state",
    "gh pr checks 3",
    "gh api repos/o/r/issues/3/comments",
    "cat ~/.cache/sysop/serve.json",
    "kaggle kernels status aa0008/x",
    "git log --grep='rm -rf /'",
]


class GuardTest(TempRepoCase):
    def bash(self, cmd):
        return self.hook("guard.py", {"tool_name": "Bash", "tool_input": {"command": cmd}})

    def test_blocks(self):
        for cmd in BASH_BLOCK:
            with self.subTest(cmd=cmd):
                proc = self.bash(cmd)
                self.assertEqual(proc.returncode, 2, f"should block: {cmd}")
                self.assertIn("sysop guard blocked", proc.stderr)
                self.assertNotIn("! <", proc.stderr)  # never hand the agent a bypass

    def test_allows(self):
        for cmd in BASH_ALLOW:
            with self.subTest(cmd=cmd):
                proc = self.bash(cmd)
                self.assertEqual(proc.returncode, 0, f"should allow: {cmd}: {proc.stderr}")

    def test_a_glob_is_judged_by_what_it_matches_now(self):
        # v1_idea: `ls ./.git*` was refused for a .git-credentials that did not exist
        for cmd in ("ls ./.git*", "cat cred*.json", "grep -l x .env*"):
            with self.subTest(cmd=cmd, present=False):
                self.assertEqual(self.bash(cmd).returncode, 0, cmd)
        (self.repo / ".env.example").write_text("A=\n")
        self.assertEqual(self.bash("cat .env*").returncode, 0)  # a template only
        for name, cmd in ((".git-credentials", "ls ./.git*"), ("credentials.json", "cat cred*.json"),
                          (".env", "grep -l x .env*")):
            (self.repo / name).write_text("secret\n")
            with self.subTest(cmd=cmd, present=True):
                self.assertEqual(self.bash(cmd).returncode, 2, cmd)
        self.assertEqual(self.bash("grep -r x --include='*.key' .").returncode, 0)
        (self.repo / "sub").mkdir()
        (self.repo / "sub" / "tls.key").write_text("k\n")
        for cmd in ("grep -r x --include='*.key' .", "rg -g '*.key' x"):
            with self.subTest(cmd=cmd, depth=True):
                self.assertEqual(self.bash(cmd).returncode, 2, cmd)  # any depth counts
        grep = lambda glob: self.hook("guard.py", {"tool_name": "Grep", "cwd": str(self.repo),
                                                   "tool_input": {"pattern": "x", "glob": glob}})
        self.assertEqual(grep("*.key").returncode, 2)  # a filter glob reaches any depth
        self.assertEqual(grep("*.py").returncode, 0)

    def test_file_tools(self):
        cases = [
            ("Read", {"file_path": "/repo/.env"}, 2),
            ("Read", {"file_path": "/home/u/.ssh/config"}, 2),
            ("Edit", {"file_path": "secrets/server.key"}, 2),
            ("Write", {"file_path": ".env.local"}, 2),
            ("Read", {"file_path": "/repo/.env.example"}, 0),
            ("Read", {"file_path": "/repo/src/environment.py"}, 0),
            ("Edit", {"file_path": "README.md"}, 0),
            ("Grep", {"pattern": "API_KEY", "path": ".env"}, 2),
            ("Grep", {"pattern": "API_KEY", "glob": ".env*"}, 0),  # no .env exists here
            ("Grep", {"pattern": ".env", "path": "src/"}, 0),
            ("Glob", {"pattern": "**/*.py"}, 0),
        ]
        for tool, tool_input, code in cases:
            with self.subTest(tool=tool, tool_input=tool_input):
                proc = self.hook("guard.py", {"tool_name": tool, "tool_input": tool_input})
                self.assertEqual(proc.returncode, code, proc.stderr)

    def test_attribution_in_commit_message(self):
        blocked = [
            'git commit -m "fix: x" -m "Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>"',
            "git commit -m \"$(cat <<'EOF'\nfix: x\n\nCo-Authored-By: Claude <noreply@anthropic.com>\nEOF\n)\"",
            'git add -A && git commit -m "feat: y\n\n🤖 Generated with [Claude Code](https://claude.com)"',
            'git -C wt commit -F msg.txt',
        ]
        (self.repo / "msg.txt").write_text("fix: z\n\nCo-authored-by: GitHub Copilot <x@y>\n")
        for cmd in blocked:
            with self.subTest(cmd=cmd):
                proc = self.hook("guard.py", {"tool_name": "Bash", "cwd": str(self.repo),
                                              "tool_input": {"command": cmd}})
                self.assertEqual(proc.returncode, 2, cmd)
                self.assertIn("credits an AI", proc.stderr)
        allowed = [
            'git commit -m "fix: add returns the sum"',
            'git commit -m "docs: thank Ana" -m "Co-Authored-By: Ana Lopez <ana@example.com>"',
            'grep -r "Co-Authored-By: Claude" docs/',
        ]
        for cmd in allowed:
            with self.subTest(cmd=cmd):
                proc = self.hook("guard.py", {"tool_name": "Bash", "cwd": str(self.repo),
                                              "tool_input": {"command": cmd}})
                self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_attribution_allowed_by_setting(self):
        (self.repo / ".claude").mkdir()
        (self.repo / ".claude" / "sysop.json").write_text('{"allow_attribution": true}')
        cmd = 'git commit -m "x" -m "Co-Authored-By: Claude <noreply@anthropic.com>"'
        proc = self.hook("guard.py", {"tool_name": "Bash", "cwd": str(self.repo),
                                      "tool_input": {"command": cmd}})
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_fails_open_on_garbage(self):
        self.assertEqual(self.hook("guard.py", "not json").returncode, 0)
        self.assertEqual(self.hook("guard.py", {"tool_name": "Bash"}).returncode, 0)


class ContextTest(TempRepoCase):
    def context(self, cwd=None):
        proc = self.hook("context.py", {"hook_event_name": "SessionStart", "source": "startup",
                                        "cwd": str(cwd or self.repo)})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def test_emits_nested_context_with_branch_and_artifacts(self):
        (self.repo / "dirty.txt").write_text("x")
        self.sysop("store")
        (self.repo / ".sysop" / "plans").mkdir(exist_ok=True)
        (self.repo / ".sysop" / "plans" / "add-login.md").write_text("plan")
        (self.repo / ".sysop" / "tickets.md").write_text("t")
        data = json.loads(self.context())
        self.assertNotIn("additionalContext", data)
        ctx = data["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(data["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn("branch main", ctx)
        self.assertIn("1 uncommitted", ctx)
        self.assertIn("plans/add-login.md", ctx)
        self.assertIn("tickets.md", ctx)
        self.assertLessEqual(len(ctx.splitlines()), 6)

    def test_counts_live_workers(self):
        import os
        self.sysop("store")
        run = self.repo / ".sysop" / "runs" / "t1" / "1"
        run.mkdir(parents=True)
        (run / "pid").write_text(str(os.getpid()))
        done = self.repo / ".sysop" / "runs" / "t2" / "1"
        done.mkdir(parents=True)
        (done / "pid").write_text(str(os.getpid()))
        (done / "exit").write_text("0")
        ctx = json.loads(self.context())["hookSpecificOutput"]["additionalContext"]
        self.assertIn("1 worker(s) still running", ctx)

    def test_does_not_create_store(self):
        self.context()
        self.assertFalse((self.repo / ".sysop").exists())

    def test_silent_outside_git(self):
        self.assertEqual(self.context(cwd=self.home), "")


class StopGateTest(TempRepoCase):
    def configure(self, check, gate=True):
        (self.repo / ".claude").mkdir(exist_ok=True)
        (self.repo / ".claude" / "sysop.json").write_text(
            json.dumps({"stop_gate": gate, "check": check}))

    def stop(self, active=False):
        return self.hook("stop_gate.py", {"hook_event_name": "Stop", "cwd": str(self.repo),
                                          "stop_hook_active": active})

    def test_green_allows(self):
        self.configure("true")
        self.assertEqual(self.stop().returncode, 0)

    def test_red_blocks_with_output(self):
        self.configure("echo boom-failure; exit 3")
        proc = self.stop()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("boom-failure", proc.stderr)
        self.assertIn("exit 3", proc.stderr)

    def test_stop_hook_active_stands_down(self):
        self.configure("exit 1")
        self.assertEqual(self.stop(active=True).returncode, 0)

    def test_disabled_gate_is_inert_even_when_red(self):
        self.configure("exit 1", gate=False)
        self.assertEqual(self.stop().returncode, 0)

    def test_no_check_configured_is_inert(self):
        self.configure("", gate=True)
        self.assertEqual(self.stop().returncode, 0)

    def test_check_runs_in_repo_root(self):
        self.configure("test -f README.md")
        sub = self.repo / "sub"
        sub.mkdir()
        proc = self.hook("stop_gate.py", {"cwd": str(sub), "stop_hook_active": False})
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_timeout_blocks(self):
        (self.repo / ".claude").mkdir(exist_ok=True)
        (self.repo / ".claude" / "sysop.json").write_text(
            json.dumps({"stop_gate": True, "check": "sleep 5", "check_timeout": 1}))
        proc = self.stop()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("timed out after 1s", proc.stderr)


class RunGateTest(TempRepoCase):
    def transcript(self, tool_calls):
        path = self.tmp / "transcript.jsonl"
        lines = ['{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash"}]}}'] * tool_calls
        path.write_text("\n".join(lines) + "\n")
        return str(path)

    def stop(self, session="S1", active=False, calls=0):
        return self.hook("stop_gate.py", {"hook_event_name": "Stop", "cwd": str(self.repo),
                                          "session_id": session, "stop_hook_active": active,
                                          "transcript_path": self.transcript(calls)})

    def test_no_active_run_allows(self):
        self.assertEqual(self.stop().returncode, 0)

    def test_active_run_blocks_its_session_only(self):
        self.sysop("run", "start", "ship-x", "--session", "S1")
        proc = self.stop(calls=3)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("run 'ship-x' is unfinished", proc.stderr)
        self.assertIn("run end ship-x", proc.stderr)
        self.assertEqual(self.stop(session="OTHER", calls=3).returncode, 0)

    def test_a_run_started_from_another_registered_repo_still_holds_the_turn(self):
        other = self.tmp / "api"
        other.mkdir()
        git(other, "init", "-q", "-b", "main")
        self.sysop("ticket", "add", "s1", "--title", "x", "--size", "small", "--base", "main", "--authority", "none",
                   cwd=other)  # registers the repo, as a lane's ticket does
        self.sysop("run", "start", "fleet", "--session", "S1", cwd=other)
        self.assertEqual(self.stop(calls=3).returncode, 2)  # the session sits in this repo; the marker is there

    def test_marker_takes_the_real_session_not_an_invented_one(self):
        self.env["CLAUDE_CODE_SESSION_ID"] = "S1"
        self.sysop("run", "start", "fleet", "--session", "fleet-1791333127")  # what an agent once passed
        self.assertEqual(self.stop(calls=3).returncode, 2)

    def test_progress_keeps_blocking_no_progress_releases(self):
        self.sysop("run", "start", "ship-x", "--session", "S1")
        self.assertEqual(self.stop(calls=3).returncode, 2)
        self.assertEqual(self.stop(active=True, calls=9).returncode, 2)   # worked, stopped again
        self.assertEqual(self.stop(active=True, calls=9).returncode, 0)   # no new tool calls: spin

    def test_hard_cap(self):
        self.sysop("run", "start", "ship-x", "--session", "S1")
        codes = [self.stop(active=i > 0, calls=i * 5).returncode for i in range(10)]
        self.assertEqual(codes.count(2), 8)

    def test_run_end_releases_and_logs(self):
        self.sysop("run", "start", "ship-x", "--session", "S1")
        self.sysop("run", "step", "ship-x", "4")
        marker = json.loads((self.repo / ".sysop" / "active" / "ship-x.json").read_text())
        self.assertEqual(marker["step"], 4)
        self.assertEqual(self.sysop("run", "end", "ship-x", "--outcome", "landed").returncode, 0)
        self.assertEqual(self.stop(calls=3).returncode, 0)
        log = (self.repo / ".sysop" / "runlog.jsonl").read_text()
        self.assertIn('"outcome": "landed"', log)

    def test_a_marker_without_a_session_holds_no_other_session(self):
        self.sysop("run", "start", "ship-x", "--session", "${CLAUDE_SESSION_ID}")  # unsubstituted: no session
        self.assertEqual(self.stop(session="whatever", calls=1).returncode, 0)
        self.assertEqual(self.hook("stop_gate.py", {"hook_event_name": "Stop", "cwd": str(self.repo),
                                                    "transcript_path": self.transcript(1)}).returncode, 2)

    def test_pending_background_work_lets_the_turn_end(self):
        self.sysop("run", "start", "fleet", "--session", "S1")
        path = Path(self.transcript(3))
        with path.open("a") as fh:
            fh.write('{"type":"user","message":{"content":[{"type":"tool_result","content":'
                     '"Async agent launched successfully. agentId: a8c5ccf5661cb9d18 (internal)"}]}}\n')
        payload = {"hook_event_name": "Stop", "cwd": str(self.repo), "session_id": "S1",
                   "stop_hook_active": False, "transcript_path": str(path)}
        self.assertEqual(self.hook("stop_gate.py", payload).returncode, 0)  # results come after the turn
        with path.open("a") as fh:
            fh.write('{"type":"user","message":{"content":"<task-notification><task-id>a8c5ccf5661cb9d18'
                     '</task-id><status>completed</status></task-notification>"}}\n')
            fh.write('{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash"}]}}\n')
        self.assertEqual(self.hook("stop_gate.py", payload).returncode, 2)  # reported back: the gate holds again

    def test_a_marker_older_than_a_day_has_expired(self):
        self.sysop("run", "start", "ship-x", "--session", "S1")
        marker = self.repo / ".sysop" / "active" / "ship-x.json"
        data = json.loads(marker.read_text())
        data["started"] -= 25 * 3600
        marker.write_text(json.dumps(data))
        self.assertEqual(self.stop(calls=3).returncode, 0)

    def test_run_gate_precedes_check_gate_and_check_still_works(self):
        (self.repo / ".claude").mkdir()
        (self.repo / ".claude" / "sysop.json").write_text('{"stop_gate": true, "check": "exit 1"}')
        self.assertEqual(self.stop(calls=1).returncode, 2)  # check gate alone, no run
        self.assertEqual(self.stop(active=True, calls=1).returncode, 0)


class FleetGuardTest(TempRepoCase):
    def edit(self, path, session="S1"):
        return self.hook("guard.py", {"tool_name": "Write", "cwd": str(self.repo), "session_id": session,
                                      "tool_input": {"file_path": path, "content": "x"}})

    def test_supervisor_cannot_edit_ticket_worktrees_during_fleet(self):
        wt_file = str(self.repo / ".sysop" / "wt" / "t1" / "calc" / "stats.py")
        self.assertEqual(self.edit(wt_file).returncode, 0)            # no fleet run: allowed
        self.sysop("run", "start", "fleet", "--session", "S1")
        proc = self.edit(wt_file)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("Workers build", proc.stderr)
        self.assertEqual(self.edit(wt_file, session="OTHER").returncode, 0)  # another session
        self.assertEqual(self.edit(str(self.repo / ".sysop" / "briefs" / "t1.md")).returncode, 0)
        self.assertEqual(self.edit(".sysop/wt/t1/x.py").returncode, 2)       # relative path too
        self.sysop("run", "end", "fleet", "--outcome", "done")
        self.assertEqual(self.edit(wt_file).returncode, 0)

    def test_ship_runs_may_edit_their_own_worktree(self):
        self.sysop("run", "start", "sysop-fix", "--session", "S1")
        self.assertEqual(self.edit(str(self.repo / ".sysop" / "wt" / "sysop-fix" / "a.py")).returncode, 0)

    def test_backgrounding_launch_or_wait_refused(self):
        bad = ["python3 /p/scripts/sysop.py launch t1 wt 'agy -p x' > log 2>&1 &",
               "python3 /p/scripts/sysop.py wait t1 t2 &",
               "python3 /p/scripts/sysop.py launch t1 wt cmd & T1=$!"]
        good = ["python3 /p/scripts/sysop.py launch t1 wt 'agy -p x' > out 2>&1",
                "python3 /p/scripts/sysop.py launch t1 wt cmd && python3 /p/scripts/sysop.py wait t1",
                "python3 /p/scripts/sysop.py ledger add --ticket t1 --note 'a & b'"]
        for cmd in bad + good:
            with self.subTest(cmd=cmd):
                code = self.hook("guard.py", {"tool_name": "Bash", "cwd": str(self.repo),
                                              "tool_input": {"command": cmd}}).returncode
                self.assertEqual(code, 2 if cmd in bad else 0)


class ScriptOwnedGuardTest(TempRepoCase):
    def check(self, tool, tool_input):
        return self.hook("guard.py", {"tool_name": tool, "cwd": str(self.repo),
                                      "tool_input": tool_input}).returncode

    def test_records_cannot_be_written_by_the_agent(self):
        blocked = [
            ("Write", {"file_path": ".sysop/reports/sysop-t2.xreview.md", "content": "forged"}),
            ("Write", {"file_path": str(self.repo / ".sysop" / "reports" / "x.xreview.json"), "content": "{}"}),
            ("Edit", {"file_path": ".sysop/ledger.jsonl", "old_string": "a", "new_string": "b"}),
            ("Write", {"file_path": ".sysop/tickets.md", "content": "x"}),
            ("Write", {"file_path": ".sysop/runs/t1/1/exit", "content": "0"}),
            ("Write", {"file_path": ".sysop/active/fleet.json", "content": "{}"}),
            ("Write", {"file_path": "~/.cache/sysop/cooldowns.json", "content": "{}"}),
            ("Write", {"file_path": "~/.cache/sysop/intel.json", "content": "{}"}),
            ("Write", {"file_path": ".sysop/kaggle/chapters/1/job.json", "content": "{}"}),
            ("Write", {"file_path": ".sysop/races/t1.json", "content": "{}"}),
            ("Bash", {"command": "echo '{}' >> .sysop/ledger.jsonl"}),
            ("Bash", {"command": "cat > .sysop/reports/sysop-t1.xreview.md <<'EOF'\nok\nEOF"}),
            ("Bash", {"command": "echo x | tee .sysop/tickets.json"}),
            ("Bash", {"command": "cp /tmp/forged.md .sysop/reports/a.xreview.md"}),
            ("Write", {"file_path": ".sysop/reports/sysop-t1.seal.json", "content": "{}"}),
            ("Bash", {"command": "echo '{\"sha\": \"abc\"}' > .sysop/reports/feature.seal.json"}),
        ]
        for tool, tool_input in blocked:
            with self.subTest(tool=tool, tool_input=tool_input):
                self.assertEqual(self.check(tool, tool_input), 2)

    def test_agent_written_reports_and_reads_stay_allowed(self):
        allowed = [
            ("Write", {"file_path": ".sysop/reports/sysop-t1.review.md", "content": "x"}),
            ("Write", {"file_path": ".sysop/reports/sysop-t1.build.md", "content": "x"}),
            ("Write", {"file_path": ".sysop/plans/sysop-t1.md", "content": "x"}),
            ("Write", {"file_path": ".sysop/briefs/t1.request.txt", "content": "x"}),
            ("Read", {"file_path": ".sysop/ledger.jsonl"}),
            ("Bash", {"command": "cat .sysop/ledger.jsonl"}),
            ("Bash", {"command": "python3 /p/scripts/sysop.py ledger add --ticket t1 > /tmp/out.json"}),
        ]
        for tool, tool_input in allowed:
            with self.subTest(tool=tool, tool_input=tool_input):
                self.assertEqual(self.check(tool, tool_input), 0)


class AgentModelGuardTest(TempRepoCase):
    def dispatch(self, agent, model=None):
        tool_input = {"subagent_type": agent, "prompt": "review"}
        if model:
            tool_input["model"] = model
        return self.hook("guard.py", {"tool_name": "Agent", "cwd": str(self.repo), "tool_input": tool_input})

    def test_the_default_preset_picks_sonnet_not_the_session_model(self):
        self.assertEqual(self.dispatch("sysop:code-reviewer").returncode, 2)  # would inherit an Opus session
        self.assertEqual(self.dispatch("sysop:code-reviewer", "sonnet").returncode, 0)
        (self.repo / ".claude").mkdir()
        (self.repo / ".claude" / "sysop.json").write_text(json.dumps({"agents": {"default": "inherit"}}))
        self.assertEqual(self.dispatch("sysop:code-reviewer").returncode, 0)

    def test_configured_model_is_enforced(self):
        (self.repo / ".claude").mkdir()
        (self.repo / ".claude" / "sysop.json").write_text(json.dumps(
            {"agents": {"default": "haiku", "sysop:code-reviewer": "sonnet"}}))
        proc = self.dispatch("sysop:code-reviewer")
        self.assertEqual(proc.returncode, 2)
        self.assertIn('model: "sonnet"', proc.stderr)
        self.assertEqual(self.dispatch("sysop:code-reviewer", "sonnet").returncode, 0)
        self.assertEqual(self.dispatch("sysop:explorer", "sonnet").returncode, 2)   # default haiku
        self.assertEqual(self.dispatch("sysop:explorer", "haiku").returncode, 0)
        self.assertEqual(self.dispatch("general-purpose").returncode, 0)          # not sysop's


class WorkflowGuardTest(TempRepoCase):
    """sysop's merge and remote-delete rules bind its own branches and runs, nobody else's (A5)."""

    def bash(self, cmd, session="S1"):
        return self.hook("guard.py", {"tool_name": "Bash", "cwd": str(self.repo), "session_id": session,
                                      "tool_input": {"command": cmd}}).returncode

    def test_rules_apply_during_a_sysop_run_of_this_session(self):
        self.assertEqual(self.bash("gh pr merge 3 --squash"), 0)
        self.sysop("run", "start", "ship-x", "--session", "S1")
        self.assertEqual(self.bash("gh pr merge 3 --squash"), 2)
        self.assertEqual(self.bash("git push origin --delete feat"), 2)
        self.assertEqual(self.bash("gh pr merge 3 --squash", session="S2"), 0)

    def test_issue_close_and_comment_are_the_scripts_during_a_run(self):
        self.assertEqual(self.bash("gh issue close 7"), 0)  # the operator's own issues, outside sysop's runs
        self.sysop("run", "start", "fleet", "--session", "S1")
        for cmd in ("gh issue close 7 --comment done", "gh issue comment 7 --body hi", "gh issue reopen 7"):
            self.assertEqual(self.bash(cmd), 2, cmd)
        self.assertEqual(self.bash("gh issue list --label sysop:bg"), 0)  # reading is fine

    def test_a_bare_merge_on_a_sysop_branch_is_sysop_work(self):
        git(self.repo, "checkout", "-q", "-b", "sysop/fix")
        self.assertEqual(self.bash("gh pr merge --squash"), 2)
        git(self.repo, "checkout", "-q", "-b", "mine")
        self.assertEqual(self.bash("gh pr merge --squash"), 0)


class RepoPathGuardTest(TempRepoCase):
    def bash(self, cmd):
        return self.hook("guard.py", {"tool_name": "Bash", "cwd": str(self.repo),
                                      "tool_input": {"command": cmd}}).returncode

    def test_absolute_rm_inside_the_repo_is_judged_as_relative(self):
        import importlib.util  # noqa: PLC0415 - the hook is a script, loaded here as a module
        from unittest import mock  # noqa: PLC0415
        from helpers import HOOKS  # noqa: PLC0415
        spec = importlib.util.spec_from_file_location("guard_under_test", HOOKS / "guard.py")
        guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard)
        with mock.patch.object(guard, "repo_root", lambda cwd: "/srv/app"):  # a repo outside any tmp root
            for target, blocked in (("/srv/app/dist", False), ("/srv/app/build/out/", False),
                                    ("/srv/app", True), ("/srv/app/..", True), ("/srv/other/dist", True),
                                    ("/srv/app/../other", True)):
                with self.subTest(target=target):
                    self.assertEqual(bool(guard.rm_reason(["rm", "-rf", target], "/srv/app")), blocked)

    def test_bash_through_a_link_to_a_secret_is_blocked(self):
        (self.repo / ".env").write_text("KEY=1\n")
        (self.repo / "notes.txt").symlink_to(self.repo / ".env")
        self.assertEqual(self.bash("cat notes.txt"), 2)
        self.assertEqual(self.bash("cat README.md"), 0)
        outside = self.home / "ssh-copy"
        outside.mkdir()
        (self.repo / "cache").symlink_to(outside)
        self.assertEqual(self.bash("rm -rf cache"), 0)  # removes the link itself, harmless
        (self.repo / "sys").symlink_to("/usr")
        self.assertEqual(self.bash("rm -rf sys"), 0)   # the link only
        self.assertEqual(self.bash("rm -rf sys/"), 2)  # follows the link: deletes /usr's contents

    def test_a_link_to_a_secret_is_the_secret(self):
        (self.repo / ".env").write_text("KEY=1\n")
        (self.repo / "notes.txt").symlink_to(self.repo / ".env")
        tool = {"tool_name": "Read", "cwd": str(self.repo), "tool_input": {"file_path": "notes.txt"}}
        self.assertEqual(self.hook("guard.py", tool).returncode, 2)
        (self.repo / "plain.txt").write_text("x\n")
        tool["tool_input"]["file_path"] = "plain.txt"
        self.assertEqual(self.hook("guard.py", tool).returncode, 0)

