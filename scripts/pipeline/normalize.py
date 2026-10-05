"""Normalize stage: map each source's raw records into a common schema:
{source, title, company, url, posted_date, snippet, tags}."""
import html
import json
import re

from . import companies, logging_setup, manifest, paths, prefilter


def _strip_html(text: str) -> str:
    """Some sources (Greenhouse's `content` field) return HTML that's itself
    entity-escaped, so a tag only becomes literal `<...>` after unescaping -
    stripping first (the old order) leaves those tags untouched and they
    survive straight into the snippet. Unescape to a fixpoint first, capped
    since this is untrusted external text, then strip."""
    text = text or ""
    for _ in range(5):
        unescaped = html.unescape(text)
        if unescaped == text:
            break
        text = unescaped
    return re.sub("<[^<]+?>", " ", text)


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

# The first-TPM lane turns on different evidence than the fractional lane:
# whether the hire would *establish* the function, not what the employment
# terms are. Salvaging only EMPLOYMENT_TERMS sentences left classify judging
# founding-ness from 400 characters of "About Us" - the same false-negative
# mechanism the docstring below describes, one lane over. Employment terms
# stay in the set because these criteria still require full-time.
FIRST_TPM_TERMS = re.compile(
    EMPLOYMENT_TERMS.pattern + "|" + prefilter.EVIDENCE_TERM.pattern, re.IGNORECASE)

HEAD_CHARS = 400
MAX_SNIPPET = 1200


def _extract_snippet(text: str, head_chars: int = HEAD_CHARS,
                     terms: "re.Pattern" = EMPLOYMENT_TERMS) -> str:
    """Keep the opening of the description, then append any later sentences
    that mention `terms`.

    Plain head-truncation systematically defeats the criteria: We Work
    Remotely descriptions open with "Headquarters: ... About Us ..."
    boilerplate and state the employment type further down, so a fixed cut
    fed the model 500 chars of marketing copy and hid the very evidence it
    was asked to find. That produces false negatives that are invisible by
    construction - and makes a zero-match week untrustworthy.

    The result is *stitched*, not contiguous: anything that measures
    distance between two terms must use `match_text` instead. See
    `prefilter._match_text`."""
    text = (text or "").strip()
    head = text[:head_chars]
    tail = text[head_chars:]
    if not tail:
        return head

    hits = [s.strip() for s in re.split(r"(?<=[.!?\n])\s+", tail)
            if terms.search(s)]
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


# Delimiters people use when they don't use pipes. Spaced em/en dashes and
# middle dots only: a spaced hyphen reads as a separator but is prose punctuation
# at least as often ("Please normalize 4DWW - Four Day Work Week" is a thread
# comment, not a posting), and a bare "-" is a hyphen far more often still
# ("on-site", "Full-Stack", "150-250k").
ALT_DELIMITER = re.compile(r"\s[—–]\s|\s·\s")


def _hn_header_fields(header: str) -> tuple:
    """Split a header into fields, and say whether the company-first
    convention can be relied on.

    Pipes are the thread template's delimiter and the one case where field 0
    is reliably the company. The ~1 in 75 headers written with em dashes
    instead follow no convention at all - of the nine in the retained runs,
    "Ambito (ambito.io) — Founding AI Engineer — Remote" leads with the
    company while "Software Engineer — Remote (US Only)" leads with the role -
    so those are split, but positionally distrusted."""
    piped = [f.strip() for f in header.split("|")]
    if len([f for f in piped if f]) > 1:
        return piped, True
    alt = [f.strip() for f in ALT_DELIMITER.split(header)]
    if len([f for f in alt if f]) > 1:
        return alt, False
    return ([header.strip()] if header.strip() else []), False


def _pick_hn_role(fields: list, company_first: bool = True) -> str:
    """Choose the header field that names the role.

    The "Company | Role | Location | Type" convention is not one people
    actually follow. A single thread mixes in "Company | Location | Type",
    "Company | Salary | Location | Roles", and headers whose second field is
    a bare URL or a YC batch - so taking fields[1] published "REMOTE
    (worldwide)", "150-250k+ + equity" and "YC 19" as job titles.

    Without a pipe there is no company-first convention to skip past, so
    field 0 stays a candidate - it is where the role actually sits in headers
    like "Software Engineer — Remote (US Only)"."""
    candidates = [f for f in (fields[1:] if company_first else fields) if f]
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


def _pick_hn_company(fields: list, company_first: bool,
                     role_after_first: bool) -> str:
    """The company, or "" when the header doesn't reliably name one.

    Only the pipe-delimited form puts the company in a known position. The
    previous `fields[0][:80]` applied that assumption to every header, so a
    header with no pipes published the first 80 characters of the *whole
    header* as the company - a truncated copy of its own title, cut mid-word,
    em dashes and all, which reads as real data and is not. "" is the honest
    answer there, and digest already falls back to the source for it.

    A dash-delimited header gets field 0 only when the role was found
    somewhere *after* it, which is the same evidence the pipe form assumes
    rather than a second guess: "Ambito (ambito.io) — Founding AI Engineer —
    Remote" names its role in field 1, so field 0 is the company. "Software
    Engineer — Remote (US Only)" names its role in field 0, so there is no
    company to take, and "SpendAi — I ship procurement-grade agents..." is
    prose whose role lands in field 0 by fallback - both correctly yield ""
    rather than another plausible-looking fabrication."""
    if not fields:
        return ""
    if not company_first and not role_after_first:
        return ""
    return _one_line(fields[0])[:80]


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
        fields, company_first = _hn_header_fields(header)
        role = _one_line(
            _pick_hn_role(fields, company_first) if len(fields) > 1
            else clean[:100])
        # Whether the role came from somewhere other than field 0 is what
        # tells _pick_hn_company that field 0 is a company and not the role.
        role_after_first = bool(
            len(fields) > 1 and role and fields[0]
            and not _one_line(fields[0]).startswith(role[:40]))
        company = _pick_hn_company(fields, company_first, role_after_first)
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


def _ats_location(ats: str, job: dict) -> dict:
    """Location signal per ATS, kept as raw text/code - eligibility judgment
    (which countries are workable) lives in prefilter.py, not here.

    lever's `country` is an ISO-3166 alpha-2 code, the one reliably
    machine-checkable signal of the three ATSes - greenhouse and ashby only
    give free-text place names, checked against prefilter's country-name
    list instead."""
    if ats == "greenhouse":
        return {"location_text": (job.get("location") or {}).get("name", ""),
                "location_country_code": ""}
    if ats == "ashby":
        parts = [job.get("location", "")]
        parts += [sl.get("location", "") for sl in job.get("secondaryLocations", [])]
        return {"location_text": "; ".join(p for p in parts if p),
                "location_country_code": ""}
    if ats == "workable":
        # `location` is an object and `locations` a list of them; both carry
        # a `country_code`, which makes workable the second ATS after lever
        # with a machine-checkable signal rather than free text alone.
        places = [job.get("location") or {}]
        places += [p for p in (job.get("locations") or []) if isinstance(p, dict)]
        text = "; ".join(
            ", ".join(str(pl.get(k, "")) for k in ("city", "region", "country")
                      if pl.get(k))
            for pl in places if isinstance(pl, dict) and any(
                pl.get(k) for k in ("city", "region", "country")))
        codes = [str(pl.get("country_code", "")).strip().upper()
                 for pl in places if isinstance(pl, dict) and pl.get("country_code")]
        # Only a single unambiguous code is authoritative - a multi-country
        # posting with a non-US code among several would otherwise be dropped
        # on one of its locations, so those fall back to the text check.
        return {"location_text": text,
                "location_country_code": codes[0] if len(set(codes)) == 1 else ""}
    # lever
    categories = job.get("categories") or {}
    return {"location_text": categories.get("location", ""),
            "location_country_code": job.get("country", "") or ""}


def _normalize_ats(companies_results: dict) -> list:
    result = []
    for info in companies_results.values():
        if info["status"] != "success":
            continue
        company = info["company"]
        ats = companies.base_ats(company["ats"])
        portfolio = companies.is_portfolio_board(company["ats"])
        for job in info["jobs"]:
            if ats == "greenhouse":
                title, url = job.get("title", ""), job.get("absolute_url", "")
                posted = job.get("first_published", "")
                desc = job.get("content", "")
            elif ats == "ashby":
                title, url = job.get("title", ""), job.get("jobUrl", "")
                posted = job.get("publishedAt", "")
                desc = job.get("descriptionPlain", "")
            elif ats == "workable":
                title = job.get("title", "")
                url = job.get("url") or job.get("shortlink", "")
                posted = job.get("published_on") or job.get("created_at", "")
                # Both halves, because `description` is the summary blurb and
                # `full_description` the body - the foundation evidence this
                # lane turns on is in the second one.
                desc = " ".join(
                    x for x in (job.get("description", ""),
                                job.get("full_description", "")) if x)
            elif ats == "lever":
                title, url = job.get("text", ""), job.get("hostedUrl", "")
                posted = job.get("createdAt", "")
                desc = job.get("descriptionPlain", job.get("description", ""))
            else:
                # fetch_ats never marks an unsupported ats as a success, so
                # this is unreachable - but lever was this branch's silent
                # default once, and a wrong job shape is worse than none.
                continue
            clean = _one_line(_strip_html(desc))
            result.append({
                # The row's own ats, so a portfolio-board listing stays
                # recognisable as one ("ashby_portfolio:Pear-VC") downstream.
                "source": f"{company['ats']}:{company['token']}",
                "title": _one_line(title),
                # On a portfolio board each job names its own company; the
                # row's name is the VC's, and would put a whole portfolio's
                # jobs under one company.
                "company": (companies.portfolio_company(job, company["name"])
                            if portfolio else company["name"]),
                "url": url,
                "posted_date": str(posted),
                "snippet": _extract_snippet(clean, terms=FIRST_TPM_TERMS),
                # The whole description, kept contiguous so prefilter can
                # measure real distances over it. Dropped again by prefilter,
                # so it only ever lives in this one checkpoint.
                "match_text": clean,
                "tags": [],
                "lane": "first_tpm",
                **_ats_location(ats, job),
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
