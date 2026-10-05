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

    def test_headcount_is_not_part_of_the_schema(self):
        """Dropped 2026-10-05. It was populated for 0 of 104 rows for its
        whole life while the fit guide lowered the score for a missing one,
        so every listing paid for a column that never held anything."""
        rows = companies.load_companies(self._write_csv())
        self.assertTrue(rows)
        for row in rows:
            self.assertNotIn("headcount", row)

    def test_an_unrecognised_column_is_ignored_not_rejected(self):
        """A hand-maintained CSV that still carries the old headcount column
        has to keep loading - the file is edited by a person, not migrated."""
        path = self._write_csv(
            "name,ats,token,headcount,source\n"
            "Legacy Co,ashby,legacyco,85,example-capital\n")
        row, = companies.load_companies(path)
        self.assertEqual(row["name"], "Legacy Co")
        self.assertEqual(row["token"], "legacyco")
        self.assertNotIn("headcount", row)

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


class ListStateTest(PipelineTestCase):
    """load_companies returns [] for a missing file as readily as for an empty
    one, so a clone with no list ran the first_tpm lane over zero companies
    and published the same "No matches this week" as an honest quiet week.
    Each cause needs a different fix, so each gets a different name."""

    def _path(self):
        return self.project_dir / "companies.csv"

    def test_a_missing_file_is_absent(self):
        self.assertEqual(companies.list_state(self._path()), companies.ABSENT)

    def test_a_header_only_file_is_empty_not_absent(self):
        self._path().write_text("name,ats,token,headcount,source\n")
        self.assertEqual(companies.list_state(self._path()), companies.EMPTY)

    def test_a_file_with_rows_is_populated(self):
        self._path().write_text(fixtures.COMPANIES_CSV_SAMPLE)
        self.assertEqual(companies.list_state(self._path()), companies.POPULATED)

    def test_the_three_states_are_distinct(self):
        self.assertEqual(
            len({companies.ABSENT, companies.EMPTY, companies.POPULATED}), 3)


class PortfolioBoardTest(unittest.TestCase):
    def test_a_portfolio_board_fetches_as_its_base_ats(self):
        self.assertEqual(companies.base_ats("ashby_portfolio"), "ashby")
        self.assertEqual(companies.base_ats("greenhouse"), "greenhouse")
        self.assertTrue(companies.is_portfolio_board("ashby_portfolio"))
        self.assertFalse(companies.is_portfolio_board("ashby"))

    def test_a_portfolio_job_is_named_from_its_department(self):
        self.assertEqual(companies.portfolio_company(
            {"department": "Quill Labs", "team": "Other"}, "Example Ventures"), "Quill Labs")

    def test_team_then_the_board_name_are_the_fallbacks(self):
        """A job is never left nameless - the board's own name is the last
        resort, which at worst reads as the VC hiring for itself."""
        self.assertEqual(companies.portfolio_company(
            {"department": "", "team": "Larkspur"}, "Example Ventures"), "Larkspur")
        self.assertEqual(companies.portfolio_company({}, "Example Ventures"),
                         "Example Ventures")


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

    def test_the_template_has_no_headcount_column(self):
        """Dropped from the schema 2026-10-05; a template still advertising
        it would invite populating a column nothing reads."""
        for row in self._rows():
            self.assertNotIn("headcount", row)


if __name__ == "__main__":
    unittest.main()
