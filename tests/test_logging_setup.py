import json
import logging
import unittest

from pipeline import logging_setup, paths

from .support import RUN_DATE, PipelineTestCase


class JsonFormatterTest(PipelineTestCase):
    def _format(self, **extra):
        record = logging.LogRecord("scout.test", logging.INFO, __file__, 1,
                                   "hello", None, None)
        for key, value in extra.items():
            setattr(record, key, value)
        return json.loads(logging_setup.JsonFormatter().format(record))

    def test_emits_the_core_fields(self):
        payload = self._format(stage="fetch")
        self.assertEqual(payload["level"], "INFO")
        self.assertEqual(payload["stage"], "fetch")
        self.assertEqual(payload["message"], "hello")
        self.assertIn("time", payload)

    def test_extra_fields_are_merged_in_at_the_top_level(self):
        payload = self._format(stage="dedupe", extra_fields={"raw_count": 5})
        self.assertEqual(payload["raw_count"], 5)

    def test_stage_defaults_to_null_when_absent(self):
        self.assertIsNone(self._format()["stage"])


class GetLoggerTest(PipelineTestCase):
    def test_writes_jsonl_lines_to_the_run_log(self):
        logger = logging_setup.get_logger(RUN_DATE)
        logging_setup.log(logger, "fetch", "fetched", count=7)
        logging_setup.log(logger, "dedupe", "deduped", raw_count=7, deduped_count=5)

        lines = (paths.logs_dir() / f"{RUN_DATE}.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), 2)
        first, second = [json.loads(line) for line in lines]
        self.assertEqual((first["stage"], first["message"], first["count"]),
                         ("fetch", "fetched", 7))
        self.assertEqual(second["deduped_count"], 5)

    def test_repeated_calls_reuse_one_handler(self):
        """get_logger is called once per stage; without the handler guard the
        same line would be written six times over."""
        logger = logging_setup.get_logger(RUN_DATE)
        again = logging_setup.get_logger(RUN_DATE)
        self.assertIs(logger, again)
        self.assertEqual(len(logger.handlers), 1)

        logging_setup.log(again, "fetch", "once")
        lines = (paths.logs_dir() / f"{RUN_DATE}.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), 1)

    def test_run_logs_are_kept_separate_per_date(self):
        logging_setup.log(logging_setup.get_logger("2026-08-03"), "fetch", "a")
        logging_setup.log(logging_setup.get_logger("2026-08-10"), "fetch", "b")
        self.assertTrue((paths.logs_dir() / "2026-08-03.jsonl").exists())
        self.assertTrue((paths.logs_dir() / "2026-08-10.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
