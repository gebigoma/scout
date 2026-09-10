"""Covers scripts/backup_private.sh - specifically the prune step, since it's
the only part of the script that can destroy data, and the one path where a
verification failure has to actually block deletion rather than just alert.

Shell has no mocking, so this runs the real script as a subprocess against a
throwaway fixture tree, the same approach test_githooks.py uses for the real
git hooks: copy the real script (and, where a case needs the pipeline.runlock
/ alert / logging_setup bridge, the real pipeline/ package) into an isolated
temp dir, and assert on what's left on disk afterwards. No network: NTFY_TOPIC
is never set, so alert.py's best-effort POST is always a silent no-op here.
"""
import json
import multiprocessing
import shutil
import subprocess
import unittest
from pathlib import Path

from .support import PipelineTestCase

REAL_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REAL_ROOT / "scripts" / "backup_private.sh"


def _series(count, start=2, group="2020-01-"):
    """`count` date-shaped folder names, zero-padded and none a monthly
    anchor (day "01") - starting at 02 rather than 01 guarantees that.
    The prune step matches these purely by regex + lexicographic sort, not
    real calendar validity, so "days" past 31 (e.g. "2020-01-35") are fine
    and deliberately used here to keep the fixture calendar-arithmetic-free."""
    return [f"{group}{i:02d}" for i in range(start, start + count)]


def _read_last_jsonl(path) -> dict:
    lines = [l for l in Path(path).read_text().splitlines() if l.strip()]
    return json.loads(lines[-1])


def _hold_lock(project_dir, ready, release):
    """Module-level so the spawn-context Process below can pickle it -
    mirrors tests/test_runlock.py's _try_acquire."""
    from pipeline import paths, runlock
    paths.PROJECT_DIR = project_dir
    with runlock.single_run():
        ready.set()
        release.wait(timeout=30)


class BackupPrivateTestCase(PipelineTestCase):
    """self.project_dir doubles as both the fake repo root (source of the
    private files) and the fake iCloud root (SCOUT_BACKUP_ROOT), in separate
    subdirectories, so nothing here ever touches the real repo or the real
    iCloud Drive."""

    def setUp(self):
        super().setUp()
        self.fixture_root = self.project_dir / "repo"
        self.icloud_root = self.project_dir / "icloud"
        (self.fixture_root / "scripts").mkdir(parents=True)
        (self.fixture_root / "logs").mkdir(parents=True)
        self.icloud_root.mkdir(parents=True)
        shutil.copytree(REAL_ROOT / "scripts" / "pipeline",
                        self.fixture_root / "scripts" / "pipeline")
        shutil.copy(SCRIPT, self.fixture_root / "scripts" / "backup_private.sh")
        (self.fixture_root / "scripts" / "backup_private.sh").chmod(0o755)

    def run_script(self, *args, root=None):
        env = {"HOME": str(self.project_dir), "PATH": "/usr/bin:/bin"}
        if root is not None:
            env["SCOUT_BACKUP_ROOT"] = str(root)
        return subprocess.run(
            [str(self.fixture_root / "scripts" / "backup_private.sh"), *args],
            cwd=str(self.fixture_root), env=env,
            capture_output=True, text=True,
        )

    def _make_dated_dirs(self, root: Path, names) -> None:
        for name in names:
            (root / name).mkdir(parents=True, exist_ok=True)

    def _remaining(self, root: Path) -> list:
        return sorted(p.name for p in root.iterdir())


class PruneTest(BackupPrivateTestCase):
    def test_keeps_exactly_30_non_monthly_folders(self):
        names = _series(40)
        self._make_dated_dirs(self.icloud_root, names)

        proc = self.run_script("--prune-dir", str(self.icloud_root))
        self.assertEqual(proc.returncode, 0, proc.stderr)

        remaining = self._remaining(self.icloud_root)
        self.assertEqual(len(remaining), 30)
        self.assertEqual(remaining, sorted(names)[-30:])

    def test_a_monthly_anchor_is_never_pruned_even_when_oldest(self):
        anchor = "2015-01-01"  # far older than everything else, and a "-01"
        names = _series(40)
        self._make_dated_dirs(self.icloud_root, names + [anchor])

        proc = self.run_script("--prune-dir", str(self.icloud_root))
        self.assertEqual(proc.returncode, 0, proc.stderr)

        remaining = self._remaining(self.icloud_root)
        self.assertIn(anchor, remaining)
        # The anchor doesn't occupy one of the 30 kept slots either.
        self.assertEqual(len(remaining), 31)

    def test_refuses_to_run_with_an_empty_root(self):
        proc = self.run_script("--prune-dir", "")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("empty backup root", proc.stderr)

    def test_ignores_entries_that_are_not_dated_directories(self):
        names = _series(40)
        self._make_dated_dirs(self.icloud_root, names)
        (self.icloud_root / "notes").mkdir()
        (self.icloud_root / "2020-13-45").touch()  # matches the regex, but a file

        proc = self.run_script("--prune-dir", str(self.icloud_root))
        self.assertEqual(proc.returncode, 0, proc.stderr)

        remaining = self._remaining(self.icloud_root)
        self.assertIn("notes", remaining)
        self.assertIn("2020-13-45", remaining)

    def test_dry_run_deletes_nothing(self):
        names = _series(40)
        self._make_dated_dirs(self.icloud_root, names)

        proc = self.run_script("--prune-dir", str(self.icloud_root), "--dry-run")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(self._remaining(self.icloud_root)), len(names))
        self.assertIn("would delete", proc.stderr)


class VerificationPassesOnFullRunTest(BackupPrivateTestCase):
    """Covers verification passing on a real, full run (copy always makes
    destination byte-identical to source, so a full run can't naturally
    produce a verification *failure* without external corruption between
    copy and verify - those branches are covered directly, in isolation, by
    VerificationEmptySourceTest via --verify-only instead)."""

    def _seed_source(self):
        data = self.fixture_root / "data"
        data.mkdir(exist_ok=True)
        (data / "companies.csv").write_text("name,ats,token,headcount,source\na,b,c,,d\n")
        (data / "known_good.csv").write_text("role,company,url,missed_at\nx,y,z,prefilter\n")

    def test_an_empty_source_csv_passes_verification_and_prunes(self):
        """An empty data/companies.csv is a legitimate state (see CLAUDE.md
        on it being absent entirely on a fresh clone) - the CSV-specific
        empty-file check must not treat "empty" as "corrupt"."""
        self._seed_source()
        (self.fixture_root / "data" / "companies.csv").write_text("")

        old_names = _series(40)
        self._make_dated_dirs(self.icloud_root, old_names)

        proc = self.run_script(root=self.icloud_root)

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("verification failed", proc.stdout + proc.stderr)
        remaining = self._remaining(self.icloud_root)
        self.assertLess(len(remaining), len(old_names) + 1)

    def test_a_valid_source_passes_verification_and_prunes(self):
        self._seed_source()
        old_names = _series(40)
        self._make_dated_dirs(self.icloud_root, old_names)

        proc = self.run_script(root=self.icloud_root)

        self.assertEqual(proc.returncode, 0, proc.stderr)
        remaining = self._remaining(self.icloud_root)
        self.assertLess(len(remaining), len(old_names) + 1)


class VerificationEmptySourceTest(BackupPrivateTestCase):
    """Verification is source-relative: an empty source producing an empty
    destination is an expected pass, not a failure. Regression coverage for
    the 2026-09-10 false alarm, where a genuinely empty notes/ made every
    single run fail verification and skip pruning."""

    def _verify(self, src_paths: dict, dst_paths: dict):
        src = self.project_dir / "vsrc"
        dst = self.project_dir / "vdst"
        for rel, content in src_paths.items():
            path = src / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            if content is None:
                path.mkdir(exist_ok=True)
            else:
                path.write_text(content)
        for rel, content in dst_paths.items():
            path = dst / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            if content is None:
                path.mkdir(exist_ok=True)
            else:
                path.write_text(content)
        return self.run_script("--verify-only", str(src), str(dst))

    def test_empty_source_and_empty_destination_passes(self):
        proc = self._verify({"notes": None}, {"notes": None})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("empty_ok:notes", proc.stdout)

    def test_non_empty_source_and_empty_destination_fails(self):
        (self.project_dir / "vsrc" / "notes").mkdir(parents=True)
        proc = self._verify({"notes/idea.md": "note"}, {"notes": None})
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("notes landed empty", proc.stdout + proc.stderr)

    def test_destination_path_missing_entirely_fails(self):
        proc = self._verify({"notes/idea.md": "note"}, {})
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("did not land in the backup", proc.stdout + proc.stderr)

    def test_an_empty_source_still_allows_prune_to_run(self):
        """The bug this fixes: verification failing on a legitimately empty
        path blocked pruning on every run, forever. A pass with an empty
        source must not have that side effect."""
        data = self.fixture_root / "data"
        data.mkdir()
        (data / "companies.csv").write_text("name\na\n")
        (data / "known_good.csv").write_text("role\nx\n")
        (self.fixture_root / "signals").mkdir()  # genuinely empty
        (self.fixture_root / "notes").mkdir()  # genuinely empty

        old_names = _series(40)
        self._make_dated_dirs(self.icloud_root, old_names)

        proc = self.run_script(root=self.icloud_root)

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("verification failed", proc.stdout + proc.stderr)
        remaining = self._remaining(self.icloud_root)
        self.assertLess(len(remaining), len(old_names) + 1,
                        "prune should have run and removed the oldest backups")


class RunlockTest(BackupPrivateTestCase):
    def test_a_held_runlock_skips_the_backup_without_failing(self):
        ctx = multiprocessing.get_context("spawn")
        ready = ctx.Event()
        release = ctx.Event()
        holder = ctx.Process(target=_hold_lock,
                              args=(self.fixture_root, ready, release))
        holder.start()
        try:
            ready.wait(timeout=10)
            proc = self.run_script(root=self.icloud_root)
        finally:
            release.set()
            holder.join(timeout=10)

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("skipping", proc.stdout)
        self.assertEqual(self._remaining(self.icloud_root), [])


class CopyAndLogTest(BackupPrivateTestCase):
    def test_a_successful_run_copies_all_four_paths_and_logs_jsonl(self):
        data = self.fixture_root / "data"
        data.mkdir()
        (data / "companies.csv").write_text("name\na\n")
        (data / "known_good.csv").write_text("role\nx\n")
        (self.fixture_root / "signals").mkdir()
        (self.fixture_root / "signals" / "2026-09-09.md").write_text("# w")
        (self.fixture_root / "notes").mkdir()
        (self.fixture_root / "notes" / "idea.md").write_text("note")

        proc = self.run_script(root=self.icloud_root)
        self.assertEqual(proc.returncode, 0, proc.stderr)

        [dated] = list(self.icloud_root.iterdir())
        self.assertTrue((dated / "data" / "companies.csv").exists())
        self.assertTrue((dated / "data" / "known_good.csv").exists())
        self.assertTrue((dated / "signals" / "2026-09-09.md").exists())
        self.assertTrue((dated / "notes" / "idea.md").exists())

        [log_file] = list((self.fixture_root / "logs").glob("*.jsonl"))
        entry = _read_last_jsonl(log_file)
        self.assertEqual(entry["stage"], "backup")
        self.assertEqual(entry["outcome"], "ok")
        self.assertEqual(sorted(entry["paths"]),
                         sorted(["data/companies.csv", "data/known_good.csv",
                                 "signals", "notes"]))

    def test_a_missing_optional_path_is_skipped_not_a_failure(self):
        """A fresh clone has no data/companies.csv yet - see CLAUDE.md."""
        data = self.fixture_root / "data"
        data.mkdir()
        (data / "known_good.csv").write_text("role\nx\n")

        proc = self.run_script(root=self.icloud_root)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        [dated] = list(self.icloud_root.iterdir())
        self.assertFalse((dated / "data" / "companies.csv").exists())
        self.assertTrue((dated / "data" / "known_good.csv").exists())


class DryRunTest(BackupPrivateTestCase):
    """Regression coverage for --dry-run actually performing a real backup:
    it called do_copy() unconditionally and only threaded dry_run into the
    prune step, so a dry run wrote a real dated folder and only skipped
    deletion. Nothing in the original suite exercised the top-level
    --dry-run flag at all, which is how that shipped unnoticed."""

    def _seed_source(self):
        data = self.fixture_root / "data"
        data.mkdir()
        (data / "companies.csv").write_text("name\na\n")
        (data / "known_good.csv").write_text("role\nx\n")
        (self.fixture_root / "signals").mkdir()
        (self.fixture_root / "signals" / "2026-09-10.md").write_text("# w")
        (self.fixture_root / "notes").mkdir()
        (self.fixture_root / "notes" / "idea.md").write_text("note")

    def test_dry_run_creates_no_dated_folder_and_deletes_nothing(self):
        self._seed_source()
        old_names = _series(40)
        anchor = "2019-06-01"
        self._make_dated_dirs(self.icloud_root, old_names + [anchor])
        before = self._remaining(self.icloud_root)

        proc = self.run_script("--dry-run", root=self.icloud_root)

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._remaining(self.icloud_root), before,
                         "dry-run must not create today's folder or prune anything")
        # Nothing was logged either - a dry run performs no writes at all,
        # logging included.
        self.assertEqual(list((self.fixture_root / "logs").glob("*.jsonl")), [])

    def test_dry_run_reports_the_prune_plan_correctly(self):
        self._seed_source()
        old_names = _series(40)
        anchor = "2019-06-01"
        self._make_dated_dirs(self.icloud_root, old_names + [anchor])

        proc = self.run_script("--dry-run", root=self.icloud_root)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        output = proc.stdout + proc.stderr

        self.assertIn("DRY RUN", output)
        self.assertIn("would copy data/companies.csv", output)
        self.assertIn(f"would keep {self.icloud_root}/{anchor} (monthly anchor)", output)
        # The 30 most recent non-anchor folders are kept...
        for name in sorted(old_names)[-30:]:
            self.assertIn(f"would keep {self.icloud_root}/{name} (within most recent 30)",
                         output)
        # ...and the 10 oldest of the 40 are reported for deletion.
        for name in sorted(old_names)[:10]:
            self.assertIn(f"would delete {self.icloud_root}/{name}", output)

    def test_dry_run_reports_preflight_and_runlock_state(self):
        self._seed_source()
        proc = self.run_script("--dry-run", root=self.icloud_root)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        output = proc.stdout + proc.stderr
        self.assertIn("preflight ok", output)
        self.assertIn("runlock is free", output)

    def test_dry_run_still_reports_when_there_is_nothing_to_prune(self):
        """An empty backup root is a legitimate first-ever run - dry-run
        must say so, not print nothing."""
        self._seed_source()
        proc = self.run_script("--dry-run", root=self.icloud_root)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("DRY RUN", proc.stdout + proc.stderr)


class ArgumentHandlingTest(BackupPrivateTestCase):
    def test_an_unrecognized_flag_exits_non_zero(self):
        proc = self.run_script("--bogus")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("usage", proc.stderr)

    def test_a_stray_extra_argument_after_dry_run_exits_non_zero(self):
        """The exact failure mode this fix targets: a mistyped second flag
        must not be silently ignored and fall through to a real run."""
        proc = self.run_script("--dry-run", "--oops", root=self.icloud_root)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(self._remaining(self.icloud_root), [])

    def test_help_prints_usage_and_exits_zero(self):
        proc = self.run_script("--help")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("usage", proc.stdout)


if __name__ == "__main__":
    unittest.main()
