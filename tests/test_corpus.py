"""corpus.py is an observer over other stages' checkpoints, not a stage
itself - these tests build the checkpoints by hand rather than running the
real stages, and pin the two things most likely to silently drift:
reached_dedupe (the historical decision) vs prefilter_passes (today's
recomputation) disagreeing on purpose, and classify_verdict being
reconstructed from chunk files since classify.json itself only records
matches."""
import json
import unittest

from pipeline import corpus, paths

from . import fixtures
from .support import RUN_DATE, PipelineTestCase


def _checkpoints(listings, prefilter_urls=None, dedupe_listings=None,
                 matches=None, scored=None):
    """A full set of checkpoint dicts for build_rows, defaulting to "every
    listing sailed straight through" so a test only has to override the
    one stage it cares about."""
    prefilter_urls = ([l["url"] for l in listings] if prefilter_urls is None
                      else prefilter_urls)
    dedupe_listings = listings if dedupe_listings is None else dedupe_listings
    return {
        "normalize": {"listings": listings},
        "prefilter": {"listings": [l for l in listings if l["url"] in prefilter_urls]},
        "dedupe": {"listings": dedupe_listings},
        "classify": {"matches": matches or []},
        "score": {"scored": scored or []},
    }


class BuildRowsTest(PipelineTestCase):
    def test_no_first_tpm_listings_returns_no_rows(self):
        normalize_cp = {"listings": [fixtures.listing("https://x/1")]}  # fractional
        rows, stats = corpus.build_rows(
            RUN_DATE, normalize_cp, {"listings": []}, {"listings": []},
            {"matches": []}, {"scored": []})
        self.assertEqual(rows, [])
        self.assertEqual(stats["classify_verdict_gaps"], 0)

    def test_maps_the_full_field_set_for_a_clean_match(self):
        listing = fixtures.ats_listing("https://x/1", company="Acme Robotics")
        cps = _checkpoints(
            [listing],
            matches=[fixtures.match("https://x/1", role_category="first_tpm")],
            scored=[fixtures.scored("https://x/1", 75, role_category="first_tpm")],
        )
        rows, stats = corpus.build_rows(
            RUN_DATE, cps["normalize"], cps["prefilter"], cps["dedupe"],
            cps["classify"], cps["score"], digest_cp={"committed": True, "pushed": True})
        self.assertEqual(len(rows), 1)
        row = rows[0]

        self.assertEqual(row["run_date"], RUN_DATE)
        self.assertEqual(row["id"], 0)
        self.assertEqual(row["url"], "https://x/1")
        self.assertEqual(row["company"], "Acme Robotics")
        self.assertEqual(row["location_text"], "Remote (US)")
        self.assertEqual(row["location_country_code"], "US")
        self.assertEqual(row["headcount"], 85)
        self.assertEqual(row["description"], listing["match_text"])
        self.assertEqual(row["source"], "greenhouse:acmerobotics")
        self.assertEqual(row["ats"], "greenhouse")
        self.assertTrue(row["reached_dedupe"])
        self.assertTrue(row["reached_classify"])
        self.assertEqual(row["classify_verdict"], "match")
        self.assertEqual(row["classify_role_category"], "first_tpm")
        self.assertEqual(row["fit_score"], 75)
        self.assertTrue(row["cleared_score_floor"])
        self.assertTrue(row["published"])
        self.assertFalse(row["prefilter_recomputed"])
        self.assertEqual(stats["classify_verdict_gaps"], 0)

    def test_description_comes_from_normalize_match_text_not_snippet(self):
        """prefilter strips match_text before dedupe ever sees the listing -
        the corpus must read it from normalize, the one checkpoint that
        still has it."""
        listing = fixtures.ats_listing(
            "https://x/1", snippet="short snippet",
            match_text="the full stitched-together description")
        cps = _checkpoints([listing])
        rows, _ = corpus.build_rows(
            RUN_DATE, cps["normalize"], cps["prefilter"], cps["dedupe"],
            cps["classify"], cps["score"])
        self.assertEqual(rows[0]["description"], "the full stitched-together description")
        self.assertEqual(rows[0]["description_source"], "match_text")

    def test_description_falls_back_to_snippet_on_pre_d223e72_runs(self):
        """match_text itself didn't exist before d223e72 (2026-09-03) - a
        listing from one of those runs has no full-description field in
        normalize.json at all, not merely one stripped downstream. Losing
        the text silently (an empty description) would be worse than a
        clearly-flagged fallback to the weaker snippet."""
        listing = fixtures.ats_listing("https://x/1", snippet="400-char head only")
        del listing["match_text"]
        cps = _checkpoints([listing])
        rows, _ = corpus.build_rows(
            RUN_DATE, cps["normalize"], cps["prefilter"], cps["dedupe"],
            cps["classify"], cps["score"])
        self.assertEqual(rows[0]["description"], "400-char head only")
        self.assertEqual(rows[0]["description_source"], "snippet")

    def test_id_is_null_when_the_listing_never_reached_dedupe(self):
        listing = fixtures.ats_listing("https://x/1")
        cps = _checkpoints([listing], prefilter_urls=[], dedupe_listings=[])
        rows, _ = corpus.build_rows(
            RUN_DATE, cps["normalize"], cps["prefilter"], cps["dedupe"],
            cps["classify"], cps["score"])
        row = rows[0]
        self.assertIsNone(row["id"])
        self.assertFalse(row["reached_dedupe"])
        self.assertFalse(row["reached_classify"])
        self.assertIsNone(row["classify_verdict"])

    def test_id_matches_the_global_dedupe_index_spanning_both_lanes(self):
        """classify assigns ids by enumerate()-ing the FULL deduped list
        (both lanes interleaved), not a first_tpm-only index - the corpus id
        has to match that numbering or it can't be joined against a real
        classify_chunks file."""
        fractional = fixtures.listing("https://frac/1")
        ft = fixtures.ats_listing("https://x/1")
        dedupe_listings = [fractional, ft]  # ft's global id is 1, not 0
        cps = _checkpoints([ft], dedupe_listings=dedupe_listings,
                          prefilter_urls=["https://x/1"])
        rows, _ = corpus.build_rows(
            RUN_DATE, cps["normalize"], cps["prefilter"], cps["dedupe"],
            cps["classify"], cps["score"])
        self.assertEqual(rows[0]["id"], 1)

    def test_reached_dedupe_is_the_historical_decision_prefilter_passes_is_not(self):
        """The exact disagreement d223e72 exists to explain: a listing
        prefilter's real, historical run let through (reached_dedupe=True)
        can recompute as a non-pass under current code, and vice versa -
        these two fields are allowed, expected, to disagree."""
        # Foundation term only, no role term - fails evaluate() under CURRENT
        # code (see prefilter.EVIDENCE_TERM / near_miss), but we assert the
        # historical checkpoint said it passed anyway (the pre-fix behavior).
        listing = fixtures.ats_listing(
            "https://x/1", title="Operations Coordinator",
            match_text="This is our first hire on the operations team.")
        cps = _checkpoints([listing], prefilter_urls=["https://x/1"])
        rows, _ = corpus.build_rows(
            RUN_DATE, cps["normalize"], cps["prefilter"], cps["dedupe"],
            cps["classify"], cps["score"])
        row = rows[0]
        self.assertTrue(row["reached_dedupe"])       # historical: passed
        self.assertFalse(row["prefilter_passes"])    # recomputed: does not

    def test_classify_verdict_no_match_comes_from_a_chunk_file(self):
        listing = fixtures.ats_listing("https://x/1")
        cps = _checkpoints([listing])
        paths.atomic_write_json(
            paths.classify_chunk_path(RUN_DATE, 0),
            {"ids": [0], "verdicts": [fixtures.verdict(0, "no_match")]})
        rows, stats = corpus.build_rows(
            RUN_DATE, cps["normalize"], cps["prefilter"], cps["dedupe"],
            cps["classify"], cps["score"])
        self.assertEqual(rows[0]["classify_verdict"], "no_match")
        self.assertIsNone(rows[0]["classify_role_category"])
        self.assertEqual(stats["classify_verdict_gaps"], 0)

    def test_classify_verdict_falls_back_to_unclassified_and_counts_the_gap(self):
        """An id that reached classify but has no chunk file and isn't a
        match - its chunk failed every retry and was never checkpointed
        (classify.run's failed_chunk_indices path). Must not be silently
        dropped."""
        listing = fixtures.ats_listing("https://x/1")
        cps = _checkpoints([listing])  # no chunk file written at all
        rows, stats = corpus.build_rows(
            RUN_DATE, cps["normalize"], cps["prefilter"], cps["dedupe"],
            cps["classify"], cps["score"])
        self.assertEqual(rows[0]["classify_verdict"], "unclassified")
        self.assertEqual(stats["classify_verdict_gaps"], 1)

    def test_classify_verdict_match_takes_precedence_over_chunk_file(self):
        listing = fixtures.ats_listing("https://x/1")
        cps = _checkpoints(
            [listing], matches=[fixtures.match("https://x/1", role_category="first_tpm")])
        paths.atomic_write_json(
            paths.classify_chunk_path(RUN_DATE, 0),
            {"ids": [0], "verdicts": [fixtures.verdict(0, "match", role_category="first_tpm")]})
        rows, stats = corpus.build_rows(
            RUN_DATE, cps["normalize"], cps["prefilter"], cps["dedupe"],
            cps["classify"], cps["score"])
        self.assertEqual(rows[0]["classify_verdict"], "match")
        self.assertEqual(stats["classify_verdict_gaps"], 0)

    def test_cleared_score_floor_respects_digest_score_floor(self):
        from pipeline import digest as digest_module
        listing_hi = fixtures.ats_listing("https://x/hi")
        listing_lo = fixtures.ats_listing("https://x/lo")
        cps = _checkpoints(
            [listing_hi, listing_lo],
            matches=[fixtures.match("https://x/hi", role_category="first_tpm"),
                    fixtures.match("https://x/lo", role_category="first_tpm")],
            scored=[fixtures.scored("https://x/hi", digest_module.SCORE_FLOOR, role_category="first_tpm"),
                   fixtures.scored("https://x/lo", digest_module.SCORE_FLOOR - 1, role_category="first_tpm")],
        )
        rows, _ = corpus.build_rows(
            RUN_DATE, cps["normalize"], cps["prefilter"], cps["dedupe"],
            cps["classify"], cps["score"])
        by_url = {r["url"]: r for r in rows}
        self.assertTrue(by_url["https://x/hi"]["cleared_score_floor"])
        self.assertFalse(by_url["https://x/lo"]["cleared_score_floor"])

    def test_published_is_null_when_there_is_no_digest_checkpoint(self):
        listing = fixtures.ats_listing("https://x/1")
        cps = _checkpoints(
            [listing], matches=[fixtures.match("https://x/1", role_category="first_tpm")],
            scored=[fixtures.scored("https://x/1", 75, role_category="first_tpm")])
        rows, _ = corpus.build_rows(
            RUN_DATE, cps["normalize"], cps["prefilter"], cps["dedupe"],
            cps["classify"], cps["score"], digest_cp=None)
        self.assertIsNone(rows[0]["published"])

    def test_published_false_when_digest_checkpoint_exists_but_not_committed(self):
        """Rendered off `main` (or otherwise never committed) - digest.json
        exists (committed=False, publish_skipped set), so published is a
        real False, not null."""
        listing = fixtures.ats_listing("https://x/1")
        cps = _checkpoints(
            [listing], matches=[fixtures.match("https://x/1", role_category="first_tpm")],
            scored=[fixtures.scored("https://x/1", 75, role_category="first_tpm")])
        rows, _ = corpus.build_rows(
            RUN_DATE, cps["normalize"], cps["prefilter"], cps["dedupe"],
            cps["classify"], cps["score"],
            digest_cp={"committed": False, "pushed": False, "publish_skipped": "some-branch"})
        self.assertFalse(rows[0]["published"])

    def test_published_false_when_committed_but_below_the_floor(self):
        listing = fixtures.ats_listing("https://x/1")
        cps = _checkpoints(
            [listing], matches=[fixtures.match("https://x/1", role_category="first_tpm")],
            scored=[fixtures.scored("https://x/1", 5, role_category="first_tpm")])
        rows, _ = corpus.build_rows(
            RUN_DATE, cps["normalize"], cps["prefilter"], cps["dedupe"],
            cps["classify"], cps["score"], digest_cp={"committed": True, "pushed": True})
        self.assertFalse(rows[0]["published"])

    def test_portfolio_joined_from_companies_csv_by_company_name(self):
        paths.companies_csv_path().parent.mkdir(parents=True, exist_ok=True)
        paths.companies_csv_path().write_text(fixtures.COMPANIES_CSV_SAMPLE)
        listing = fixtures.ats_listing("https://x/1", company="Acme Robotics")
        cps = _checkpoints([listing])
        rows, _ = corpus.build_rows(
            RUN_DATE, cps["normalize"], cps["prefilter"], cps["dedupe"],
            cps["classify"], cps["score"])
        self.assertEqual(rows[0]["portfolio"], "lightspeed")

    def test_portfolio_is_null_when_companies_csv_is_missing(self):
        listing = fixtures.ats_listing("https://x/1", company="Acme Robotics")
        cps = _checkpoints([listing])
        self.assertFalse(paths.companies_csv_path().exists())
        rows, _ = corpus.build_rows(
            RUN_DATE, cps["normalize"], cps["prefilter"], cps["dedupe"],
            cps["classify"], cps["score"])
        self.assertIsNone(rows[0]["portfolio"])

    def test_portfolio_is_null_when_the_company_is_not_in_companies_csv(self):
        paths.companies_csv_path().parent.mkdir(parents=True, exist_ok=True)
        paths.companies_csv_path().write_text(fixtures.COMPANIES_CSV_SAMPLE)
        listing = fixtures.ats_listing("https://x/1", company="Not Listed Inc")
        cps = _checkpoints([listing])
        rows, _ = corpus.build_rows(
            RUN_DATE, cps["normalize"], cps["prefilter"], cps["dedupe"],
            cps["classify"], cps["score"])
        self.assertIsNone(rows[0]["portfolio"])


class WriteCorpusTest(PipelineTestCase):
    def test_writes_one_compact_json_object_per_line(self):
        rows = [{"url": "https://x/1", "n": 1}, {"url": "https://x/2", "n": 2}]
        path = corpus.write_corpus(RUN_DATE, rows)
        lines = path.read_text().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0]), rows[0])
        self.assertEqual(json.loads(lines[1]), rows[1])

    def test_rewriting_a_date_replaces_rather_than_appends(self):
        corpus.write_corpus(RUN_DATE, [{"url": "https://x/1"}])
        corpus.write_corpus(RUN_DATE, [{"url": "https://x/2"}])
        lines = paths.corpus_path(RUN_DATE).read_text().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["url"], "https://x/2")

    def test_no_tmp_file_left_behind(self):
        corpus.write_corpus(RUN_DATE, [{"url": "https://x/1"}])
        tmp = paths.corpus_path(RUN_DATE).with_suffix(".jsonl.tmp")
        self.assertFalse(tmp.exists())


class RunTest(PipelineTestCase):
    def test_run_writes_a_file_and_published_is_always_null(self):
        """run() is the live call site, wired in after score and before
        digest - digest_cp can't exist yet at that point in a real run, so
        every row it writes has published=None by construction."""
        listing = fixtures.ats_listing("https://x/1")
        cps = _checkpoints(
            [listing], matches=[fixtures.match("https://x/1", role_category="first_tpm")],
            scored=[fixtures.scored("https://x/1", 75, role_category="first_tpm")])
        result = corpus.run(RUN_DATE, cps["normalize"], cps["prefilter"], cps["dedupe"],
                            cps["classify"], cps["score"])
        self.assertEqual(result["count"], 1)
        self.assertTrue(paths.corpus_path(RUN_DATE).exists())
        rows = [json.loads(l) for l in paths.corpus_path(RUN_DATE).read_text().splitlines()]
        self.assertIsNone(rows[0]["published"])
        self.assertFalse(rows[0]["prefilter_recomputed"])


class BackfillTest(PipelineTestCase):
    def _write_checkpoint(self, run_date, stage, data):
        paths.atomic_write_json(paths.checkpoint_path(run_date, stage), data)

    def test_skips_a_date_with_no_normalize_checkpoint(self):
        (paths.PROJECT_DIR / "data" / "runs" / "2026-08-01").mkdir(parents=True)
        results = corpus.backfill()
        self.assertEqual(results, [{
            "run_date": "2026-08-01", "status": "skipped",
            "reason": "no normalize checkpoint"}])
        self.assertFalse(paths.corpus_path("2026-08-01").exists())

    def test_writes_an_empty_corpus_for_a_date_with_no_first_tpm_listings(self):
        self._write_checkpoint("2026-08-01", "normalize",
                               {"listings": [fixtures.listing("https://x/1")]})
        results = corpus.backfill()
        self.assertEqual(results, [{
            "run_date": "2026-08-01", "status": "ok", "count": 0,
            "classify_verdict_gaps": 0}])
        self.assertEqual(paths.corpus_path("2026-08-01").read_text(), "")

    def test_skips_a_date_with_first_tpm_listings_but_a_missing_downstream_checkpoint(self):
        listing = fixtures.ats_listing("https://x/1")
        self._write_checkpoint("2026-08-01", "normalize", {"listings": [listing]})
        # No prefilter/dedupe/classify/score checkpoints written.
        results = corpus.backfill()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "skipped")
        self.assertIn("prefilter", results[0]["reason"])
        self.assertFalse(paths.corpus_path("2026-08-01").exists())

    def test_one_bad_date_does_not_abort_the_rest(self):
        (paths.PROJECT_DIR / "data" / "runs" / "2026-08-01").mkdir(parents=True)  # bad: no normalize
        listing = fixtures.ats_listing("https://x/1")
        for stage, data in [
            ("normalize", {"listings": [listing]}),
            ("prefilter", {"listings": [listing]}),
            ("dedupe", {"listings": [listing]}),
            ("classify", {"matches": []}),
            ("score", {"scored": []}),
        ]:
            self._write_checkpoint("2026-08-02", stage, data)

        results = corpus.backfill()
        statuses = {r["run_date"]: r["status"] for r in results}
        self.assertEqual(statuses["2026-08-01"], "skipped")
        self.assertEqual(statuses["2026-08-02"], "ok")
        self.assertTrue(paths.corpus_path("2026-08-02").exists())
        self.assertFalse(paths.corpus_path("2026-08-01").exists())

    def test_backfilled_rows_are_marked_recomputed(self):
        listing = fixtures.ats_listing("https://x/1")
        for stage, data in [
            ("normalize", {"listings": [listing]}),
            ("prefilter", {"listings": [listing]}),
            ("dedupe", {"listings": [listing]}),
            ("classify", {"matches": []}),
            ("score", {"scored": []}),
        ]:
            self._write_checkpoint("2026-08-02", stage, data)

        corpus.backfill()
        rows = [json.loads(l) for l in paths.corpus_path("2026-08-02").read_text().splitlines()]
        self.assertTrue(rows[0]["prefilter_recomputed"])

    def test_backfill_picks_up_an_existing_digest_checkpoint(self):
        listing = fixtures.ats_listing("https://x/1")
        for stage, data in [
            ("normalize", {"listings": [listing]}),
            ("prefilter", {"listings": [listing]}),
            ("dedupe", {"listings": [listing]}),
            ("classify", {"matches": [fixtures.match("https://x/1", role_category="first_tpm")]}),
            ("score", {"scored": [fixtures.scored("https://x/1", 90, role_category="first_tpm")]}),
            ("digest", {"committed": True, "pushed": True}),
        ]:
            self._write_checkpoint("2026-08-02", stage, data)

        corpus.backfill()
        rows = [json.loads(l) for l in paths.corpus_path("2026-08-02").read_text().splitlines()]
        self.assertTrue(rows[0]["published"])

    def test_backfill_is_idempotent_across_repeated_runs(self):
        listing = fixtures.ats_listing("https://x/1")
        for stage, data in [
            ("normalize", {"listings": [listing]}),
            ("prefilter", {"listings": [listing]}),
            ("dedupe", {"listings": [listing]}),
            ("classify", {"matches": []}),
            ("score", {"scored": []}),
        ]:
            self._write_checkpoint("2026-08-02", stage, data)

        corpus.backfill()
        corpus.backfill()
        lines = paths.corpus_path("2026-08-02").read_text().splitlines()
        self.assertEqual(len(lines), 1)


if __name__ == "__main__":
    unittest.main()
