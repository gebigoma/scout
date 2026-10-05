"""Covers scripts/git-freshen.sh against a real repo and a real remote.

The job exists because a behind-main clone makes `digest`'s push fail
unrecoverably (issue #34): the 2026-09-28 run lost its publish that way, and
it recurred three days later. These pin the two states that matter - behind
(fast-forward it) and diverged (don't touch it, say so) - plus the guards
that keep a scheduled job from mutating work in progress.
"""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pipeline import runlock

from .support import PipelineTestCase, git, init_repo_with_remote

REAL_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REAL_ROOT / "scripts" / "git-freshen.sh"


class GitFreshenTest(PipelineTestCase):
    def _run(self, *args, cwd=None):
        return subprocess.run(["sh", str(SCRIPT), *args],
                              cwd=str(cwd or self.project_dir),
                              capture_output=True, text=True)

    def _second_clone(self):
        """A second clone of the same bare remote, so the remote can advance
        behind this clone's back - the real shape of a GitHub-side merge.

        In its own cleaned temp dir, deliberately *outside* project_dir:
        nested under it, the clone shows up as an untracked path and the
        script's own dirty-worktree guard fires, so the tests for behind and
        diverged would pass for the wrong reason. Not a bare sibling either -
        that would survive the test and collide on the next run.
        """
        remote = self.project_dir.parent / (self.project_dir.name + "-remote.git")
        scratch = Path(tempfile.mkdtemp(prefix="scout-test-clone-")).resolve()
        self.addCleanup(shutil.rmtree, scratch, ignore_errors=True)
        other = scratch / "second-clone"
        git("clone", str(remote), str(other), cwd=scratch)
        git("config", "user.email", "other@example.com", cwd=other)
        git("config", "user.name", "other clone", cwd=other)
        return other

    def _advance_remote(self, filename="from-elsewhere.txt"):
        other = self._second_clone()
        (other / filename).write_text("landed via another clone\n")
        git("add", filename, cwd=other)
        git("commit", "-m", f"add {filename}", cwd=other)
        git("push", "origin", "HEAD:main", cwd=other)
        return git("rev-parse", "HEAD", cwd=other).strip()

    # --- the case the job exists for -------------------------------------

    def test_a_behind_clone_is_fast_forwarded(self):
        init_repo_with_remote(self.project_dir)
        remote_head = self._advance_remote()

        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("fast-forwarded main", proc.stdout)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.project_dir).strip(),
                         remote_head)

    def test_fast_forward_introduces_no_merge_commit(self):
        """A merge commit on main would be rejected by hygiene.check_main_push,
        so this has to stay a true fast-forward."""
        init_repo_with_remote(self.project_dir)
        self._advance_remote()
        self._run()
        parents = git("rev-list", "--parents", "-1", "HEAD",
                      cwd=self.project_dir).split()
        self.assertEqual(len(parents), 2, "expected exactly one parent")

    def test_an_up_to_date_clone_is_left_alone(self):
        init_repo_with_remote(self.project_dir)
        before = git("rev-parse", "HEAD", cwd=self.project_dir).strip()
        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("already up to date", proc.stdout)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.project_dir).strip(),
                         before)

    # --- the state it must not try to fix --------------------------------

    def test_a_diverged_main_is_reported_not_resolved(self):
        """The 2026-09-28 state: a stranded local commit plus a moved remote.
        Resolving it means choosing rebase or merge, which is not a scheduled
        job's call - but it must not pass silently either."""
        init_repo_with_remote(self.project_dir)
        self._advance_remote()
        (self.project_dir / "local-only.txt").write_text("local work\n")
        git("add", "local-only.txt", cwd=self.project_dir)
        git("commit", "-m", "local commit", cwd=self.project_dir)
        local_head = git("rev-parse", "HEAD", cwd=self.project_dir).strip()

        proc = self._run()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("diverged", proc.stderr)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.project_dir).strip(),
                         local_head, "diverged main must not be touched")

    def test_unpushed_commits_alone_are_not_a_divergence(self):
        init_repo_with_remote(self.project_dir)
        (self.project_dir / "local-only.txt").write_text("local work\n")
        git("add", "local-only.txt", cwd=self.project_dir)
        git("commit", "-m", "local commit", cwd=self.project_dir)

        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("unpushed commit", proc.stdout)

    # --- guards against mutating work in progress ------------------------

    # --- the run lock: whose lock it is matters -------------------------

    def _hold_target_lock(self):
        """Take the target repo's own run lock, as a pipeline run in that repo
        would. logs/ is excluded so the dirty-worktree guard, which runs
        first, doesn't answer before the lock check does."""
        with (self.project_dir / ".git" / "info" / "exclude").open("a") as f:
            f.write("logs/\n")
        cm = runlock.single_run()  # paths.PROJECT_DIR is the temp repo here
        cm.__enter__()
        self.addCleanup(cm.__exit__, None, None, None)

    def test_a_run_in_progress_in_the_target_repo_is_respected(self):
        """Fast-forwarding main under a running pipeline would change its
        working tree mid-run. Never tested until 2026-10-05: the only thing
        that exercised this path was the bug below, by accident."""
        init_repo_with_remote(self.project_dir)
        self._advance_remote()
        self._hold_target_lock()
        head = git("rev-parse", "HEAD", cwd=self.project_dir).strip()

        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("pipeline run in progress, skipping", proc.stdout)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.project_dir).strip(), head)

    def test_a_lock_held_beside_the_script_does_not_block_another_repo(self):
        """The 2026-10-05 failure. The check read the logs/run.lock next to
        the script, not the target repo's, so whenever a real run held the
        real lock every freshen test saw "run in progress" and failed - and
        the pre-push hook runs the suite during digest's push, which is
        exactly when a run holds it. Every digest push was rejected. Replayed
        with a copy of scripts/ whose own lock is held, so the real repo's
        lock is never touched."""
        init_repo_with_remote(self.project_dir)
        remote_head = self._advance_remote()

        elsewhere = Path(tempfile.mkdtemp(prefix="scout-test-scriptroot-")).resolve()
        self.addCleanup(shutil.rmtree, elsewhere, ignore_errors=True)
        shutil.copytree(REAL_ROOT / "scripts", elsewhere / "scripts",
                        ignore=shutil.ignore_patterns("__pycache__"))
        holder = subprocess.Popen(
            [sys.executable, "-c",
             "import sys, time; sys.path.insert(0, sys.argv[1]);"
             "from pipeline import runlock\n"
             "with runlock.single_run():\n"
             "    print('held', flush=True); time.sleep(60)",
             str(elsewhere / "scripts")],
            stdout=subprocess.PIPE, text=True)
        self.addCleanup(holder.wait)
        self.addCleanup(holder.kill)
        self.assertEqual(holder.stdout.readline().strip(), "held")

        proc = subprocess.run(["sh", str(elsewhere / "scripts" / "git-freshen.sh")],
                              cwd=str(self.project_dir), capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("run in progress", proc.stdout)
        self.assertIn("fast-forwarded main", proc.stdout)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.project_dir).strip(),
                         remote_head)

    def test_it_does_nothing_off_main(self):
        init_repo_with_remote(self.project_dir)
        self._advance_remote()
        git("checkout", "-b", "feat/in-progress", cwd=self.project_dir)
        before = git("rev-parse", "HEAD", cwd=self.project_dir).strip()

        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("not main", proc.stdout)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.project_dir).strip(),
                         before)

    def test_it_does_nothing_with_a_dirty_worktree(self):
        init_repo_with_remote(self.project_dir)
        self._advance_remote()
        (self.project_dir / "README.md").write_text("edited, uncommitted\n")
        before = git("rev-parse", "HEAD", cwd=self.project_dir).strip()

        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("dirty", proc.stdout)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.project_dir).strip(),
                         before)

    # --- dry run ----------------------------------------------------------

    def test_dry_run_does_not_fetch_and_says_so(self):
        """--dry-run performs no writes at all, and `git fetch` writes to
        .git - so on an unfetched clone it cannot know the remote moved. It
        has to report that limit rather than imply it checked, which is the
        bug f6454e3 fixed for backup_private.sh's --dry-run."""
        init_repo_with_remote(self.project_dir)
        self._advance_remote()
        before = git("rev-parse", "HEAD", cwd=self.project_dir).strip()
        remote_ref_before = git("rev-parse", "origin/main",
                                cwd=self.project_dir).strip()

        proc = self._run("--dry-run")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("not fetched", proc.stdout)
        self.assertIn("DRY RUN", proc.stdout)
        self.assertNotIn("would fast-forward", proc.stdout)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.project_dir).strip(),
                         before)
        self.assertEqual(git("rev-parse", "origin/main",
                             cwd=self.project_dir).strip(), remote_ref_before,
                         "--dry-run must not update remote-tracking refs")

    def test_dry_run_reports_the_fast_forward_once_refs_are_fresh(self):
        init_repo_with_remote(self.project_dir)
        self._advance_remote()
        git("fetch", "origin", cwd=self.project_dir)
        before = git("rev-parse", "HEAD", cwd=self.project_dir).strip()

        proc = self._run("--dry-run")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("would fast-forward main", proc.stdout)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.project_dir).strip(),
                         before, "--dry-run must not move the branch")

    def test_a_stray_argument_after_dry_run_is_an_error_not_a_real_run(self):
        """Same reasoning as backup_private.sh's flag handling: silently
        falling through to a real run is how a typo does damage."""
        init_repo_with_remote(self.project_dir)
        self._advance_remote()
        before = git("rev-parse", "HEAD", cwd=self.project_dir).strip()

        proc = self._run("--dry-run", "--whoops")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.project_dir).strip(),
                         before)

    def test_an_unknown_flag_is_an_error(self):
        init_repo_with_remote(self.project_dir)
        proc = self._run("--definitely-not-a-flag")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("usage:", proc.stderr)

    def test_outside_a_repo_it_fails_cleanly(self):
        outside = self.project_dir / "not-a-repo"
        outside.mkdir()
        proc = self._run(cwd=outside)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("not a git repository", proc.stderr)


if __name__ == "__main__":
    unittest.main()
