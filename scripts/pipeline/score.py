"""Score stage: one structured Claude call over ONLY the classified matches
(a small set), producing a 0-100 fit score + rationale per match."""
import json
import subprocess

from . import lanes, llm, logging_setup, manifest, paths, retry

SCHEMA = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "fit_score": {"type": "integer", "minimum": 0, "maximum": 100},
                    "rationale": {"type": "string"},
                },
                "required": ["url", "fit_score", "rationale"],
            },
        },
    },
    "required": ["scores"],
}


def _build_prompt(matches: list[dict], lane: str) -> str:
    template = (paths.prompts_dir() / "score_prompt.md").read_text()
    # Each lane has its own fit-score guide, and they are not interchangeable:
    # ROLE_CRITERIA.md awards 85-100 only for explicit, generous fractional
    # terms, which a full-time first-TPM role cannot satisfy by construction.
    # Scoring a first_tpm match against the fractional rubric drove real
    # matches below digest's score floor and into "Rejected on scoring".
    role_criteria = paths.role_criteria_path(lane).read_text()
    matches_json = json.dumps(
        [{"url": m["url"], "role_category": m["role_category"], "reason": m["reason"],
          "title": m["listing"]["title"], "company": m["listing"].get("company", ""),
          "source": m["listing"]["source"], "snippet": m["listing"]["snippet"],
          "headcount": m["listing"].get("headcount")}
         for m in matches],
        indent=2,
    )
    return template.format(role_criteria=role_criteria, matches_json=matches_json)


def _call_claude(prompt: str) -> dict:
    proc = subprocess.run(
        [llm.claude_bin(), "-p", "--model", llm.MODEL,
         "--tools", "", "--json-schema", json.dumps(SCHEMA)],
        input=prompt, text=True, capture_output=True, timeout=300,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude score call failed (exit {proc.returncode}): {proc.stderr[:500]}")
    return json.loads(proc.stdout)


def run(run_date: str, classify_checkpoint: dict) -> dict:
    logger = logging_setup.get_logger(run_date)
    manifest.stage_started(run_date, "score")

    matches = classify_checkpoint["matches"]

    if not matches:
        checkpoint = {"scored": []}
        paths.atomic_write_json(paths.checkpoint_path(run_date, "score"), checkpoint)
        logging_setup.log(logger, "score", "no matches to score")
        manifest.stage_succeeded(run_date, "score", count=0)
        return checkpoint

    # One call per lane, each carrying only that lane's fit-score guide. A
    # single call holding both rubrics would make the model pick which one
    # applies per listing - a judgment it has no reliable basis for making.
    by_lane = {}
    for m in matches:
        by_lane.setdefault(m["listing"].get("lane", lanes.FRACTIONAL), []).append(m)

    scores_by_url = {}
    failed_lanes = []
    total_attempts = 0

    for lane in sorted(by_lane):
        lane_matches = by_lane[lane]
        prompt = _build_prompt(lane_matches, lane)
        attempts_made = 0

        def attempt(prompt=prompt):
            nonlocal attempts_made
            attempts_made += 1
            return _call_claude(prompt)

        def on_retry(attempt_num, exc, lane=lane):
            logging_setup.log(logger, "score", "score call failed, retrying",
                               lane=lane, attempt=attempt_num, error=str(exc))

        try:
            result = retry.with_backoff(attempt, attempts=2, base_delay=2.0, on_retry=on_retry)
        except Exception as e:
            # Mirror classify's per-chunk semantics: one lane failing costs
            # that lane's matches, not the whole run. Unscored matches are
            # never published and never recorded as seen, so they stay
            # eligible on the next run.
            logging_setup.log(logger, "score", "lane failed permanently",
                               lane=lane, attempts=attempts_made, error=str(e))
            failed_lanes.append(lane)
            total_attempts += attempts_made
            continue

        for s in result.get("scores", []):
            scores_by_url[s["url"]] = s
        total_attempts += attempts_made

    if failed_lanes and len(failed_lanes) == len(by_lane):
        # Don't write a checkpoint on total failure - a re-run should actually
        # retry scoring, not treat this as cached and skip it.
        manifest.stage_failed(run_date, "score", "all lanes failed",
                               failed_lanes=failed_lanes, attempts=total_attempts)
        raise RuntimeError("score stage: all lanes failed")

    scored = []
    unresolved = 0
    for m in matches:
        s = scores_by_url.get(m["url"])
        if not s:
            unresolved += 1
            continue
        scored.append({**m, "fit_score": s["fit_score"], "rationale": s["rationale"]})

    checkpoint = {"scored": scored, "failed_lanes": failed_lanes,
                  "unscored_count": len(matches) - len(scored)}
    paths.atomic_write_json(paths.checkpoint_path(run_date, "score"), checkpoint)

    logging_setup.log(logger, "score", "scored matches",
                       count=len(scored), unresolved_urls=unresolved,
                       failed_lanes=failed_lanes, attempts=total_attempts)
    manifest.stage_succeeded(run_date, "score",
                              count=len(scored), unresolved_urls=unresolved,
                              failed_lanes=failed_lanes, attempts=total_attempts)
    return checkpoint
