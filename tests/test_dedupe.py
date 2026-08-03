import json
import unittest

from pipeline import dedupe, manifest, paths

from . import fixtures
from .support import RUN_DATE, PipelineTestCase


class LoadSeenTest(PipelineTestCase):
    def test_missing_file_is_an_empty_map(self):
        self.assertEqual(dedupe.load_seen(), {})

    def test_reads_the_url_to_first_seen_date_map(self):
        paths.seen_path().parent.mkdir(parents=True, exist_ok=True)
        paths.seen_path().write_text(json.dumps({"seen": {"u1": "2026-08-01"}}))
        self.assertEqual(dedupe.load_seen(), {"u1": "2026-08-01"})

    def test_legacy_flat_list_is_read_as_seen_on_an_unknown_earlier_date(self):
        paths.seen_path().parent.mkdir(parents=True, exist_ok=True)
        paths.seen_path().write_text(json.dumps({"matched_urls": ["u1", "u2"]}))
        self.assertEqual(dedupe.load_seen(), {"u1": "", "u2": ""})


class DedupeRunTest(PipelineTestCase):
    def _write_seen(self, seen):
        paths.seen_path().parent.mkdir(parents=True, exist_ok=True)
        paths.seen_path().write_text(json.dumps({"seen": seen}))

    def _run(self, listings):
        return dedupe.run(RUN_DATE, {"listings": listings})

    def test_passes_through_distinct_unseen_listings(self):
        checkpoint = self._run([fixtures.listing("u1"), fixtures.listing("u2")])
        self.assertEqual([l["url"] for l in checkpoint["listings"]], ["u1", "u2"])

    def test_drops_listings_with_no_url(self):
        checkpoint = self._run([fixtures.listing(""), fixtures.listing("u1")])
        self.assertEqual([l["url"] for l in checkpoint["listings"]], ["u1"])
        self.assertEqual(manifest.load(RUN_DATE)["stages"]["dedupe"]["dropped_no_url"], 1)

    def test_keeps_the_first_of_an_in_run_duplicate(self):
        checkpoint = self._run([
            fixtures.listing("u1", title="First"),
            fixtures.listing("u1", title="Second"),
        ])
        self.assertEqual(len(checkpoint["listings"]), 1)
        self.assertEqual(checkpoint["listings"][0]["title"], "First")

    def test_drops_urls_matched_on_an_earlier_date(self):
        self._write_seen({"u1": "2026-07-27"})
        checkpoint = self._run([fixtures.listing("u1"), fixtures.listing("u2")])
        self.assertEqual([l["url"] for l in checkpoint["listings"]], ["u2"])

    def test_legacy_seen_entries_are_still_excluded(self):
        paths.seen_path().parent.mkdir(parents=True, exist_ok=True)
        paths.seen_path().write_text(json.dumps({"matched_urls": ["u1"]}))
        checkpoint = self._run([fixtures.listing("u1"), fixtures.listing("u2")])
        self.assertEqual([l["url"] for l in checkpoint["listings"]], ["u2"])

    def test_urls_first_seen_on_this_run_date_are_not_dropped(self):
        """This is what makes --force idempotent. digest writes seen.json at
        the end of a run and dedupe reads it near the start, so keying on the
        first-seen date is the only thing stopping a re-run of a completed
        date from deduping away its own matches and overwriting the real
        digest with an empty one - while still reporting success."""
        self._write_seen({"u1": RUN_DATE, "u2": "2026-07-27"})
        checkpoint = self._run([fixtures.listing("u1"), fixtures.listing("u2")])
        self.assertEqual([l["url"] for l in checkpoint["listings"]], ["u1"])

    def test_counts_each_drop_reason_separately(self):
        self._write_seen({"u_old": "2026-07-27"})
        self._run([
            fixtures.listing(""),
            fixtures.listing("u_old"),
            fixtures.listing("u1"),
            fixtures.listing("u1"),
        ])
        entry = manifest.load(RUN_DATE)["stages"]["dedupe"]
        self.assertEqual(entry["raw_count"], 4)
        self.assertEqual(entry["deduped_count"], 1)
        self.assertEqual(entry["dropped_no_url"], 1)
        self.assertEqual(entry["dropped_previously_matched"], 1)
        self.assertEqual(entry["dropped_in_run_dupe"], 1)

    def test_writes_the_checkpoint_to_disk(self):
        self._run([fixtures.listing("u1")])
        on_disk = self.read_json(paths.checkpoint_path(RUN_DATE, "dedupe"))
        self.assertEqual([l["url"] for l in on_disk["listings"]], ["u1"])

    def test_empty_input_is_not_an_error(self):
        checkpoint = self._run([])
        self.assertEqual(checkpoint["listings"], [])
        self.assertEqual(manifest.load(RUN_DATE)["stages"]["dedupe"]["status"], "success")


if __name__ == "__main__":
    unittest.main()
