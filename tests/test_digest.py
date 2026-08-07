import json
import unittest
from unittest import mock

from pipeline import digest, lanes, manifest, paths

from . import fixtures
from .support import RUN_DATE, PipelineTestCase, git, init_repo_with_remote


class RenderMarkdownTest(unittest.TestCase):
    def test_lists_matches_under_their_role_category(self):
        md = digest._render_markdown(RUN_DATE, 120, [
            fixtures.scored("u1", 90, "senior_tpm", title="Fractional TPM"),
            fixtures.scored("u2", 80, "agentic_ai_engineer", title="AI Agent Engineer"),
        ], [])
        tpm_section = md.split("### Agentic AI Engineer")[0]
        self.assertIn("Fractional TPM", tpm_section)
        self.assertNotIn("AI Agent Engineer", tpm_section)

    def test_matches_are_ordered_by_fit_score_descending(self):
        md = digest._render_markdown(RUN_DATE, 10, [
            fixtures.scored("u1", 45, title="Weakest"),
            fixtures.scored("u2", 92, title="Strongest"),
            fixtures.scored("u3", 70, title="Middle"),
        ], [])
        order = [md.index(t) for t in ("Strongest", "Middle", "Weakest")]
        self.assertEqual(order, sorted(order))

    def test_each_match_shows_score_url_and_rationale(self):
        md = digest._render_markdown(RUN_DATE, 10, [fixtures.scored("https://x/1", 88)], [])
        self.assertIn("(fit: 88/100)", md)
        self.assertIn("https://x/1", md)
        self.assertIn("Scored 88", md)

    def test_empty_category_says_so_explicitly(self):
        """A silent missing section reads as a rendering bug; "No matches this
        week" is a claim the pipeline is making on purpose."""
        md = digest._render_markdown(RUN_DATE, 10, [fixtures.scored("u1", 90)], [])
        self.assertEqual(md.count("No matches this week."), 1)

    def test_both_categories_render_when_there_are_no_matches_at_all(self):
        md = digest._render_markdown(RUN_DATE, 10, [], [], active_lanes=[lanes.FRACTIONAL])
        for label in digest.LANES[lanes.FRACTIONAL]["categories"].values():
            self.assertIn(f"### {label}", md)
        self.assertEqual(md.count("No matches this week."), 2)

    def test_a_lane_not_in_active_lanes_renders_no_heading_at_all(self):
        md = digest._render_markdown(RUN_DATE, 10, [], [], active_lanes=[lanes.FRACTIONAL])
        self.assertNotIn(digest.LANES[lanes.FIRST_TPM]["label"], md)

    def test_an_active_lane_with_no_matches_still_says_so(self):
        md = digest._render_markdown(RUN_DATE, 10, [], [], active_lanes=[lanes.FIRST_TPM])
        self.assertIn(f"## {digest.LANES[lanes.FIRST_TPM]['label']}", md)
        self.assertIn("No matches this week.", md)

    def test_a_first_tpm_match_renders_under_its_lane_and_category_heading(self):
        md = digest._render_markdown(RUN_DATE, 10, [
            fixtures.scored("u1", 90, "first_tpm", title="First TPM Hire"),
        ], [], active_lanes=[lanes.FIRST_TPM])
        self.assertIn(f"## {digest.LANES[lanes.FIRST_TPM]['label']}", md)
        self.assertIn("### First Technical Program Manager", md)
        self.assertIn("First TPM Hire", md)

    def test_both_lanes_render_their_own_headings_when_both_are_active(self):
        md = digest._render_markdown(RUN_DATE, 10, [], [],
                                     active_lanes=[lanes.FRACTIONAL, lanes.FIRST_TPM])
        self.assertIn(f"## {digest.LANES[lanes.FRACTIONAL]['label']}", md)
        self.assertIn(f"## {digest.LANES[lanes.FIRST_TPM]['label']}", md)

    def test_header_reports_the_candidate_count_reviewed(self):
        md = digest._render_markdown(RUN_DATE, 137, [], [])
        self.assertIn(f"# Matches — {RUN_DATE}", md)
        self.assertIn("137 candidate listings reviewed", md)

    def test_header_names_only_the_sources_of_lanes_that_actually_ran(self):
        """A first-TPM-only run must not claim RemoteOK/WWR/HN as its sources -
        those belong to the fractional lane and weren't fetched this run."""
        md = digest._render_markdown(RUN_DATE, 0, [], [], active_lanes=[lanes.FIRST_TPM])
        self.assertNotIn("RemoteOK", md)
        self.assertNotIn("ROLE_CRITERIA.md", md)
        self.assertIn("VC-portfolio companies", md)
        self.assertIn("ROLE_CRITERIA_FIRST_TPM.md", md)

    def test_header_names_both_lanes_sources_when_both_are_active(self):
        md = digest._render_markdown(RUN_DATE, 0, [], [],
                                     active_lanes=[lanes.FRACTIONAL, lanes.FIRST_TPM])
        self.assertIn("RemoteOK", md)
        self.assertIn("VC-portfolio companies", md)
        self.assertIn("ROLE_CRITERIA.md", md)
        self.assertIn("ROLE_CRITERIA_FIRST_TPM.md", md)

    def test_company_falls_back_to_the_source_when_unknown(self):
        md = digest._render_markdown(RUN_DATE, 10, [
            fixtures.scored("u1", 90, company="", source="hn:Who is hiring")], [])
        self.assertIn("hn:Who is hiring", md)

    def test_rejected_section_is_omitted_when_nothing_was_rejected(self):
        self.assertNotIn("Rejected on scoring",
                         digest._render_markdown(RUN_DATE, 10, [fixtures.scored("u1", 90)], []))

    def test_rejected_matches_are_published_separately_for_auditability(self):
        md = digest._render_markdown(RUN_DATE, 10,
                                     [fixtures.scored("u1", 90, title="Real match")],
                                     [fixtures.scored("u2", 12, title="Disagreement")])
        self.assertIn("## Rejected on scoring", md)
        self.assertIn("Disagreement", md)
        # The rejected section must come after the real matches, not mix in.
        self.assertGreater(md.index("Disagreement"), md.index("Real match"))

    def test_ends_with_exactly_one_trailing_newline(self):
        md = digest._render_markdown(RUN_DATE, 10, [fixtures.scored("u1", 90)], [])
        self.assertTrue(md.endswith("\n"))
        self.assertFalse(md.endswith("\n\n"))


class UpdateSeenTest(PipelineTestCase):
    def test_records_published_matches_against_the_run_date(self):
        digest._update_seen(RUN_DATE, [fixtures.scored("u1", 90)])
        self.assertEqual(self.read_json(paths.seen_path()),
                         {"seen": {"u1": RUN_DATE}})

    def test_preserves_the_original_first_seen_date(self):
        paths.seen_path().parent.mkdir(parents=True, exist_ok=True)
        paths.seen_path().write_text(json.dumps({"seen": {"u1": "2026-07-27"}}))
        digest._update_seen(RUN_DATE, [fixtures.scored("u1", 90)])
        self.assertEqual(self.read_json(paths.seen_path())["seen"]["u1"], "2026-07-27")

    def test_migrates_the_legacy_flat_list_in_place(self):
        paths.seen_path().parent.mkdir(parents=True, exist_ok=True)
        paths.seen_path().write_text(json.dumps({"matched_urls": ["old"]}))
        digest._update_seen(RUN_DATE, [fixtures.scored("new", 90)])
        seen = self.read_json(paths.seen_path())["seen"]
        self.assertEqual(seen, {"old": "", "new": RUN_DATE})

    def test_entries_are_sorted_for_a_readable_diff(self):
        digest._update_seen(RUN_DATE, [fixtures.scored("b", 90), fixtures.scored("a", 90)])
        self.assertEqual(list(self.read_json(paths.seen_path())["seen"]), ["a", "b"])


class UnpushedCommitCountTest(PipelineTestCase):
    def test_zero_when_up_to_date_with_upstream(self):
        init_repo_with_remote(self.project_dir)
        self.assertEqual(digest._unpushed_commit_count(), 0)

    def test_counts_local_commits_not_yet_pushed(self):
        init_repo_with_remote(self.project_dir)
        (self.project_dir / "a.txt").write_text("a")
        git("add", "a.txt", cwd=self.project_dir)
        git("commit", "-m", "a", cwd=self.project_dir)
        self.assertEqual(digest._unpushed_commit_count(), 1)

    def test_no_upstream_is_reported_as_nothing_to_push(self):
        git("init", "-b", "main", cwd=self.project_dir)
        git("config", "user.email", "test@example.com", cwd=self.project_dir)
        git("config", "user.name", "scout tests", cwd=self.project_dir)
        (self.project_dir / "a.txt").write_text("a")
        git("add", "a.txt", cwd=self.project_dir)
        git("commit", "-m", "a", cwd=self.project_dir)
        self.assertEqual(digest._unpushed_commit_count(), 0)


class DigestRunTest(PipelineTestCase):
    def setUp(self):
        super().setUp()
        self.remote = init_repo_with_remote(self.project_dir)

    def _run(self, scored, candidates=25):
        dedupe_cp = {"listings": [fixtures.listing(f"c{i}") for i in range(candidates)]}
        return digest.run(RUN_DATE, dedupe_cp, {"scored": scored})

    def test_publishes_commits_and_pushes_the_digest(self):
        checkpoint = self._run([fixtures.scored("https://x/1", 88)])
        self.assertEqual(checkpoint["matches"], 1)
        self.assertTrue(checkpoint["committed"])
        self.assertTrue(checkpoint["pushed"])
        self.assertIn("https://x/1", paths.matches_path(RUN_DATE).read_text())
        self.assertIn(f"Weekly matches: {RUN_DATE}",
                      git("log", "-1", "--pretty=%s", cwd=self.remote))

    def test_scores_below_the_floor_do_not_count_as_matches(self):
        checkpoint = self._run([
            fixtures.scored("u1", digest.SCORE_FLOOR),
            fixtures.scored("u2", digest.SCORE_FLOOR - 1),
        ])
        self.assertEqual((checkpoint["matches"], checkpoint["rejected"]), (1, 1))
        self.assertIn("## Rejected on scoring", paths.matches_path(RUN_DATE).read_text())

    def test_rejected_matches_stay_eligible_to_resurface(self):
        """Only published matches are recorded as seen - a listing the score
        stage threw out should be reconsidered if it is re-posted."""
        self._run([fixtures.scored("u_published", 90), fixtures.scored("u_rejected", 10)])
        self.assertEqual(list(self.read_json(paths.seen_path())["seen"]),
                         ["u_published"])

    def test_a_zero_match_week_still_publishes_a_digest(self):
        checkpoint = self._run([])
        self.assertEqual(checkpoint["matches"], 0)
        self.assertTrue(paths.matches_path(RUN_DATE).exists())
        self.assertTrue(checkpoint["committed"])

    def test_rerunning_an_unchanged_date_commits_nothing(self):
        self._run([fixtures.scored("u1", 88)])
        before = git("rev-parse", "HEAD", cwd=self.project_dir)
        checkpoint = self._run([fixtures.scored("u1", 88)])
        self.assertFalse(checkpoint["committed"])
        self.assertFalse(checkpoint["pushed"])
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.project_dir), before)

    def test_unrelated_staged_work_is_never_swept_into_the_commit(self):
        (self.project_dir / "notes.txt").write_text("in-progress work")
        git("add", "notes.txt", cwd=self.project_dir)
        self._run([fixtures.scored("u1", 88)])
        committed = git("show", "--name-only", "--pretty=", "HEAD", cwd=self.project_dir)
        self.assertNotIn("notes.txt", committed)
        self.assertIn(f"matches/{RUN_DATE}.md", committed)

    def test_a_stranded_commit_from_a_failed_push_is_pushed_on_the_next_run(self):
        """Deciding on the index alone would report success here and leave the
        commit stranded forever: the re-run finds nothing to stage.

        Set up the state a failed push leaves behind - a local commit with no
        counterpart on the remote - and check the next run pushes it."""
        (self.project_dir / "stranded.txt").write_text("x")
        git("add", "stranded.txt", cwd=self.project_dir)
        git("commit", "-m", "stranded", cwd=self.project_dir)
        self.assertEqual(digest._unpushed_commit_count(), 1)

        checkpoint = self._run([fixtures.scored("u1", 88)])
        self.assertTrue(checkpoint["pushed"])
        self.assertEqual(digest._unpushed_commit_count(), 0)
        self.assertIn("stranded", git("log", "--pretty=%s", cwd=self.remote))

    def test_records_counts_in_the_manifest(self):
        self._run([fixtures.scored("u1", 88), fixtures.scored("u2", 5)], candidates=40)
        entry = manifest.load(RUN_DATE)["stages"]["digest"]
        self.assertEqual(entry["status"], "success")
        self.assertEqual(entry["matches"], 1)
        self.assertEqual(entry["classify_disagreements"], 1)

    def test_a_failure_marks_the_stage_failed_and_propagates(self):
        with mock.patch.object(digest, "_git", side_effect=RuntimeError("git exploded")):
            with self.assertRaises(RuntimeError):
                self._run([fixtures.scored("u1", 88)])
        m = manifest.load(RUN_DATE)
        self.assertEqual(m["stages"]["digest"]["status"], "failed")
        self.assertEqual(m["status"], "failed")
        self.assertFalse(paths.checkpoint_path(RUN_DATE, "digest").exists())


if __name__ == "__main__":
    unittest.main()
