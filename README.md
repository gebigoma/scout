# Scout

A weekly workflow that finds fractional job listings (senior Technical Program
Management, Agentic AI Engineer, and similar roles) and writes the matches to
this repo.

## How it works

`scripts/run_pipeline.py` orchestrates six explicit, checkpointed stages:

```
fetch → normalize → dedupe → classify → score → digest
```

- **fetch** — pulls raw listings from public sources with no auth required
  ([RemoteOK](https://remoteok.com/api) JSON API,
  [We Work Remotely](https://weworkremotely.com/categories/remote-programming-jobs.rss) RSS,
  [HN "Who is hiring"](https://hn.algolia.com/api/v1/search) via Algolia),
  with retry+backoff per source.
- **normalize** — maps each source's fields into a common schema. Snippets
  keep the description's opening *plus* any later sentence mentioning
  employment terms: these sources bury "contract"/"part-time" under
  "About Us" boilerplate, and a plain head-truncation would hide the exact
  evidence the criteria require the classifier to find.
- **dedupe** — drops in-run duplicate URLs and anything already surfaced as
  a match on an *earlier* date. `data/seen.json` maps url → first-seen date;
  keying on the date is what makes `--force` safe to re-run (see below).
- **classify** — one structured Claude call judges every deduped listing
  against [`ROLE_CRITERIA.md`](./ROLE_CRITERIA.md) and returns genuine
  matches only. This is reasoning about fit, not keyword matching.
- **score** — one structured Claude call gives each match a 0-100 fit score
  + rationale, per the fit-score guide in `ROLE_CRITERIA.md`.
- **digest** — pure Python, no LLM: renders `matches/<date>.md`, updates
  `data/seen.json`, commits and pushes. Matches scoring below 35 go to a
  "Rejected on scoring" section instead of the main listing — the score
  stage disagreeing with the classify stage is a signal worth keeping, and
  those listings are deliberately *not* recorded as seen so they stay
  eligible if re-posted.

Each stage writes its output to `data/runs/<date>/<stage>.json` (gitignored
— scratch/operational, not the deliverable) and updates
`data/runs/<date>/manifest.json` with status/timing/counts. Re-running the
pipeline for the same date skips stages that already have a checkpoint and
resumes from the first one that doesn't — a failure at `classify` doesn't
force re-fetching everything. Checkpoints are written atomically (temp file
+ `os.replace`) so an interrupted run can't leave a truncated file that
wedges the date, and an unreadable checkpoint is treated as missing rather
than fatal. Structured logs go to `logs/<date>.jsonl` (gitignored). On
failure, a push notification goes out via ntfy.sh (topic set via the
`NTFY_TOPIC` env var in the launchd job, not in this repo).

A local `launchd` job (`~/Library/LaunchAgents/com.scout.weeklyfetch.plist`)
runs the pipeline every Monday morning on this machine.

## Running it manually

```
python3 scripts/run_pipeline.py            # today's run
python3 scripts/run_pipeline.py --date 2026-08-03
python3 scripts/run_pipeline.py --force    # ignore checkpoints, redo every stage
```
