import contextlib
import io
import unittest
from datetime import date as real_date
from unittest import mock

import run_pipeline
from pipeline import alert, classify, dedupe, digest, fetch, manifest, normalize, paths, score

from .support import RUN_DATE, PipelineTestCase

STAGE_OUTPUTS = {
    "fetch": {"sources": {}},
    "normalize": {"listings": []},
    "dedupe": {"listings": []},
    "classify": {"matches": []},
    "score": {"scored": []},
    "digest": {"matches": 0, "rejected": 0, "committed": True, "pushed": True},
}


class LoadCheckpointTest(PipelineTestCase):
    def test_missing_checkpoint_is_none(self):
        self.assertIsNone(run_pipeline.load_checkpoint(RUN_DATE, "fetch"))

    def test_returns_the_parsed_checkpoint(self):
        paths.atomic_write_json(paths.checkpoint_path(RUN_DATE, "fetch"), {"sources": {}})
        self.assertEqual(run_pipeline.load_checkpoint(RUN_DATE, "fetch"), {"sources": {}})

    def test_a_corrupt_checkpoint_costs_one_stage_not_the_whole_date(self):
        """Otherwise an interrupted write would wedge that date permanently."""
        paths.checkpoint_path(RUN_DATE, "fetch").write_text("{ truncated")
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertIsNone(run_pipeline.load_checkpoint(RUN_DATE, "fetch"))
        self.assertIn("re-running stage", stderr.getvalue())


class MainTest(PipelineTestCase):
    def setUp(self):
        super().setUp()
        self.stages = {}
        for name, module in [("fetch", fetch), ("normalize", normalize),
                             ("dedupe", dedupe), ("classify", classify),
                             ("score", score), ("digest", digest)]:
            patcher = mock.patch.object(module, "run", return_value=STAGE_OUTPUTS[name])
            self.stages[name] = patcher.start()
            self.addCleanup(patcher.stop)
        for fn in ("send_failure_alert", "send_success_heartbeat"):
            patcher = mock.patch.object(alert, fn)
            setattr(self, fn, patcher.start())
            self.addCleanup(patcher.stop)

    def _main(self, *argv):
        with mock.patch("sys.argv", ["run_pipeline.py", *argv]):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                run_pipeline.main()
        return out.getvalue()

    def _write_checkpoint(self, stage):
        paths.atomic_write_json(paths.checkpoint_path(RUN_DATE, stage),
                                STAGE_OUTPUTS[stage])

    def test_runs_every_stage_in_order(self):
        self._main("--date", RUN_DATE)
        for name, stage in self.stages.items():
            self.assertEqual(stage.call_count, 1, name)

    def test_stages_receive_the_previous_stages_output(self):
        self._main("--date", RUN_DATE)
        self.stages["normalize"].assert_called_once_with(RUN_DATE, STAGE_OUTPUTS["fetch"])
        # digest needs the dedupe candidate list as well, for the header count.
        self.stages["digest"].assert_called_once_with(
            RUN_DATE, STAGE_OUTPUTS["dedupe"], STAGE_OUTPUTS["score"])

    def test_resumes_from_the_first_stage_without_a_checkpoint(self):
        """A failure at classify must not force a re-fetch of everything."""
        for stage in ("fetch", "normalize", "dedupe"):
            self._write_checkpoint(stage)
        self._main("--date", RUN_DATE)
        for stage in ("fetch", "normalize", "dedupe"):
            self.stages[stage].assert_not_called()
        for stage in ("classify", "score", "digest"):
            self.assertEqual(self.stages[stage].call_count, 1, stage)

    def test_force_ignores_existing_checkpoints(self):
        for stage in STAGE_OUTPUTS:
            self._write_checkpoint(stage)
        self._main("--date", RUN_DATE, "--force")
        for name, stage in self.stages.items():
            self.assertEqual(stage.call_count, 1, name)

    def test_marks_the_run_succeeded_even_when_digest_came_from_cache(self):
        """Run lifecycle belongs to the orchestrator; if digest owned it, a
        fully-cached re-run would never be marked complete."""
        for stage in STAGE_OUTPUTS:
            self._write_checkpoint(stage)
        self._main("--date", RUN_DATE)
        self.stages["digest"].assert_not_called()
        self.assertEqual(manifest.load(RUN_DATE)["status"], "success")

    def test_sends_the_success_heartbeat_with_the_final_manifest(self):
        self._main("--date", RUN_DATE)
        self.send_success_heartbeat.assert_called_once()
        run_date, final = self.send_success_heartbeat.call_args.args
        self.assertEqual(run_date, RUN_DATE)
        self.assertEqual(final["status"], "success")
        self.send_failure_alert.assert_not_called()

    def test_a_stage_failure_alerts_and_exits_nonzero(self):
        self.stages["classify"].side_effect = RuntimeError("claude call failed")
        with contextlib.redirect_stderr(io.StringIO()) as err:
            with self.assertRaises(SystemExit) as ctx:
                self._main("--date", RUN_DATE)
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("claude call failed", err.getvalue())
        self.send_failure_alert.assert_called_once()
        self.assertIn("claude call failed", self.send_failure_alert.call_args.args[1])
        self.send_success_heartbeat.assert_not_called()

    def test_later_stages_do_not_run_after_a_failure(self):
        self.stages["classify"].side_effect = RuntimeError("boom")
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                self._main("--date", RUN_DATE)
        self.stages["score"].assert_not_called()
        self.stages["digest"].assert_not_called()

    def test_defaults_to_today(self):
        with mock.patch.object(run_pipeline, "date") as fake_date:
            fake_date.today.return_value.isoformat.return_value = "2026-08-10"
            fake_date.fromisoformat = real_date.fromisoformat
            self._main()
        self.stages["fetch"].assert_called_once_with("2026-08-10")

    def test_a_malformed_date_is_rejected_before_anything_runs(self):
        """A typo like 2026-8-3 would otherwise create an inconsistently named
        run dir and digest file."""
        with contextlib.redirect_stderr(io.StringIO()) as err:
            with self.assertRaises(SystemExit) as ctx:
                self._main("--date", "2026-8-3")
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("YYYY-MM-DD", err.getvalue())
        self.stages["fetch"].assert_not_called()

    def test_reports_the_outcome_on_stdout(self):
        self.stages["digest"].return_value = {
            "matches": 3, "rejected": 1, "committed": True, "pushed": True}
        output = self._main("--date", RUN_DATE)
        self.assertIn("3 matches", output)
        self.assertIn("pushed=True", output)


if __name__ == "__main__":
    unittest.main()
