import os
import unittest
from unittest import mock

from pipeline import lanes


class ActiveLanesTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("SCOUT_LANES", None)

    def test_defaults_to_both_lanes(self):
        self.assertEqual(sorted(lanes.active_lanes()), sorted([lanes.FRACTIONAL, lanes.FIRST_TPM]))

    def test_a_single_lane_can_be_selected(self):
        os.environ["SCOUT_LANES"] = "fractional"
        self.assertEqual(lanes.active_lanes(), ["fractional"])

    def test_a_comma_separated_list_selects_both_explicitly(self):
        os.environ["SCOUT_LANES"] = "fractional,first_tpm"
        self.assertEqual(lanes.active_lanes(), ["fractional", "first_tpm"])

    def test_whitespace_around_lane_names_is_tolerated(self):
        os.environ["SCOUT_LANES"] = " fractional , first_tpm "
        self.assertEqual(lanes.active_lanes(), ["fractional", "first_tpm"])

    def test_an_unknown_lane_name_raises(self):
        os.environ["SCOUT_LANES"] = "fractional,bogus"
        with self.assertRaises(ValueError):
            lanes.active_lanes()


if __name__ == "__main__":
    unittest.main()
