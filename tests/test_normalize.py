import unittest

from pipeline import manifest, normalize, paths

from . import fixtures
from .support import RUN_DATE, PipelineTestCase


class StripHtmlTest(unittest.TestCase):
    def test_removes_tags_and_unescapes_entities(self):
        self.assertEqual(
            normalize._strip_html("<p>Contract &amp; part-time</p>").strip(),
            "Contract & part-time",
        )

    def test_handles_hn_style_double_escaped_entities(self):
        self.assertIn("We're", normalize._strip_html("<p>We&#x27;re hiring"))

    def test_tolerates_empty_and_none(self):
        self.assertEqual(normalize._strip_html(""), "")
        self.assertEqual(normalize._strip_html(None), "")


class ExtractSnippetTest(unittest.TestCase):
    def test_short_text_is_returned_whole(self):
        self.assertEqual(normalize._extract_snippet("Contract role."), "Contract role.")

    def test_long_text_without_employment_terms_is_head_truncated(self):
        text = "a" * 1000
        self.assertEqual(normalize._extract_snippet(text), "a" * normalize.HEAD_CHARS)

    def test_later_employment_sentences_are_appended_after_the_head(self):
        text = ("Boilerplate. " * 40) + "This is a part-time contract engagement. " \
               + ("More filler. " * 20)
        snippet = normalize._extract_snippet(text)
        self.assertIn("part-time contract engagement", snippet)
        self.assertIn(" […] ", snippet)

    def test_unrelated_tail_sentences_are_not_appended(self):
        text = ("Boilerplate. " * 40) + "Apply with a cover letter."
        self.assertNotIn("cover letter", normalize._extract_snippet(text))

    def test_result_is_capped_at_max_snippet(self):
        text = ("Boilerplate. " * 40) + ("This role is part-time. " * 200)
        self.assertLessEqual(len(normalize._extract_snippet(text)),
                             normalize.MAX_SNIPPET)

    def test_wwr_boilerplate_does_not_hide_the_employment_terms(self):
        """The regression that made a zero-match week untrustworthy: WWR
        descriptions open with ~400 chars of "About Us" copy and state the
        terms much further down, so head truncation alone fed the classifier
        marketing text and hid the evidence the criteria require."""
        text = normalize._strip_html(fixtures.WWR_ITEM["description"])
        head_only = text[:normalize.HEAD_CHARS]
        self.assertNotIn("part-time", head_only)
        self.assertIn("part-time contract basis", normalize._extract_snippet(text))

    def test_head_chars_is_configurable_per_source(self):
        text = "b" * 1000
        self.assertEqual(len(normalize._extract_snippet(text, head_chars=600)), 600)


class NormalizeRemoteOKTest(unittest.TestCase):
    def test_maps_every_common_schema_field(self):
        listing, = normalize._normalize_remoteok([fixtures.REMOTEOK_ITEM])
        self.assertEqual(listing["source"], "remoteok")
        self.assertEqual(listing["title"], "Fractional Senior Technical Program Manager")
        self.assertEqual(listing["company"], "Acme Robotics")
        self.assertEqual(listing["url"], fixtures.REMOTEOK_ITEM["url"])
        self.assertEqual(listing["posted_date"], "2026-08-02T15:57:11+00:00")
        self.assertNotIn("<strong>", listing["snippet"])

    def test_keeps_tags_the_strongest_employment_type_signal(self):
        """RemoteOK marks contract/part-time in tags; these were normalized
        and then dropped before reaching the model."""
        listing, = normalize._normalize_remoteok([fixtures.REMOTEOK_ITEM])
        self.assertEqual(listing["tags"], ["contract", "part time", "program management"])

    def test_missing_fields_degrade_to_empty_strings(self):
        listing, = normalize._normalize_remoteok([{}])
        self.assertEqual(listing["title"], "")
        self.assertEqual(listing["url"], "")
        self.assertEqual(listing["tags"], [])


class NormalizeWwrTest(unittest.TestCase):
    def test_splits_company_from_role(self):
        """WWR titles are "Company: Role"; unsplit, the digest fell back to
        printing the literal source name as the company."""
        listing, = normalize._normalize_wwr([fixtures.WWR_ITEM])
        self.assertEqual(listing["company"], "Cloudflare")
        self.assertEqual(listing["title"], "Principal Partner Solutions Engineer")

    def test_title_without_a_colon_keeps_the_whole_string_as_the_role(self):
        listing, = normalize._normalize_wwr([fixtures.WWR_ITEM_NO_COMPANY])
        self.assertEqual(listing["company"], "")
        self.assertEqual(listing["title"], "Senior Product Designer")

    def test_only_the_first_colon_separates_company_from_role(self):
        listing, = normalize._normalize_wwr(
            [{"title": "Acme: Senior TPM: Platform", "link": "u"}])
        self.assertEqual(listing["company"], "Acme")
        self.assertEqual(listing["title"], "Senior TPM: Platform")

    def test_maps_link_and_pubdate_into_the_common_schema(self):
        listing, = normalize._normalize_wwr([fixtures.WWR_ITEM])
        self.assertEqual(listing["source"], "weworkremotely")
        self.assertEqual(listing["url"], fixtures.WWR_ITEM["link"])
        self.assertEqual(listing["posted_date"], fixtures.WWR_ITEM["pubDate"])


class NormalizeHnTest(unittest.TestCase):
    def test_parses_the_pipe_delimited_hiring_convention(self):
        """Splitting on newlines instead treated the whole comment as the
        title and chopped it mid-word."""
        listing, = normalize._normalize_hn([fixtures.HN_ITEM])
        self.assertEqual(listing["company"], "Snout")
        self.assertEqual(listing["title"], "Agentic AI Engineer")

    def test_builds_a_stable_item_url_from_the_comment_id(self):
        listing, = normalize._normalize_hn([fixtures.HN_ITEM])
        self.assertEqual(listing["url"],
                         "https://news.ycombinator.com/item?id=49156689")

    def test_source_carries_the_thread_title(self):
        listing, = normalize._normalize_hn([fixtures.HN_ITEM])
        self.assertEqual(listing["source"],
                         "hn:Ask HN: Who is hiring? (August 2026)")

    def test_header_field_order_is_not_a_convention_people_follow(self):
        """fields[1] is the role in barely half of a real thread, so the role
        has to be picked by content: taking position 1 published locations,
        salary bands, YC batches and bare URLs as job titles."""
        for header, company, title in fixtures.HN_HEADERS:
            with self.subTest(header=header):
                listing, = normalize._normalize_hn(
                    [{"id": 1, "text": header + "<p>Body text here."}])
                self.assertEqual(listing["company"], company)
                self.assertEqual(listing["title"], title)

    def test_a_location_is_never_promoted_to_a_job_title(self):
        """The Flywheel Motion regression: the header lists only location and
        terms, and "REMOTE (worldwide)" was published as the role."""
        listing, = normalize._normalize_hn([{
            "id": 1,
            "text": "Acme | REMOTE (worldwide) | Contract<p>Hiring a Sr Agentic Engineer.",
        }])
        self.assertNotEqual(listing["title"], "REMOTE (worldwide)")
        self.assertIn("Contract", listing["title"])

    def test_the_body_is_not_glued_onto_the_last_header_field(self):
        """<p> separates an HN header from its body, and _strip_html turns it
        into a space - which ran the opening sentence of the body into the
        final header field and produced headings cut off mid-word."""
        listing, = normalize._normalize_hn([{
            "id": 1,
            "text": "Acme | Remote | Senior Platform Engineer<p>We build things for people whose",
        }])
        self.assertEqual(listing["title"], "Senior Platform Engineer")

    def test_a_newline_separated_header_is_also_split_from_the_body(self):
        listing, = normalize._normalize_hn(
            [{"id": 1, "text": "Acme | Senior TPM | Contract\nWe build things."}])
        self.assertEqual(listing["title"], "Senior TPM")

    def test_titles_never_contain_newlines(self):
        """Titles are rendered into markdown list items, where an embedded
        newline silently breaks the entry."""
        listing, = normalize._normalize_hn(
            [{"id": 1, "text": "Location: Brazil\n  Remote: Yes\n  Willing to relocate: No"}])
        self.assertNotIn("\n", listing["title"])
        self.assertNotIn("\n", listing["company"])

    def test_employment_terms_stay_in_the_title_when_they_name_a_role(self):
        listing, = normalize._normalize_hn(
            [{"id": 1, "text": "Acme | Contract Senior TPM | Remote<p>Body."}])
        self.assertEqual(listing["title"], "Contract Senior TPM")

    def test_comment_without_pipes_falls_back_to_leading_text(self):
        listing, = normalize._normalize_hn(
            [{"id": 1, "text": "We are hiring a fractional TPM, remote."}])
        self.assertEqual(listing["title"], "We are hiring a fractional TPM, remote.")

    def test_company_and_role_are_length_capped(self):
        listing, = normalize._normalize_hn(
            [{"id": 1, "text": "C" * 200 + " | " + "R" * 200}])
        self.assertEqual(len(listing["company"]), 80)
        self.assertEqual(len(listing["title"]), 100)


class NormalizeAtsTest(unittest.TestCase):
    def _companies_results(self, ats, job, **company_overrides):
        company = {"name": "Acme Robotics", "ats": ats, "token": "acmerobotics",
                   "headcount": 85, "source": "lightspeed", **company_overrides}
        return {company["name"]: {"status": "success", "company": company, "jobs": [job]}}

    def test_greenhouse_fields_are_mapped_and_tagged_first_tpm(self):
        listing, = normalize._normalize_ats(
            self._companies_results("greenhouse", fixtures.GREENHOUSE_JOB))
        self.assertEqual(listing["title"], "First Technical Program Manager")
        self.assertEqual(listing["company"], "Acme Robotics")
        self.assertEqual(listing["url"], fixtures.GREENHOUSE_JOB["absolute_url"])
        self.assertEqual(listing["lane"], "first_tpm")
        self.assertEqual(listing["headcount"], 85)
        self.assertIn("first TPM hire", listing["snippet"])

    def test_ashby_uses_description_plain(self):
        listing, = normalize._normalize_ats(
            self._companies_results("ashby", fixtures.ASHBY_JOB))
        self.assertEqual(listing["title"], "Senior Technical Program Manager")
        self.assertEqual(listing["url"], fixtures.ASHBY_JOB["jobUrl"])
        self.assertIn("second TPM", listing["snippet"])

    def test_lever_uses_text_and_hosted_url(self):
        listing, = normalize._normalize_ats(
            self._companies_results("lever", fixtures.LEVER_JOB))
        self.assertEqual(listing["title"], "Staff Program Manager, Infrastructure")
        self.assertEqual(listing["url"], fixtures.LEVER_JOB["hostedUrl"])

    def test_failed_or_skipped_companies_contribute_no_listings(self):
        results = {
            "Down Co": {"status": "failed", "company": {"ats": "greenhouse"}, "jobs": []},
            "Gone Co": {"status": "skipped", "company": {"ats": "greenhouse"}, "jobs": []},
        }
        self.assertEqual(normalize._normalize_ats(results), [])

    def test_missing_headcount_is_carried_through_as_none(self):
        listing, = normalize._normalize_ats(
            self._companies_results("greenhouse", fixtures.GREENHOUSE_JOB, headcount=None))
        self.assertIsNone(listing["headcount"])


class NormalizeRunTest(PipelineTestCase):
    def _fetch_checkpoint(self, **sources):
        return {"sources": {name: {"status": "success", "items": items}
                            for name, items in sources.items()}}

    def test_normalizes_every_source_into_one_list(self):
        checkpoint = normalize.run(RUN_DATE, self._fetch_checkpoint(
            remoteok=[fixtures.REMOTEOK_ITEM],
            weworkremotely=[fixtures.WWR_ITEM],
            hn_whoishiring=[fixtures.HN_ITEM],
        ))
        self.assertEqual(len(checkpoint["listings"]), 3)
        self.assertEqual({l["source"].split(":")[0] for l in checkpoint["listings"]},
                         {"remoteok", "weworkremotely", "hn"})

    def test_a_failed_source_is_simply_absent_not_fatal(self):
        checkpoint = normalize.run(RUN_DATE,
                                   self._fetch_checkpoint(remoteok=[fixtures.REMOTEOK_ITEM]))
        self.assertEqual(len(checkpoint["listings"]), 1)

    def test_every_listing_has_the_full_common_schema(self):
        checkpoint = normalize.run(RUN_DATE, self._fetch_checkpoint(
            remoteok=[fixtures.REMOTEOK_ITEM],
            weworkremotely=[fixtures.WWR_ITEM],
            hn_whoishiring=[fixtures.HN_ITEM],
        ))
        expected = {"source", "title", "company", "url", "posted_date", "snippet", "tags", "lane"}
        for listing in checkpoint["listings"]:
            self.assertEqual(set(listing), expected)
            self.assertEqual(listing["lane"], "fractional")

    def test_writes_the_checkpoint_and_marks_the_stage_succeeded(self):
        normalize.run(RUN_DATE, self._fetch_checkpoint(remoteok=[fixtures.REMOTEOK_ITEM]))
        on_disk = self.read_json(paths.checkpoint_path(RUN_DATE, "normalize"))
        self.assertEqual(len(on_disk["listings"]), 1)
        entry = manifest.load(RUN_DATE)["stages"]["normalize"]
        self.assertEqual((entry["status"], entry["count"]), ("success", 1))

    def test_works_unchanged_with_no_ats_checkpoint(self):
        """Backward compatibility for the fractional-only call shape."""
        checkpoint = normalize.run(RUN_DATE, self._fetch_checkpoint(
            remoteok=[fixtures.REMOTEOK_ITEM]))
        self.assertEqual(len(checkpoint["listings"]), 1)

    def test_merges_in_ats_listings_when_a_fetch_ats_checkpoint_is_given(self):
        fetch_ats_cp = {"companies": {
            "Acme Robotics": {
                "status": "success",
                "company": {"name": "Acme Robotics", "ats": "greenhouse",
                            "token": "acmerobotics", "headcount": 85, "source": "lightspeed"},
                "jobs": [fixtures.GREENHOUSE_JOB],
            },
        }}
        checkpoint = normalize.run(
            RUN_DATE, self._fetch_checkpoint(remoteok=[fixtures.REMOTEOK_ITEM]), fetch_ats_cp)
        self.assertEqual(len(checkpoint["listings"]), 2)
        lanes = {l["lane"] for l in checkpoint["listings"]}
        self.assertEqual(lanes, {"fractional", "first_tpm"})


if __name__ == "__main__":
    unittest.main()
