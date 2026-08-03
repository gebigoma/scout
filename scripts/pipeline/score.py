"""Score stage: one structured Claude call over ONLY the classified matches
(a small set), producing a 0-100 fit score + rationale per match."""
import json
import subprocess

from . import llm, logging_setup, manifest, paths, retry

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


def _build_prompt(matches: list[dict]) -> str:
    template = (paths.prompts_dir() / "score_prompt.md").read_text()
    role_criteria = paths.role_criteria_path().read_text()
    matches_json = json.dumps(
        [{"url": m["url"], "role_category": m["role_category"], "reason": m["reason"],
          "title": m["listing"]["title"], "company": m["listing"].get("company", ""),
          "source": m["listing"]["source"], "snippet": m["listing"]["snippet"]}
         for m in matches],
        indent=2,
    )
    return template.format(role_criteria=role_criteria, matches_json=matches_json)


def _call_claude(prompt: str) -> dict:
    proc = subprocess.run(
        [llm.claude_bin(), "-p", "--tools", "", "--json-schema", json.dumps(SCHEMA)],
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

    prompt = _build_prompt(matches)
    attempts_made = 0

    def attempt():
        nonlocal attempts_made
        attempts_made += 1
        return _call_claude(prompt)

    def on_retry(attempt_num, exc):
        logging_setup.log(logger, "score", "score call failed, retrying",
                           attempt=attempt_num, error=str(exc))

    try:
        result = retry.with_backoff(attempt, attempts=2, base_delay=2.0, on_retry=on_retry)
    except Exception as e:
        manifest.stage_failed(run_date, "score", str(e), attempts=attempts_made)
        raise

    scores_by_url = {s["url"]: s for s in result.get("scores", [])}
    scored = []
    unresolved = 0
    for m in matches:
        s = scores_by_url.get(m["url"])
        if not s:
            unresolved += 1
            continue
        scored.append({**m, "fit_score": s["fit_score"], "rationale": s["rationale"]})

    checkpoint = {"scored": scored}
    paths.atomic_write_json(paths.checkpoint_path(run_date, "score"), checkpoint)

    logging_setup.log(logger, "score", "scored matches",
                       count=len(scored), unresolved_urls=unresolved, attempts=attempts_made)
    manifest.stage_succeeded(run_date, "score",
                              count=len(scored), unresolved_urls=unresolved, attempts=attempts_made)
    return checkpoint
