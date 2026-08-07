"""Incrementally-updated record of a pipeline run: per-stage status/timing/
errors plus overall run status. Written to disk after every change so a
mid-run crash still leaves an accurate partial manifest."""
import json
from datetime import datetime, timezone

from . import paths

STAGES = ["fetch", "fetch_ats", "normalize", "prefilter", "dedupe", "classify", "score", "digest"]


def load(run_date: str) -> dict:
    path = paths.manifest_path(run_date)
    if path.exists():
        return json.loads(path.read_text())
    return {
        "date": run_date,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "status": "in_progress",
        "stages": {s: {"status": "pending"} for s in STAGES},
        "error": None,
    }


def save(run_date: str, manifest: dict) -> None:
    paths.atomic_write_json(paths.manifest_path(run_date), manifest)


def stage_started(run_date: str, stage: str) -> dict:
    m = load(run_date)
    m["stages"][stage] = {
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    save(run_date, m)
    return m


def stage_succeeded(run_date: str, stage: str, **details) -> dict:
    m = load(run_date)
    started = m["stages"].get(stage, {}).get("started_at")
    finished = datetime.now(timezone.utc).isoformat()
    entry = {"status": "success", "started_at": started, "finished_at": finished}
    if started:
        entry["duration_s"] = round(
            (datetime.fromisoformat(finished) - datetime.fromisoformat(started)).total_seconds(), 2
        )
    entry.update(details)
    m["stages"][stage] = entry
    save(run_date, m)
    return m


def stage_failed(run_date: str, stage: str, error: str, **details) -> dict:
    m = load(run_date)
    started = m["stages"].get(stage, {}).get("started_at")
    finished = datetime.now(timezone.utc).isoformat()
    entry = {"status": "failed", "started_at": started, "finished_at": finished, "error": error}
    entry.update(details)
    m["stages"][stage] = entry
    m["status"] = "failed"
    m["error"] = f"{stage}: {error}"
    m["finished_at"] = finished
    save(run_date, m)
    return m


def run_succeeded(run_date: str) -> dict:
    m = load(run_date)
    m["status"] = "success"
    # Clear any error left over from an earlier failed attempt at this date,
    # otherwise a resumed run reads as status=success alongside a stale error.
    m["error"] = None
    m["finished_at"] = datetime.now(timezone.utc).isoformat()
    save(run_date, m)
    return m
