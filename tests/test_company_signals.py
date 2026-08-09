import unittest
from datetime import datetime, timedelta, timezone

from pipeline import company_signals, manifest, paths
from tests import fixtures
from tests.support import RUN_DATE, PipelineTestCase


def _iso_days_ago(days: float, now=None) -> str:
    now = now or datetime.now(timezone.utc)
    return (now - timedelta(days=days)).isoformat()


def _gh_job(title, days_ago, department="Engineering", url=None):
    return {
        "title": title,
        "absolute_url": url or f"https://boards.greenhouse.io/co/{title.replace(' ', '-')}",
        "first_published": _iso_days_ago(days_ago),
        "content": "",
        "departments": [{"name": department}] if department else [],
    }


def _company_result(name, ats, jobs, status="success", token=None, **overrides):
    return {
        "status": status,
        "company": {"name": name, "ats": ats, "token": token or name.lower(),
                    "headcount": overrides.get("headcount"),
                    "source": overrides.get("source", "")},
        "jobs": jobs,
    }


class ExtractReqTest(unittest.TestCase):
    def test_greenhouse_uses_first_published_not_updated_at(self):
        req = company_signals._extract_req(fixtures.GREENHOUSE_JOB, "greenhouse")
        self.assertEqual(req["posted_date"], fixtures.GREENHOUSE_JOB["first_published"])
        self.assertNotEqual(req["posted_date"], fixtures.GREENHOUSE_JOB["updated_at"])

    def test_lever_epoch_ms_created_at_is_converted(self):
        req = company_signals._extract_req(fixtures.LEVER_JOB, "lever")
        dt = datetime.fromisoformat(req["posted_date"])
        expected = datetime.fromtimestamp(
            fixtures.LEVER_JOB["createdAt"] / 1000, tz=timezone.utc)
        self.assertEqual(dt, expected)

    def test_lever_malformed_created_at_does_not_crash(self):
        job = {**fixtures.LEVER_JOB, "createdAt": "not-a-number"}
        req = company_signals._extract_req(job, "lever")
        self.assertEqual(req["posted_date"], "")

    def test_ashby_cost_center_departments_classify_as_engineering(self):
        for dept in ["R&D", "Research & Development"]:
            job = {"title": "Program Coordinator",
                  "jobUrl": "https://jobs.ashbyhq.com/co/program-coordinator",
                  "publishedAt": "2026-07-01T00:00:00.000Z",
                  "department": dept, "team": ""}
            req = company_signals._extract_req(job, "ashby")
            self.assertTrue(req["is_eng"], f"department {dept!r} should classify as engineering")

    def test_ashby_non_eng_departments_do_not_classify(self):
        for dept in ["Revenue", "General & Administrative", "S&M", "COGs", "G&A"]:
            job = {"title": "Account Executive",
                  "jobUrl": "https://jobs.ashbyhq.com/co/ae",
                  "publishedAt": "2026-07-01T00:00:00.000Z",
                  "department": dept, "team": ""}
            req = company_signals._extract_req(job, "ashby")
            self.assertFalse(req["is_eng"], f"department {dept!r} should not classify as engineering")

    def test_trailing_whitespace_department_normalizes(self):
        job = {"title": "Operations Analyst",
              "jobUrl": "https://jobs.ashbyhq.com/co/ops-analyst",
              "publishedAt": "2026-07-01T00:00:00.000Z",
              "department": "", "team": "Engineering "}
        req = company_signals._extract_req(job, "ashby")
        self.assertEqual(req["department"], "Engineering")
        self.assertTrue(req["is_eng"])

    def test_security_hardware_title_not_swept_in_by_jargon_or_department(self):
        """A TPM-hardware-flavored title with a department that doesn't hit
        the recognized cost-center/engineering labels must not classify as
        engineering just because the vocabulary sounds technical."""
        job = {"title": "TPM Compliance Auditor",
              "jobUrl": "https://jobs.ashbyhq.com/co/tpm-compliance-auditor",
              "publishedAt": "2026-07-01T00:00:00.000Z",
              "department": "Hardware Security", "team": ""}
        req = company_signals._extract_req(job, "ashby")
        self.assertFalse(req["is_eng"])


class ScoreCompanyTest(unittest.TestCase):
    def test_concentration_is_recent_eng_over_total_open_reqs(self):
        now = datetime.now(timezone.utc)
        reqs = [
            {"posted_date": _iso_days_ago(10, now), "is_eng": True},
            {"posted_date": _iso_days_ago(20, now), "is_eng": True},
            {"posted_date": _iso_days_ago(200, now), "is_eng": False},
            {"posted_date": _iso_days_ago(5, now), "is_eng": False},
        ]
        totals = company_signals._score_company(reqs, window_days=90, now=now)
        self.assertEqual(totals["total"], 4)
        self.assertEqual(totals["eng"], 2)
        self.assertEqual(totals["recent_eng"], 2)
        self.assertEqual(totals["concentration"], 0.5)

    def test_eng_req_outside_window_does_not_count_as_recent(self):
        now = datetime.now(timezone.utc)
        reqs = [{"posted_date": _iso_days_ago(120, now), "is_eng": True}]
        totals = company_signals._score_company(reqs, window_days=90, now=now)
        self.assertEqual(totals["eng"], 1)
        self.assertEqual(totals["recent_eng"], 0)

    def test_undated_eng_req_is_tracked_not_crashed_on(self):
        now = datetime.now(timezone.utc)
        reqs = [{"posted_date": "", "is_eng": True},
               {"posted_date": _iso_days_ago(1, now), "is_eng": True}]
        totals = company_signals._score_company(reqs, window_days=90, now=now)
        self.assertEqual(totals["undated"], 1)
        self.assertEqual(totals["recent_eng"], 1)


class RunTest(PipelineTestCase):
    def test_lane_inactive_is_a_clean_skip(self):
        checkpoint = company_signals.run(RUN_DATE, None)
        self.assertEqual(checkpoint["surveyed"], 0)
        self.assertEqual(checkpoint["qualified"], 0)
        self.assertFalse(paths.signals_path(RUN_DATE).exists())
        entry = manifest.load(RUN_DATE)["stages"]["company_signals"]
        self.assertEqual(entry["status"], "success")

    def test_empty_but_active_checkpoint_reports_zero_loudly(self):
        checkpoint = company_signals.run(RUN_DATE, {"companies": {}})
        self.assertEqual(checkpoint["surveyed"], 0)
        self.assertEqual(checkpoint["qualified"], 0)
        self.assertTrue(paths.signals_path(RUN_DATE).exists())
        text = paths.signals_path(RUN_DATE).read_text()
        self.assertIn("0 of 0 companies surveyed cleared the threshold", text)

    def test_zero_open_reqs_is_not_an_error(self):
        fetch_ats_cp = {"companies": {
            "No Reqs Co": _company_result("No Reqs Co", "greenhouse", []),
        }}
        checkpoint = company_signals.run(RUN_DATE, fetch_ats_cp)
        self.assertEqual(checkpoint["zero_req_companies"], 1)
        self.assertEqual(checkpoint["surveyed"], 1)
        self.assertEqual(checkpoint["companies"], [])

    def test_non_success_companies_are_skipped_not_counted_as_zero(self):
        fetch_ats_cp = {"companies": {
            "Failed Co": _company_result("Failed Co", "greenhouse", [], status="failed"),
            "Not On This Ats Co": _company_result("Not On This Ats Co", "greenhouse", [],
                                                  status="skipped"),
        }}
        checkpoint = company_signals.run(RUN_DATE, fetch_ats_cp)
        self.assertEqual(checkpoint["zero_req_companies"], 0)
        self.assertEqual(checkpoint["failed_companies"], 1)
        self.assertEqual(checkpoint["not_on_ats"], 1)
        self.assertEqual(checkpoint["surveyed"], 0)

    def test_volume_floor_excludes_low_absolute_count_despite_high_concentration(self):
        under_jobs = [_gh_job("Software Engineer", 5), _gh_job("Software Engineer", 10)]
        over_jobs = ([_gh_job(f"Software Engineer {i}", 5) for i in range(14)] +
                    [_gh_job(f"Recruiter {i}", 5, department="G&A") for i in range(4)])
        fetch_ats_cp = {"companies": {
            "Undervolume Co": _company_result("Undervolume Co", "greenhouse", under_jobs),
            "Overvolume Co": _company_result("Overvolume Co", "greenhouse", over_jobs),
        }}
        checkpoint = company_signals.run(RUN_DATE, fetch_ats_cp, min_eng=8)
        qualified_names = {c["name"] for c in checkpoint["companies"]
                           if c["totals"]["recent_eng"] >= 8}
        self.assertNotIn("Undervolume Co", qualified_names)
        self.assertIn("Overvolume Co", qualified_names)
        self.assertEqual(checkpoint["qualified"], 1)

    def test_writes_checkpoint_and_signals_markdown_ranked_by_concentration(self):
        # 8/8 = 100% concentration.
        top = [_gh_job(f"Software Engineer {i}", 5) for i in range(8)]
        # 9/12 = 75% concentration - still qualifies (recent_eng >= 8) but
        # ranks below the 100% company.
        second = ([_gh_job(f"Software Engineer {i}", 5) for i in range(9)] +
                 [_gh_job(f"Recruiter {i}", 5, department="G&A") for i in range(3)])
        fetch_ats_cp = {"companies": {
            "Second Co": _company_result("Second Co", "greenhouse", second, source="fundB"),
            "Top Co": _company_result("Top Co", "greenhouse", top, source="fundA"),
        }}
        checkpoint = company_signals.run(RUN_DATE, fetch_ats_cp, min_eng=8)
        on_disk = self.read_json(paths.checkpoint_path(RUN_DATE, "company_signals"))
        self.assertEqual(on_disk["qualified"], 2)

        text = paths.signals_path(RUN_DATE).read_text()
        top_idx = text.index("Top Co")
        second_idx = text.index("Second Co")
        self.assertLess(top_idx, second_idx)  # higher concentration ranks first


if __name__ == "__main__":
    unittest.main()
