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


class CompaniesExampleTest(unittest.TestCase):
    """The committed template is the only description of this schema a fresh
    clone gets, since the real companies.csv is private. Load it with the real
    loader so it can't drift from what the loader accepts - a template that
    parses to nothing would send someone straight back to the silent-empty
    failure it exists to prevent."""

    def _rows(self):
        # Deliberately not PipelineTestCase: this reads the real committed
        # file, not a temp-dir copy.
        from pipeline import paths
        return companies.load_companies(
            paths.PROJECT_DIR / "data" / "companies.example.csv")

    def test_the_template_parses_and_is_not_empty(self):
        self.assertTrue(self._rows())

    def test_the_template_demonstrates_every_supported_ats(self):
        self.assertEqual({c["ats"] for c in self._rows()}, companies.ATS_CHOICES)

    def test_the_template_demonstrates_unknown_headcount(self):
        """Blank headcount is the column's one real trap - it means unknown,
        never 0 - so the template has to show it."""
        self.assertIn(None, [c["headcount"] for c in self._rows()])


if __name__ == "__main__":
    unittest.main()
