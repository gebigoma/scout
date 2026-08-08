"""Normalize stage: map each source's raw records into a common schema:
{source, title, company, url, posted_date, snippet, tags}."""
import html
import json
import re

from . import logging_setup, manifest, paths


def _strip_html(text: str) -> str:
    return html.unescape(re.sub("<[^<]+?>", " ", text or ""))


def _one_line(text: str) -> str:
    """Collapse whitespace. Titles and companies are rendered into markdown
    list items, where an embedded newline silently breaks the entry."""
    return re.sub(r"\s+", " ", text or "").strip()


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


# Words that mean a header field is naming a job, not describing the terms.
ROLE_WORDS = re.compile(
    r"engineer|developer|programmer|designer|scientist|architect|analyst|"
    r"researcher|manager|management|director|founding|head of|lead\b|leader|"
    r"devops|sre\b|tpm\b|\bpm\b|cto\b|cpo\b|vp\b|specialist|consultant|"
    r"roles?\b|position|hiring|intern\b",
    re.IGNORECASE,
)

# Fields that demonstrably are *not* a role: locations, employment terms,
# money, links, YC batches.
METADATA_FIELD = re.compile(
    r"(remote|onsite|on-site|hybrid|anywhere|worldwide)\b|"
    r"(full|part)[\s-]?time\b|(contract|freelance|permanent|intern)\b|"
    r"\$|[\d,.]+\s*k?\s*[-–]|yc[\s(]|[swfx]\d{2}\b",
    re.IGNORECASE,
)

URL_FIELD = re.compile(r"(https?://|www\.)", re.IGNORECASE)


def _pick_hn_role(fields: list) -> str:
    """Choose the header field that names the role.

    The "Company | Role | Location | Type" convention is not one people
    actually follow. A single thread mixes in "Company | Location | Type",
    "Company | Salary | Location | Roles", and headers whose second field is
    a bare URL or a YC batch - so taking fields[1] published "REMOTE
    (worldwide)", "150-250k+ + equity" and "YC 19" as job titles."""
    candidates = [f for f in fields[1:] if f]
    for field in candidates:  # a field that names a role
        if ROLE_WORDS.search(field) and not URL_FIELD.match(field):
            return field[:100]
    for field in candidates:  # failing that, one that isn't terms/location/link
        if not METADATA_FIELD.match(field) and not URL_FIELD.match(field):
            return field[:100]
    # The header genuinely carries no role (the roles are listed in the body,
    # which the classifier reads via the snippet). Show the terms rather than
    # promoting a location to a job title.
    return " | ".join(candidates)[:100]


def _normalize_hn(items: list) -> list:
    result = []
    for item in items:
        raw = item.get("text", "")
        clean = _strip_html(raw)
        thread_title = item.get("thread_title", "")
        # The pipe-delimited header is the comment's first paragraph. Strip
        # HTML first and it runs straight into the body - <p> becomes a space
        # - which is what glued "Contract We build authority infrastructure
        # for people whose " into one heading.
        header = _strip_html(re.split(r"<p>|\n", raw, maxsplit=1)[0]).strip()
        fields = [f.strip() for f in header.split("|")] if header else []
        company = _one_line(fields[0])[:80] if fields else ""
        role = _one_line(
            _pick_hn_role(fields) if len(fields) > 1 else clean[:100])
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


def _normalize_ats(companies_results: dict) -> list:
    result = []
    for info in companies_results.values():
        if info["status"] != "success":
            continue
        company = info["company"]
        for job in info["jobs"]:
            if company["ats"] == "greenhouse":
                title, url = job.get("title", ""), job.get("absolute_url", "")
                posted = job.get("first_published", "")
                desc = job.get("content", "")
            elif company["ats"] == "ashby":
                title, url = job.get("title", ""), job.get("jobUrl", "")
                posted = job.get("publishedAt", "")
                desc = job.get("descriptionPlain", "")
            else:  # lever
                title, url = job.get("text", ""), job.get("hostedUrl", "")
                posted = job.get("createdAt", "")
                desc = job.get("descriptionPlain", job.get("description", ""))
            result.append({
                "source": f"{company['ats']}:{company['token']}",
                "title": _one_line(title),
                "company": company["name"],
                "url": url,
                "posted_date": str(posted),
                "snippet": _extract_snippet(_strip_html(desc)),
                "tags": [],
                "lane": "first_tpm",
                "headcount": company.get("headcount"),
            })
    return result


def run(run_date: str, fetch_checkpoint: dict, fetch_ats_checkpoint: dict = None) -> dict:
    logger = logging_setup.get_logger(run_date)
    manifest.stage_started(run_date, "normalize")

    listings = []
    for source_name, normalize_fn in NORMALIZERS.items():
        source_data = fetch_checkpoint["sources"].get(source_name, {})
        listings.extend({**l, "lane": "fractional"}
                        for l in normalize_fn(source_data.get("items", [])))

    if fetch_ats_checkpoint:
        listings.extend(_normalize_ats(fetch_ats_checkpoint["companies"]))

    checkpoint = {"listings": listings}
    paths.atomic_write_json(paths.checkpoint_path(run_date, "normalize"), checkpoint)

    logging_setup.log(logger, "normalize", "normalized listings", count=len(listings))
    manifest.stage_succeeded(run_date, "normalize", count=len(listings))
    return checkpoint
