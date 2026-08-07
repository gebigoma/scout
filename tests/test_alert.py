import os
import unittest
from unittest import mock

from pipeline import alert

from .support import RUN_DATE, PipelineTestCase


class NotifyTest(PipelineTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(alert.urllib.request, "urlopen")
        self.urlopen = patcher.start()
        self.addCleanup(patcher.stop)

    def _posted(self):
        request = self.urlopen.call_args.args[0]
        return request, request.data.decode()

    def test_nothing_is_sent_when_no_topic_is_configured(self):
        """NTFY_TOPIC lives in the launchd plist, not the repo - an unset
        topic means notifications are simply off, not an error."""
        alert.send_failure_alert(RUN_DATE, "fetch: all sources failed")
        self.urlopen.assert_not_called()

    def test_failure_alert_is_high_priority_and_names_the_error(self):
        os.environ["NTFY_TOPIC"] = "scout-test"
        alert.send_failure_alert(RUN_DATE, "fetch: all sources failed")
        request, body = self._posted()
        self.assertEqual(request.full_url, "https://ntfy.sh/scout-test")
        self.assertEqual(request.get_header("Priority"), "high")
        self.assertIn("scout weekly run failed", request.get_header("Title"))
        self.assertIn(RUN_DATE, body)
        self.assertIn("all sources failed", body)

    def test_delivery_failure_never_masks_the_real_error(self):
        os.environ["NTFY_TOPIC"] = "scout-test"
        self.urlopen.side_effect = OSError("network down")
        alert.send_failure_alert(RUN_DATE, "boom")  # must not raise


class SuccessHeartbeatTest(PipelineTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(alert.urllib.request, "urlopen")
        self.urlopen = patcher.start()
        self.addCleanup(patcher.stop)
        os.environ["NTFY_TOPIC"] = "scout-test"

    @staticmethod
    def _manifest(matches=2, rejected=0, candidates=137, per_source=None,
                  unclassified=0, failed_chunk_indices=None):
        return {"stages": {
            "fetch": {"per_source": per_source if per_source is not None else {
                "hn_whoishiring": {"count": 20},
                "remoteok": {"count": 100},
                "weworkremotely": {"count": 78},
            }},
            "dedupe": {"deduped_count": candidates},
            "classify": {"unclassified_count": unclassified,
                        "failed_chunk_indices": failed_chunk_indices or []},
            "digest": {"matches": matches, "classify_disagreements": rejected},
        }}

    def _body(self, manifest):
        alert.send_success_heartbeat(RUN_DATE, manifest)
        return self.urlopen.call_args.args[0].data.decode()

    def test_success_is_reported_so_that_silence_means_something(self):
        """The likeliest real failure is the job never running at all (lid
        closed at 9am). Failure-only alerting cannot detect that."""
        body = self._body(self._manifest())
        self.assertIn("2 matches from 137 candidates", body)
        request = self.urlopen.call_args.args[0]
        self.assertEqual(request.get_header("Priority"), "low")
        self.assertIn(RUN_DATE, request.get_header("Title"))

    def test_per_source_counts_surface_a_source_that_went_quiet(self):
        """A source returning zero still "succeeds" and would otherwise
        publish an empty digest that looks like an honest quiet week."""
        body = self._body(self._manifest(per_source={
            "hn_whoishiring": {"count": 20},
            "remoteok": {"count": 0},
        }))
        self.assertIn("hn_whoishiring 20", body)
        self.assertIn("remoteok 0", body)

    def test_scoring_disagreements_are_mentioned_only_when_present(self):
        self.assertIn("(3 rejected on scoring)", self._body(self._manifest(rejected=3)))
        self.assertNotIn("rejected on scoring", self._body(self._manifest(rejected=0)))

    def test_missing_stage_details_do_not_break_the_heartbeat(self):
        body = self._body({"stages": {}})
        self.assertIn("? matches from ? candidates", body)

    def test_unclassified_listings_are_mentioned_only_when_present(self):
        body = self._body(self._manifest(unclassified=20, failed_chunk_indices=[0]))
        self.assertIn("(20 listings unclassified, 1 chunks failed)", body)
        self.assertNotIn("unclassified", self._body(self._manifest()))


if __name__ == "__main__":
    unittest.main()
