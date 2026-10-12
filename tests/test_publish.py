"""publish: a fresh, squashed export without the build paperwork, refused when anything personal is in it."""
import json

from helpers import TempRepoCase, git


class PublishTest(TempRepoCase):
    def setUp(self):
        super().setUp()
        (self.repo / ".claude" / "plans").mkdir(parents=True)
        (self.repo / ".claude" / "plans" / "m1.md").write_text("a milestone plan\n")
        (self.repo / ".claude-plugin").mkdir()
        (self.repo / ".claude-plugin" / "plugin.json").write_text('{"name": "x", "version": "1.0.0"}\n')
        (self.repo / "docs" / "history").mkdir(parents=True)
        (self.repo / "docs" / "history" / "tickets.md").write_text("the build's tickets\n")
        (self.repo / "docs" / "build-log.md").write_text("one line per step\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "work")
        self.commit("second.txt", "more\n", "a second commit")

    def test_export_is_one_commit_without_paperwork(self):
        out = self.sysop_json("publish", str(self.tmp / "public"))
        public = self.tmp / "public"
        self.assertTrue((public / "README.md").is_file())
        self.assertTrue((public / ".claude-plugin" / "plugin.json").is_file())
        for left_out in (".claude", "docs/history", "docs/build-log.md"):
            self.assertFalse((public / left_out).exists(), left_out)
        self.assertEqual(git(public, "rev-list", "--count", "HEAD"), "1")
        self.assertEqual(git(public, "log", "--format=%s"), "sysop 1.0.0")
        self.assertIn("push -u origin main", out["next"])
        self.assertIn("not empty", self.sysop("publish", str(public)).stderr)

    def test_personal_and_secret_strings_refuse(self):
        for name, text in (("paths.md", f"see {self.home}/notes\n"),
                           ("serve.md", "domain: " + "-".join(("purple", "otter", "jumps")) + ".ngrok-free.dev\n"),
                           ("key.txt", "ANTHROPIC_API_KEY=" + "sk-" + "ant-abcdefghijklmnop\n")):
            with self.subTest(name=name):
                self.commit(name, text, f"add {name}")
                proc = self.sysop("publish", str(self.tmp / f"out-{name}"))
                self.assertEqual(proc.returncode, 1)
                self.assertIn(f"{name}:1", proc.stderr)
                git(self.repo, "rm", "-q", name)
                git(self.repo, "commit", "-qm", f"drop {name}")

    def test_an_export_that_fails_lint_refuses(self):
        (self.repo / "skills" / "bad").mkdir(parents=True)
        self.commit("skills/bad/SKILL.md", "---\nname: bad\ndescription: Owns one change: contract, plan.\n---\nbody\n",
                    "a skill with invalid YAML frontmatter")
        proc = self.sysop("publish", str(self.tmp / "lint-out"))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("lint: skills/bad", proc.stderr)

    def test_placeholders_pass(self):
        self.commit("doc.md", "claim <name>.ngrok-free.dev, then set serve.domain\n", "doc")
        self.assertEqual(self.sysop("publish", str(self.tmp / "ok")).returncode, 0)

    def test_a_link_to_what_is_left_out_or_missing_refuses(self):
        for name, text in (("a.md", "the [log](docs/build-log.md)\n"), ("b.md", "![shot](docs/img/none.png)\n")):
            with self.subTest(name=name):
                self.commit(name, text, f"add {name}")
                proc = self.sysop("publish", str(self.tmp / f"out-{name}"))
                self.assertEqual(proc.returncode, 1)
                self.assertIn(f"{name}:1", proc.stderr)
                git(self.repo, "rm", "-q", name)
                git(self.repo, "commit", "-qm", f"drop {name}")

