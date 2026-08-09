import json
import unittest
from unittest import mock

from pipeline import funding, retry


def _hit(cik, display_name, file_date):
    return {"_source": {"ciks": [cik], "display_names": [display_name], "file_date": file_date}}


def _response(hits):
    return json.dumps({"hits": {"hits": hits}}).encode()


class LookupFormDTest(unittest.TestCase):
    def test_zero_hits_returns_none_not_zero_or_false(self):
        opener = lambda url: _response([])
        result = funding.lookup_form_d("Checkly", opener=opener)
        self.assertIsNone(result)

    def test_single_cik_is_not_ambiguous(self):
        opener = lambda url: _response([_hit("0001756001", "Acme Robotics, Inc.", "2024-07-29")])
        result = funding.lookup_form_d("Acme Robotics", opener=opener)
        self.assertEqual(result["last_form_d"], "2024-07-29")
        self.assertEqual(result["ciks"], ["0001756001"])
        self.assertFalse(result["ambiguous"])

    def test_multi_cik_response_flags_ambiguous_and_does_not_pick_newest_blindly(self):
        """entityName is a fuzzy match - "Coder" really does return both
        Coder Technologies, Inc. and the unrelated Coder Kids, Inc. The
        newest filing must not be silently trusted as the right company."""
        opener = lambda url: _response([
            _hit("0001756001", "Coder Technologies, Inc.", "2022-01-10"),
            _hit("0001748254", "Coder Kids, Inc.", "2024-09-01"),
        ])
        result = funding.lookup_form_d("Coder", opener=opener)
        self.assertTrue(result["ambiguous"])
        self.assertEqual(set(result["ciks"]), {"0001756001", "0001748254"})
        self.assertEqual(set(result["display_names"]),
                         {"Coder Technologies, Inc.", "Coder Kids, Inc."})


class EnrichCompaniesTest(unittest.TestCase):
    def setUp(self):
        for module in (funding, retry):
            patcher = mock.patch.object(module.time, "sleep")
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_one_company_erroring_is_partial_not_a_stage_failure(self):
        def opener(url):
            if "BadCo" in url:
                raise funding.URLError("boom")
            return _response([_hit("0001111111", "GoodCo, Inc.", "2023-05-01")])

        results = funding.enrich_companies(["GoodCo", "BadCo"], opener=opener)
        self.assertEqual(results["GoodCo"]["status"], "success")
        self.assertEqual(results["GoodCo"]["funding"]["last_form_d"], "2023-05-01")
        self.assertEqual(results["BadCo"]["status"], "failed")
        self.assertIsNone(results["BadCo"]["funding"])

    def test_zero_hits_for_one_company_still_yields_success_status_with_none_funding(self):
        opener = lambda url: _response([])
        results = funding.enrich_companies(["Checkly"], opener=opener)
        self.assertEqual(results["Checkly"]["status"], "success")
        self.assertIsNone(results["Checkly"]["funding"])


if __name__ == "__main__":
    unittest.main()
