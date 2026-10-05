"""digest_archive parses matches/*.md, which exist in two structural
generations (lane-wrapped `###` sections vs legacy `##` sections) and two score
generations (fit scores vs `(via ...)` only). All fixtures here are synthetic -
the suite never reads the real matches/."""
import contextlib
import io
import unittest

import digest_archive
import viewer
from pipeline import paths

from .support import PipelineTestCase

CURRENT = """# Matches — 2026-10-05

Sources: RemoteOK, HN "Who is hiring"; Greenhouse boards. 351 candidate listings reviewed against [`ROLE_CRITERIA.md`](../ROLE_CRITERIA.md).

## Fractional Roles

### Senior Technical Program Management

No matches this week.

### Agentic AI Engineer

- **Agent Builder (£30/hr)** — Acme Co (acme.example) (fit: 68/100)
  https://example.com/jobs/1
  Explicit hourly contract role with a clean title match,
  fully remote, and centrally agentic work -
  held back by agency-style delivery.

- **Odd — Title — With Dashes** — Odd — Company — Name (fit: 35/100)
  https://example.com/jobs/2
  Mangled row.

## First TPM

### First Technical Program Manager

No matches this week.

## Rejected on scoring

Cleared the initial match bar but scored below 35/100 — kept here for auditability.

- **Head of Ops** — Shopco (fit: 28/100)
  https://example.com/jobs/3
  Operational coordination, not TPM.
"""

LEGACY_SCORED = """# Matches — 2026-08-03

Sources: RemoteOK. 239 candidate listings reviewed against [`ROLE_CRITERIA.md`](../ROLE_CRITERIA.md).

## Senior Technical Program Management

No matches this week.

## Agentic AI Engineer

- **Sr Agentic Engineer** — Flywheel (flywheel.example) (fit: 72/100)
  https://example.com/jobs/4
  Explicit contract terms.
"""

LEGACY_UNSCORED = """# Matches — 2026-08-01

Sources: RemoteOK, HN (July 2026).
453 raw listings reviewed against [`ROLE_CRITERIA.md`](../ROLE_CRITERIA.md).

## Senior Technical Program Management

No matches this week. TPM postings found (Twilio "Senior Principal")
were full-time only, with no contract option mentioned.

## Agentic AI Engineer

- **Independent AI Engineer** — A.Team (via We Work Remotely)
  https://example.com/jobs/5
  Invite-only network.

- **AI Engineer, Lead** — EggAI (via HN, July 2026)
  https://example.com/jobs/6
  Full-time or Contract.
"""


class DigestArchiveTest(PipelineTestCase):
    def _write(self, date, text):
        d = paths.matches_dir()
        d.mkdir(exist_ok=True)
        (d / (date + ".md")).write_text(text)

    def _by_date(self):
        return {d["date"]: d for d in digest_archive.collect_digests()}

    def setUp(self):
        super().setUp()
        self._write("2026-10-05", CURRENT)
        self._write("2026-08-03", LEGACY_SCORED)
        self._write("2026-08-01", LEGACY_UNSCORED)

    def test_sorted_newest_first(self):
        self.assertEqual([d["date"] for d in digest_archive.collect_digests()],
                         ["2026-10-05", "2026-08-03", "2026-08-01"])

    def test_current_format_has_lanes_and_scores(self):
        d = self._by_date()["2026-10-05"]
        self.assertIsNone(d["error"])
        self.assertEqual(d["reviewed"], 351)
        self.assertTrue(d["sources"].startswith("Sources: RemoteOK"))
        self.assertNotIn("](", d["sources"])  # markdown links are flattened
        self.assertEqual(
            [(s["role"], s["lane"], s["rejected"]) for s in d["sections"]],
            [("Senior Technical Program Management", "Fractional Roles", False),
             ("Agentic AI Engineer", "Fractional Roles", False),
             ("First Technical Program Manager", "First TPM", False),
             ("Rejected on scoring", None, True)])
        first = d["sections"][1]["entries"][0]
        self.assertEqual(first["title"], "Agent Builder (£30/hr)")
        self.assertEqual(first["company"], "Acme Co (acme.example)")
        self.assertEqual(first["url"], "https://example.com/jobs/1")
        self.assertEqual(first["score"], 68)

    def test_wrapped_rationale_joined_into_one_paragraph(self):
        e = self._by_date()["2026-10-05"]["sections"][1]["entries"][0]
        self.assertEqual(
            e["rationale"],
            "Explicit hourly contract role with a clean title match, fully remote, "
            "and centrally agentic work - held back by agency-style delivery.")

    def test_legacy_headings_have_no_lane(self):
        d = self._by_date()["2026-08-03"]
        self.assertEqual([s["lane"] for s in d["sections"]], [None, None])
        self.assertEqual([s["role"] for s in d["sections"]],
                         ["Senior Technical Program Management", "Agentic AI Engineer"])
        self.assertEqual(d["sections"][1]["entries"][0]["score"], 72)

    def test_legacy_file_without_fit_scores(self):
        d = self._by_date()["2026-08-01"]
        self.assertEqual(d["reviewed"], 453)  # "raw", not "candidate", and on line 2
        entries = d["sections"][1]["entries"]
        self.assertEqual([e["score"] for e in entries], [None, None])
        self.assertEqual([e["via"] for e in entries],
                         ["We Work Remotely", "HN, July 2026"])
        self.assertEqual(entries[0]["company"], "A.Team")

    def test_em_dashes_in_title_do_not_misslice(self):
        e = self._by_date()["2026-10-05"]["sections"][1]["entries"][1]
        # Title is the whole bold span; the remainder after `** — ` is the company.
        self.assertEqual(e["title"], "Odd — Title — With Dashes")
        self.assertEqual(e["company"], "Odd — Company — Name")
        self.assertEqual(e["score"], 35)
        self.assertEqual(e["url"], "https://example.com/jobs/2")

    def test_non_bold_title_splits_on_first_dash(self):
        self._write("2026-09-01", "# Matches — 2026-09-01\n\n## Role\n\n"
                    "- Plain — Co — Inc (fit: 50/100)\n  https://example.com/x\n  why\n")
        e = self._by_date()["2026-09-01"]["sections"][0]["entries"][0]
        self.assertEqual((e["title"], e["company"]), ("Plain", "Co — Inc"))

    def test_no_matches_prose_is_preserved(self):
        s = self._by_date()["2026-08-01"]["sections"][0]
        self.assertTrue(s["no_matches"])
        self.assertEqual(s["entries"], [])
        self.assertEqual(
            s["no_matches_note"],
            'TPM postings found (Twilio "Senior Principal") were full-time only, '
            "with no contract option mentioned.")
        bare = self._by_date()["2026-10-05"]["sections"][0]
        self.assertTrue(bare["no_matches"])
        self.assertEqual(bare["no_matches_note"], "")

    def test_rejected_section_is_flagged(self):
        d = self._by_date()["2026-10-05"]
        rej = [s for s in d["sections"] if s["rejected"]]
        self.assertEqual(len(rej), 1)
        self.assertEqual(rej[0]["entries"][0]["score"], 28)
        self.assertEqual(len(digest_archive.rejected_entries(d)), 1)
        self.assertEqual(len(digest_archive.match_entries(d)), 2)
        self.assertFalse(any(s["rejected"] for s in self._by_date()["2026-08-03"]["sections"]))

    def test_garbage_and_truncated_files_are_skipped_not_fatal(self):
        self._write("2026-09-01", "this is not a digest\n")
        self._write("2026-09-02", "# Matches — 2026-09-02\n\n## Fractional Roles\n")
        self._write("2026-09-03", "# Matches — 2026-09-03\n\n## Role\n\n"
                    "- **Cut off** — Co (fit: 50/100)\n")
        self._write("2026-09-04", "")
        (paths.matches_dir() / "2026-09-05.md").write_bytes(b"\xff\xfe\x00bad")
        by = self._by_date()
        for date in ("2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-05"):
            self.assertTrue(by[date]["error"], date)
            self.assertEqual(by[date]["sections"], [], date)
        self.assertIsNone(by["2026-10-05"]["error"])  # neighbours unaffected
        page = viewer.render_digests(digest_archive.collect_digests())
        self.assertIn("Skipped: matches/2026-09-01.md", page)

    def test_missing_matches_dir_is_empty_and_not_created(self):
        import shutil
        shutil.rmtree(paths.matches_dir())
        self.assertEqual(digest_archive.collect_digests(), [])
        self.assertFalse(paths.matches_dir().exists())
        self.assertIn("No digests found", viewer.render_digests([]))


class DigestPageTest(PipelineTestCase):
    def _page(self, text):
        d = paths.matches_dir()
        d.mkdir()
        (d / "2026-10-05.md").write_text(text)
        return viewer.render_digests(digest_archive.collect_digests())

    def test_script_in_title_company_and_rationale_is_escaped(self):
        page = self._page(
            "# Matches — 2026-10-05\n\n## Role\n\n"
            "- **<script>alert(1)</script>** — <b>Evil</b> (fit: 70/100)\n"
            "  https://example.com/a?x=1&y=\"2\"\n"
            "  <img src=x onerror=alert(2)> **not bold**\n")
        self.assertNotIn("<script>", page)
        self.assertNotIn("<img", page)
        self.assertNotIn("<b>Evil", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertIn("&lt;img src=x onerror=alert(2)&gt; **not bold**", page)  # markdown not rendered
        self.assertIn('href="https://example.com/a?x=1&amp;y=&quot;2&quot;"', page)

    def test_non_http_url_is_never_linked(self):
        page = self._page("# Matches — 2026-10-05\n\n## Role\n\n"
                          "- **T** — C (fit: 70/100)\n  javascript:alert(1)\n  why\n")
        self.assertNotIn('href="javascript:', page)
        self.assertIn("Skipped: matches/2026-10-05.md", page)  # malformed, not rendered

    def test_summary_counts_and_rejected_marking(self):
        page = self._page(CURRENT)
        self.assertIn('<a href="#d-2026-10-05">2026-10-05</a>', page)
        self.assertIn("<td>351</td><td>2</td><td>1</td><td>68</td><td>52</td>", page)
        self.assertIn('<div class="entry rejected">', page)
        self.assertIn('<span class="tag">REJECTED</span>', page)
        self.assertIn('<span class="score low"', page)
        self.assertIn("Quiet weeks are published as zeros on purpose", page)

    def test_unscored_entry_shows_dash_not_zero(self):
        page = self._page(LEGACY_UNSCORED)
        self.assertIn('title="no fit score recorded">%s</span>' % viewer.MISSING, page)
        self.assertIn("(via We Work Remotely)", page)

    def test_pages_cross_link(self):
        self.assertIn('<a href="digests.html">Digests</a>', viewer.render([]))
        self.assertIn('<a href="index.html">Run health</a>', viewer.render_digests([]))

    def test_main_writes_both_pages_under_temp_project(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(viewer.main(), 0)
        self.assertEqual(paths.viewer_digests_path(),
                         self.project_dir / "viewer" / "digests.html")
        self.assertTrue(paths.viewer_digests_path().exists())
        self.assertTrue(paths.viewer_index_path().exists())
        self.assertFalse(paths.matches_dir().exists())


if __name__ == "__main__":
    unittest.main()
