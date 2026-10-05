#!/usr/bin/env python3
"""Mine candidate rows for data/companies.csv out of HN "Who is hiring" posts
the pipeline has already fetched, and write them to a review queue.

    python3 scripts/discover_companies.py
    python3 scripts/discover_companies.py --dismiss greenhouse:acmeconsulting --reason agency

Why HN, and why this works when name-based discovery doesn't: an ATS board
token is the hard part of adding a company (CLAUDE.md: "board tokens are
guesswork"), and a Who-is-hiring comment that links its own Ashby, Greenhouse,
Lever or Workable board carries the token *in the URL*. Those links are the
most common hosts in the thread - more than LinkedIn, more than GitHub - and
the companies posting them have self-selected as hiring right now, mostly at
the stage the first_tpm lane is for.

Offline by construction: reads data/runs/*/fetch.json and data/companies.csv,
nothing else, so it can be checked against every retained run before its
output is trusted. Things that would need the network are counted rather than
followed - greenhouse's grnh.se short links hide the token behind a redirect,
and the number left unresolved is printed so the gap is visible, not silent.

Writes data/company_candidates.csv - a review queue, never an append to
companies.csv. That file is hand-maintained by design, and these tokens are
extracted from free text: some will 404, some boards belong to agencies
hiring for clients, some companies are wrong for this lane. A person decides.
The queue is regenerated wholesale on every run, so move a row into
companies.csv rather than editing it here. It names companies, so it is
gitignored and in hygiene.NEVER_COMMIT, the same as companies.csv.

The leading columns are companies.csv's own (name, ats, token, source) so any
other discovery source - EDGAR Form D, a resolved short link - can write rows
to the same queue later without a format change.
"""
import argparse
import collections
import csv
import io
import json
import re
import sys
from datetime import date

from pipeline import companies, normalize, paths

SOURCE = "hn-who-is-hiring"

# One pattern per supported ATS, matched against comment text *after*
# normalize._strip_html: HN double-escapes entities, so a raw-text search finds
# "https:&#x2F;&#x2F;..." and matches nothing at all. That is not hypothetical -
# it is how the first version of this idea returned zero tokens from 717
# comments that held 339 ATS links.
#
# EU-hosted boards (job-boards.eu.greenhouse.io, jobs.eu.lever.co) are left out
# on purpose: fetch_ats calls the US API hosts, where an EU token 404s and is
# skipped as "not on this ATS" - a candidate that cannot work.
ATS_PATTERNS = (
    ("ashby", re.compile(r"\bjobs\.ashbyhq\.com/([A-Za-z0-9._%-]+)")),
    ("greenhouse", re.compile(r"\b(?:job-)?boards\.greenhouse\.io/([A-Za-z0-9_-]+)")),
    # The embed form carries the token in the query string instead.
    ("greenhouse", re.compile(
        r"\b(?:job-)?boards\.greenhouse\.io/embed/[a-z_]+\?(?:[^\s\"'<>]*&)?for=([A-Za-z0-9_-]+)")),
    ("lever", re.compile(r"\bjobs\.lever\.co/([A-Za-z0-9_-]+)")),
    ("workable", re.compile(r"\bapply\.workable\.com/([A-Za-z0-9_-]+)")),
    # Legacy workable boards live on a per-company subdomain.
    ("workable", re.compile(r"\b(?!apply\.|www\.|jobs\.)([a-z0-9-]+)\.workable\.com\b")),
)

# Path segments that sit where a token would but are routing, not a company:
# workable's "/j/<shortcode>" links carry no token at all.
NOT_A_TOKEN = frozenset({"embed", "j", "api", "v1", "jobs", "careers", "job_board",
                         "job_app", "widget", "accounts"})

SHORTLINK = re.compile(r"\bgrnh\.se/([A-Za-z0-9]+)")

# A board that hires on someone else's behalf - a VC's portfolio board, a
# staffing agency, a consultancy. fetch_ats tags every job on a board with the
# row's company name, so adding one of these labels a whole portfolio's jobs
# as one company. Real example from the retained runs: a "Phaselaw" comment
# linking jobs.ashbyhq.com/Pear-VC, which is Pear VC's portfolio board. A note,
# not a filter - Black Canyon Consulting is a company with its own board, and
# a person can tell the two apart where a pattern can't. "vc" needs a
# separator around it; the longer words are distinctive enough as substrings.
OTHERS_BOARD = re.compile(
    r"(?:^|[-_.])vc(?:$|[-_.])|ventures?|capital|talent|recruit|staffing|consulting|agency",
    re.IGNORECASE)

# What an HN header puts around a company name: "(YC W22)", "( https://... )",
# "(Sequoia-backed, Series B ...", and a role after a dash.
_PAREN = re.compile(r"\s*\([^)]*\)")
_NAME_CUT = re.compile(r"\s*\(|\s+[-—–|]\s+")


def _clean_name(name: str, token: str) -> str:
    """The header's company field minus its decorations, or the token.

    A convenience for the reviewer only - the token is what fetch_ats uses and
    is always shown beside it, which is what makes a wrong name ("NYC", from a
    header that led with its location) harmless rather than misleading."""
    name = _PAREN.sub("", name or "")
    name = _NAME_CUT.split(name, maxsplit=1)[0]  # an unclosed "(" cut off at 80 chars
    name = re.sub(r"\s+", " ", name).strip(" ,;:-")
    return name or token

CANDIDATE_COLUMNS = ("name", "ats", "token", "source", "mentions",
                     "first_seen", "last_seen", "evidence_url", "note")


def _clean_token(raw: str) -> str:
    # Prose punctuation sticks to the end of a URL ("apply at
    # jobs.ashbyhq.com/acme."), and a token is never only digits.
    token = raw.strip(".-_%")
    if len(token) < 2 or token.isdigit() or token.lower() in NOT_A_TOKEN:
        return ""
    return token


# The ats values a candidate can carry - the four this script extracts from
# HN links, so the four a dismissal can name.
CANDIDATE_ATS = ("ashby", "greenhouse", "lever", "workable")
DISMISSED_COLUMNS = ("ats", "token", "reason", "dismissed_on")


def load_dismissed():
    """{(ats, token_lower)} a person has rejected, plus a list of problems.

    The queue is regenerated wholesale, so without this a board judged and
    rejected - an agency, a wrong-stage company - comes back on every run and
    has to be judged again. Hand-maintained like companies.csv. A file that
    can't be read is reported, not silently treated as empty: a quietly
    ignored dismissed list would just make rejected boards reappear with no
    hint why."""
    path = paths.company_candidates_dismissed_path()
    if not path.exists():
        return set(), []
    try:
        with path.open(newline="") as f:
            reader = csv.DictReader(f)
            missing = {"ats", "token"} - set(reader.fieldnames or ())
            if missing:
                return set(), [f"{path.name} has no {'/'.join(sorted(missing))} column "
                               f"- nothing in it was applied"]
            rows = list(reader)
    except (OSError, UnicodeDecodeError, csv.Error) as e:
        return set(), [f"{path.name} could not be read ({e}) - nothing in it was applied"]
    dismissed, problems = set(), []
    for n, row in enumerate(rows, start=2):
        ats = (row.get("ats") or "").strip().lower()
        token = (row.get("token") or "").strip()
        if ats in CANDIDATE_ATS and token:
            dismissed.add((ats, token.lower()))
        elif ats or token:
            problems.append(f"{path.name} line {n}: unrecognised ats {ats!r} or empty token "
                            f"- skipped")
    return dismissed, problems


def parse_board(spec: str):
    """"greenhouse:acmeconsulting" -> ("greenhouse", "acmeconsulting"), the
    same ats:token pair the queue's own columns give, or None."""
    ats, sep, token = (spec or "").partition(":")
    ats, token = ats.strip().lower(), token.strip()
    if not sep or ats not in CANDIDATE_ATS or not token:
        return None
    return ats, token


def dismiss(boards: list, reason: str = "") -> list:
    """Append boards to the dismissed list, skipping ones already on it.
    Returns the boards actually added."""
    path = paths.company_candidates_dismissed_path()
    already, _ = load_dismissed()
    added = []
    for ats, token in boards:
        if (ats, token.lower()) in already:
            continue
        already.add((ats, token.lower()))
        added.append((ats, token))
    if not added:
        return added
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=DISMISSED_COLUMNS, lineterminator="\n")
        if write_header:
            writer.writeheader()
        today = date.today().isoformat()
        for ats, token in added:
            writer.writerow({"ats": ats, "token": token, "reason": reason,
                             "dismissed_on": today})
    return added


def extract_tokens(text: str) -> list:
    """(ats, token) pairs linked from one comment, in order, deduplicated."""
    found = []
    for ats, pattern in ATS_PATTERNS:
        for m in pattern.finditer(text):
            token = _clean_token(m.group(1))
            pair = (ats, token)
            if token and (ats, token.lower()) not in {(a, t.lower()) for a, t in found}:
                found.append(pair)
    return found


def _hn_items():
    """(run_date, item) for every HN comment in every readable fetch checkpoint,
    oldest run first so first_seen is the earliest date."""
    for path in sorted(paths.runs_dir().glob("*/fetch.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue  # an unreadable checkpoint is missing, not fatal
        sources = data.get("sources") if isinstance(data, dict) else None
        hn = (sources or {}).get("hn_whoishiring") or {}
        for item in hn.get("items") or []:
            if isinstance(item, dict):
                yield path.parent.name, item


def _listed():
    """{(ats, token_lower)} and {token_lower: ats} for companies.csv - the
    second so a token listed under a *different* ATS can be flagged rather
    than silently passed over."""
    rows = companies.load_companies()
    exact = {(r["ats"], r["token"].lower()) for r in rows}
    by_token = {r["token"].lower(): r["ats"] for r in rows}
    return exact, by_token


def collect():
    exact, by_token = _listed()
    dismissed, dismissed_problems = load_dismissed()
    dismissed_hits = set()
    candidates = {}
    comment_ids = set()
    runs = set()
    shortlinks = set()
    already_listed = set()

    for run_date, item in _hn_items():
        runs.add(run_date)
        item_id = item.get("id")
        text = normalize._strip_html(item.get("text", "") or "")
        shortlinks.update(SHORTLINK.findall(text))
        pairs = extract_tokens(text)
        if item_id in comment_ids:
            # The monthly thread is refetched every week; the same comment in
            # a later run moves last_seen and nothing else.
            for ats, token in pairs:
                c = candidates.get((ats, token.lower()))
                if c:
                    c["last_seen"] = max(c["last_seen"], run_date)
            continue
        comment_ids.add(item_id)

        # The header's company name only describes the board when the comment
        # links exactly one - a recruiter's comment listing three clients'
        # boards has one header and three companies.
        header_name = ""
        if len(pairs) == 1:
            header_name = normalize._normalize_hn([item])[0]["company"]

        for ats, token in pairs:
            key = (ats, token.lower())
            if key in exact:
                already_listed.add(key)
                continue
            if key in dismissed:
                dismissed_hits.add(key)
                continue
            c = candidates.get(key)
            if c is None:
                listed_on = by_token.get(token.lower())
                notes = []
                if listed_on:
                    # Same token, different ATS: the company may have moved,
                    # which makes its existing companies.csv row a silent 404.
                    notes.append(f"token already listed under {listed_on} - may have moved ATS")
                if OTHERS_BOARD.search(token):
                    notes.append("may be a VC/agency board hiring for others - check before adding")
                c = candidates[key] = {
                    "name": _clean_name(header_name, token),
                    "ats": ats,
                    "token": token,
                    "source": SOURCE,
                    "mentions": 0,
                    "first_seen": run_date,
                    "last_seen": run_date,
                    "evidence_url": f"https://news.ycombinator.com/item?id={item_id}",
                    "note": "; ".join(notes),
                }
            c["mentions"] += 1
            c["first_seen"] = min(c["first_seen"], run_date)
            c["last_seen"] = max(c["last_seen"], run_date)

    ordered = sorted(candidates.values(),
                     key=lambda c: (-c["mentions"], c["name"].lower(), c["ats"]))
    return {
        "candidates": ordered,
        "runs": sorted(runs),
        "comments": len(comment_ids),
        "already_listed": len(already_listed),
        "dismissed": len(dismissed_hits),
        "dismissed_problems": dismissed_problems,
        "unresolved_shortlinks": len(shortlinks),
        "company_list_state": companies.list_state(),
    }


def render_csv(candidates: list) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=CANDIDATE_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for c in candidates:
        writer.writerow({k: c[k] for k in CANDIDATE_COLUMNS})
    return out.getvalue()


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Mine companies.csv candidates from HN into a review queue.")
    parser.add_argument(
        "--dismiss", action="append", default=[], metavar="ATS:TOKEN",
        help="reject a board so the queue stops offering it, e.g. "
             "greenhouse:acmeconsulting - the queue's own ats and token columns "
             "(repeatable)")
    parser.add_argument("--reason", default="",
                        help="why, recorded beside each --dismiss (e.g. agency)")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    if args.dismiss:
        boards = [parse_board(spec) for spec in args.dismiss]
        bad = [spec for spec, b in zip(args.dismiss, boards) if b is None]
        if bad:
            # Nothing is written if any spec is wrong - a half-applied batch
            # leaves the list in a state nobody asked for.
            print(f"Not a board: {', '.join(bad)}. Use ATS:TOKEN, where ATS is one "
                  f"of {', '.join(CANDIDATE_ATS)} - the queue's ats and token "
                  f"columns.", file=sys.stderr)
            return 2
        added = dismiss(boards, args.reason)
        skipped = len(boards) - len(added)
        for ats, token in added:
            print(f"Dismissed {ats}:{token}" + (f" ({args.reason})" if args.reason else ""))
        if skipped:
            print(f"{skipped} board(s) were already dismissed.")

    result = collect()
    path = paths.company_candidates_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_csv(result["candidates"]))

    by_ats = collections.Counter(c["ats"] for c in result["candidates"])
    print(f"Scanned {result['comments']} HN comments across "
          f"{len(result['runs'])} retained run(s).")
    if not result["runs"]:
        print("No fetch checkpoints found under data/runs/ - nothing to mine.")
    for ats in sorted(by_ats):
        print(f"  {ats:<11} {by_ats[ats]:>3} candidate(s)")
    print(f"  {'total':<11} {len(result['candidates']):>3}")
    if result["dismissed"]:
        print(f"{result['dismissed']} dismissed board(s) left out "
              f"({paths.company_candidates_dismissed_path().name}).")
    for problem in result["dismissed_problems"]:
        print(f"Warning: {problem}")
    if result["company_list_state"] == companies.POPULATED:
        print(f"{result['already_listed']} linked board(s) already in companies.csv, left out.")
    else:
        print(f"companies.csv is {result['company_list_state']} - nothing was excluded "
              f"as already listed.")
    noted = sum(1 for c in result["candidates"] if c["note"])
    if noted:
        print(f"{noted} candidate(s) carry a note (moved ATS, or a board that may "
              f"hire for others) - see the note column.")
    if result["unresolved_shortlinks"]:
        print(f"{result['unresolved_shortlinks']} grnh.se short link(s) not resolved: "
              f"the token is behind a redirect and this script makes no network calls.")
    print(f"Unverified: none of these tokens has been fetched. Review before moving "
          f"rows into companies.csv.")
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
