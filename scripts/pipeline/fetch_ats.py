"""Fetch stage for the first-TPM lane: one HTTP call per company in
data/companies.csv, across the three ATS APIs those portfolio companies use.

A single company's call failing doesn't fail the whole stage - it's
recorded as a partial failure and the others still proceed, mirroring
fetch.py's per-source semantics. A 404 means "this company isn't on this
ATS", which is normal and expected (board tokens are guesswork), so it's
tracked separately as a skip rather than a failure, and doesn't count
toward "everything is down"."""
import json
import os
import socket
import time
import urllib.request
from urllib.error import HTTPError, URLError

from . import companies, logging_setup, manifest, paths, retry
from .fetch import USER_AGENT

FETCH_ERRORS = (URLError, HTTPError, socket.timeout, TimeoutError, json.JSONDecodeError)

# One call per company, so a portfolio of a few hundred is the slowest stage
# by far - a small delay keeps it polite to the ATS endpoints.
DELAY_BETWEEN_CALLS = float(os.environ.get("SCOUT_ATS_DELAY", "0.3"))


def _url(company: dict) -> str:
    token = company["token"]
    if company["ats"] == "greenhouse":
        # ?content=true is load-bearing - the bare list endpoint has no
        # description field at all.
        return f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    if company["ats"] == "ashby":
        return f"https://api.ashbyhq.com/posting-api/job-board/{token}"
    return f"https://api.lever.co/v0/postings/{token}?mode=json"


def _http_get(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _fetch_company_raw(company: dict):
    """Returns the raw list of job dicts, or None if this company isn't on
    this ATS (404) - a per-company skip, not an error."""
    try:
        raw = _http_get(_url(company))
    except HTTPError as e:
        if e.code == 404:
            return None
        raise
    data = json.loads(raw)
    if company["ats"] in ("greenhouse", "ashby"):
        return data.get("jobs", [])
    return data if isinstance(data, list) else []  # lever


def run(run_date: str) -> dict:
    logger = logging_setup.get_logger(run_date)
    manifest.stage_started(run_date, "fetch_ats")

    company_list = companies.load_companies()
    results = {}
    for company in company_list:
        attempts_made = 0

        def attempt(company=company):
            nonlocal attempts_made
            attempts_made += 1
            return _fetch_company_raw(company)

        def on_retry(attempt_num, exc, name=company["name"]):
            logging_setup.log(logger, "fetch_ats", f"{name} fetch failed, retrying",
                               company=name, attempt=attempt_num, error=str(exc))

        try:
            jobs = retry.with_backoff(attempt, attempts=2, base_delay=1.0, on_retry=on_retry)
        except FETCH_ERRORS as e:
            results[company["name"]] = {
                "status": "failed", "company": company,
                "attempts": attempts_made, "jobs": [], "error": str(e),
            }
            logging_setup.log(logger, "fetch_ats", f"{company['name']} fetch failed permanently",
                               company=company["name"], attempts=attempts_made, error=str(e))
        else:
            if jobs is None:
                results[company["name"]] = {
                    "status": "skipped", "company": company,
                    "attempts": attempts_made, "jobs": [],
                }
            else:
                results[company["name"]] = {
                    "status": "success", "company": company,
                    "attempts": attempts_made, "jobs": jobs,
                }
        time.sleep(DELAY_BETWEEN_CALLS)

    failed = [n for n, r in results.items() if r["status"] == "failed"]
    skipped = [n for n, r in results.items() if r["status"] == "skipped"]
    succeeded = [n for n, r in results.items() if r["status"] == "success"]

    # A 404 is expected/normal (board tokens are guesswork) and must not
    # count toward "everything is down" - only genuine transport failures
    # do, so a company list with some entries not on a given ATS can't
    # trip a false stage failure.
    reachable = len(company_list) - len(skipped)
    if company_list and reachable and len(failed) == reachable:
        manifest.stage_failed(run_date, "fetch_ats", "all reachable ATS calls failed",
                               failed_companies=failed)
        raise RuntimeError("fetch_ats stage: all reachable ATS calls failed")

    checkpoint = {"companies": results}
    paths.atomic_write_json(paths.checkpoint_path(run_date, "fetch_ats"), checkpoint)

    logging_setup.log(logger, "fetch_ats", "fetched ATS listings",
                       company_count=len(company_list), succeeded=len(succeeded),
                       skipped=len(skipped), failed=failed)
    manifest.stage_succeeded(run_date, "fetch_ats",
                              company_count=len(company_list), succeeded=len(succeeded),
                              skipped=len(skipped), failed=failed)
    return checkpoint
