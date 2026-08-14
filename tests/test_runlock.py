"""The lock is the only thing standing between a manual run and the
scheduled one writing the same checkpoints and racing each other to git."""
import multiprocessing
import unittest

from pipeline import runlock

from .support import PipelineTestCase


def _try_acquire(project_dir, result):
    """Attempt the lock in a separate process. flock is held per open file
    description, so a second attempt inside the *same* process would also
    fail - testing across processes is what actually models the bug."""
    from pipeline import paths, runlock as child_runlock
    paths.PROJECT_DIR = project_dir
    try:
        with child_runlock.single_run():
            result.value = 1  # acquired
    except child_runlock.AlreadyRunning:
        result.value = 2  # correctly refused


class RunLockTest(PipelineTestCase):
    def test_the_lock_is_acquired_when_free(self):
        with runlock.single_run():
            self.assertTrue(runlock.lock_path().exists())

    def test_a_second_run_is_refused_while_the_first_holds_it(self):
        ctx = multiprocessing.get_context("spawn")
        result = ctx.Value("i", 0)
        with runlock.single_run():
            child = ctx.Process(target=_try_acquire,
                                args=(self.project_dir, result))
            child.start()
            child.join(timeout=30)
        self.assertEqual(result.value, 2, "second run should have been refused")

    def test_the_lock_is_released_when_the_block_exits(self):
        with runlock.single_run():
            pass
        ctx = multiprocessing.get_context("spawn")
        result = ctx.Value("i", 0)
        child = ctx.Process(target=_try_acquire, args=(self.project_dir, result))
        child.start()
        child.join(timeout=30)
        self.assertEqual(result.value, 1, "lock should be free after the block")

    def test_the_lock_is_released_even_when_the_block_raises(self):
        """A crashed run must not wedge every future run."""
        with self.assertRaises(ValueError):
            with runlock.single_run():
                raise ValueError("stage blew up")
        with runlock.single_run():
            pass  # acquiring again is the assertion

    def test_the_lock_file_lives_outside_version_control(self):
        """logs/ is gitignored wholesale; data/ is not."""
        self.assertEqual(runlock.lock_path().parent.name, "logs")


if __name__ == "__main__":
    unittest.main()
