import json
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import ROOT, TempRepoCase, run

LINT = ROOT / "scripts" / "lint.py"


def skill(desc="Does a thing. Use when the user says 'do the thing'.", name=None, body="Do it.\n",
          extra=""):
    return f"---\nname: {name}\ndescription: {desc}\n{extra}---\n\n{body}"


class LintTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def add_skill(self, dirname, text, files=()):
        d = self.root / "skills" / dirname
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(text)
        for f in files:
            (d / f).parent.mkdir(parents=True, exist_ok=True)
            (d / f).write_text("x")

    def lint(self):
        return run([sys.executable, str(LINT), str(self.root)], self.root)

    def assert_fails(self, needle):
        proc = self.lint()
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn(needle, proc.stdout)

    def test_good_plugin_passes(self):
        self.add_skill("ship", skill(name="ship", body="Read `references/x.md` first.\n"),
                       files=["references/x.md"])
        self.add_skill("tune", skill(name="tune", extra="disable-model-invocation: true\n"))
        (self.root / "agents").mkdir()
        (self.root / "agents" / "explorer.md").write_text(
            "---\nname: explorer\ndescription: Finds code.\ntools: Read, Grep, Glob, Bash\n---\nbody\n")
        proc = self.lint()
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("user-only", proc.stdout)

    def test_name_must_match_directory(self):
        self.add_skill("ship", skill(name="shipit"))
        self.assert_fails("must equal the directory")

    def test_missing_frontmatter(self):
        self.add_skill("ship", "no frontmatter here\n")
        self.assert_fails("frontmatter")

    def test_colon_in_plain_description_is_invalid_yaml(self):
        self.add_skill("ship", skill(desc="Owns one change: contract, plan, build.", name="ship"))
        self.assert_fails("not valid YAML")

    def test_block_description_may_hold_colons(self):
        self.add_skill("ship", skill(desc=">-\n  Owns one change: contract, plan, build.", name="ship"))
        proc = self.lint()
        self.assertEqual(proc.returncode, 0, proc.stdout)

    def test_strict_frontmatter_rejections(self):
        # PyYAML (when installed) reports first with its own wording, so assert the skill is named, not the text
        cases = {
            "duplicate": ("Does a thing.", "description: again\n", "skills/ship:"),
            "tabs": ("Does a thing.", "\tindented: x\n", "skills/ship:"),
            "quote": ('"never closed', "", "skills/ship:"),
            "flow": ("[a, b", "", "skills/ship:"),
        }
        for name, (desc, extra, needle) in cases.items():
            with self.subTest(name):
                self.setUp()
                self.add_skill("ship", skill(desc=desc, name="ship", extra=extra))
                self.assert_fails(needle)
                self.tearDown()

    def test_empty_description(self):
        self.add_skill("ship", skill(name="ship", desc='""'))
        self.assert_fails("description is empty")

    def test_listing_cap_counts_when_to_use(self):
        self.add_skill("ship", skill(name="ship", desc="a" * 1000, extra=f"when_to_use: {'b' * 600}\n"))
        self.assert_fails("cap 1536")

    def test_body_line_limit(self):
        self.add_skill("ship", skill(name="ship", body="line\n" * 520))
        self.assert_fails("body is")

    def test_dead_reference(self):
        self.add_skill("ship", skill(name="ship", body="See `references/missing.md`.\n"))
        self.assert_fails("missing file")

    def test_unlinked_bundled_file(self):
        self.add_skill("ship", skill(name="ship"), files=["templates/report.md"])
        self.assert_fails("not linked")

    def test_agent_tools_restricted(self):
        (self.root / "agents").mkdir()
        (self.root / "agents" / "bad.md").write_text(
            "---\nname: bad\ndescription: x\ntools: Read, Edit\n---\n")
        self.assert_fails("exceed")

    def test_agent_tools_required(self):
        (self.root / "agents").mkdir()
        (self.root / "agents" / "bad.md").write_text("---\nname: bad\ndescription: x\n---\n")
        self.assert_fails("tools must be listed")

    def test_hook_script_must_exist(self):
        (self.root / "hooks").mkdir()
        (self.root / "hooks" / "hooks.json").write_text(json.dumps({"hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": 'python3 "${CLAUDE_PLUGIN_ROOT}/hooks/nope.py"'}]}]}}))
        self.assert_fails("missing hooks/nope.py")

    def test_hook_module_must_exist(self):
        (self.root / "hooks").mkdir()
        (self.root / "hooks" / "hooks.json").write_text(json.dumps({"modules": ["./nope.mod.js"]}))
        self.assert_fails("module ./nope.mod.js is missing")

    def test_budget_fails_and_user_only_is_free(self):
        for i in range(5):
            self.add_skill(f"s{i}", skill(name=f"s{i}", desc="d" * 1400))
        self.assert_fails("budget")

    def test_user_only_skills_do_not_count(self):
        for i in range(5):
            self.add_skill(f"s{i}", skill(name=f"s{i}", desc="d" * 1400,
                                          extra="disable-model-invocation: true\n"))
        self.assertEqual(self.lint().returncode, 0)

    def test_folded_description(self):
        self.add_skill("ship", "---\nname: ship\ndescription: >-\n  Folded text\n  continues.\n---\nbody\n")
        self.assertEqual(self.lint().returncode, 0)

    def test_dead_plugin_root_pointer(self):
        self.add_skill("ship", skill(name="ship", body="See `${CLAUDE_PLUGIN_ROOT}/skills/plan/nope.md`.\n"))
        self.assert_fails("missing plugin file")

    def test_uncovered_dynamic_context_fails(self):
        self.add_skill("ship", skill(name="ship", body="Store: !`python3 x.py store`\n"))
        self.assert_fails("not covered by allowed-tools")

    def test_covered_dynamic_context_passes(self):
        self.add_skill("ship", skill(name="ship", body="Store: !`python3 x.py store`\n",
                                     extra="allowed-tools: Bash(python3 x.py *)\n"))
        self.assertEqual(self.lint().returncode, 0)

    def test_example_text_that_looks_like_injection_fails(self):
        self.add_skill("doc", skill(name="doc", body="Use dynamic context `` !`cmd` `` carefully.\n"))
        self.assert_fails("`cmd`")

    def test_repo_itself_is_clean(self):
        proc = run([sys.executable, str(LINT), str(ROOT)], ROOT)
        self.assertEqual(proc.returncode, 0, proc.stdout)


class InjectedCommandsTest(TempRepoCase):
    def test_every_injected_command_exits_0_outside_git(self):
        """Skills run these at load time; one that fails outside a git repo aborts the skill."""
        import re
        import subprocess
        import sys
        from helpers import ROOT
        cmds = set()
        for skill in (ROOT / "skills").glob("*/SKILL.md"):
            cmds |= set(re.findall(r"!`python3 \$\{CLAUDE_PLUGIN_ROOT\}/scripts/sysop\.py ([^`]*)`", skill.read_text()))
        self.assertGreater(len(cmds), 5)
        self.use_fakebin()
        nogit = self.tmp / "nogit"
        nogit.mkdir()
        for args in sorted(cmds):
            proc = subprocess.run(f"{sys.executable} {ROOT}/scripts/sysop.py {args}", shell=True, cwd=nogit,
                                  env=self.env, capture_output=True, text=True, timeout=120)
            self.assertEqual(proc.returncode, 0, f"`sysop.py {args}` outside git: {proc.stderr[-300:]}")


class StraySkillTest(TempRepoCase):
    def test_a_skill_inside_claude_plugin_fails_lint(self):
        import subprocess
        import sys
        plug = self.tmp / "plug"
        (plug / ".claude-plugin" / "skills" / "hello").mkdir(parents=True)
        (plug / ".claude-plugin" / "plugin.json").write_text('{"name": "p"}')
        (plug / ".claude-plugin" / "skills" / "hello" / "SKILL.md").write_text("---\nname: hello\ndescription: x\n---\nhi\n")
        proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "lint.py"), str(plug)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("not inside .claude-plugin/", proc.stdout)
