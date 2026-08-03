"""Classify stage: one structured Claude call over ALL deduped listings +
ROLE_CRITERIA.md, deciding which are genuine matches. No keyword
pre-filtering - the model sees everything and reasons about fit, rather
than string-matching. Only matches are returned (keeps output small)."""
import json
import subprocess

from . import llm, logging_setup, manifest, paths, retry

SCHEMA = {
    "type": "object",
    "properties": {
        "matches": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "role_category": {
                        "type": "string",
                        "enum": ["senior_tpm", "agentic_ai_engineer"],
                    },
                    "reason": {"type": "string"},
                },
                "required": ["url", "role_category", "reason"],
            },
        },
    },
    "required": ["matches"],
}


def _build_prompt(listings: list) -> str:
    template = (paths.prompts_dir() / "classify_prompt.md").read_text()
    role_criteria = paths.role_criteria_path().read_text()
    # tags carries the single most structured employment-type signal we get
    # (RemoteOK marks "contract"/"part time" there), and posted_date lets the
    # model discount stale postings - both were previously normalized and
    # then dropped before ever reaching the model.
    listings_json = json.dumps(
        [{"url": l["url"], "title": l["title"], "company": l.get("company", ""),
          "source": l["source"], "posted_date": l.get("posted_date", ""),
          "tags": l.get("tags", []), "snippet": l["snippet"]} for l in listings],
        indent=2,
    )
    return template.format(role_criteria=role_criteria, listings_json=listings_json)


def _call_claude(prompt: str) -> dict:
    proc = subprocess.run(
        [llm.claude_bin(), "-p", "--tools", "", "--json-schema", json.dumps(SCHEMA)],
        input=prompt, text=True, capture_output=True, timeout=600,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude classify call failed (exit {proc.returncode}): {proc.stderr[:500]}")
    return json.loads(proc.stdout)


def run(run_date: str, dedupe_checkpoint: dict) -> dict:
    logger = logging_setup.get_logger(run_date)
    manifest.stage_started(run_date, "classify")

    listings = dedupe_checkpoint["listings"]
    prompt = _build_prompt(listings)

    attempts_made = 0

    def attempt():
        nonlocal attempts_made
        attempts_made += 1
        return _call_claude(prompt)

    def on_retry(attempt_num, exc):
        logging_setup.log(logger, "classify", "classify call failed, retrying",
                           attempt=attempt_num, error=str(exc))

    try:
        result = retry.with_backoff(attempt, attempts=2, base_delay=2.0, on_retry=on_retry)
    except Exception as e:
        manifest.stage_failed(run_date, "classify", str(e), attempts=attempts_made)
        raise

    by_url = {l["url"]: l for l in listings}
    matches = []
    unresolved = 0
    for m in result.get("matches", []):
        listing = by_url.get(m["url"])
        if not listing:
            unresolved += 1
            continue
        matches.append({**m, "listing": listing})

    checkpoint = {"matches": matches}
    paths.atomic_write_json(paths.checkpoint_path(run_date, "classify"), checkpoint)

    logging_setup.log(logger, "classify", "classified listings",
                       candidates=len(listings), matches=len(matches),
                       unresolved_urls=unresolved, attempts=attempts_made)
    manifest.stage_succeeded(run_date, "classify",
                              candidates=len(listings), matches=len(matches),
                              unresolved_urls=unresolved, attempts=attempts_made)
    return checkpoint
