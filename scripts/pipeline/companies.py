"""Loads the hand-maintained VC-portfolio company list (data/companies.csv)
that drives the first-TPM lane's fetch stage. CSV, not YAML/JSON - the repo
is stdlib-only, and a flat few-hundred-row list is easiest to bulk-edit as a
spreadsheet."""
import csv

from . import paths

# A portfolio board is one ATS board carrying jobs for many companies - a VC's
# talent board. It fetches exactly like its base ATS, but each job's company
# comes from the job rather than from the companies.csv row: Pear VC's Ashby
# board held 99 jobs across 44 portfolio companies on 2026-10-05, each naming
# its company in Ashby's `department` field. Read as an ordinary row, all 99
# would be published as one company. Only Ashby is supported, because it is
# the only portfolio board whose per-job company field has been checked
# against a live payload.
PORTFOLIO_BOARDS = {"ashby_portfolio": "ashby"}

ATS_CHOICES = {"greenhouse", "ashby", "lever", "workable"} | set(PORTFOLIO_BOARDS)


def base_ats(ats: str) -> str:
    """The ATS whose API and job shape a row uses - "ashby" for an Ashby
    portfolio board, the value itself for everything else."""
    return PORTFOLIO_BOARDS.get(ats, ats)


def is_portfolio_board(ats: str) -> bool:
    return ats in PORTFOLIO_BOARDS


def portfolio_company(job: dict, board_name: str) -> str:
    """The company a portfolio-board job belongs to. Ashby's `department` is
    the portfolio company on these boards (and `team` repeats it); the board's
    own name is the fallback, so a job with neither is never left nameless."""
    for field in ("department", "team"):
        value = " ".join(str(job.get(field) or "").split())
        if value:
            return value
    return board_name

# Why the first-TPM lane produced no companies, which is not the same question
# as why it produced no matches. load_companies returns [] for a missing file
# as readily as for an empty one, so a fresh clone ran the lane over zero
# companies and published "No matches this week" - indistinguishable from an
# honest quiet week, and from a week where every board token 404'd. Each of
# those needs a different fix, so each gets a different name.
ABSENT = "absent"        # no data/companies.csv at all - the lane cannot run
EMPTY = "empty"          # the file is there but carries no data rows
POPULATED = "populated"  # rows to fetch; whether they yield anything is fetch_ats's answer


def list_state(path=None) -> str:
    path = path or paths.companies_csv_path()
    if not path.exists():
        return ABSENT
    return POPULATED if load_companies(path) else EMPTY


def load_companies(path=None) -> list:
    path = path or paths.companies_csv_path()
    if not path.exists():
        return []
    with path.open(newline="") as f:
        companies = []
        for row in csv.DictReader(f):
            # `headcount` was dropped from this schema on 2026-10-05. It was
            # populated for 0 of 104 rows for its whole life, while the fit
            # guide lowered the score for a missing one - so every listing
            # paid for a column that never held anything. An extra column in
            # a hand-maintained CSV is ignored rather than rejected, so an
            # un-updated copy of the file still loads.
            companies.append({
                "name": row["name"].strip(),
                "ats": row["ats"].strip(),
                "token": row["token"].strip(),
                "source": (row.get("source") or "").strip(),
            })
        return companies
