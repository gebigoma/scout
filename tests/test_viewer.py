"""The viewer is read-only over data/runs/ and must survive the real shape of
that directory: failed runs, runs predating the prefilter counters, and
manifests that were truncated mid-write."""
import contextlib
import io
import json
import unittest

import viewer
from pipeline import paths

from .support import PipelineTestCase


def _manifest(date, status="success", **prefilter):
    return {
        "date": date,
        "started_at": date + "T16:00:00+00:00",
        "finished_at": date + "T16:10:30+00:00",
        "status": status,
        "stages": {
            "fetch": {"status": "success", "total_count": 350},
            "fetch_ats": {"status": "success", "succeeded": 98, "skipped": 6,
                          "failed": []},
            "normalize": {"status": "success", "count": 2278},
            "prefilter": dict({"status": "success"}, **prefilter),
            "classify": {"status": "success", "unclassified_count": 0,
                         "failed_chunk_indices": []},
            "digest": {"status": "success" if status == "success" else "failed"},
        },
        "error": None if status == "success" else "digest: boom",
    }


class ViewerTest(PipelineTestCase):
    def _write(self, date, content):
        d = paths.runs_dir() / date
        d.mkdir(parents=True)
        text = content if isinstance(content, str) else json.dumps(content)
        (d / "manifest.json").write_text(text)

    def setUp(self):
        super().setUp()
        self._write("2026-09-07", _manifest("2026-09-07", role_terms_seen=37,
                                            first_tpm_seen=1900))
        self._write("2026-09-14", _manifest("2026-09-14", status="failed",
                                            role_terms_seen=35, first_tpm_seen=1800))
        m = _manifest("2026-08-17")  # predates the prefilter counters
        self._write("2026-08-17", m)
        self._write("2026-09-21", '{"date": "2026-09-21", "status": "succ')

    def test_valid_dates_appear_and_invalid_manifest_is_skipped(self):
        runs = viewer.collect_runs()
        self.assertEqual([r["date"] for r in runs],
                         ["2026-09-14", "2026-09-07", "2026-08-17"])
        page = viewer.render(runs)
        for date in ("2026-09-07", "2026-09-14", "2026-08-17"):
            self.assertIn(date, page)
        self.assertNotIn("2026-09-21", page)

    def test_failed_run_is_marked_failed(self):
        runs = {r["date"]: r for r in viewer.collect_runs()}
        self.assertEqual(runs["2026-09-14"]["status"], "failed")
        self.assertIn('<td class="bad">failed</td>', viewer.render(list(runs.values())))
        self.assertIn('<td class="ok">success</td>', viewer.render(list(runs.values())))

    def test_missing_counters_render_as_dash_without_raising(self):
        runs = {r["date"]: r for r in viewer.collect_runs()}
        old = runs["2026-08-17"]
        self.assertIsNone(old["counters"][("prefilter", "role_terms_seen")])
        self.assertIsNone(old["counters"][("prefilter", "first_tpm_seen")])
        page = viewer.render([old])
        self.assertIn('<td class="key">%s</td>' % viewer.MISSING, page)

    def test_role_terms_seen_has_its_own_column(self):
        page = viewer.render(viewer.collect_runs())
        self.assertIn("role terms seen", page)
        self.assertIn('<td class="key">37</td>', page)

    def test_sparse_and_odd_manifests_do_not_raise(self):
        self._write("2026-07-01", {})
        self._write("2026-07-02", [])
        self._write("2026-07-03", {"status": "failed", "stages": "nope",
                                   "started_at": "garbage"})
        runs = viewer.collect_runs()
        dates = [r["date"] for r in runs]
        self.assertIn("2026-07-01", dates)
        self.assertIn("2026-07-03", dates)
        self.assertNotIn("2026-07-02", dates)
        viewer.render(runs)

    def test_values_are_html_escaped(self):
        self._write("2026-07-04", {"date": "2026-07-04", "status": "<script>x</script>"})
        page = viewer.render(viewer.collect_runs())
        self.assertNotIn("<script>x", page)

    def test_collect_does_not_create_data_runs(self):
        for child in paths.runs_dir().iterdir():
            for f in child.iterdir():
                f.unlink()
            child.rmdir()
        paths.runs_dir().rmdir()
        self.assertEqual(viewer.collect_runs(), [])
        self.assertFalse(paths.runs_dir().exists())
        self.assertIn("No readable manifests", viewer.render([]))

    def test_main_writes_index_under_temp_project(self):
        with contextlib.redirect_stdout(io.StringIO()) as out_:
            self.assertEqual(viewer.main(), 0)
        self.assertEqual(out_.getvalue().strip(), str(paths.viewer_index_path()))
        out = paths.viewer_index_path()
        self.assertEqual(out, self.project_dir / "viewer" / "index.html")
        self.assertIn("<title>", out.read_text())


if __name__ == "__main__":
    unittest.main()
