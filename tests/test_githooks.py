"""Covers .githooks/pre-push against the refspec digest actually pushes.

Nothing in the suite exercised the hooks, which is how a hook that rejected
every scheduled digest push survived three runs (2026-08-17, 08-24, 08-31).
Each one committed, failed to push, stranded the commit, and reported an exit
status with no reason attached.
"""
import shutil
import subprocess
import unittest
from pathlib import Path

from .support import PipelineTestCase, git, init_repo_with_remote

REAL_ROOT = Path(__file__).resolve().parent.parent


class PrePushHookTest(PipelineTestCase):
    def _repo_with_hooks(self):
        """A throwaway repo running the real hook against the real hygiene
        rules, so this can't pass while the shipped hook is broken."""
        init_repo_with_remote(self.project_dir)
        shutil.copytree(REAL_ROOT / ".githooks", self.project_dir / ".githooks")
        (self.project_dir / "scripts").mkdir(exist_ok=True)
        shutil.copy(REAL_ROOT / "scripts" / "hygiene.py",
                    self.project_dir / "scripts" / "hygiene.py")
        # The hook's last step runs the suite against $root. Give it an empty
        # package so discovery succeeds trivially instead of re-entering the
        # real suite from inside a test.
        (self.project_dir / "tests").mkdir(exist_ok=True)
        (self.project_dir / "tests" / "__init__.py").write_text("")
        git("config", "core.hooksPath", ".githooks", cwd=self.project_dir)

    def _commit_digest(self, date="2026-08-31"):
        (self.project_dir / "matches").mkdir(exist_ok=True)
        (self.project_dir / "matches" / f"{date}.md").write_text("# Matches\n")
        git("add", f"matches/{date}.md", cwd=self.project_dir)
        git("commit", "-m", f"Weekly matches: {date}", cwd=self.project_dir)

    def _push(self, *refspec):
        return subprocess.run(["git", "push", "origin", *refspec],
                              cwd=str(self.project_dir), capture_output=True, text=True)

    def test_the_refspec_digest_actually_pushes_is_accepted(self):
        """`HEAD:main` reports its local ref as the literal string "HEAD",
        which is not a branch name. Rejecting it here failed three real runs."""
        self._repo_with_hooks()
        self._commit_digest()
        proc = self._push("HEAD:main")
        self.assertEqual(proc.returncode, 0, f"digest push rejected:\n{proc.stderr}")

    def test_a_named_branch_refspec_still_works(self):
        self._repo_with_hooks()
        self._commit_digest()
        proc = self._push("main:main")
        self.assertEqual(proc.returncode, 0, f"named-branch push rejected:\n{proc.stderr}")

    def test_a_badly_named_branch_is_still_rejected(self):
        """Resolving HEAD must not weaken the check it was bypassing."""
        self._repo_with_hooks()
        git("checkout", "-b", "notaslug", cwd=self.project_dir)
        self._commit_digest()
        proc = self._push("notaslug:notaslug")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("should look like", proc.stderr)

    def test_a_non_digest_commit_pushed_to_main_is_still_rejected(self):
        """The HEAD resolution must not smuggle arbitrary commits onto main:
        push-to-main runs after the branch check and still has to fire."""
        self._repo_with_hooks()
        (self.project_dir / "unrelated.py").write_text("x = 1\n")
        git("add", "unrelated.py", cwd=self.project_dir)
        git("commit", "-m", "not a digest commit", cwd=self.project_dir)
        proc = self._push("HEAD:main")
        self.assertNotEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
