import unittest

from pipeline import companies

from . import fixtures
from .support import PipelineTestCase


class LoadCompaniesTest(PipelineTestCase):
    def _write_csv(self, text=fixtures.COMPANIES_CSV_SAMPLE):
        path = self.project_dir / "companies.csv"
        path.write_text(text)
        return path

    def test_parses_all_three_ats_values(self):
        rows = companies.load_companies(self._write_csv())
        self.assertEqual([c["ats"] for c in rows], ["greenhouse", "ashby", "lever"])

    def test_blank_headcount_parses_as_unknown_not_zero(self):
        rows = companies.load_companies(self._write_csv())
        bounce = next(c for c in rows if c["name"] == "Bounce Systems")
        self.assertIsNone(bounce["headcount"])

    def test_a_present_headcount_parses_as_an_int(self):
        rows = companies.load_companies(self._write_csv())
        acme = next(c for c in rows if c["name"] == "Acme Robotics")
        self.assertEqual(acme["headcount"], 85)
        self.assertIsInstance(acme["headcount"], int)

    def test_token_and_source_are_captured(self):
        rows = companies.load_companies(self._write_csv())
        acme = next(c for c in rows if c["name"] == "Acme Robotics")
        self.assertEqual(acme["token"], "acmerobotics")
        self.assertEqual(acme["source"], "lightspeed")

    def test_a_missing_file_returns_an_empty_list(self):
        self.assertEqual(companies.load_companies(self.project_dir / "nope.csv"), [])

    def test_defaults_to_the_project_companies_csv_path(self):
        path = self.project_dir / "data" / "companies.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(fixtures.COMPANIES_CSV_SAMPLE)
        self.assertEqual(len(companies.load_companies()), 3)


if __name__ == "__main__":
    unittest.main()
