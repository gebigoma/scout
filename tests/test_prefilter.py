import unittest

from pipeline import manifest, prefilter

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


if __name__ == "__main__":
    unittest.main()
