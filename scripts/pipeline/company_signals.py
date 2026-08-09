"""Company-signal stage (no LLM): aggregates the raw fetch_ats checkpoint
into a per-company engineering-hiring concentration score, to surface
companies likely to need a first TPM soon - before the req exists, not
after. Produces a watchlist (signals/<date>.md), not matches; it is
deliberately not lane-keyed and not fed through data/seen.json.

Reads the RAW fetch_ats checkpoint directly rather than normalize's output:
_normalize_ats (normalize.py) drops department, which is needed here as a
secondary classification hint. Department is a weak signal on its own -
"engineering" never appears as an Ashby/Greenhouse department value across
the portfolio; companies invent their own cost-center taxonomies instead
(R&D, S&M, COGs, G&A) - so title regex does the primary classification and
department only adds true positives department-only would otherwise miss,
via a single OR expression. It is never queried as a standalone signal."""
import os
import re
import statistics
from datetime import datetime, timezone

from . import logging_setup, manifest, paths

# Primary classifier. Word-boundary on \bml\b for the same reason
# prefilter.py word-bounds \btpm\b - unbounded "ml" matches inside unrelated
# words (e.g. "html") constantly.
ENG_TITLE = re.compile(
    r"engineer|developer|infrastructure|platform|backend|frontend|full[- ]stack|"
    r"\bsre\b|devops|machine learning|\bml\b|security|architect|scientist",
    re.IGNORECASE,
)

# Secondary OR-hint only - always evaluated together with ENG_TITLE in the
# same expression, never read by itself. Covers the cost-center labels
# ("R&D", "Research & Development") that are the dominant department shape
# on Ashby, which is 69 of 104 portfolio companies.
ENG_DEPT = re.compile(r"\bR&D\b|research\s*&\s*development|engineering", re.IGNORECASE)

# Grounded in the full 104-company survey (2026-08-08): a 90-day window and
# an 8-req floor yield 23 qualifying companies with a meaningful spread.
# Pure concentration with no floor puts 1-2 req companies at a false 100%.
WINDOW_DAYS_DEFAULT = int(os.environ.get("SCOUT_SIGNAL_WINDOW_DAYS", "90"))
MIN_ENG_DEFAULT = int(os.environ.get("SCOUT_SIGNAL_MIN_ENG", "8"))


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _parse_date(raw):
    """Returns an aware datetime, or None if raw is empty/unparseable. Never
    raises - a single bad date must cost one req, not the whole stage."""
    if not raw:
        return None
    s = str(raw).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _extract_req(job: dict, ats: str) -> dict:
    """Per-ATS field extraction from a RAW job dict, mirroring the shapes
    _normalize_ats (normalize.py) uses but keeping department."""
    if ats == "greenhouse":
        title = job.get("title", "")
        url = job.get("absolute_url", "")
        posted_raw = job.get("first_published", "")
        dept = " ".join(d.get("name", "") for d in (job.get("departments") or []))
    elif ats == "ashby":
        title = job.get("title", "")
        url = job.get("jobUrl", "")
        posted_raw = job.get("publishedAt", "")
        dept = " ".join(filter(None, [job.get("department", ""), job.get("team", "")]))
    else:  # lever
        title = job.get("text", "")
        url = job.get("hostedUrl", "")
        # Lever's createdAt is epoch milliseconds, not ISO - the one ATS
        # shape that differs. Guarded rather than trusted: only 4 lever rows
        # exist in the portfolio and one of them is the single 404, so this
        # path is low-traffic but must not crash the stage.
        created = job.get("createdAt")
        try:
            posted_raw = datetime.fromtimestamp(
                float(created) / 1000, tz=timezone.utc).isoformat()
        except (TypeError, ValueError, OSError):
            posted_raw = ""
        dept = (job.get("categories") or {}).get("team", "")

    title = _normalize_ws(title)
    dept = _normalize_ws(dept)
    is_eng = bool(ENG_TITLE.search(title) or ENG_DEPT.search(dept))
    return {"url": url, "title": title, "posted_date": posted_raw,
            "department": dept, "is_eng": is_eng}


def _score_company(reqs: list, window_days: int, now=None) -> dict:
    now = now or datetime.now(timezone.utc)
    eng_reqs = [r for r in reqs if r["is_eng"]]

    ages, undated, recent_eng = [], 0, 0
    for r in eng_reqs:
        dt = _parse_date(r["posted_date"])
        if dt is None:
            undated += 1
            continue
        age_days = (now - dt).total_seconds() / 86400
        ages.append(age_days)
        if age_days <= window_days:
            recent_eng += 1

    total = len(reqs)
    return {
        "total": total,
        "eng": len(eng_reqs),
        "recent_eng": recent_eng,
        "concentration": round(recent_eng / total, 4) if total else 0.0,
        "median_age_days": round(statistics.median(ages), 1) if ages else None,
        "undated": undated,
    }


def _render_markdown(checkpoint: dict, qualified: list, run_date: str) -> str:
    lines = [f"# Company Signals — {run_date}", ""]
    lines.append(
        f"Engineering-hiring concentration over the trailing "
        f"{checkpoint['window_days']} days, across "
        f"{checkpoint['total_companies']} VC-portfolio companies. Ranks "
        f"companies whose *currently open* reqs skew toward engineering - a "
        f"leading indicator that a first-TPM req is coming, not a lane "
        f"match. This is a watchlist, not matches; nothing here is recorded "
        f"as seen. Threshold: concentration = recent_eng / total_open_reqs, "
        f"requiring >= {checkpoint['min_eng']} engineering reqs posted in "
        f"the window."
    )
    lines.append("")
    lines.append(
        f"{checkpoint['surveyed']} companies surveyed successfully "
        f"({checkpoint['zero_req_companies']} with zero open reqs); "
        f"{checkpoint['not_on_ats']} not reachable on their configured ATS "
        f"(404); {checkpoint['failed_companies']} failed to fetch this run."
    )
    lines.append("")

    if not qualified:
        lines.append(
            f"0 of {checkpoint['surveyed']} companies surveyed cleared the threshold."
        )
        return "\n".join(lines).rstrip() + "\n"

    lines.append(
        f"{len(qualified)} of {checkpoint['surveyed']} companies surveyed "
        f"cleared the threshold, ranked by concentration:"
    )
    lines.append("")
    lines.append("| Company | Concentration | Recent eng / total | Median age (d) | "
                 "Headcount | Last Form D | Fund |")
    lines.append("|---|---|---|---|---|---|---|")
    for c in qualified:
        t = c["totals"]
        headcount = c["headcount"]
        funding = c["funding"] or {}
        lines.append(
            f"| {c['name']} | {t['concentration'] * 100:.0f}% | "
            f"{t['recent_eng']}/{t['total']} | "
            f"{t['median_age_days'] if t['median_age_days'] is not None else ''} | "
            f"{headcount if headcount is not None else ''} | "
            f"{funding.get('last_form_d', '')} | {c['source']} |"
        )
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def run(run_date: str, fetch_ats_checkpoint: dict = None,
       window_days: int = None, min_eng: int = None) -> dict:
    logger = logging_setup.get_logger(run_date)
    manifest.stage_started(run_date, "company_signals")
    window_days = WINDOW_DAYS_DEFAULT if window_days is None else window_days
    min_eng = MIN_ENG_DEFAULT if min_eng is None else min_eng

    # first_tpm lane inactive - fetch_ats_cp is None. Skip cleanly, exactly
    # as normalize.py does for the same input, and write no artifact: there
    # is nothing to report, unlike the "ran but found zero" case below.
    if fetch_ats_checkpoint is None:
        checkpoint = {
            "companies": [], "window_days": window_days, "min_eng": min_eng,
            "total_companies": 0, "surveyed": 0, "zero_req_companies": 0,
            "not_on_ats": 0, "failed_companies": 0, "qualified": 0,
        }
        paths.atomic_write_json(paths.checkpoint_path(run_date, "company_signals"), checkpoint)
        logging_setup.log(logger, "company_signals", "first_tpm lane inactive; skipped")
        manifest.stage_succeeded(run_date, "company_signals", skipped=True)
        return checkpoint

    try:
        companies_out = []
        zero_req = not_on_ats = failed = 0
        for info in fetch_ats_checkpoint["companies"].values():
            status = info["status"]
            if status == "skipped":  # 404 - not on this ATS, expected/normal
                not_on_ats += 1
                continue
            if status != "success":
                failed += 1
                continue
            company = info["company"]
            reqs = [_extract_req(job, company["ats"]) for job in info["jobs"]]
            if not reqs:
                zero_req += 1
                continue
            companies_out.append({
                "name": company["name"], "ats": company["ats"], "token": company["token"],
                "headcount": company.get("headcount"), "source": company.get("source", ""),
                "totals": _score_company(reqs, window_days),
                "reqs": reqs,
                # Populated by a future funding.py enrichment pass - reserved
                # here so that pass needs no checkpoint restructuring later.
                "funding": None,
            })

        surveyed = len(companies_out) + zero_req
        qualified = sorted(
            (c for c in companies_out if c["totals"]["recent_eng"] >= min_eng),
            key=lambda c: c["totals"]["concentration"], reverse=True,
        )

        checkpoint = {
            "companies": companies_out,
            "window_days": window_days, "min_eng": min_eng,
            "total_companies": len(fetch_ats_checkpoint["companies"]),
            "surveyed": surveyed,
            "zero_req_companies": zero_req,
            "not_on_ats": not_on_ats,
            "failed_companies": failed,
            "qualified": len(qualified),
        }
        paths.atomic_write_json(paths.checkpoint_path(run_date, "company_signals"), checkpoint)

        # Written even when qualified is empty (or surveyed is 0) - a run
        # against a lane-active-but-empty checkpoint must report zero loudly
        # rather than leave no trace. See digest's two reverted 2026-08-07
        # empties for why a silent empty artifact is the failure mode here.
        markdown = _render_markdown(checkpoint, qualified, run_date)
        paths.signals_path(run_date).parent.mkdir(parents=True, exist_ok=True)
        paths.signals_path(run_date).write_text(markdown)
    except Exception as e:
        manifest.stage_failed(run_date, "company_signals", str(e))
        raise

    logging_setup.log(logger, "company_signals", "scored companies",
                       surveyed=surveyed, qualified=len(qualified), zero_req=zero_req,
                       not_on_ats=not_on_ats, failed=failed,
                       window_days=window_days, min_eng=min_eng)
    manifest.stage_succeeded(run_date, "company_signals", surveyed=surveyed,
                              qualified=len(qualified), zero_req_companies=zero_req,
                              not_on_ats=not_on_ats, failed_companies=failed,
                              window_days=window_days, min_eng=min_eng)
    return checkpoint
