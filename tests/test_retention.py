"""The sweep deletes real directories, so its failure mode is data loss
rather than a wrong number. The tests lean on what it must *refuse* to
delete at least as hard as on what it removes."""
import unittest

from pipeline import paths, retention

from .support import PipelineTestCase


class RetentionTestCase(PipelineTestCase):
    def _make_run(self, run_date, payload_bytes=16):
        d = paths.PROJECT_DIR / "data" / "runs" / run_date
        (d / "classify_chunks").mkdir(parents=True, exist_ok=True)
        (d / "fetch.json").write_text("x" * payload_bytes)
        (d / "classify_chunks" / "0.json").write_text("{}")
        return d

    def _make_raw(self, run_date):
        d = paths.PROJECT_DIR / "data" / "raw"
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{run_date}.json"
        path.write_text("[]")
        return path

    def _run_dates(self):
        parent = paths.PROJECT_DIR / "data" / "runs"
        return sorted(p.name for p in parent.iterdir()) if parent.is_dir() else []


class SweepTest(RetentionTestCase):
    def test_keeps_the_newest_n_runs(self):
        for day in range(1, 8):
            self._make_run(f"2026-08-0{day}")
        retention.sweep("2026-08-07", retain=3)
        self.assertEqual(self._run_dates(),
                         ["2026-08-05", "2026-08-06", "2026-08-07"])

    def test_deletes_the_whole_run_tree_not_just_loose_files(self):
        self._make_run("2026-07-01")
        self._make_run("2026-08-01")
        retention.sweep("2026-08-01", retain=1)
        self.assertFalse((paths.PROJECT_DIR / "data" / "runs" / "2026-07-01").exists())

    def test_nothing_is_deleted_when_under_the_limit(self):
        for day in range(1, 4):
            self._make_run(f"2026-08-0{day}")
        swept = retention.sweep("2026-08-03", retain=8)
        self.assertEqual(swept["runs"], [])
        self.assertEqual(len(self._run_dates()), 3)

    def test_the_in_flight_run_survives_even_when_it_is_the_oldest(self):
        """`--force --date 2026-01-01` must not delete the checkpoints it is
        in the middle of writing."""
        self._make_run("2026-01-01")
        for day in range(1, 6):
            self._make_run(f"2026-08-0{day}")
        retention.sweep("2026-01-01", retain=2)
        self.assertIn("2026-01-01", self._run_dates())

    def test_entries_that_are_not_run_dates_are_left_alone(self):
        """The sweep should never delete something it doesn't recognise,
        whatever put it there."""
        parent = paths.PROJECT_DIR / "data" / "runs"
        parent.mkdir(parents=True, exist_ok=True)
        (parent / "notes.md").write_text("keep me")
        (parent / "2026-13-45").mkdir()  # not a real date
        for day in range(1, 6):
            self._make_run(f"2026-08-0{day}")
        retention.sweep("2026-08-05", retain=1)
        remaining = self._run_dates()
        self.assertIn("notes.md", remaining)
        self.assertIn("2026-13-45", remaining)

    def test_raw_payloads_are_pruned_on_the_same_schedule(self):
        for day in range(1, 6):
            self._make_raw(f"2026-08-0{day}")
        swept = retention.sweep("2026-08-05", retain=2)
        remaining = sorted(p.name for p in (paths.PROJECT_DIR / "data" / "raw").iterdir())
        self.assertEqual(remaining, ["2026-08-04.json", "2026-08-05.json"])
        self.assertEqual(len(swept["raw"]), 3)

    def test_non_json_files_in_raw_are_left_alone(self):
        raw = paths.PROJECT_DIR / "data" / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        (raw / "2026-01-01.txt").write_text("not ours")
        for day in range(1, 6):
            self._make_raw(f"2026-08-0{day}")
        retention.sweep("2026-08-05", retain=1)
        self.assertTrue((raw / "2026-01-01.txt").exists())

    def test_missing_directories_are_not_an_error(self):
        """A first-ever run has no data/runs or data/raw yet."""
        self.assertEqual(retention.sweep("2026-08-01"), {"runs": [], "raw": []})

    def test_reports_what_it_deleted(self):
        for day in range(1, 5):
            self._make_run(f"2026-08-0{day}")
        swept = retention.sweep("2026-08-04", retain=2)
        self.assertEqual(sorted(swept["runs"]), ["2026-08-01", "2026-08-02"])

    def test_published_matches_and_seen_are_never_touched(self):
        """The deliverables live outside the swept trees - this pins that
        the sweep can't reach them if someone moves a path later."""
        paths.seen_path().parent.mkdir(parents=True, exist_ok=True)
        paths.seen_path().write_text('{"seen": {}}')
        paths.matches_path("2026-08-01").parent.mkdir(parents=True, exist_ok=True)
        paths.matches_path("2026-08-01").write_text("# Matches")
        for day in range(1, 6):
            self._make_run(f"2026-08-0{day}")
        retention.sweep("2026-08-05", retain=1)
        self.assertTrue(paths.seen_path().exists())
        self.assertTrue(paths.matches_path("2026-08-01").exists())


if __name__ == "__main__":
    unittest.main()
