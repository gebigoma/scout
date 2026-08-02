"""Fetch stage: pull raw data from each source, with retry+backoff per
source. A single source failing doesn't fail the whole stage - it's
recorded as a partial failure and the other sources still proceed."""
import json
import urllib.request
import xml.etree.ElementTree as ET
from urllib.error import URLError

from . import logging_setup, manifest, paths, retry

USER_AGENT = "scout-fractional-role-finder/1.0 (personal job search tool)"

WWR_FEEDS = [
    "https://weworkremotely.com/categories/remote-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-product-jobs.rss",
]

HN_HIRING_QUERY = (
    "https://hn.algolia.com/api/v1/search_by_date"
    "?tags=story,author_whoishiring&query=Who%20is%20hiring"
)


def _http_get(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _fetch_remoteok_raw() -> list[dict]:
    data = json.loads(_http_get("https://remoteok.com/api"))
    return [item for item in data if "position" in item]


def _fetch_wwr_raw() -> list[dict]:
    items = []
    for feed_url in WWR_FEEDS:
        raw = _http_get(feed_url)
        root = ET.fromstring(raw)
        for item in root.findall(".//item"):
            items.append({
                "title": (item.findtext("title") or "").strip(),
                "link": (item.findtext("link") or "").strip(),
                "pubDate": (item.findtext("pubDate") or "").strip(),
                "description": (item.findtext("description") or "").strip(),
            })
    return items


def _fetch_hn_raw() -> list[dict]:
    hits = json.loads(_http_get(HN_HIRING_QUERY)).get("hits", [])
    if not hits:
        return []
    thread_id = hits[0]["objectID"]
    thread_title = hits[0].get("title", "")
    thread = json.loads(_http_get(f"https://hn.algolia.com/api/v1/items/{thread_id}"))
    items = []
    for comment in thread.get("children", []) or []:
        text = comment.get("text") or ""
        if not text.strip():
            continue
        items.append({
            "thread_title": thread_title,
            "id": comment.get("id"),
            "created_at": comment.get("created_at", ""),
            "text": text,
        })
    return items


SOURCES = {
    "remoteok": _fetch_remoteok_raw,
    "weworkremotely": _fetch_wwr_raw,
    "hn_whoishiring": _fetch_hn_raw,
}


def run(run_date: str) -> dict:
    logger = logging_setup.get_logger(run_date)
    manifest.stage_started(run_date, "fetch")

    sources_result = {}
    for name, fetch_fn in SOURCES.items():
        attempts_made = 0

        def attempt(fetch_fn=fetch_fn):
            nonlocal attempts_made
            attempts_made += 1
            return fetch_fn()

        def on_retry(attempt_num, exc, name=name):
            logging_setup.log(
                logger, "fetch", f"{name} fetch failed, retrying",
                source=name, attempt=attempt_num, error=str(exc),
            )

        try:
            items = retry.with_backoff(attempt, attempts=3, base_delay=1.0, on_retry=on_retry)
            sources_result[name] = {
                "status": "success",
                "attempts": attempts_made,
                "count": len(items),
                "items": items,
            }
            logging_setup.log(logger, "fetch", f"{name} fetch succeeded",
                               source=name, attempts=attempts_made, count=len(items))
        except (URLError, Exception) as e:
            sources_result[name] = {
                "status": "failed",
                "attempts": attempts_made,
                "count": 0,
                "items": [],
                "error": str(e),
            }
            logging_setup.log(logger, "fetch", f"{name} fetch failed permanently",
                               source=name, attempts=attempts_made, error=str(e))

    failed_sources = [n for n, r in sources_result.items() if r["status"] == "failed"]
    total_count = sum(r["count"] for r in sources_result.values())

    if failed_sources and len(failed_sources) == len(SOURCES):
        # Don't write a checkpoint on total failure - a later re-run should
        # actually retry the fetch, not treat this as cached and skip it.
        manifest.stage_failed(run_date, "fetch", "all sources failed",
                               failed_sources=failed_sources)
        raise RuntimeError("fetch stage: all sources failed")

    checkpoint = {"sources": sources_result}
    paths.checkpoint_path(run_date, "fetch").write_text(json.dumps(checkpoint, indent=2))

    manifest.stage_succeeded(
        run_date, "fetch",
        total_count=total_count,
        failed_sources=failed_sources,
        per_source={n: {"status": r["status"], "count": r["count"], "attempts": r["attempts"]}
                    for n, r in sources_result.items()},
    )
    return checkpoint
