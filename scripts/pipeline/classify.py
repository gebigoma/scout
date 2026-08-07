"""Classify stage: structured Claude calls over the deduped listings +
ROLE_CRITERIA.md, deciding which are genuine matches. No keyword
pre-filtering - the model sees everything and reasons about fit, rather
than string-matching.

Listings are addressed by a run-global integer id (their index in the
deduped list) rather than by echoing the url back - a url round-tripped
through the model can come back mangled, silently dropping a real match.
Ids make that failure mode structural: there's nothing for the model to
mangle, and a returned id either belongs to this chunk or it doesn't.

The single call is also split into chunks so a run over hundreds of
listings doesn't hinge on one huge call, and so every listing gets an
explicit verdict (match or no_match) instead of only matches being
returned - a model that silently skips listings should be distinguishable
from a model that genuinely found nothing."""
import json
import os
import subprocess

from . import llm, logging_setup, manifest, paths, retry

CHUNK_SIZE = int(os.environ.get("SCOUT_CLASSIFY_CHUNK_SIZE", "50"))

SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "verdict": {"type": "string", "enum": ["match", "no_match"]},
                    "role_category": {
                        "type": "string",
                        "enum": ["senior_tpm", "agentic_ai_engineer", "first_tpm"],
                    },
                    "reason": {"type": "string"},
                },
                "required": ["id", "verdict"],
            },
        },
    },
    "required": ["verdicts"],
}

# Per-lane wording for the two spots in the prompt that name the fractional
# lane's specific categories - the first-TPM lane has a single category and
# a different match rule (full-time, foundation-laying, not contract terms).
LANE_PROMPT_CONTEXT = {
    "fractional": {
        "match_rule": (
            "A listing is a genuine match only if it explicitly states "
            "fractional/contract/part-time/interim terms AND fits one of the "
            "target roles (senior Technical Program Management, or Agentic "
            "AI Engineer / AI engineering leadership, or a close adjacent)."
        ),
        "role_category_hint": '("senior_tpm" or "agentic_ai_engineer")',
    },
    "first_tpm": {
        "match_rule": (
            "A listing is a genuine match only if it meets the criteria "
            "above: a full-time role where the hire would be establishing "
            "the program management function, not joining an existing one."
        ),
        "role_category_hint": '("first_tpm")',
    },
}


def _chunks(id_listing_pairs: list) -> list:
    return [id_listing_pairs[i:i + CHUNK_SIZE]
            for i in range(0, len(id_listing_pairs), CHUNK_SIZE)]


def _build_prompt(id_listing_pairs: list, lane: str = "fractional") -> str:
    template = (paths.prompts_dir() / "classify_prompt.md").read_text()
    role_criteria = paths.role_criteria_path(lane).read_text()
    lane_context = LANE_PROMPT_CONTEXT[lane]
    # tags carries the single most structured employment-type signal we get
    # (RemoteOK marks "contract"/"part time" there), and posted_date lets the
    # model discount stale postings - both were previously normalized and
    # then dropped before ever reaching the model.
    listings_json = json.dumps(
        [{"id": i, "url": l["url"], "title": l["title"], "company": l.get("company", ""),
          "source": l["source"], "posted_date": l.get("posted_date", ""),
          "tags": l.get("tags", []), "snippet": l["snippet"]} for i, l in id_listing_pairs],
        indent=2,
    )
    return template.format(role_criteria=role_criteria, listings_json=listings_json,
                           **lane_context)


def _call_claude(prompt: str) -> dict:
    proc = subprocess.run(
        [llm.claude_bin(), "-p", "--model", llm.MODEL,
         "--tools", "", "--json-schema", json.dumps(SCHEMA)],
        input=prompt, text=True, capture_output=True, timeout=600,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude classify call failed (exit {proc.returncode}): {proc.stderr[:500]}")
    return json.loads(proc.stdout)


def _validate_verdicts(verdicts: list, expected_ids: set) -> list:
    """Raises ValueError on any malformed response: a missing id, a
    duplicate id, an id not in this chunk (hallucinated or leaked from
    another chunk), or a match verdict missing role_category/reason. Never
    partially accepts a malformed chunk - the caller retries the whole
    thing."""
    seen = set()
    for v in verdicts:
        vid = v.get("id")
        if vid is None:
            raise ValueError(f"verdict missing id: {v!r}")
        if vid in seen:
            raise ValueError(f"duplicate id in response: {vid}")
        if vid not in expected_ids:
            raise ValueError(f"id not in this chunk: {vid}")
        seen.add(vid)
        if v.get("verdict") == "match" and not (v.get("role_category") and v.get("reason")):
            raise ValueError(f"match verdict missing role_category/reason for id {vid}")
    missing = expected_ids - seen
    if missing:
        raise ValueError(f"missing verdicts for ids: {sorted(missing)}")
    return verdicts


def _load_chunk_checkpoint(run_date: str, chunk_index: int, expected_ids: set):
    path = paths.classify_chunk_path(run_date, chunk_index)
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    # Guards against a resume where SCOUT_CLASSIFY_CHUNK_SIZE changed between
    # attempts - a stale checkpoint's id range no longer matches this chunk,
    # so treat it as absent rather than silently misapplying it.
    if set(data.get("ids", [])) != expected_ids:
        return None
    return data["verdicts"]


def run(run_date: str, dedupe_checkpoint: dict) -> dict:
    logger = logging_setup.get_logger(run_date)
    manifest.stage_started(run_date, "classify")

    listings = dedupe_checkpoint["listings"]
    id_to_listing = dict(enumerate(listings))

    by_lane = {}
    for i, listing in id_to_listing.items():
        by_lane.setdefault(listing.get("lane", "fractional"), []).append((i, listing))

    # Each lane uses its own criteria file, so a chunk can't mix lanes - but
    # chunk indices (and therefore classify_chunks/<n>.json filenames) stay
    # globally unique across lanes by flattening into one sequence here.
    lane_chunks = [(lane, pairs) for lane in sorted(by_lane) for pairs in _chunks(by_lane[lane])]

    all_verdicts = []
    failed_chunk_indices = []
    total_attempts = 0

    for chunk_index, (lane, pairs) in enumerate(lane_chunks):
        chunk_ids = {i for i, _ in pairs}

        cached = _load_chunk_checkpoint(run_date, chunk_index, chunk_ids)
        if cached is not None:
            all_verdicts.extend(cached)
            logging_setup.log(logger, "classify", "using cached chunk checkpoint",
                               chunk=chunk_index)
            continue

        prompt = _build_prompt(pairs, lane)
        attempts_made = 0

        def attempt():
            nonlocal attempts_made
            attempts_made += 1
            result = _call_claude(prompt)
            return _validate_verdicts(result.get("verdicts", []), chunk_ids)

        def on_retry(attempt_num, exc, chunk_index=chunk_index):
            logging_setup.log(logger, "classify", "chunk failed, retrying",
                               chunk=chunk_index, attempt=attempt_num, error=str(exc))

        try:
            verdicts = retry.with_backoff(attempt, attempts=2, base_delay=2.0, on_retry=on_retry)
        except Exception as e:
            logging_setup.log(logger, "classify", "chunk failed permanently",
                               chunk=chunk_index, attempts=attempts_made, error=str(e))
            failed_chunk_indices.append(chunk_index)
            total_attempts += attempts_made
            continue

        paths.atomic_write_json(paths.classify_chunk_path(run_date, chunk_index),
                                 {"ids": sorted(chunk_ids), "verdicts": verdicts})
        all_verdicts.extend(verdicts)
        total_attempts += attempts_made

    unclassified_count = sum(len(lane_chunks[i][1]) for i in failed_chunk_indices)

    if lane_chunks and len(failed_chunk_indices) == len(lane_chunks):
        # Don't write a checkpoint on total failure - a later re-run should
        # actually retry classify, not treat this as cached and skip it.
        manifest.stage_failed(run_date, "classify", "all chunks failed",
                               failed_chunk_indices=failed_chunk_indices,
                               chunk_count=len(lane_chunks), attempts=total_attempts)
        raise RuntimeError("classify stage: all chunks failed")

    matches = [
        {"id": v["id"], "url": id_to_listing[v["id"]]["url"],
         "role_category": v["role_category"], "reason": v["reason"],
         "listing": id_to_listing[v["id"]]}
        for v in all_verdicts if v["verdict"] == "match"
    ]

    checkpoint = {
        "matches": matches,
        "failed_chunk_indices": failed_chunk_indices,
        "unclassified_count": unclassified_count,
    }
    paths.atomic_write_json(paths.checkpoint_path(run_date, "classify"), checkpoint)

    logging_setup.log(logger, "classify", "classified listings",
                       candidates=len(listings), matches=len(matches),
                       chunk_count=len(lane_chunks), failed_chunk_indices=failed_chunk_indices,
                       unclassified_count=unclassified_count, attempts=total_attempts)
    manifest.stage_succeeded(run_date, "classify",
                              candidates=len(listings), matches=len(matches),
                              chunk_count=len(lane_chunks), failed_chunk_indices=failed_chunk_indices,
                              unclassified_count=unclassified_count, attempts=total_attempts)
    return checkpoint
