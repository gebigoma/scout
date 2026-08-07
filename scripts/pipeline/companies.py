"""Loads the hand-maintained VC-portfolio company list (data/companies.csv)
that drives the first-TPM lane's fetch stage. CSV, not YAML/JSON - the repo
is stdlib-only, and a flat few-hundred-row list is easiest to bulk-edit as a
spreadsheet."""
import csv

from . import paths

ATS_CHOICES = {"greenhouse", "ashby", "lever"}


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
