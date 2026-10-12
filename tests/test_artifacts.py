"""Fast artifact checks, no subprocess: the files Claude Code and GitHub read must be well-formed.

Runs alone in seconds (`python3 -m unittest discover -s tests -p test_artifacts.py`). PyYAML, when installed
(CI installs it), adds the real parser; without it the strict subset parser in scripts/lint.py still runs.
"""
import json
import re
import sys
import unittest

from helpers import ROOT

sys.path.insert(0, str(ROOT / "scripts"))
import lint  # noqa: E402


class ArtifactsTest(unittest.TestCase):
    def test_every_agent_and_skill_frontmatter_parses_strictly(self):
        files = sorted((ROOT / "agents").glob("*.md")) + sorted((ROOT / "skills").glob("*/SKILL.md"))
        self.assertGreater(len(files), 10)
        for path in files:
            with self.subTest(path=str(path.relative_to(ROOT))):
                fm, _ = lint.parse_frontmatter(path.read_text())
                self.assertTrue(str(fm.get("description", "")).strip())

    def test_presets_have_the_fields_the_loader_reads(self):
        for path in sorted((ROOT / "presets").glob("*.json")):
            with self.subTest(preset=path.name):
                data = json.loads(path.read_text())
                self.assertEqual(data.get("name"), path.stem)
                for key in ("summary", "session", "needs", "settings"):
                    self.assertIn(key, data)

    def test_manifests_agree(self):
        plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
        market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
        self.assertIn(plugin["name"], [p["name"] for p in market["plugins"]])
        top = re.search(r"^## ([\d.]+)", (ROOT / "CHANGELOG.md").read_text(), re.M)
        self.assertEqual(top.group(1), plugin["version"], "CHANGELOG's newest version must equal plugin.json's")

    def test_ci_workflow_is_valid_yaml(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        data = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
        self.assertIn("jobs", data)

    def test_hooks_json_is_valid(self):
        data = json.loads((ROOT / "hooks" / "hooks.json").read_text())
        self.assertIn("hooks", data)


if __name__ == "__main__":
    unittest.main()
