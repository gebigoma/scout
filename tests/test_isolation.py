"""The suite must never touch a real repository, whatever environment it was
launched from. Run in a child process because the guard lives in tests/
__init__.py: inside this process GIT_DIR is already gone, so asserting on it
here would pass whether or not the guard exists."""
import os
import subprocess
import sys
import unittest
from pathlib import Path

from .support import PipelineTestCase, git

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class InheritedGitEnvTest(PipelineTestCase):
    def test_a_suite_launched_with_git_dir_set_leaves_that_repo_alone(self):
        """The 2026-10-05 incident, replayed against a throwaway repo: with
        GIT_DIR inherited, a test that builds its own repo rewrote the real
        one's identity to the test fixture and committed onto its branch."""
        victim = self.project_dir / "victim"
        victim.mkdir()
        git("init", "-b", "main", cwd=victim)
        git("config", "user.name", "Real Owner", cwd=victim)
        git("config", "user.email", "owner@example.com", cwd=victim)
        (victim / "f").write_text("x\n")
        git("add", "f", cwd=victim)
        git("commit", "-m", "real work", cwd=victim)
        head_before = git("rev-parse", "HEAD", cwd=victim)

        env = dict(os.environ, GIT_DIR=str(victim / ".git"))
        # One test that builds a repo through support.init_repo_with_remote.
        proc = subprocess.run(
            [sys.executable, "-m", "unittest",
             "tests.test_digest.UnpushedCommitCountTest.test_counts_local_commits_not_yet_pushed"],
            cwd=str(PROJECT_ROOT), env=env, capture_output=True, text=True, timeout=120)

        # The victim first: a regression should name what it overwrote, not
        # just report that the child test errored.
        self.assertEqual(git("config", "user.name", cwd=victim), "Real Owner")
        self.assertEqual(git("config", "user.email", cwd=victim), "owner@example.com")
        self.assertEqual(git("config", "--bool", "core.bare", cwd=victim), "false")
        self.assertEqual(git("rev-parse", "HEAD", cwd=victim), head_before)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
