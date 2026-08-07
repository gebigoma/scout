import json
import os
import unittest
from unittest import mock

from pipeline import paths

from .support import PipelineTestCase


class AtomicWriteJsonTest(PipelineTestCase):
    def test_writes_readable_json(self):
        path = self.project_dir / "out.json"
        paths.atomic_write_json(path, {"a": [1, 2]})
        self.assertEqual(json.loads(path.read_text()), {"a": [1, 2]})

    def test_leaves_no_temp_file_behind(self):
        path = self.project_dir / "out.json"
        paths.atomic_write_json(path, {"a": 1})
        self.assertEqual([p.name for p in self.project_dir.iterdir()], ["out.json"])

    def test_overwrites_existing_file_completely(self):
        path = self.project_dir / "out.json"
        paths.atomic_write_json(path, {"long": "x" * 500})
        paths.atomic_write_json(path, {"short": 1})
        self.assertEqual(json.loads(path.read_text()), {"short": 1})

    def test_failure_mid_write_leaves_previous_content_intact(self):
        """The whole point of the temp-file dance: a crash during the write
        must not truncate the file that was already there."""
        path = self.project_dir / "out.json"
        paths.atomic_write_json(path, {"good": True})

        with mock.patch.object(paths.os, "replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                paths.atomic_write_json(path, {"bad": True})

        self.assertEqual(json.loads(path.read_text()), {"good": True})


class PathLayoutTest(PipelineTestCase):
    def test_run_dir_is_created_on_demand(self):
        d = paths.run_dir("2026-08-03")
        self.assertTrue(d.is_dir())
        self.assertEqual(d, self.project_dir / "data" / "runs" / "2026-08-03")

    def test_checkpoint_and_manifest_live_in_the_run_dir(self):
        self.assertEqual(paths.checkpoint_path("2026-08-03", "fetch").name, "fetch.json")
        self.assertEqual(paths.manifest_path("2026-08-03").name, "manifest.json")
        self.assertEqual(
            paths.checkpoint_path("2026-08-03", "fetch").parent,
            paths.manifest_path("2026-08-03").parent,
        )

    def test_classify_chunks_dir_is_created_on_demand(self):
        d = paths.classify_chunks_dir("2026-08-03")
        self.assertTrue(d.is_dir())
        self.assertEqual(d, paths.run_dir("2026-08-03") / "classify_chunks")

    def test_classify_chunk_path_lives_under_the_chunks_dir(self):
        p = paths.classify_chunk_path("2026-08-03", 3)
        self.assertEqual(p.name, "3.json")
        self.assertEqual(p.parent, paths.classify_chunks_dir("2026-08-03"))

    def test_logs_dir_is_created_on_demand(self):
        self.assertTrue(paths.logs_dir().is_dir())

    def test_matches_path_is_the_published_deliverable(self):
        self.assertEqual(paths.matches_path("2026-08-03"),
                         self.project_dir / "matches" / "2026-08-03.md")

    def test_prompts_ship_with_the_code_not_the_project_dir(self):
        """prompts/ is resolved relative to the package, so a repointed
        PROJECT_DIR (like this test's) still finds the real templates."""
        self.assertTrue((paths.prompts_dir() / "classify_prompt.md").exists())
        self.assertTrue((paths.prompts_dir() / "score_prompt.md").exists())

    def test_role_criteria_path_defaults_to_the_fractional_lane(self):
        self.assertEqual(paths.role_criteria_path(),
                         self.project_dir / "ROLE_CRITERIA.md")
        self.assertEqual(paths.role_criteria_path("fractional"),
                         self.project_dir / "ROLE_CRITERIA.md")

    def test_role_criteria_path_selects_the_first_tpm_lane(self):
        self.assertEqual(paths.role_criteria_path("first_tpm"),
                         self.project_dir / "ROLE_CRITERIA_FIRST_TPM.md")

    def test_companies_csv_path_lives_under_data(self):
        self.assertEqual(paths.companies_csv_path(),
                         self.project_dir / "data" / "companies.csv")


if __name__ == "__main__":
    unittest.main()
