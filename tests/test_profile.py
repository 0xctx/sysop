import sys

from helpers import ROOT, TempRepoCase, git

sys.path.insert(0, str(ROOT / "scripts"))
import repo_profile  # noqa: E402


class ProfileTest(TempRepoCase):
    def add(self, files: dict) -> None:
        for name, text in files.items():
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text if isinstance(text, bytes) else text.encode())
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "files")

    def test_code_repo(self):
        self.add({"app/main.py": "x = 1\n" * 4000, "app/util.ts": "let a = 1;\n" * 500})
        p = repo_profile.profile(self.repo)
        self.assertEqual(p["kind"], "code")
        self.assertEqual(p["languages"][0]["name"], "python")
        self.assertEqual(repo_profile.skill_for(p["kind"]), "code")

    def test_prose_repo_is_judged_by_writing(self):
        self.add({f"chapters/{i:02}.md": "It was a dark night. " * 2000 for i in range(5)})
        self.add({"tools/wordcount.py": "print(1)\n" * 20})
        p = repo_profile.profile(self.repo)
        self.assertEqual(p["kind"], "prose")
        self.assertEqual(repo_profile.skill_for(p["kind"]), "writing")

    def test_mixed_repo(self):
        self.add({"docs/guide.md": "word " * 4000, "src/lib.rs": "fn a() {}\n" * 2500})
        self.assertEqual(repo_profile.profile(self.repo)["kind"], "mixed")

    def test_binary_and_huge_files_are_ignored(self):
        self.add({"logo.png": b"\x89PNG\0" * 1000, "data.bin": b"\0" * 10, "big.py": "x" * 2_100_000,
                  "small.py": "y = 2\n" * 100})
        p = repo_profile.profile(self.repo)
        self.assertEqual(p["kind"], "code")
        self.assertLess(p["tokens"], 1000)

    def test_only_the_readme(self):
        p = repo_profile.profile(self.repo)  # README.md from the fixture
        self.assertEqual(p["kind"], "prose")

    def test_outside_git_raises(self):
        with self.assertRaises(OSError):
            repo_profile.profile(self.tmp)


    def test_time_budget_gives_a_partial_but_scaled_profile(self):
        self.add({f"src/m{i}.py": "x = 1\n" * 100 for i in range(50)})
        p = repo_profile.profile(self.repo, budget=0)
        self.assertTrue(p["partial"])
        full = repo_profile.profile(self.repo)
        self.assertFalse(full["partial"])
