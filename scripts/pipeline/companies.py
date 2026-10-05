"""Loads the hand-maintained VC-portfolio company list (data/companies.csv)
that drives the first-TPM lane's fetch stage. CSV, not YAML/JSON - the repo
is stdlib-only, and a flat few-hundred-row list is easiest to bulk-edit as a
spreadsheet."""
import csv

from . import paths

ATS_CHOICES = {"greenhouse", "ashby", "lever", "workable"}

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
