import json
import socket
import unittest
from unittest import mock
from urllib.error import HTTPError

from pipeline import companies, fetch_ats, manifest, paths, retry

from . import fixtures
from .support import RUN_DATE, PipelineTestCase

GREENHOUSE_CO = {"name": "Acme Robotics", "ats": "greenhouse",
                 "token": "acmerobotics", "headcount": 85, "source": "lightspeed"}
ASHBY_CO = {"name": "Bounce Systems", "ats": "ashby",
           "token": "bouncesystems", "headcount": None, "source": "bessemer"}
LEVER_CO = {"name": "Cavil Data", "ats": "lever",
           "token": "cavildata", "headcount": 140, "source": "accel"}


class SourceParserTest(unittest.TestCase):
    def _patch_http(self, payload_by_url):
        def fake_get(url, timeout=15):
            for fragment, payload in payload_by_url.items():
                if fragment in url:
                    return payload if isinstance(payload, bytes) \
                        else json.dumps(payload).encode()
            raise AssertionError(f"unexpected url: {url}")
        return mock.patch.object(fetch_ats, "_http_get", side_effect=fake_get)

    def test_greenhouse_request_includes_content_true(self):
        """Without it there is no description field at all - silently."""
        with self._patch_http({"boards-api.greenhouse.io": {"jobs": [fixtures.GREENHOUSE_JOB]}}) as http:
            fetch_ats._fetch_company_raw(GREENHOUSE_CO)
        self.assertIn("?content=true", http.call_args.args[0])

    def test_greenhouse_jobs_are_parsed(self):
        with self._patch_http({"boards-api.greenhouse.io": {"jobs": [fixtures.GREENHOUSE_JOB]}}):
            jobs = fetch_ats._fetch_company_raw(GREENHOUSE_CO)
        self.assertEqual(jobs, [fixtures.GREENHOUSE_JOB])

    def test_ashby_jobs_are_parsed(self):
        with self._patch_http({"api.ashbyhq.com": {"jobs": [fixtures.ASHBY_JOB]}}):
            jobs = fetch_ats._fetch_company_raw(ASHBY_CO)
        self.assertEqual(jobs, [fixtures.ASHBY_JOB])

    def test_lever_returns_a_bare_list(self):
        with self._patch_http({"api.lever.co": [fixtures.LEVER_JOB]}):
            jobs = fetch_ats._fetch_company_raw(LEVER_CO)
        self.assertEqual(jobs, [fixtures.LEVER_JOB])

    def test_a_404_is_a_skip_not_an_error(self):
        def raise_404(url, timeout=15):
            raise HTTPError(url, 404, "Not Found", {}, None)
        with mock.patch.object(fetch_ats, "_http_get", side_effect=raise_404):
            self.assertIsNone(fetch_ats._fetch_company_raw(GREENHOUSE_CO))

    def test_a_non_404_http_error_propagates(self):
        def raise_500(url, timeout=15):
            raise HTTPError(url, 500, "Server Error", {}, None)
        with mock.patch.object(fetch_ats, "_http_get", side_effect=raise_500):
            with self.assertRaises(HTTPError):
                fetch_ats._fetch_company_raw(GREENHOUSE_CO)


class FetchAtsRunTest(PipelineTestCase):
    def setUp(self):
        super().setUp()
        self.patch_sleep(retry)
        # fetch_ats also sleeps between calls - silence that too.
        time_patcher = mock.patch.object(fetch_ats.time, "sleep")
        time_patcher.start()
        self.addCleanup(time_patcher.stop)

    def _run_with_companies(self, company_list):
        with mock.patch.object(companies, "load_companies", return_value=company_list):
            return fetch_ats.run(RUN_DATE)

    def test_records_a_result_per_company(self):
        with mock.patch.object(fetch_ats, "_fetch_company_raw",
                               return_value=[fixtures.GREENHOUSE_JOB]):
            checkpoint = self._run_with_companies([GREENHOUSE_CO, ASHBY_CO])
        self.assertEqual(checkpoint["companies"]["Acme Robotics"]["status"], "success")
        self.assertEqual(checkpoint["companies"]["Bounce Systems"]["status"], "success")

    def test_a_404_is_skipped_and_does_not_fail_the_stage(self):
        def fake(company):
            return None if company["name"] == "Acme Robotics" else [fixtures.ASHBY_JOB]
        with mock.patch.object(fetch_ats, "_fetch_company_raw", side_effect=fake):
            checkpoint = self._run_with_companies([GREENHOUSE_CO, ASHBY_CO])
        self.assertEqual(checkpoint["companies"]["Acme Robotics"]["status"], "skipped")
        self.assertEqual(checkpoint["companies"]["Bounce Systems"]["status"], "success")
        entry = manifest.load(RUN_DATE)["stages"]["fetch_ats"]
        self.assertEqual(entry["status"], "success")

    def test_a_transport_failure_does_not_stop_other_companies(self):
        def fake(company):
            if company["name"] == "Acme Robotics":
                raise socket.timeout("timed out")
            return [fixtures.ASHBY_JOB]
        with mock.patch.object(fetch_ats, "_fetch_company_raw", side_effect=fake):
            checkpoint = self._run_with_companies([GREENHOUSE_CO, ASHBY_CO])
        self.assertEqual(checkpoint["companies"]["Acme Robotics"]["status"], "failed")
        self.assertEqual(checkpoint["companies"]["Bounce Systems"]["status"], "success")
        entry = manifest.load(RUN_DATE)["stages"]["fetch_ats"]
        self.assertEqual(entry["status"], "success")
        self.assertEqual(entry["failed"], ["Acme Robotics"])

    def test_all_reachable_companies_failing_fails_the_stage_and_writes_no_checkpoint(self):
        def down(company):
            raise socket.timeout("timed out")
        with mock.patch.object(fetch_ats, "_fetch_company_raw", side_effect=down):
            with self.assertRaises(RuntimeError):
                self._run_with_companies([GREENHOUSE_CO, ASHBY_CO])
        self.assertFalse(paths.checkpoint_path(RUN_DATE, "fetch_ats").exists())
        m = manifest.load(RUN_DATE)
        self.assertEqual(m["stages"]["fetch_ats"]["status"], "failed")
        self.assertEqual(m["status"], "failed")

    def test_all_companies_404ing_is_not_a_failure(self):
        """A skip isn't an outage - a run where every board token turned out
        to be wrong should still succeed with zero results, not fail."""
        with mock.patch.object(fetch_ats, "_fetch_company_raw", return_value=None):
            checkpoint = self._run_with_companies([GREENHOUSE_CO, ASHBY_CO])
        entry = manifest.load(RUN_DATE)["stages"]["fetch_ats"]
        self.assertEqual(entry["status"], "success")
        self.assertEqual(entry["succeeded"], 0)
        self.assertEqual(entry["skipped"], 2)
        self.assertEqual(checkpoint["companies"]["Acme Robotics"]["status"], "skipped")

    def test_an_empty_company_list_succeeds_trivially(self):
        checkpoint = self._run_with_companies([])
        self.assertEqual(checkpoint["companies"], {})
        entry = manifest.load(RUN_DATE)["stages"]["fetch_ats"]
        self.assertEqual(entry["status"], "success")
        self.assertEqual(entry["company_count"], 0)


if __name__ == "__main__":
    unittest.main()
