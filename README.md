# Scout

A weekly workflow that finds fractional job listings (senior Technical Program
Management, Agentic AI Engineer, and similar roles) and writes the matches to
this repo.

## How it works

1. `scripts/fetch_listings.py` pulls raw listings from a few public sources
   with no auth required:
   - [RemoteOK](https://remoteok.com/api) (JSON API)
   - [We Work Remotely](https://weworkremotely.com/categories/remote-programming-jobs.rss) (RSS)
   - [Hacker News "Who is hiring"](https://hn.algolia.com/api/v1/search) (Algolia API)

   It writes normalized results to `data/raw/<date>.json` (gitignored — this
   is scratch input, not the deliverable).

2. Claude reads the raw listings against the criteria in
   [`ROLE_CRITERIA.md`](./ROLE_CRITERIA.md) and picks out genuine matches —
   this is a judgment call, not a keyword filter, since job titles and
   descriptions are inconsistent across sources. Matches (with a short
   rationale each) are written to `matches/<date>.md`.

3. A weekly scheduled run does steps 1–2 automatically and commits the
   result.

## Running it manually

```
python3 scripts/fetch_listings.py
```

Then ask Claude to review `data/raw/<date>.json` against `ROLE_CRITERIA.md`
and write `matches/<date>.md`.
