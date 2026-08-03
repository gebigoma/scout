"""Normalize stage: map each source's raw records into a common schema:
{source, title, company, url, posted_date, snippet, tags}."""
import html
import json
import re

from . import logging_setup, manifest, paths


def _strip_html(text: str) -> str:
    return html.unescape(re.sub("<[^<]+?>", " ", text or ""))


# The criteria require a listing to *explicitly state* fractional terms, so
# these are the exact words the classifier needs to see to say yes.
EMPLOYMENT_TERMS = re.compile(
    r"fractional|contract|part[- ]time|part time|interim|advisory|retainer|"
    r"full[- ]time|hourly|hrs?/w|hours per week|freelance|consultant",
    re.IGNORECASE,
)

HEAD_CHARS = 400
MAX_SNIPPET = 1200


def _extract_snippet(text: str, head_chars: int = HEAD_CHARS) -> str:
    """Keep the opening of the description, then append any later sentences
    that mention employment terms.

    Plain head-truncation systematically defeats the criteria: We Work
    Remotely descriptions open with "Headquarters: ... About Us ..."
    boilerplate and state the employment type further down, so a fixed cut
    fed the model 500 chars of marketing copy and hid the very evidence it
    was asked to find. That produces false negatives that are invisible by
    construction - and makes a zero-match week untrustworthy."""
    text = (text or "").strip()
    head = text[:head_chars]
    tail = text[head_chars:]
    if not tail:
        return head

    hits = [s.strip() for s in re.split(r"(?<=[.!?\n])\s+", tail)
            if EMPLOYMENT_TERMS.search(s)]
    if not hits:
        return head
    return (head + " […] " + " ".join(hits))[:MAX_SNIPPET]


def _normalize_remoteok(items: list) -> list:
    return [{
        "source": "remoteok",
        "title": item.get("position", ""),
        "company": item.get("company", ""),
        "url": item.get("url", ""),
        "posted_date": item.get("date", ""),
        "snippet": _extract_snippet(_strip_html(item.get("description", ""))),
        "tags": item.get("tags", []),
    } for item in items]


def _normalize_wwr(items: list) -> list:
    result = []
    for item in items:
        # WWR titles are "Company: Role" - previously left unsplit, so the
        # digest fell back to printing the literal source name as company.
        raw_title = item.get("title", "")
        company, _, role = raw_title.partition(": ")
        if not role:
            company, role = "", raw_title
        result.append({
            "source": "weworkremotely",
            "title": role.strip(),
            "company": company.strip(),
            "url": item.get("link", ""),
            "posted_date": item.get("pubDate", ""),
            "snippet": _extract_snippet(_strip_html(item.get("description", ""))),
            "tags": [],
        })
    return result


def _normalize_hn(items: list) -> list:
    result = []
    for item in items:
        clean = _strip_html(item.get("text", ""))
        thread_title = item.get("thread_title", "")
        # "Who is hiring" comments follow a "Company | Role | Location | Type"
        # convention. Splitting on "\n" instead treated the whole comment as
        # the title and chopped it mid-word, which is what produced the
        # mangled headings in earlier digests.
        fields = [f.strip() for f in clean.split("|")]
        company = fields[0][:80] if fields else ""
        role = fields[1][:100] if len(fields) > 1 else clean[:100]
        result.append({
            "source": f"hn:{thread_title}",
            "title": role,
            "company": company,
            "url": f"https://news.ycombinator.com/item?id={item.get('id')}",
            "posted_date": item.get("created_at", ""),
            "snippet": _extract_snippet(clean, head_chars=600),
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
    paths.atomic_write_json(paths.checkpoint_path(run_date, "normalize"), checkpoint)

    logging_setup.log(logger, "normalize", "normalized listings", count=len(listings))
    manifest.stage_succeeded(run_date, "normalize", count=len(listings))
    return checkpoint
