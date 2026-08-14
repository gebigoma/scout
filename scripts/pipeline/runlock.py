"""Single-run guard.

launchd won't start a second copy of a job that's still running, so the
scheduled run can't race itself. A manual `python3 scripts/run_pipeline.py`
started while the scheduled one is mid-flight can: both write the same
data/runs/<date>/ checkpoints, and both reach digest's git add/commit/push,
where the loser dies on index.lock with a message that has nothing to do
with the actual problem.

flock rather than a pidfile: the kernel drops the lock when the process
exits, however it exits. A laptop that sleeps mid-run or a job launchd
kills leaves nothing stale behind to clean up by hand - which is exactly
the failure a pidfile would introduce on the machine this runs on.
"""
import fcntl
from contextlib import contextmanager

from . import paths


class AlreadyRunning(RuntimeError):
    """Raised when another run already holds the lock."""


def lock_path():
    # logs/ rather than data/: logs/ is gitignored wholesale, so the lock
    # file can never show up as untracked noise in `git status` (or trip
    # the session-end hygiene check).
    return paths.logs_dir() / "run.lock"


@contextmanager
def single_run():
    """Hold an exclusive lock for the duration of the block, or raise
    AlreadyRunning immediately if another run holds it."""
    path = lock_path()
    # Append mode, not write: opening "w" would truncate the lock file out
    # from under a run that already holds it. Nothing reads the contents,
    # but clobbering another process's file to take a lock we don't have is
    # the kind of thing that's fine until it isn't.
    handle = path.open("a")
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise AlreadyRunning(
                f"another scout run holds {path}. Wait for it to finish, or "
                f"check for a stuck process before starting a second one."
            )
        yield
    finally:
        # Closing our descriptor releases the lock. Other processes' locks
        # live on their own descriptors and are unaffected.
        handle.close()
