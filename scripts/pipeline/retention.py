"""Prune old per-run scratch output.

Stage checkpoints under data/runs/<date>/ and raw source payloads under
data/raw/<date>.json are operational scratch - the deliverables are
matches/ and data/seen.json, both of which are committed and untouched
here. Nothing pruned the scratch, so a single first-TPM run writing ~27MB
of ATS payloads grew the working copy without bound.

Retention is counted in runs, not days, on purpose: this pipeline runs on
a laptop that can miss weeks at a time (lid closed, machine off). An
age-in-days rule would delete the last few real runs for having aged out
while nothing ran, which is precisely when you still want them.
"""
import shutil
from datetime import date

from . import paths

# Eight runs is roughly two months of weekly history - enough to debug a
# regression against the run before it, without keeping a year of payloads.
RETAIN_RUNS = 8


def _is_run_date(name: str) -> bool:
    """Whether an entry name is a run date this module owns. Anything that
    isn't a plain YYYY-MM-DD is left alone: the sweep should never delete a
    file it doesn't recognise, whatever put it there."""
    try:
        return date.fromisoformat(name).isoformat() == name
    except ValueError:
        return False


def _dated_entries(parent, suffix: str = "") -> list:
    """(date, path) for every entry in `parent` named for a run date,
    newest first. Missing parent means nothing to prune, not an error."""
    if not parent.is_dir():
        return []
    entries = []
    for child in parent.iterdir():
        if suffix:
            if not child.name.endswith(suffix):
                continue
            name = child.name[:-len(suffix)]
        else:
            name = child.name
        if _is_run_date(name):
            entries.append((name, child))
    return sorted(entries, reverse=True)


def _prune(entries: list, keep_date: str, retain: int) -> list:
    """Delete all but the `retain` newest, never touching `keep_date`."""
    deleted = []
    for run_date, path in entries[retain:]:
        # The in-flight run is kept regardless of how far down the sorted
        # list it falls - --force against an old date must not delete the
        # checkpoints it is in the middle of writing.
        if run_date == keep_date:
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        deleted.append(run_date)
    return deleted


def sweep(run_date: str, retain: int = RETAIN_RUNS) -> dict:
    """Prune old run dirs and raw payloads. Returns what was deleted."""
    runs = _prune(_dated_entries(paths.PROJECT_DIR / "data" / "runs"),
                  run_date, retain)
    raw = _prune(_dated_entries(paths.PROJECT_DIR / "data" / "raw", ".json"),
                 run_date, retain)
    return {"runs": runs, "raw": raw}
