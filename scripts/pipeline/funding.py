"""Funding-signal enrichment via SEC EDGAR full-text search (Form D
filings). Not wired into the pipeline - callers invoke enrich_companies
explicitly. Kept separate from company_signals.py so that stage's offline,
no-network verification path is never at the mercy of EDGAR's rate limits.

https://efts.sec.gov/LATEST/search-index?entityName=<name>&forms=D returns
plain JSON, no key required."""
import json
import os
import socket
import time
import urllib.parse
import urllib.request
from urllib.error import HTTPError, URLError

from . import retry
from .fetch import USER_AGENT as _FETCH_USER_AGENT

# EDGAR blocks generic user agents and wants real contact info - stricter
# than the ATS endpoints fetch_ats.py hits with the bare fetch.USER_AGENT.
USER_AGENT = f"{_FETCH_USER_AGENT}; contact tuna.park@gmail.com"

FETCH_ERRORS = (URLError, HTTPError, socket.timeout, TimeoutError, json.JSONDecodeError)

# EDGAR is stricter than the ATS APIs about request rate.
DELAY_BETWEEN_CALLS = float(os.environ.get("SCOUT_EDGAR_DELAY", "0.5"))

SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"


def _http_get(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def lookup_form_d(company_name: str, opener=_http_get) -> dict:
    """Look up the most recent Form D filing for company_name.

    entityName matching is fuzzy and can return filings for unrelated
    companies that merely share a name prefix (e.g. "Coder" also returns
    "Coder Kids, Inc."). Multiple distinct CIKs in the result are flagged
    `ambiguous` rather than silently resolved by picking the newest filing -
    "newest" has no reason to be the right company.

    Returns None (never False/0) when there are zero hits: EDGAR having no
    record means "unknown", not "no funding" - Checkly (a German GmbH)
    returns zero hits for a reason unrelated to whether it raised."""
    params = urllib.parse.urlencode({"entityName": company_name, "forms": "D"})
    data = json.loads(opener(f"{SEARCH_URL}?{params}"))
    hits = ((data.get("hits") or {}).get("hits")) or []
    if not hits:
        return None

    sources = [h.get("_source", {}) for h in hits]
    ciks = sorted({cik for s in sources for cik in (s.get("ciks") or [])})
    display_names = sorted({n for s in sources for n in (s.get("display_names") or [])})
    newest = max(sources, key=lambda s: s.get("file_date", ""))

    return {
        "last_form_d": newest.get("file_date"),
        "ciks": ciks,
        "display_names": display_names,
        "ambiguous": len(ciks) > 1,
    }


def enrich_companies(company_names: list, opener=_http_get) -> dict:
    """One HTTP call per name. A single company's lookup failing is a
    partial failure, not a stage failure - mirrors fetch_ats.py's per-company
    semantics, since a name collision or a transient EDGAR error shouldn't
    cost every other company its funding data."""
    results = {}
    for name in company_names:
        attempts_made = 0

        def attempt(name=name):
            nonlocal attempts_made
            attempts_made += 1
            return lookup_form_d(name, opener=opener)

        try:
            results[name] = {"status": "success",
                             "funding": retry.with_backoff(attempt, attempts=2, base_delay=1.0)}
        except FETCH_ERRORS as e:
            results[name] = {"status": "failed", "funding": None, "error": str(e)}
        time.sleep(DELAY_BETWEEN_CALLS)
    return results
