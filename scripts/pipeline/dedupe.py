"""Dedupe stage: drop listings with no URL, duplicate URLs within this run,
or URLs already surfaced as a match in a previous week (data/seen.json)."""
import json

from . import logging_setup, manifest, paths


def load_seen_urls() -> set[str]:
    path = paths.seen_path()
    if not path.exists():
        return set()
    return set(json.loads(path.read_text()).get("matched_urls", []))


def run(run_date: str, normalize_checkpoint: dict) -> dict:
    logger = logging_setup.get_logger(run_date)
    manifest.stage_started(run_date, "dedupe")

    seen_urls = load_seen_urls()
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
    paths.checkpoint_path(run_date, "dedupe").write_text(json.dumps(checkpoint, indent=2))

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
