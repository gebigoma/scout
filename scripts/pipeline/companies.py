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
            # Blank headcount means "unknown", never 0 - a 0 would score as
            # badly out-of-band rather than as missing data.
            headcount_raw = (row.get("headcount") or "").strip()
            companies.append({
                "name": row["name"].strip(),
                "ats": row["ats"].strip(),
                "token": row["token"].strip(),
                "headcount": int(headcount_raw) if headcount_raw else None,
                "source": (row.get("source") or "").strip(),
            })
        return companies
