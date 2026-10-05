import json
import socket
import unittest
from unittest import mock
from urllib.error import HTTPError

from pipeline import companies, fetch_ats, manifest, paths, retry

from . import fixtures
from .support import RUN_DATE, PipelineTestCase

GREENHOUSE_CO = {"name": "Acme Robotics", "ats": "greenhouse",
                 "token": "acmerobotics", "source": "lightspeed"}
ASHBY_CO = {"name": "Bounce Systems", "ats": "ashby",
           "token": "bouncesystems", "source": "bessemer"}
LEVER_CO = {"name": "Cavil Data", "ats": "lever",
           "token": "cavildata", "source": "accel"}
PORTFOLIO_CO = {"name": "Example Ventures", "ats": "ashby_portfolio",
                "token": "example-ventures", "source": "example-ventures"}
WORKABLE_CO = {"name": "Dunlin Labs", "ats": "workable",
               "token": "dunlinlabs", "source": "angel"}


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

    def test_workable_request_includes_details_true(self):
        """Load-bearing the same way greenhouse's ?content=true is: without
        it each job carries summary fields only, with no description or
        full_description, so prefilter has nothing to match over."""
        with self._patch_http({"apply.workable.com": {"jobs": [fixtures.WORKABLE_JOB]}}) as http:
            fetch_ats._fetch_company_raw(WORKABLE_CO)
        self.assertIn("?details=true", http.call_args.args[0])

    def test_workable_requests_the_redirect_target_directly(self):
        """The documented host 302s to apply.workable.com for the same
        payload; asking for the target keeps this to one request per
        company, and this stage makes one call per company already."""
        with self._patch_http({"apply.workable.com": {"jobs": []}}) as http:
            fetch_ats._fetch_company_raw(WORKABLE_CO)
        url = http.call_args.args[0]
        self.assertIn("apply.workable.com/api/v1/widget/accounts/dunlinlabs", url)
        self.assertNotIn("www.workable.com", url)

    def test_workable_jobs_are_parsed_from_the_jobs_envelope(self):
        with self._patch_http({"apply.workable.com": {"jobs": [fixtures.WORKABLE_JOB]}}):
            jobs = fetch_ats._fetch_company_raw(WORKABLE_CO)
        self.assertEqual(jobs, [fixtures.WORKABLE_JOB])

    def test_a_workable_404_is_a_skip_like_any_other_ats(self):
        """Board tokens are guesswork on every ATS - a company not on
        workable is skipped, not a failure."""
        def raise_404(url, timeout=15):
            raise HTTPError(url, 404, "Not Found", {}, None)
        with mock.patch.object(fetch_ats, "_http_get", side_effect=raise_404):
            self.assertIsNone(fetch_ats._fetch_company_raw(WORKABLE_CO))

    def test_a_portfolio_board_is_fetched_from_the_ashby_endpoint(self):
        with self._patch_http({"api.ashbyhq.com": {"jobs": fixtures.ASHBY_PORTFOLIO_JOBS}}) as http:
            jobs = fetch_ats._fetch_company_raw(PORTFOLIO_CO)
        self.assertIn("api.ashbyhq.com/posting-api/job-board/example-ventures",
                      http.call_args.args[0])
        self.assertEqual(jobs, fixtures.ASHBY_PORTFOLIO_JOBS)

    def test_an_unsupported_ats_is_not_sent_to_lever(self):
        """Lever was the fall-through for any unrecognised value, so a typo
        in the hand-typed ats column became a Lever 404 - reported as "not
        on this ATS", which reads as a normal skip."""
        with self.assertRaises(ValueError):
            fetch_ats._url({"name": "X", "ats": "ashby-portfolio", "token": "x"})

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

    def test_an_unsupported_ats_row_is_reported_and_never_requested(self):
        typo = dict(ASHBY_CO, name="Typo Co", ats="ashby-portfolio")
        with mock.patch.object(fetch_ats, "_fetch_company_raw",
                               side_effect=lambda c: (
                                   self.fail("requested an unsupported ats")
                                   if c["ats"] == "ashby-portfolio"
                                   else [fixtures.ASHBY_JOB])):
            checkpoint = self._run_with_companies([typo, ASHBY_CO])
        self.assertEqual(checkpoint["companies"]["Typo Co"]["status"], "invalid")
        entry = manifest.load(RUN_DATE)["stages"]["fetch_ats"]
        self.assertEqual(entry["status"], "success")
        self.assertEqual(entry["invalid_ats"], ["Typo Co"])

    def test_an_unsupported_ats_does_not_count_as_an_outage(self):
        """A data error is not a transport failure, so a list whose only
        other rows failed must not read "all reachable calls failed" on its
        account - nor be rescued by it."""
        typo = dict(ASHBY_CO, name="Typo Co", ats="ashby-portfolio")
        with mock.patch.object(fetch_ats, "_fetch_company_raw",
                               side_effect=socket.timeout("timed out")):
            with self.assertRaises(RuntimeError):
                self._run_with_companies([typo, ASHBY_CO])

    def _run_with_state(self, company_list, state, fetch_return=None):
        with mock.patch.object(companies, "list_state", return_value=state), \
             mock.patch.object(fetch_ats, "_fetch_company_raw",
                               return_value=fetch_return):
            return self._run_with_companies(company_list)

    def test_no_company_list_is_named_in_the_manifest(self):
        """Three different ways this lane ends up with nothing, all of which
        used to publish the identical "No matches this week": there is no
        list, the list reaches no supported ATS, or the week was quiet. Only
        the last is honest, and each needs a different fix."""
        self._run_with_state([], companies.ABSENT)
        entry = manifest.load(RUN_DATE)["stages"]["fetch_ats"]
        self.assertEqual(entry["company_list_state"], companies.ABSENT)
        self.assertFalse(entry["yielded_nothing"])

    def test_an_empty_list_is_distinguished_from_a_missing_one(self):
        self._run_with_state([], companies.EMPTY)
        entry = manifest.load(RUN_DATE)["stages"]["fetch_ats"]
        self.assertEqual(entry["company_list_state"], companies.EMPTY)

    def test_a_populated_list_that_yields_nothing_says_so(self):
        """Every token 404ing is not an outage and must stay a success - but
        it is also not a quiet week, and the manifest now tells them apart."""
        self._run_with_state([GREENHOUSE_CO, ASHBY_CO], companies.POPULATED)
        entry = manifest.load(RUN_DATE)["stages"]["fetch_ats"]
        self.assertEqual(entry["status"], "success")
        self.assertEqual(entry["company_list_state"], companies.POPULATED)
        self.assertTrue(entry["yielded_nothing"])
        self.assertEqual(entry["succeeded"], 0)

    def test_a_populated_list_that_fetches_is_not_flagged(self):
        self._run_with_state([GREENHOUSE_CO], companies.POPULATED,
                              fetch_return=[fixtures.GREENHOUSE_JOB])
        entry = manifest.load(RUN_DATE)["stages"]["fetch_ats"]
        self.assertEqual(entry["company_list_state"], companies.POPULATED)
        self.assertFalse(entry["yielded_nothing"])


if __name__ == "__main__":
    unittest.main()
