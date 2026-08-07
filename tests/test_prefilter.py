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
        """Near-miss means exactly one of the two term families is present -
        a role term alone is weak evidence worth logging, not a silent drop."""
        result = prefilter.evaluate(_first_tpm_listing("Our TPM team is expanding this quarter."))
        self.assertFalse(result["passes"])
        self.assertTrue(result["near_miss"])

    def test_a_foundation_term_with_no_role_term_anywhere_is_a_near_miss(self):
        result = prefilter.evaluate(
            _first_tpm_listing("This is our first hire on the operations team.",
                               title="Operations Coordinator"))
        self.assertFalse(result["passes"])
        self.assertTrue(result["near_miss"])

    def test_both_terms_present_but_too_far_apart_fails_tier2_and_is_not_a_near_miss(self):
        """Both term families are present here, just >200 chars apart - tier2
        correctly rejects it, and it's not counted as a near-miss since both
        conditions (not just one) were actually met somewhere in the text."""
        text = ("We were first to market with our platform. " + ("Filler text. " * 40) +
               "Our program management practices are excellent.")
        result = prefilter.evaluate(_first_tpm_listing(text))
        self.assertFalse(result["passes"])
        self.assertFalse(result["tier2"])
        self.assertFalse(result["near_miss"])

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


if __name__ == "__main__":
    unittest.main()
