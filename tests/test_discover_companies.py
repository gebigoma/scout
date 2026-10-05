"""discover_companies mines ATS board tokens out of HN comments already on
disk. Every test here builds its own fetch checkpoints in a temp project, with
links escaped the way HN actually serves them - the first version of this
idea searched raw text and found zero tokens in 717 comments that held 339."""
import contextlib
import csv
import io
import json
import unittest
from unittest import mock

import discover_companies as dc
from pipeline import paths

from .support import PipelineTestCase


def _hn_escape(text):
    # What the Algolia API returns: entities, including slashes, escaped.
    return text.replace("/", "&#x2F;").replace("'", "&#x27;")


def _item(item_id, text):
    return {"id": item_id, "text": _hn_escape(text),
            "thread_title": "Ask HN: Who is hiring? (August 2026)"}


class ExtractTokensTest(unittest.TestCase):
    def test_each_supported_ats_url_form_yields_its_token(self):
        cases = [
            ("https://jobs.ashbyhq.com/acme/1b2c3d", ("ashby", "acme")),
            ("https://job-boards.greenhouse.io/acme/jobs/123", ("greenhouse", "acme")),
            ("https://boards.greenhouse.io/acme", ("greenhouse", "acme")),
            ("https://boards.greenhouse.io/embed/job_board?for=acme&b=x",
             ("greenhouse", "acme")),
            ("https://jobs.lever.co/acme/4f5e", ("lever", "acme")),
            ("https://apply.workable.com/acme/j/ABC123", ("workable", "acme")),
            # Legacy per-company workable subdomain.
            ("https://acme.workable.com/jobs/1", ("workable", "acme")),
        ]
        for url, expected in cases:
            with self.subTest(url=url):
                self.assertEqual(dc.extract_tokens(url), [expected])

    def test_a_dotted_ashby_slug_is_kept_whole(self):
        """Real slugs: jobs.ashbyhq.com/somana.tech/<job-id>."""
        self.assertEqual(dc.extract_tokens("https://jobs.ashbyhq.com/somana.tech/abc"),
                         [("ashby", "somana.tech")])

    def test_trailing_prose_punctuation_is_not_part_of_the_token(self):
        self.assertEqual(dc.extract_tokens("Apply at https://jobs.ashbyhq.com/acme."),
                         [("ashby", "acme")])

    def test_a_query_string_is_not_part_of_the_token(self):
        self.assertEqual(
            dc.extract_tokens("https://jobs.ashbyhq.com/norm-ai?utm_source=x"),
            [("ashby", "norm-ai")])

    def test_a_workable_shortlink_carries_no_token(self):
        """apply.workable.com/j/<shortcode> routes to a job, not a board -
        taking "j" as a company token would 404 every week."""
        self.assertEqual(dc.extract_tokens("https://apply.workable.com/j/ABC123"), [])

    def test_eu_hosted_boards_are_not_candidates(self):
        """fetch_ats calls the US API hosts, where an EU token 404s."""
        text = ("https://job-boards.eu.greenhouse.io/acme/jobs/1 "
                "https://jobs.eu.lever.co/acme/2")
        self.assertEqual(dc.extract_tokens(text), [])

    def test_the_same_board_linked_twice_is_one_token(self):
        text = "https://jobs.ashbyhq.com/Acme/1 and https://jobs.ashbyhq.com/acme/2"
        self.assertEqual(dc.extract_tokens(text), [("ashby", "Acme")])


class CleanNameTest(unittest.TestCase):
    def test_header_decorations_are_removed(self):
        cases = [
            ("PermitFlow (YC W22)", "PermitFlow"),
            ("Smarkets ( https://www.smarkets.co", "Smarkets"),
            ("Anterior (Sequoia-backed, Series B", "Anterior"),
            ("forus - founding security engineer", "forus"),
            ("Eleos Technologies ( https://eleos.health )", "Eleos Technologies"),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(dc._clean_name(raw, "tok"), expected)

    def test_an_empty_name_falls_back_to_the_token(self):
        self.assertEqual(dc._clean_name("", "acme"), "acme")
        self.assertEqual(dc._clean_name("(YC S24)", "acme"), "acme")


class CollectTest(PipelineTestCase):
    def _write_fetch(self, run_date, items):
        path = paths.checkpoint_path(run_date, "fetch")
        path.write_text(json.dumps(
            {"sources": {"hn_whoishiring": {"status": "success", "items": items}}}))

    def _write_companies(self, text):
        path = paths.companies_csv_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def _main(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(dc.main([]), 0)
        with paths.company_candidates_path().open(newline="") as f:
            rows = list(csv.DictReader(f))
        return rows, out.getvalue()

    def test_escaped_links_are_found(self):
        """The regression this script exists to avoid: HN double-escapes
        entities, and a raw-text search matches nothing."""
        self._write_fetch("2026-09-07", [
            _item(1, "Acme | Senior TPM | Remote<p>Apply: https://jobs.ashbyhq.com/acme/1")])
        rows, _ = self._main()
        self.assertEqual([(r["ats"], r["token"]) for r in rows], [("ashby", "acme")])
        self.assertEqual(rows[0]["name"], "Acme")
        self.assertEqual(rows[0]["source"], dc.SOURCE)
        self.assertEqual(rows[0]["evidence_url"], "https://news.ycombinator.com/item?id=1")

    def test_a_board_already_in_companies_csv_is_left_out(self):
        """Case-insensitively - ashby slugs show up as both "Acme" and "acme"."""
        self._write_companies("name,ats,token,source\nAcme,ashby,acme,greylock\n")
        self._write_fetch("2026-09-07", [
            _item(1, "Acme | TPM<p>https://jobs.ashbyhq.com/Acme/1"),
            _item(2, "Bolt | TPM<p>https://jobs.lever.co/bolt/1")])
        rows, out = self._main()
        self.assertEqual([r["token"] for r in rows], ["bolt"])
        self.assertIn("1 linked board(s) already in companies.csv", out)

    def test_a_token_listed_under_another_ats_is_flagged(self):
        """A company that moved ATS leaves its companies.csv row 404ing
        every week, skipped as "not on this ATS" - silent unless named."""
        self._write_companies("name,ats,token,source\nAcme,greenhouse,acme,greylock\n")
        self._write_fetch("2026-09-07", [_item(1, "Acme | TPM<p>https://jobs.ashbyhq.com/acme/1")])
        rows, _ = self._main()
        self.assertEqual(len(rows), 1)
        self.assertIn("already listed under greenhouse", rows[0]["note"])

    def test_a_vc_portfolio_board_is_flagged_not_dropped(self):
        """The real case: a "Phaselaw" comment linking Pear VC's portfolio
        board. Every job on it would be labelled Phaselaw."""
        self._write_fetch("2026-09-07", [
            _item(1, "Phaselaw | Founding Engineer<p>https://jobs.ashbyhq.com/Pear-VC/abc")])
        rows, _ = self._main()
        self.assertEqual(rows[0]["token"], "Pear-VC")
        self.assertIn("VC/agency", rows[0]["note"])

    def test_vc_inside_a_word_is_not_flagged(self):
        self._write_fetch("2026-09-07", [
            _item(1, "Ivc | TPM<p>https://jobs.ashbyhq.com/invcorp/1")])
        rows, _ = self._main()
        self.assertEqual(rows[0]["note"], "")

    def test_the_header_name_is_used_only_for_a_single_board_comment(self):
        """A recruiter's comment linking three clients' boards has one header
        and three companies - naming all three after the header is wrong."""
        self._write_fetch("2026-09-07", [
            _item(1, "TalentCo | Many roles<p>https://jobs.ashbyhq.com/alpha/1 "
                     "https://jobs.lever.co/beta/2")])
        rows, _ = self._main()
        self.assertEqual(sorted(r["name"] for r in rows), ["alpha", "beta"])

    def test_the_same_comment_refetched_weekly_counts_once(self):
        """The monthly thread is refetched every week; a comment seen in four
        runs is one mention, with first_seen/last_seen spanning them."""
        item = _item(1, "Acme | TPM<p>https://jobs.ashbyhq.com/acme/1")
        for d in ("2026-08-03", "2026-08-10", "2026-08-17"):
            self._write_fetch(d, [item])
        rows, _ = self._main()
        self.assertEqual(rows[0]["mentions"], "1")
        self.assertEqual(rows[0]["first_seen"], "2026-08-03")
        self.assertEqual(rows[0]["last_seen"], "2026-08-17")

    def test_separate_comments_add_mentions_and_sort_first(self):
        self._write_fetch("2026-09-07", [
            _item(1, "Zeta | TPM<p>https://jobs.ashbyhq.com/zeta/1"),
            _item(2, "Acme | TPM<p>https://jobs.ashbyhq.com/acme/1"),
            _item(3, "Acme | Eng<p>https://jobs.ashbyhq.com/acme/2")])
        rows, _ = self._main()
        self.assertEqual([(r["token"], r["mentions"]) for r in rows],
                         [("acme", "2"), ("zeta", "1")])

    def test_greenhouse_short_links_are_counted_not_followed(self):
        self._write_fetch("2026-09-07", [
            _item(1, "Acme | TPM<p>https://grnh.se/a1b2c3 and https://grnh.se/d4e5f6")])
        rows, out = self._main()
        self.assertEqual(rows, [])
        self.assertIn("2 grnh.se short link(s) not resolved", out)

    def test_it_makes_no_network_calls(self):
        """Offline by construction - that is what lets it be checked against
        every retained run before its output is trusted."""
        self._write_fetch("2026-09-07", [
            _item(1, "Acme | TPM<p>https://jobs.ashbyhq.com/acme/1 https://grnh.se/x")])
        with mock.patch("urllib.request.urlopen",
                        side_effect=AssertionError("network call")):
            rows, _ = self._main()
        self.assertEqual(len(rows), 1)

    def test_companies_csv_is_never_written(self):
        """A review queue, never an append: the list is hand-maintained."""
        path = self._write_companies("name,ats,token,headcount,source\nAcme,ashby,acme,,x\n")
        before = path.read_bytes()
        self._write_fetch("2026-09-07", [_item(1, "Bolt | TPM<p>https://jobs.lever.co/bolt/1")])
        self._main()
        self.assertEqual(path.read_bytes(), before)

    def test_an_unreadable_checkpoint_is_skipped_not_fatal(self):
        paths.checkpoint_path("2026-08-31", "fetch").write_text('{"sources": {"hn_')
        self._write_fetch("2026-09-07", [_item(1, "Acme | TPM<p>https://jobs.ashbyhq.com/acme/1")])
        rows, _ = self._main()
        self.assertEqual(len(rows), 1)

    def test_no_runs_writes_an_empty_queue_and_says_so(self):
        rows, out = self._main()
        self.assertEqual(rows, [])
        self.assertIn("No fetch checkpoints found", out)
        header = paths.company_candidates_path().read_text().splitlines()[0]
        self.assertEqual(header.split(","), list(dc.CANDIDATE_COLUMNS))

    def test_the_leading_columns_match_companies_csv(self):
        """So other discovery sources can write to the same queue later, and
        a reviewed row maps onto companies.csv without reshaping."""
        self.assertEqual(dc.CANDIDATE_COLUMNS[:4], ("name", "ats", "token", "source"))

    def test_a_missing_company_list_is_reported_not_assumed_empty(self):
        self._write_fetch("2026-09-07", [_item(1, "Acme | TPM<p>https://jobs.ashbyhq.com/acme/1")])
        _, out = self._main()
        self.assertIn("companies.csv is absent", out)



class DismissedTest(PipelineTestCase):
    """The queue is regenerated wholesale, so a board judged and rejected came
    back on every run. The dismissed list makes a judgment stick."""

    def _write_fetch(self, items):
        paths.checkpoint_path("2026-09-07", "fetch").write_text(json.dumps(
            {"sources": {"hn_whoishiring": {"status": "success", "items": items}}}))

    def _write_dismissed(self, text):
        path = paths.company_candidates_dismissed_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def _run(self, *argv):
        with contextlib.redirect_stdout(io.StringIO()) as out, \
             contextlib.redirect_stderr(io.StringIO()) as err:
            code = dc.main(list(argv))
        rows = []
        if paths.company_candidates_path().exists():
            with paths.company_candidates_path().open(newline="") as f:
                rows = list(csv.DictReader(f))
        return code, rows, out.getvalue(), err.getvalue()

    BCC = _item(1, "BCC | Analyst<p>https://job-boards.greenhouse.io/blackcanyon/jobs/1")
    ACME = _item(2, "Acme | TPM<p>https://jobs.ashbyhq.com/acme/1")

    def test_a_dismissed_board_is_left_out_and_counted(self):
        self._write_dismissed("ats,token,reason,dismissed_on\n"
                              "greenhouse,blackcanyon,agency,2026-10-05\n")
        self._write_fetch([self.BCC, self.ACME])
        _, rows, out, _ = self._run()
        self.assertEqual([r["token"] for r in rows], ["acme"])
        self.assertIn("1 dismissed board(s) left out", out)

    def test_dismissal_matches_tokens_case_insensitively(self):
        self._write_dismissed("ats,token\ngreenhouse,BlackCanyon\n")
        self._write_fetch([self.BCC])
        _, rows, _, _ = self._run()
        self.assertEqual(rows, [])

    def test_dismissing_on_one_ats_does_not_hide_another(self):
        """A board is an ats and a token; the same slug on a different ATS
        is a different board, and may be a different company."""
        self._write_dismissed("ats,token\nlever,acme\n")
        self._write_fetch([self.ACME])
        _, rows, _, _ = self._run()
        self.assertEqual([(r["ats"], r["token"]) for r in rows], [("ashby", "acme")])

    def test_the_flag_records_the_dismissal_and_applies_it_in_the_same_run(self):
        self._write_fetch([self.BCC, self.ACME])
        code, rows, out, _ = self._run("--dismiss", "greenhouse:blackcanyon",
                                       "--reason", "agency")
        self.assertEqual(code, 0)
        self.assertEqual([r["token"] for r in rows], ["acme"])
        self.assertIn("Dismissed greenhouse:blackcanyon (agency)", out)
        with paths.company_candidates_dismissed_path().open(newline="") as f:
            row, = list(csv.DictReader(f))
        self.assertEqual((row["ats"], row["token"], row["reason"]),
                         ("greenhouse", "blackcanyon", "agency"))
        self.assertRegex(row["dismissed_on"], r"^\d{4}-\d{2}-\d{2}$")

    def test_dismissing_the_same_board_twice_writes_it_once(self):
        self._write_fetch([self.BCC])
        self._run("--dismiss", "greenhouse:blackcanyon")
        _, _, out, _ = self._run("--dismiss", "greenhouse:BlackCanyon")
        self.assertIn("1 board(s) were already dismissed", out)
        lines = paths.company_candidates_dismissed_path().read_text().splitlines()
        self.assertEqual(len(lines), 2)  # header + one row

    def test_a_malformed_dismissal_writes_nothing(self):
        """All or nothing - a batch with one typo is not half-applied."""
        self._write_fetch([self.BCC])
        code, _, _, err = self._run("--dismiss", "greenhouse:blackcanyon",
                                    "--dismiss", "greenhose:acme")
        self.assertEqual(code, 2)
        self.assertIn("greenhose:acme", err)
        self.assertFalse(paths.company_candidates_dismissed_path().exists())

    def test_an_unusable_dismissed_list_is_reported_not_silently_ignored(self):
        """Ignored quietly, every rejected board would reappear with no hint
        why - the file has to say it wasn't applied."""
        self._write_dismissed("board,why\ngreenhouse:blackcanyon,agency\n")
        self._write_fetch([self.BCC])
        _, rows, out, _ = self._run()
        self.assertEqual(len(rows), 1)
        self.assertIn("Warning:", out)
        self.assertIn("nothing in it was applied", out)

    def test_one_bad_row_is_skipped_with_a_warning_and_the_rest_applied(self):
        self._write_dismissed("ats,token\ngreenhose,acme\ngreenhouse,blackcanyon\n")
        self._write_fetch([self.BCC, self.ACME])
        _, rows, out, _ = self._run()
        self.assertEqual([r["token"] for r in rows], ["acme"])
        self.assertIn("line 2: unrecognised ats 'greenhose'", out)

    def test_dismissing_never_writes_companies_csv(self):
        path = paths.companies_csv_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("name,ats,token,source\nAcme,ashby,acme,x\n")
        before = path.read_bytes()
        self._write_fetch([self.BCC])
        self._run("--dismiss", "greenhouse:blackcanyon", "--reason", "agency")
        self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
