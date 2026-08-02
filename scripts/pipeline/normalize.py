"""Normalize stage: map each source's raw records into a common schema:
{source, title, company, url, posted_date, snippet, tags}."""
import html
import json
import re

from . import logging_setup, manifest, paths


def _strip_html(text: str) -> str:
    return html.unescape(re.sub("<[^<]+?>", " ", text or ""))


def _normalize_remoteok(items: list[dict]) -> list[dict]:
    return [{
        "source": "remoteok",
        "title": item.get("position", ""),
        "company": item.get("company", ""),
        "url": item.get("url", ""),
        "posted_date": item.get("date", ""),
        "snippet": _strip_html(item.get("description", ""))[:500],
        "tags": item.get("tags", []),
    } for item in items]


def _normalize_wwr(items: list[dict]) -> list[dict]:
    return [{
        "source": "weworkremotely",
        "title": item.get("title", ""),
        "company": "",  # WWR titles are usually "Company: Role"
        "url": item.get("link", ""),
        "posted_date": item.get("pubDate", ""),
        "snippet": _strip_html(item.get("description", ""))[:500],
        "tags": [],
    } for item in items]


def _normalize_hn(items: list[dict]) -> list[dict]:
    result = []
    for item in items:
        clean = _strip_html(item.get("text", ""))
        thread_title = item.get("thread_title", "")
        result.append({
            "source": f"hn:{thread_title}",
            "title": clean.split("\n")[0][:120],
            "company": "",
            "url": f"https://news.ycombinator.com/item?id={item.get('id')}",
            "posted_date": item.get("created_at", ""),
            "snippet": clean[:800],
            "tags": [],
        })
    return result


NORMALIZERS = {
    "remoteok": _normalize_remoteok,
    "weworkremotely": _normalize_wwr,
    "hn_whoishiring": _normalize_hn,
}


def run(run_date: str, fetch_checkpoint: dict) -> dict:
    logger = logging_setup.get_logger(run_date)
    manifest.stage_started(run_date, "normalize")

    listings = []
    for source_name, normalize_fn in NORMALIZERS.items():
        source_data = fetch_checkpoint["sources"].get(source_name, {})
        listings.extend(normalize_fn(source_data.get("items", [])))

    checkpoint = {"listings": listings}
    paths.checkpoint_path(run_date, "normalize").write_text(json.dumps(checkpoint, indent=2))

    logging_setup.log(logger, "normalize", "normalized listings", count=len(listings))
    manifest.stage_succeeded(run_date, "normalize", count=len(listings))
    return checkpoint
