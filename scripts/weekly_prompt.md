You are running the weekly automated update for this repo (a fractional-role
finder — see README.md and ROLE_CRITERIA.md for full context). Do the
following, in order, using today's date (YYYY-MM-DD, local) everywhere a date
is needed:

1. Run `python3 scripts/fetch_listings.py`. This fetches fresh listings,
   drops anything already in `data/seen.json` (roles already surfaced in a
   past week), and writes what's left to `data/raw/<today>.json`.
2. Read `data/raw/<today>.json` and `ROLE_CRITERIA.md`.
3. Apply the criteria in `ROLE_CRITERIA.md` strictly — a listing only counts
   as a match if it explicitly states fractional/contract/part-time/interim
   terms AND fits one of the target roles (senior Technical Program
   Management, or Agentic AI Engineer / AI engineering leadership, or a close
   adjacent — use judgment per the criteria doc). Do not loosen the bar to
   force results. If a role category has zero matches this week, say so
   explicitly in the output file rather than omitting that section — that's
   intentional and expected some weeks.
4. Write `matches/<today>.md`, following the same structure as
   `matches/2026-08-01.md` (source list + listing count at the top, one
   section per role category, each match with title, company/source, link,
   and a one-line rationale for why it fits).
5. Update `data/seen.json`: add the URL of every match written in step 4 to
   the `matched_urls` array (keep existing entries, just append the new
   ones — don't remove anything).
6. Run:
   ```
   git add matches/<today>.md data/seen.json
   git commit -m "Weekly matches: <today>"
   git push
   ```
7. Finish with a short plain-text summary (2-3 sentences) of what was found —
   this is the only output that needs to be human-readable on its own, since
   it'll be captured to a log file.
