"""Shared test harness.

Every stage writes under `paths.PROJECT_DIR` (checkpoints, manifests, logs,
seen.json, matches/). PipelineTestCase repoints that constant at a fresh
temp directory per test, so the suite never touches the real repo and tests
can't leak state into each other.
"""
import logging
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pipeline import paths

RUN_DATE = "2026-08-03"

ROLE_CRITERIA_STUB = """# What counts as a match

A listing is a match if it is explicitly fractional AND a senior TPM or
agentic AI engineering role.

## Fit score guide

85-100 ideal, 60-84 strong, 35-59 marginal.
"""


class PipelineTestCase(unittest.TestCase):
    def setUp(self):
        # .resolve() matters on macOS: tempfile hands back /var/... which is a
        # symlink to /private/var, and git rejects absolute paths that don't
        # match the resolved worktree root.
        self.project_dir = Path(tempfile.mkdtemp(prefix="scout-test-")).resolve()
        self.addCleanup(shutil.rmtree, self.project_dir, ignore_errors=True)

        patcher = mock.patch.object(paths, "PROJECT_DIR", self.project_dir)
        patcher.start()
        self.addCleanup(patcher.stop)

        # Loggers are cached process-wide by name and hold an open handler on
        # the (soon to be deleted) temp logs dir, so drop them after each test.
        self.addCleanup(self._reset_loggers)

        # Never send a real push notification from a test run.
        env_patcher = mock.patch.dict(os.environ)
        env_patcher.start()
        self.addCleanup(env_patcher.stop)
        os.environ.pop("NTFY_TOPIC", None)

    @staticmethod
    def _reset_loggers():
        manager = logging.Logger.manager
        for name in [n for n in manager.loggerDict if n.startswith("scout")]:
            logger = manager.loggerDict[name]
            for handler in list(getattr(logger, "handlers", [])):
                handler.close()
                logger.removeHandler(handler)
            del manager.loggerDict[name]

    def write_role_criteria(self, text=ROLE_CRITERIA_STUB):
        path = self.project_dir / "ROLE_CRITERIA.md"
        path.write_text(text)
        return path

    def patch_sleep(self, module):
        """Silence backoff delays so retry paths run instantly."""
        patcher = mock.patch.object(module.time, "sleep")
        sleep = patcher.start()
        self.addCleanup(patcher.stop)
        return sleep

    def read_json(self, path):
        import json
        return json.loads(Path(path).read_text())


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), check=True,
                          capture_output=True, text=True).stdout.strip()


def init_repo_with_remote(project_dir):
    """Make project_dir a git repo on `main` tracking a local bare remote, so
    digest's commit+push path can be exercised for real without a network."""
    remote = project_dir.parent / (project_dir.name + "-remote.git")
    git("init", "--bare", "-b", "main", str(remote), cwd=project_dir.parent)

    git("init", "-b", "main", cwd=project_dir)
    git("config", "user.email", "test@example.com", cwd=project_dir)
    git("config", "user.name", "scout tests", cwd=project_dir)
    (project_dir / "README.md").write_text("scout test repo\n")
    git("add", "README.md", cwd=project_dir)
    git("commit", "-m", "initial", cwd=project_dir)
    git("remote", "add", "origin", str(remote), cwd=project_dir)
    git("push", "-u", "origin", "main", cwd=project_dir)
    return remote
