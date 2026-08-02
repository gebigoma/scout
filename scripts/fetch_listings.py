#!/usr/bin/env python3
"""Pull raw job listings from public sources with no auth required.

Writes normalized listings to data/raw/<date>.json. This step is
deliberately dumb (no filtering/judgment) - a later step reads the output
against ROLE_CRITERIA.md and decides what actually matches.
"""
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.error import URLError

USER_AGENT = "scout-fractional-role-finder/1.0 (personal job search tool)"

WWR_FEEDS = [
    "https://weworkremotely.com/categories/remote-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-product-jobs.rss",
]

HN_HIRING_QUERY = (
    "https://hn.algolia.com/api/v1/search_by_date"
    "?tags=story,author_whoishiring&query=Who%20is%20hiring"
)


def fetch(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_remoteok() -> list[dict]:
    try:
        raw = fetch("https://remoteok.com/api")
    except URLError as e:
        print(f"  ! RemoteOK fetch failed: {e}")
        return []
    data = json.loads(raw)
    listings = []
    for item in data:
        if "position" not in item:
            continue  # first element is a legal-notice stub, not a listing
        listings.append({
            "source": "remoteok",
            "title": item.get("position", ""),
            "company": item.get("company", ""),
            "url": item.get("url", ""),
            "posted_date": item.get("date", ""),
            "snippet": re.sub("<[^<]+?>", "", item.get("description", ""))[:500],
            "tags": item.get("tags", []),
        })
    return listings


def fetch_wwr() -> list[dict]:
    listings = []
    for feed_url in WWR_FEEDS:
        try:
            raw = fetch(feed_url)
        except URLError as e:
            print(f"  ! WWR fetch failed ({feed_url}): {e}")
            continue
        root = ET.fromstring(raw)
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()
            description = (item.findtext("description") or "").strip()
            listings.append({
                "source": "weworkremotely",
                "title": title,
                "company": "",  # WWR titles are usually "Company: Role"
                "url": link,
                "posted_date": pub_date,
                "snippet": re.sub("<[^<]+?>", "", description)[:500],
                "tags": [],
            })
    return listings


def fetch_hn_hiring() -> list[dict]:
    """Find the latest 'Who is hiring' thread, then pull its top-level comments."""
    try:
        raw = fetch(HN_HIRING_QUERY)
    except URLError as e:
        print(f"  ! HN search fetch failed: {e}")
        return []
    hits = json.loads(raw).get("hits", [])
    if not hits:
        return []
    thread_id = hits[0]["objectID"]
    thread_title = hits[0].get("title", "")

    try:
        raw_thread = fetch(f"https://hn.algolia.com/api/v1/items/{thread_id}")
    except URLError as e:
        print(f"  ! HN thread fetch failed: {e}")
        return []
    thread = json.loads(raw_thread)

    listings = []
    for comment in thread.get("children", []) or []:
        text = comment.get("text") or ""
        if not text.strip():
            continue
        clean = re.sub("<[^<]+?>", " ", text)
        listings.append({
            "source": f"hn:{thread_title}",
            "title": clean.split("\n")[0][:120],
            "company": "",
            "url": f"https://news.ycombinator.com/item?id={comment.get('id')}",
            "posted_date": comment.get("created_at", ""),
            "snippet": clean[:800],
            "tags": [],
        })
    return listings


def load_seen_urls(seen_path: Path) -> set[str]:
    if not seen_path.exists():
        return set()
    return set(json.loads(seen_path.read_text()).get("matched_urls", []))


def dedupe(listings: list[dict], seen_urls: set[str]) -> list[dict]:
    """Drop listings with no URL, duplicate URLs within this run, or URLs
    already surfaced as a match in a previous week (per data/seen.json)."""
    result = []
    seen_this_run = set()
    for listing in listings:
        url = listing.get("url", "")
        if not url or url in seen_this_run or url in seen_urls:
            continue
        seen_this_run.add(url)
        result.append(listing)
    return result


def main():
    print("Fetching RemoteOK...")
    remoteok = fetch_remoteok()
    print(f"  {len(remoteok)} listings")

    print("Fetching We Work Remotely...")
    wwr = fetch_wwr()
    print(f"  {len(wwr)} listings")

    print("Fetching HN Who is hiring...")
    hn = fetch_hn_hiring()
    print(f"  {len(hn)} listings")

    project_dir = Path(__file__).resolve().parent.parent
    seen_urls = load_seen_urls(project_dir / "data" / "seen.json")

    raw_count = len(remoteok) + len(wwr) + len(hn)
    all_listings = dedupe(remoteok + wwr + hn, seen_urls)
    print(f"\nDeduped {raw_count} raw listings -> {len(all_listings)} "
          f"(dropped duplicates + {len(seen_urls)} previously-matched URLs)")

    out_dir = project_dir / "data" / "raw"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{date.today().isoformat()}.json"
    out_path.write_text(json.dumps({
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "count": len(all_listings),
        "listings": all_listings,
    }, indent=2))

    print(f"Wrote {len(all_listings)} raw listings to {out_path}")


if __name__ == "__main__":
    main()
