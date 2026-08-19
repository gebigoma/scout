"""The hooks and CI both shell out to hygiene.py, so a bug here either blocks
every commit or silently stops enforcing anything. Test the rules directly;
the git plumbing around them is exercised by the hooks themselves."""
import unittest

import hygiene
from pipeline import paths


def _reader(contents):
    """Stand in for 'read this path out of the index' / 'off disk'."""
    return lambda path: contents[path]


class FileSizeTest(unittest.TestCase):
    def test_a_small_file_is_fine(self):
        self.assertEqual(hygiene.check_file_sizes(["a.py"], _reader({"a.py": b"x" * 100})), [])

    def test_a_file_over_the_limit_is_reported(self):
        big = b"x" * (hygiene.MAX_FILE_BYTES + 1)
        problems = hygiene.check_file_sizes(["Claude.dmg"], _reader({"Claude.dmg": big}))
        self.assertEqual(len(problems), 1)
        self.assertIn("Claude.dmg", problems[0])

    def test_a_file_exactly_at_the_limit_is_allowed(self):
        at_limit = b"x" * hygiene.MAX_FILE_BYTES
        self.assertEqual(
            hygiene.check_file_sizes(["a.bin"], _reader({"a.bin": at_limit})), [])


class NeverCommitTest(unittest.TestCase):
    def test_generated_run_output_is_rejected(self):
        problems = hygiene.check_never_commit(["data/runs/2026-08-10/fetch.json"])
        self.assertEqual(len(problems), 1)

    def test_logs_are_rejected(self):
        self.assertEqual(len(hygiene.check_never_commit(["logs/2026-08-10.jsonl"])), 1)

    def test_a_nested_ds_store_is_rejected(self):
        self.assertEqual(len(hygiene.check_never_commit(["matches/.DS_Store"])), 1)

    def test_local_claude_settings_are_rejected(self):
        """settings.local.json holds machine-specific permissions."""
        self.assertEqual(len(hygiene.check_never_commit([".claude/settings.local.json"])), 1)

    def test_shared_claude_settings_are_allowed(self):
        """The committed settings.json is the point of the Stop hook."""
        self.assertEqual(hygiene.check_never_commit([".claude/settings.json"]), [])

    def test_published_matches_are_allowed(self):
        self.assertEqual(hygiene.check_never_commit(["matches/2026-08-10.md"]), [])

    def test_a_path_that_merely_starts_the_same_is_allowed(self):
        """'data/rawest.csv' is not under 'data/raw/'."""
        self.assertEqual(hygiene.check_never_commit(["data/rawest.csv"]), [])


class ConflictMarkerTest(unittest.TestCase):
    def test_clean_text_passes(self):
        self.assertEqual(
            hygiene.check_conflict_markers(["a.py"], _reader({"a.py": b"x = 1\n"})), [])

    def test_an_unresolved_marker_is_reported_with_its_line(self):
        blob = b"a\n<<<<<<< HEAD\nb\n=======\nc\n>>>>>>> other\n"
        problems = hygiene.check_conflict_markers(["a.py"], _reader({"a.py": blob}))
        self.assertEqual(len(problems), 1)
        self.assertIn("a.py:2", problems[0])

    def test_binary_files_are_skipped(self):
        """A PNG can contain those bytes without being a conflict."""
        blob = b"\x89PNG\0<<<<<<< HEAD\n"
        self.assertEqual(
            hygiene.check_conflict_markers(["a.png"], _reader({"a.png": blob})), [])

    def test_a_markdown_horizontal_rule_is_not_a_marker(self):
        """'=======' underlines a setext heading; it needs the marker length
        to be exactly seven and not part of a longer run."""
        blob = b"Heading\n=========\n"
        self.assertEqual(
            hygiene.check_conflict_markers(["a.md"], _reader({"a.md": blob})), [])


class BranchNameTest(unittest.TestCase):
    def test_main_is_exempt(self):
        self.assertEqual(hygiene.check_branch_name("main"), [])

    def test_a_conventional_branch_passes(self):
        self.assertEqual(hygiene.check_branch_name("fix/first-tpm-location"), [])

    def test_an_agent_generated_worktree_name_is_rejected(self):
        self.assertEqual(len(hygiene.check_branch_name("worktree-agent-abbbb31f")), 1)

    def test_a_bare_slug_without_a_type_prefix_is_rejected(self):
        self.assertEqual(len(hygiene.check_branch_name("some-fix")), 1)

    def test_an_unknown_type_prefix_is_rejected(self):
        self.assertEqual(len(hygiene.check_branch_name("wip/things")), 1)

    def test_a_detached_head_is_not_a_branch_name_problem(self):
        self.assertEqual(hygiene.check_branch_name(""), [])


class MainPushTest(unittest.TestCase):
    def test_a_digest_commit_may_go_straight_to_main(self):
        commits = [("Weekly matches: 2026-08-10",
                    ["matches/2026-08-10.md", "data/seen.json"])]
        self.assertEqual(hygiene.check_main_push(commits), [])

    def test_a_digest_commit_carrying_signals_is_blocked(self):
        """signals/ is private and digest no longer commits it. The 2026-08-17
        run failed on the other side of this: digest committed the signals file
        while the pattern only allowed data/signals*.md, so the push was
        rejected after the commit had already been made. The lesson was that
        this pattern and digest's commit list must agree - the direction they
        agree in changed when signals stopped being published."""
        commits = [("Weekly matches: 2026-08-17",
                    ["matches/2026-08-17.md", "data/seen.json",
                     "signals/2026-08-17.md"])]
        self.assertEqual(len(hygiene.check_main_push(commits)), 1)

    def test_the_pattern_matches_exactly_what_digest_commits(self):
        """Pins the coupling the 2026-08-17 outage came from, rather than the
        specific paths involved: anything digest.run puts in commit_paths has
        to be pushable to main, and anything it deliberately leaves out has to
        not be."""
        relative = lambda p: str(p.relative_to(paths.PROJECT_DIR))
        for published in (paths.matches_path("2026-08-17"), paths.seen_path()):
            self.assertRegex(relative(published), hygiene.DIGEST_PATH_PATTERN)
        self.assertNotRegex(relative(paths.signals_path("2026-08-17")),
                            hygiene.DIGEST_PATH_PATTERN)

    def test_the_private_company_files_are_never_committable(self):
        """Each names specific companies being watched or applied to, in a
        public repo. gitignore alone would let `git add -f` through."""
        private = ["signals/2026-08-17.md", "data/companies.csv",
                   "data/known_good.csv"]
        self.assertEqual(len(hygiene.check_never_commit(private)), len(private))

    def test_other_hand_maintained_data_is_still_committable(self):
        """The rule is about naming companies, not about living under data/ -
        seen.json is hand-adjacent published output and must stay allowed."""
        self.assertEqual(hygiene.check_never_commit(["data/seen.json"]), [])

    def test_ordinary_work_pushed_to_main_is_blocked(self):
        commits = [("Fix greenhouse posted date field", ["scripts/pipeline/normalize.py"])]
        self.assertEqual(len(hygiene.check_main_push(commits)), 1)

    def test_a_digest_subject_hiding_a_code_change_is_blocked(self):
        """The subject alone can't be the test - it's trivially spoofed by a
        commit that sweeps in unrelated staged work."""
        commits = [("Weekly matches: 2026-08-10",
                    ["matches/2026-08-10.md", "scripts/pipeline/digest.py"])]
        self.assertEqual(len(hygiene.check_main_push(commits)), 1)

    def test_every_bad_commit_in_the_range_is_listed(self):
        commits = [("a", ["x.py"]), ("b", ["y.py"])]
        self.assertEqual(len(hygiene.check_main_push(commits)), 2)

    def test_pushing_nothing_is_fine(self):
        self.assertEqual(hygiene.check_main_push([]), [])


if __name__ == "__main__":
    unittest.main()
