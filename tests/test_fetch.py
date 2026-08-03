import json
import socket
import unittest
from unittest import mock

from pipeline import fetch, manifest, paths, retry

from . import fixtures
from .support import RUN_DATE, PipelineTestCase


class HttpGetTest(unittest.TestCase):
    def test_sends_an_identifying_user_agent(self):
        """Several of these sources 403 an unidentified client."""
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"{}"
        with mock.patch.object(fetch.urllib.request, "urlopen",
                               return_value=response) as urlopen:
            self.assertEqual(fetch._http_get("https://example.com"), b"{}")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_header("User-agent"), fetch.USER_AGENT)


class SourceParserTest(unittest.TestCase):
    def _patch_http(self, payload_by_url):
        def fake_get(url, timeout=15):
            for fragment, payload in payload_by_url.items():
                if fragment in url:
                    return payload if isinstance(payload, bytes) \
                        else json.dumps(payload).encode()
            raise AssertionError(f"unexpected url: {url}")
        return mock.patch.object(fetch, "_http_get", side_effect=fake_get)

    def test_remoteok_drops_the_legal_notice_element(self):
        """The API's first element is a terms-of-service object with no
        "position" key; treating it as a listing produced a junk row."""
        payload = [fixtures.REMOTEOK_LEGAL_NOTICE, fixtures.REMOTEOK_ITEM]
        with self._patch_http({"remoteok.com/api": payload}):
            items = fetch._fetch_remoteok_raw()
        self.assertEqual([i["id"] for i in items], ["1136038"])

    def test_wwr_reads_every_configured_feed(self):
        with self._patch_http({"weworkremotely.com": fixtures.WWR_RSS.encode()}):
            items = fetch._fetch_wwr_raw()
        self.assertEqual(len(items), 2 * len(fetch.WWR_FEEDS))
        self.assertEqual(items[0]["title"], "Acme: Fractional AI Engineer")
        self.assertEqual(items[0]["link"],
                         "https://weworkremotely.com/remote-jobs/acme-fractional-ai-engineer")
        self.assertIn("Contract", items[0]["description"])

    def test_wwr_missing_elements_become_empty_strings(self):
        rss = b"<rss><channel><item><title>Only a title</title></item></channel></rss>"
        with self._patch_http({"weworkremotely.com": rss}):
            items = fetch._fetch_wwr_raw()
        self.assertEqual(items[0]["link"], "")
        self.assertEqual(items[0]["description"], "")

    def test_hn_follows_the_most_recent_hiring_thread(self):
        with self._patch_http({"search_by_date": fixtures.HN_SEARCH_RESPONSE,
                               "items/49156000": fixtures.HN_THREAD_RESPONSE}) as http:
            items = fetch._fetch_hn_raw()
        self.assertIn("items/49156000", http.call_args_list[-1].args[0])
        self.assertTrue(all(i["thread_title"] == "Ask HN: Who is hiring? (August 2026)"
                            for i in items))

    def test_hn_skips_empty_and_deleted_comments(self):
        with self._patch_http({"search_by_date": fixtures.HN_SEARCH_RESPONSE,
                               "items/49156000": fixtures.HN_THREAD_RESPONSE}):
            items = fetch._fetch_hn_raw()
        self.assertEqual([i["id"] for i in items], [49156689, 49156692])

    def test_hn_with_no_hiring_thread_returns_nothing(self):
        with self._patch_http({"search_by_date": {"hits": []}}):
            self.assertEqual(fetch._fetch_hn_raw(), [])

    def test_hn_thread_without_children_returns_nothing(self):
        with self._patch_http({"search_by_date": fixtures.HN_SEARCH_RESPONSE,
                               "items/49156000": {"children": None}}):
            self.assertEqual(fetch._fetch_hn_raw(), [])


class FetchRunTest(PipelineTestCase):
    def setUp(self):
        super().setUp()
        self.patch_sleep(retry)

    def _run_with_sources(self, **sources):
        with mock.patch.dict(fetch.SOURCES, sources, clear=True):
            return fetch.run(RUN_DATE)

    def test_records_a_result_for_every_source(self):
        checkpoint = self._run_with_sources(
            remoteok=lambda: [fixtures.REMOTEOK_ITEM],
            weworkremotely=lambda: [fixtures.WWR_ITEM, fixtures.WWR_ITEM_NO_COMPANY],
        )
        self.assertEqual(checkpoint["sources"]["remoteok"]["count"], 1)
        self.assertEqual(checkpoint["sources"]["weworkremotely"]["count"], 2)
        self.assertTrue(all(s["status"] == "success"
                            for s in checkpoint["sources"].values()))

    def test_one_source_failing_does_not_stop_the_others(self):
        """A single flaky feed shouldn't cost the week's digest."""
        def down():
            raise socket.timeout("timed out")

        checkpoint = self._run_with_sources(
            remoteok=lambda: [fixtures.REMOTEOK_ITEM], weworkremotely=down)
        self.assertEqual(checkpoint["sources"]["remoteok"]["status"], "success")
        self.assertEqual(checkpoint["sources"]["weworkremotely"]["status"], "failed")
        self.assertEqual(checkpoint["sources"]["weworkremotely"]["items"], [])
        entry = manifest.load(RUN_DATE)["stages"]["fetch"]
        self.assertEqual(entry["status"], "success")
        self.assertEqual(entry["failed_sources"], ["weworkremotely"])
        self.assertEqual(entry["total_count"], 1)

    def test_a_transient_failure_is_retried(self):
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise socket.timeout("timed out")
            return [fixtures.REMOTEOK_ITEM]

        checkpoint = self._run_with_sources(remoteok=flaky)
        self.assertEqual(checkpoint["sources"]["remoteok"]["status"], "success")
        self.assertEqual(checkpoint["sources"]["remoteok"]["attempts"], 3)

    def test_all_sources_failing_fails_the_stage_and_writes_no_checkpoint(self):
        """No checkpoint on total failure, so a re-run actually re-fetches
        instead of resuming from an empty cached result."""
        def down():
            raise socket.timeout("timed out")

        with self.assertRaises(RuntimeError):
            self._run_with_sources(remoteok=down, weworkremotely=down)
        self.assertFalse(paths.checkpoint_path(RUN_DATE, "fetch").exists())
        m = manifest.load(RUN_DATE)
        self.assertEqual(m["stages"]["fetch"]["status"], "failed")
        self.assertEqual(m["status"], "failed")

    def test_our_own_parsing_bugs_crash_loudly(self):
        """A TypeError means our code is broken, not that the source is down -
        recording it as a per-source outage would hide the bug behind a
        plausible-looking "that feed was quiet this week"."""
        def broken():
            raise TypeError("'NoneType' object is not subscriptable")

        with self.assertRaises(TypeError):
            self._run_with_sources(remoteok=broken,
                                   weworkremotely=lambda: [fixtures.WWR_ITEM])

    def test_json_and_xml_parse_errors_count_as_source_outages(self):
        def bad_json():
            raise json.JSONDecodeError("boom", "", 0)

        checkpoint = self._run_with_sources(
            remoteok=bad_json, weworkremotely=lambda: [fixtures.WWR_ITEM])
        self.assertEqual(checkpoint["sources"]["remoteok"]["status"], "failed")

    def test_checkpoint_is_written_with_the_raw_items(self):
        self._run_with_sources(remoteok=lambda: [fixtures.REMOTEOK_ITEM])
        on_disk = self.read_json(paths.checkpoint_path(RUN_DATE, "fetch"))
        self.assertEqual(on_disk["sources"]["remoteok"]["items"][0]["id"], "1136038")


if __name__ == "__main__":
    unittest.main()
