import importlib
import os
import unittest
from unittest import mock

from pipeline import llm

from .support import PipelineTestCase


class ClaudeBinTest(PipelineTestCase):
    def test_explicit_claude_bin_wins(self):
        """The launchd job runs without the user's interactive PATH, so it
        sets CLAUDE_BIN explicitly."""
        os.environ["CLAUDE_BIN"] = "/opt/claude/bin/claude"
        with mock.patch.object(llm.shutil, "which", return_value="/usr/local/bin/claude"):
            self.assertEqual(llm.claude_bin(), "/opt/claude/bin/claude")

    def test_falls_back_to_the_cli_on_path(self):
        os.environ.pop("CLAUDE_BIN", None)
        with mock.patch.object(llm.shutil, "which", return_value="/usr/local/bin/claude"):
            self.assertEqual(llm.claude_bin(), "/usr/local/bin/claude")

    def test_a_missing_cli_raises_an_actionable_error(self):
        """A hardcoded path would make the repo useless on any other machine,
        so the failure has to explain the two ways to fix it."""
        os.environ.pop("CLAUDE_BIN", None)
        with mock.patch.object(llm.shutil, "which", return_value=None):
            with self.assertRaises(RuntimeError) as ctx:
                llm.claude_bin()
        self.assertIn("CLAUDE_BIN", str(ctx.exception))


class ModelTest(PipelineTestCase):
    def _reload_with_env(self, **env):
        """Reload llm with env applied; a None value means "unset"."""
        with mock.patch.dict(os.environ, {k: v for k, v in env.items() if v is not None}):
            for key, value in env.items():
                if value is None:
                    os.environ.pop(key, None)
            return importlib.reload(llm)

    def tearDown(self):
        importlib.reload(llm)
        super().tearDown()

    def test_model_is_pinned_rather_than_inherited_from_the_cli_default(self):
        """Without a pin these calls take whatever /model the user last chose
        interactively, so an editor preference silently changes what every
        scheduled run costs."""
        self.assertEqual(self._reload_with_env(SCOUT_MODEL=None).MODEL, "sonnet")

    def test_scout_model_overrides_the_pin_for_a_one_off(self):
        self.assertEqual(self._reload_with_env(SCOUT_MODEL="opus").MODEL, "opus")


if __name__ == "__main__":
    unittest.main()
