import html
import os
import re
import unittest

from pipeline import manifest, normalize, prefilter

from . import fixtures
from .support import RUN_DATE, PipelineTestCase


def _first_tpm_listing(snippet, title="Open Role", **overrides):
    return fixtures.listing("https://x/1", title=title, snippet=snippet,
                            lane="first_tpm", **overrides)


class EvaluateTest(unittest.TestCase):
    def test_each_exact_tier1_phrase_passes(self):
        phrases = [
            "You will be our first technical program manager.",
            "Join as our second TPM on the team.",
            "You will establish the program management function here.",
            "Help us build out our TPM practice from day one.",
            "This is a first TPM hire for the company.",
        ]
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                result = prefilter.evaluate(_first_tpm_listing(phrase))
                self.assertTrue(result["passes"])
                self.assertTrue(result["tier1"])

    def test_a_proximity_hit_passes(self):
        text = "We are hiring our first TPM to run engineering programs."
        result = prefilter.evaluate(_first_tpm_listing(text))
        self.assertTrue(result["passes"])
        self.assertTrue(result["tier2"])

    def test_a_role_term_in_the_title_passes_without_foundation_evidence(self):
        """Tier 3. Tiers 1 and 2 both require foundation vocabulary, and over
        the eval corpus that dropped 12 of the 14 listings whose title said
        "Technical Program Manager" or "TPM" - TPM openings at Chainguard,
        Deepgram, Cribl, Together AI, Sardine and Anyscale, this lane's exact
        target profile, invisible because of ad wording rather than the job.
        Whether such a role is foundational is classify's judgment to make."""
        result = prefilter.evaluate(_first_tpm_listing(
            "Own cross-functional delivery across engineering and product.",
            title="Senior Technical Program Manager"))
        self.assertTrue(result["passes"])
        self.assertTrue(result["tier3"])
        self.assertFalse(result["tier1"])
        self.assertFalse(result["tier2"])

    def test_tpm_in_a_title_passes_on_the_acronym_too(self):
        result = prefilter.evaluate(_first_tpm_listing(
            "Drive programs end to end.", title="Sr. Technical Program Manager (TPM)"))
        self.assertTrue(result["tier3"])

    def test_a_program_manager_title_that_is_not_a_tpm_does_not_pass(self):
        """ROLE_TERM needs "technical program manager", "program management"
        or a word-boundary "tpm" - title matching is only affordable because
        it is this narrow. These are real corpus titles."""
        for title in ("Recruiting Operations Program Manager",
                      "Capacity Manager, Programs",
                      "Senior Partner Manager - Technology Partnerships"):
            with self.subTest(title=title):
                result = prefilter.evaluate(_first_tpm_listing(
                    "Run programs for the business.", title=title))
                self.assertFalse(result["tier3"])
                self.assertFalse(result["passes"])

    def test_a_trusted_platform_module_title_does_not_pass_on_tier3(self):
        """"TPM" is Trusted Platform Module across this lane's infra and
        security sources; word-boundary matching is what keeps tier 3 from
        admitting every attestation posting."""
        result = prefilter.evaluate(_first_tpm_listing(
            "Firmware for vTPM attestation.", title="Engineer, vTPM Firmware"))
        self.assertFalse(result["tier3"])

    def test_tier3_still_obeys_the_us_eligibility_gate(self):
        result = prefilter.evaluate(_first_tpm_listing(
            "Own delivery across engineering.",
            title="Technical Program Manager", location_text="Australia (Remote)"))
        self.assertTrue(result["tier3"])
        self.assertFalse(result["passes"])

    def test_a_role_term_with_no_foundation_term_anywhere_is_a_near_miss(self):
        """Near-miss means a role term was present and dropped anyway - weak
        evidence worth logging, not a silent drop."""
        result = prefilter.evaluate(_first_tpm_listing("Our TPM team is expanding this quarter."))
        self.assertFalse(result["passes"])
        self.assertTrue(result["near_miss"])

    def test_a_foundation_term_with_no_role_term_is_not_a_near_miss(self):
        """Foundation vocabulary alone is ordinary job-description filler -
        two thirds of any real corpus says "first" or "establish" somewhere.
        Counting those as near-misses (the pre-2026-09-03 definition, which
        was survivable only because matching ran over a 400-char snippet)
        buries the handful of drops actually worth reading under ~1300 log
        lines a run."""
        result = prefilter.evaluate(
            _first_tpm_listing("This is our first hire on the operations team.",
                               title="Operations Coordinator"))
        self.assertFalse(result["passes"])
        self.assertFalse(result["near_miss"])

    def test_both_terms_present_but_too_far_apart_fails_tier2_and_is_a_near_miss(self):
        """Both term families are present, just >200 chars apart - tier2
        correctly rejects it, and a role term was still seen and dropped, so
        it is exactly the case near_miss exists to surface."""
        text = ("We were first to market with our platform. " + ("Filler text. " * 40) +
               "Our program management practices are excellent.")
        result = prefilter.evaluate(_first_tpm_listing(text))
        self.assertFalse(result["passes"])
        self.assertFalse(result["tier2"])
        self.assertTrue(result["near_miss"])

    def test_trusted_platform_module_posting_does_not_match(self):
        """This lane sources heavily from infra/security companies where TPM
        means Trusted Platform Module, not Technical Program Manager."""
        text = "Vulnerability scanning on TPM 2.0 modules and vTPM attestation chips."
        result = prefilter.evaluate(_first_tpm_listing(text, title="Security Engineer"))
        self.assertFalse(result["passes"])

    def test_word_boundary_rejects_vtpm_and_tpmd_substrings(self):
        text = "Our agent monitors vtpm and tpmd daemons for attestation drift."
        result = prefilter.evaluate(_first_tpm_listing(text, title="Infra Engineer"))
        self.assertFalse(result["passes"])
        self.assertFalse(result["tier2"])

    def test_a_listing_with_neither_term_family_is_not_a_near_miss(self):
        result = prefilter.evaluate(_first_tpm_listing("Totally unrelated posting text."))
        self.assertFalse(result["passes"])
        self.assertFalse(result["near_miss"])

    def test_a_role_fit_match_tied_to_a_specific_non_us_country_is_dropped(self):
        """The Armada case: role-fit passes tier2, but location.name says
        "Australia (Remote)" - not workable, so it's dropped regardless."""
        text = "We are hiring our first TPM to run engineering programs."
        result = prefilter.evaluate(_first_tpm_listing(
            text, location_text="Australia (Remote)", location_country_code=""))
        self.assertFalse(result["passes"])
        self.assertTrue(result["role_fit"])
        self.assertFalse(result["us_eligible"])

    def test_a_role_fit_match_with_us_location_text_passes(self):
        text = "We are hiring our first TPM to run engineering programs."
        result = prefilter.evaluate(_first_tpm_listing(
            text, location_text="United States (Remote)", location_country_code=""))
        self.assertTrue(result["passes"])

    def test_a_role_fit_match_with_no_location_data_passes(self):
        """Most listings won't carry a location signal at all - absence isn't
        treated as disqualifying, only an explicit non-US signal is."""
        text = "We are hiring our first TPM to run engineering programs."
        result = prefilter.evaluate(_first_tpm_listing(text))
        self.assertTrue(result["passes"])

    def test_lever_non_us_country_code_fails_even_with_no_matching_location_text(self):
        """lever's ISO-3166 country code is checked directly and takes
        priority - it doesn't depend on the country name list."""
        text = "We are hiring our first TPM to run engineering programs."
        result = prefilter.evaluate(_first_tpm_listing(
            text, location_text="Bengaluru", location_country_code="IN"))
        self.assertFalse(result["passes"])
        self.assertFalse(result["us_eligible"])

    def test_matching_uses_match_text_not_the_truncated_snippet(self):
        """The bug that made this lane return zero candidates for its entire
        life (issue #13). normalize's snippet is a 400-char head, and the
        terms this stage matches on are not in the first 400 characters of a
        job description - on the 2026-08-17 corpus, 37 of 2018 listings
        carried a role term in the full text and only 9 did in the snippet.
        Matching must read `match_text`."""
        buried = ("About Us. " * 60) + "We are hiring our first TPM to run programs."
        listing = _first_tpm_listing(buried[:400], match_text=buried)
        self.assertTrue(prefilter.evaluate(listing)["passes"])
        # ...and the snippet alone genuinely does not contain the evidence,
        # so this test would pass vacuously if it didn't check that too.
        self.assertFalse(prefilter.evaluate(_first_tpm_listing(buried[:400]))["passes"])

    def test_proximity_is_not_measured_across_a_stitched_snippet_seam(self):
        """A snippet is `head + " […] " + salvaged sentences`, so a distance
        measured over it is a distance that doesn't exist in the document.
        Here the two terms are ~2000 chars apart in `match_text` and would
        look adjacent in the snippet; tier2 must believe the document."""
        text = ("Our program management org is mature. " + ("Filler text. " * 160) +
               "We were first to market.")
        stitched = "Our program management org is mature. […] We were first to market."
        listing = _first_tpm_listing(stitched, match_text=text)
        result = prefilter.evaluate(listing)
        self.assertFalse(result["tier2"])
        self.assertFalse(result["passes"])

    def test_fractional_listings_without_match_text_fall_back_to_the_snippet(self):
        """Only ATS listings carry match_text; nothing else should break."""
        result = prefilter.evaluate(
            _first_tpm_listing("We are hiring our first TPM to run programs."))
        self.assertTrue(result["passes"])

    def test_conflicting_location_signals_are_dropped_not_reconciled(self):
        """A US country code alongside free text naming another country is
        still dropped - any non-US signal disqualifies, per the Armada case
        where Greenhouse's own fields disagreed with each other."""
        text = "We are hiring our first TPM to run engineering programs."
        result = prefilter.evaluate(_first_tpm_listing(
            text, location_text="Australia (Remote)", location_country_code="US"))
        self.assertFalse(result["passes"])


class PrefilterRunTest(PipelineTestCase):
    def test_fractional_lane_listings_bypass_filtering_entirely(self):
        listing = fixtures.listing("https://x/1", title="Unrelated Role",
                                   snippet="Nothing about TPMs here.", lane="fractional")
        checkpoint = prefilter.run(RUN_DATE, {"listings": [listing]})
        self.assertEqual(checkpoint["listings"], [listing])

    def test_first_tpm_listings_that_fail_both_tiers_are_dropped(self):
        listing = _first_tpm_listing("Totally unrelated posting text.")
        checkpoint = prefilter.run(RUN_DATE, {"listings": [listing]})
        self.assertEqual(checkpoint["listings"], [])

    def test_records_counts_in_the_manifest(self):
        passing = _first_tpm_listing("Our first TPM hire will define the function.")
        near_miss = _first_tpm_listing("Our TPM will lead cross-team planning.")
        failing = fixtures.listing("https://x/3", title="x", snippet="nothing relevant",
                                   lane="first_tpm")
        prefilter.run(RUN_DATE, {"listings": [passing, near_miss, failing]})
        entry = manifest.load(RUN_DATE)["stages"]["prefilter"]
        self.assertEqual(entry["status"], "success")
        self.assertEqual(entry["passed"], 1)
        self.assertEqual(entry["filtered"], 2)
        self.assertEqual(entry["near_misses"], 1)
        # Distinguishes "saw no role terms" from "saw them and dropped them" -
        # both previously produced an identical empty digest and a success.
        self.assertEqual(entry["first_tpm_seen"], 3)
        self.assertEqual(entry["role_terms_seen"], 2)

    def test_match_text_is_dropped_from_listings_passed_downstream(self):
        """It's a whole job description and prefilter is its last reader;
        carrying it on would bloat every later checkpoint for no one."""
        listing = _first_tpm_listing("head", match_text="Our first TPM hire defines the function.")
        checkpoint = prefilter.run(RUN_DATE, {"listings": [listing]})
        self.assertEqual(len(checkpoint["listings"]), 1)
        self.assertNotIn("match_text", checkpoint["listings"][0])
        self.assertEqual(checkpoint["listings"][0]["snippet"], "head")


def _normalized(ats, name):
    listing, = normalize._normalize_ats({name: fixtures.payload(ats, name)})
    return listing


def _on_snippet_only(listing):
    """What prefilter would decide if it matched the snippet, as it did
    before issue #13 was fixed."""
    return prefilter.evaluate({**listing, "match_text": ""})


class RealPayloadTest(unittest.TestCase):
    """Raw ATS payloads through normalize and then prefilter.

    The synthetic tests above hand `evaluate` a finished listing, which is
    how they passed on every CI run while the stage rejected 100% of real
    listings (issues #13, #14): the bug lived in what normalize hands
    prefilter, and no synthetic listing goes through normalize. These start
    from the payload instead: real postings from retained runs, with their
    structure and markup kept and their prose rewritten for a public repo
    (tests/payloads/README.md). Every outcome asserted here was checked to be
    the same on the unredacted original."""

    def test_a_founding_tpm_ad_passes_on_an_exact_thesis_phrase(self):
        result = prefilter.evaluate(_normalized("ashby", "founding_tpm"))
        self.assertTrue(result["passes"])
        self.assertTrue(result["tier1"])

    def test_a_tpm_title_passes_when_its_evidence_is_out_of_proximity(self):
        """The ad says "establish the processes" and "program management",
        but more than 200 characters apart - the case tier 3 exists for."""
        listing = _normalized("greenhouse", "title_only_tpm")
        result = prefilter.evaluate(listing)
        self.assertTrue(result["passes"])
        self.assertTrue(result["tier3"])
        self.assertFalse(result["tier1"] or result["tier2"])
        # Greenhouse's `content` is entity-escaped HTML; none of it may
        # survive into the text the matchers read.
        self.assertNotIn("&lt;", listing["match_text"])
        self.assertNotIn("<", listing["match_text"])

    def test_evidence_past_the_snippet_cap_is_still_matched(self):
        """The direction issue #13 failed in. The only role term sits ~3,500
        characters in, and the 1,200-character snippet fills with salvaged
        foundation-vocabulary sentences long before reaching it - matching
        the snippet never sees the role at all."""
        listing = _normalized("greenhouse", "evidence_past_snippet_cap")
        self.assertFalse(_on_snippet_only(listing)["role_term"],
                         "fixture no longer puts the role term past the snippet")
        result = prefilter.evaluate(listing)
        self.assertTrue(result["passes"])
        self.assertTrue(result["tier2"])

    def test_a_stitched_snippet_cannot_fake_proximity(self):
        """The other direction. The ad says "This job is not program
        management", ~370 characters from any foundation term - but the snippet
        stitches salvaged sentences together, so there the two sit side by
        side and tier 2 would pass a listing that disclaims the role."""
        listing = _normalized("greenhouse", "stitched_false_adjacency")
        self.assertTrue(_on_snippet_only(listing)["tier2"],
                        "fixture no longer exercises the stitched seam")
        result = prefilter.evaluate(listing)
        self.assertFalse(result["passes"])
        self.assertTrue(result["near_miss"])

    def test_a_program_manager_title_that_is_not_a_tpm_is_a_near_miss(self):
        """"Recruiting Operations Program Manager" - a real title, and the
        reason ROLE_TERM is narrow enough for tier 3 to afford title matching."""
        result = prefilter.evaluate(_normalized("ashby", "program_manager_not_tpm"))
        self.assertFalse(result["passes"])
        self.assertFalse(result["tier3"])
        self.assertTrue(result["near_miss"])

    def test_a_tpm_at_a_non_us_office_is_dropped_despite_us_metadata(self):
        """The posting NON_US_COUNTRIES' comment describes: `location.name`
        "Australia (Remote)" alongside a custom metadata field saying "United
        States (Remote)". Only `location.name` is read."""
        result = prefilter.evaluate(_normalized("greenhouse", "non_us_tpm"))
        self.assertTrue(result["role_fit"])
        self.assertFalse(result["us_eligible"])
        self.assertFalse(result["passes"])

    def test_a_tpm_open_in_the_us_and_canada_is_dropped(self):
        """Pins current behaviour, not a judgment that it's right. Ashby's
        `secondaryLocations` lists "United States" and "Canada", normalize
        joins them into `location_text`, and "Canada" alone fails the
        country check - so a remote TPM role open to US candidates is
        dropped. One real opening went this way on every run from 2026-09-14
        to 2026-10-05. Issue #46 asks whether it should."""
        listing = _normalized("ashby", "non_us_tpm")
        self.assertIn("United States", listing["location_text"])
        result = prefilter.evaluate(listing)
        self.assertTrue(result["tier3"])
        self.assertFalse(result["us_eligible"])

    def test_a_lever_country_code_is_authoritative(self):
        listing = _normalized("lever", "no_role_term")
        self.assertEqual(listing["location_country_code"], "IN")
        result = prefilter.evaluate(listing)
        self.assertFalse(result["us_eligible"])
        self.assertFalse(result["role_term"])

    @unittest.expectedFailure
    def test_lever_list_sections_reach_match_text(self):
        """Issue #44. normalize reads only `descriptionPlain`, which in
        Lever's payload is the opening paragraph; the responsibilities and
        requirements arrive separately in `lists`. In this posting that is
        the whole role. Remove the decorator when #44 is fixed."""
        listing = _normalized("lever", "no_role_term")
        self.assertIn("Check eligibility and benefits", listing["match_text"])


class RealPayloadRunTest(PipelineTestCase):
    def test_normalize_then_prefilter_over_every_payload(self):
        """The two stages wired together as run_pipeline wires them, over
        every captured payload at once - the seam issue #13 lived in."""
        entries = {f"{ats}/{name}": fixtures.payload(ats, name)
                   for ats, name in [
                       ("ashby", "founding_tpm"),
                       ("ashby", "non_us_tpm"),
                       ("ashby", "program_manager_not_tpm"),
                       ("greenhouse", "evidence_past_snippet_cap"),
                       ("greenhouse", "non_us_tpm"),
                       ("greenhouse", "stitched_false_adjacency"),
                       ("greenhouse", "title_only_tpm"),
                       ("lever", "no_role_term"),
                   ]}
        normalized = normalize.run(RUN_DATE, {"sources": {}}, {"companies": entries})
        checkpoint = prefilter.run(RUN_DATE, normalized)

        self.assertEqual(
            sorted(l["title"] for l in checkpoint["listings"]),
            ["Founding Technical Program Manager",
             "Senior Manager, Public Sector Partners",
             "Senior Technical Program Manager"])
        for listing in checkpoint["listings"]:
            self.assertNotIn("match_text", listing)

        entry = manifest.load(RUN_DATE)["stages"]["prefilter"]
        self.assertEqual(entry["first_tpm_seen"], 8)
        self.assertEqual(entry["passed"], 3)
        self.assertEqual(entry["title_only"], 1)
        self.assertEqual(entry["near_misses"], 2)
        self.assertEqual(entry["filtered_non_us"], 2)
        self.assertEqual(entry["role_terms_seen"], 7)


class PayloadFilesTest(unittest.TestCase):
    """A guard on what gets committed, not on the pipeline. This repo is
    public, so a payload added later must not carry a link back to the
    company behind it. Company names can't be checked here - the watchlist
    is private and absent in CI - so that part stays a review step."""

    ALLOWED_HOSTS = ("jobs.ashbyhq.com", "job-boards.greenhouse.io",
                     "jobs.lever.co", "example.com")

    def test_every_link_is_an_ats_host_or_example_com(self):
        url = re.compile(r"https?://([^/\s\"'<>\\)&]+)")
        for root, _, files in os.walk(fixtures.PAYLOAD_DIR):
            for fname in files:
                if not fname.endswith(".json"):
                    continue
                with open(os.path.join(root, fname), encoding="utf-8") as f:
                    # Greenhouse content is escaped twice over.
                    text = html.unescape(html.unescape(f.read()))
                for host in url.findall(text):
                    with self.subTest(file=fname, host=host):
                        self.assertIn(host, self.ALLOWED_HOSTS)


if __name__ == "__main__":
    unittest.main()
