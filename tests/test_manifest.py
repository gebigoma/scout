import json
import unittest

from pipeline import manifest, paths

from .support import RUN_DATE, PipelineTestCase


class ManifestLifecycleTest(PipelineTestCase):
    def test_load_returns_a_fresh_in_progress_manifest(self):
        m = manifest.load(RUN_DATE)
        self.assertEqual(m["date"], RUN_DATE)
        self.assertEqual(m["status"], "in_progress")
        self.assertIsNone(m["finished_at"])
        self.assertIsNone(m["error"])
        self.assertEqual(sorted(m["stages"]), sorted(manifest.STAGES))
        self.assertTrue(all(s["status"] == "pending" for s in m["stages"].values()))

    def test_load_does_not_write_anything_to_disk(self):
        manifest.load(RUN_DATE)
        self.assertFalse(paths.manifest_path(RUN_DATE).exists())

    def test_stage_started_persists_running_status(self):
        manifest.stage_started(RUN_DATE, "fetch")
        m = self.read_json(paths.manifest_path(RUN_DATE))
        self.assertEqual(m["stages"]["fetch"]["status"], "running")
        self.assertIn("started_at", m["stages"]["fetch"])

    def test_stage_succeeded_records_duration_and_details(self):
        manifest.stage_started(RUN_DATE, "fetch")
        m = manifest.stage_succeeded(RUN_DATE, "fetch", total_count=42)
        entry = m["stages"]["fetch"]
        self.assertEqual(entry["status"], "success")
        self.assertEqual(entry["total_count"], 42)
        self.assertGreaterEqual(entry["duration_s"], 0)
        self.assertIsNotNone(entry["finished_at"])

    def test_stage_succeeded_without_a_start_omits_duration(self):
        """Stages resumed from a cached checkpoint never call stage_started."""
        m = manifest.stage_succeeded(RUN_DATE, "fetch", total_count=1)
        self.assertNotIn("duration_s", m["stages"]["fetch"])
        self.assertIsNone(m["stages"]["fetch"]["started_at"])

    def test_stage_failed_marks_the_whole_run_failed(self):
        manifest.stage_started(RUN_DATE, "classify")
        m = manifest.stage_failed(RUN_DATE, "classify", "exit 1", attempts=2)
        self.assertEqual(m["stages"]["classify"]["status"], "failed")
        self.assertEqual(m["stages"]["classify"]["attempts"], 2)
        self.assertEqual(m["status"], "failed")
        self.assertEqual(m["error"], "classify: exit 1")
        self.assertIsNotNone(m["finished_at"])

    def test_run_succeeded_clears_a_stale_error_from_an_earlier_attempt(self):
        """A resumed run must not report status=success next to the error
        left behind by the attempt that failed."""
        manifest.stage_failed(RUN_DATE, "classify", "exit 1")
        m = manifest.run_succeeded(RUN_DATE)
        self.assertEqual(m["status"], "success")
        self.assertIsNone(m["error"])
        self.assertIsNotNone(m["finished_at"])

    def test_updates_accumulate_across_stages(self):
        manifest.stage_started(RUN_DATE, "fetch")
        manifest.stage_succeeded(RUN_DATE, "fetch", total_count=3)
        manifest.stage_started(RUN_DATE, "normalize")
        m = manifest.stage_succeeded(RUN_DATE, "normalize", count=3)
        self.assertEqual(m["stages"]["fetch"]["status"], "success")
        self.assertEqual(m["stages"]["normalize"]["status"], "success")
        self.assertEqual(m["stages"]["dedupe"]["status"], "pending")

    def test_manifest_is_valid_json_after_every_transition(self):
        manifest.stage_started(RUN_DATE, "fetch")
        json.loads(paths.manifest_path(RUN_DATE).read_text())
        manifest.stage_succeeded(RUN_DATE, "fetch", total_count=1)
        json.loads(paths.manifest_path(RUN_DATE).read_text())


if __name__ == "__main__":
    unittest.main()
