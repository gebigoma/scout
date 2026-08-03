# Scout

[![tests](https://github.com/gebigoma/scout/actions/workflows/tests.yml/badge.svg)](https://github.com/gebigoma/scout/actions/workflows/tests.yml)

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
  evidence the criteria require the classifier to find. HN comments get
  their pipe-delimited header split from the body first, and the role is
  picked out of that header by content rather than by position — the
  "Company | Role | Location | Type" convention holds for barely half of a
  real thread, so position alone published locations, salary bands and "YC
  19" as job titles.
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
runs the pipeline every Monday morning on this machine. Fetching is part of
that weekly run, which costs recall on the two sources that are rolling
windows rather than archives — see
[`docs/daily-ingest-split.md`](./docs/daily-ingest-split.md) for the
proposed daily-ingest/weekly-digest split.

## Running it manually

```
python3 scripts/run_pipeline.py            # today's run
python3 scripts/run_pipeline.py --date 2026-08-03
python3 scripts/run_pipeline.py --force    # ignore checkpoints, redo every stage
```

## Tests

```
python3 -m unittest discover -s tests -t .          # whole suite
python3 -m unittest tests.test_dedupe -v            # one module
```

Stdlib `unittest`, no dependencies, no network: the two stages that call
Claude are tested against a stubbed CLI, the three sources against captured
response shapes, and `digest`'s commit/push path against a throwaway git repo
with a local bare remote. Each test redirects `paths.PROJECT_DIR` at a temp
directory, so the suite never writes to the real `data/`, `logs/`, or
`matches/`.

The tests deliberately pin the behaviours that were previously *bugs* — the
snippet extraction that keeps buried "part-time"/"contract" sentences, the
first-seen-date keying that makes `--force` idempotent, the score floor, the
scoped commit, and pushing a commit stranded by a failed push — so a
regression shows up as a named failing test rather than as a quiet week with
no matches. GitHub Actions runs the suite on every push and PR against Python
3.9 (the macOS system Python the scheduled job uses), 3.11, and 3.13.
