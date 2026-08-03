"""Dedupe stage: drop listings with no URL, duplicate URLs within this run,
or URLs already surfaced as a match on an EARLIER date (data/seen.json).

seen.json maps url -> the run date it was first surfaced on, rather than
being a flat list. That date matters: digest (the last stage) writes this
file and dedupe (the third stage) reads it, so re-running a completed date
with --force would otherwise make dedupe drop that date's own matches as
"already seen", yielding an empty digest that overwrites the real one and
still reports success. Excluding only URLs first seen on a *different* date
makes re-runs idempotent."""
import json

from . import logging_setup, manifest, paths


def load_seen(path=None) -> dict:
    """Return {url: first_seen_date}. Tolerates the legacy flat-list format
    by treating those URLs as seen on an unknown earlier date."""
    path = path or paths.seen_path()
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    if "seen" in data:
        return data["seen"]
    return {url: "" for url in data.get("matched_urls", [])}


def run(run_date: str, normalize_checkpoint: dict) -> dict:
    logger = logging_setup.get_logger(run_date)
    manifest.stage_started(run_date, "dedupe")

    seen = load_seen()
    seen_urls = {url for url, first_seen in seen.items() if first_seen != run_date}
    listings = normalize_checkpoint["listings"]

    result = []
    seen_this_run = set()
    dropped_no_url = 0
    dropped_in_run_dupe = 0
    dropped_previously_matched = 0
    for listing in listings:
        url = listing.get("url", "")
        if not url:
            dropped_no_url += 1
            continue
        if url in seen_urls:
            dropped_previously_matched += 1
            continue
        if url in seen_this_run:
            dropped_in_run_dupe += 1
            continue
        seen_this_run.add(url)
        result.append(listing)

    checkpoint = {"listings": result}
    paths.atomic_write_json(paths.checkpoint_path(run_date, "dedupe"), checkpoint)

    logging_setup.log(
        logger, "dedupe", "deduped listings",
        raw_count=len(listings), deduped_count=len(result),
        dropped_no_url=dropped_no_url, dropped_in_run_dupe=dropped_in_run_dupe,
        dropped_previously_matched=dropped_previously_matched,
    )
    manifest.stage_succeeded(
        run_date, "dedupe",
        raw_count=len(listings), deduped_count=len(result),
        dropped_no_url=dropped_no_url, dropped_in_run_dupe=dropped_in_run_dupe,
        dropped_previously_matched=dropped_previously_matched,
    )
    return checkpoint
