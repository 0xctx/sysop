import json

from helpers import TempRepoCase, git


class ProseCheckTest(TempRepoCase):
    def write(self, name, text):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        git(self.repo, "add", name)

    def test_clean_manuscript_passes_with_progress(self):
        self.write("ch01.md", "# One\n\n## The harbour\n\nMara walked to the harbour. See [two](ch02.md).\n")
        self.write("ch02.md", "# Two\n\nShe had had enough.\n")
        proc = self.sysop("prose-check", "--words", "100")
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("0 error(s)", proc.stdout)
        self.assertIn("of 100 words", proc.stdout)

    def test_errors_and_warnings(self):
        self.write("ch01.md", "# One\n\n### Too deep\n\nThe the boat. TK check date.\n"
                              "![map](img/map.png) and [web](https://x.dev)\n\n```\nunclosed\n")
        proc = self.sysop("prose-check", "--json")
        self.assertEqual(proc.returncode, 1)
        out = json.loads(proc.stdout)
        issues = {f["issue"]: f["level"] for f in out["findings"]}
        self.assertEqual(issues["broken link: img/map.png"], "error")
        self.assertEqual(issues["code fence never closed"], "error")
        self.assertEqual(issues["heading jumps from H1 to H3"], "warning")
        self.assertEqual(issues["leftover TK marker"], "warning")
        self.assertEqual(issues['doubled word: "The the"'], "warning")
        self.assertNotIn("broken link: https://x.dev", issues)

    def test_code_inside_fences_is_not_prose(self):
        self.write("a.md", "# A\n\n```\nthe the [x](missing.md) TODO\n```\n\nRun `config set notify notify-send` or flag `TK`.\n")
        self.assertEqual(self.sysop("prose-check").returncode, 0)

    def test_the_check_setting_names_the_installed_gate(self):
        self.write("a.md", "# A\n\n[x](nowhere.md)\n")
        self.sysop("config", "set", "check", "sysop:prose-check")
        shown = json.loads(self.sysop("settings", "--key", "check").stdout)
        self.assertTrue(shown.endswith("sysop.py prose-check"), shown)
        import subprocess
        self.assertEqual(subprocess.run(shown, shell=True, cwd=self.repo, capture_output=True).returncode, 1)
