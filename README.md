# Scout

[![tests](https://github.com/gebigoma/scout/actions/workflows/tests.yml/badge.svg)](https://github.com/gebigoma/scout/actions/workflows/tests.yml)

A weekly workflow that finds job listings across two lanes and writes the
matches to this repo:

- **fractional** — senior Technical Program Management, Agentic AI Engineer,
  and similar roles, explicitly fractional/contract/part-time/interim, from
  public job boards.
- **first_tpm** — full-time roles where the hire would be the first (or
  near-first) Technical Program Manager at a startup, sourced directly from
  VC-portfolio companies' applicant-tracking-system endpoints.

Both lanes run by default every week; see [Lanes](#lanes) below to run just
one.

## How it works

`scripts/run_pipeline.py` orchestrates checkpointed stages:

```
fetch ─────┐
           ├→ normalize → prefilter → dedupe → classify → score → digest
fetch_ats ─┘
```

`fetch` and `fetch_ats` run in parallel lanes but not parallel processes —
each is just skipped entirely if its lane isn't active. Every other stage
sees both lanes' listings together, distinguishing them by a `lane` field.

- **fetch** (fractional lane) — pulls raw listings from public sources with
  no auth required ([RemoteOK](https://remoteok.com/api) JSON API,
  [We Work Remotely](https://weworkremotely.com/categories/remote-programming-jobs.rss) RSS,
  [HN "Who is hiring"](https://hn.algolia.com/api/v1/search) via Algolia),
  with retry+backoff per source.
- **fetch_ats** (first_tpm lane) — one HTTP call per company in
  `data/companies.csv` (hand-maintained — see [Lanes](#lanes)) against
  Greenhouse (`?content=true` is required; the bare endpoint has no
  description field at all), Ashby, or Lever, whichever ATS that company
  uses. A 404 means the company isn't on that ATS and is skipped, not
  treated as a failure — board tokens are often guesswork.
- **normalize** — maps each source's fields into a common schema and tags
  every listing with its lane. Snippets keep the description's opening
  *plus* any later sentence mentioning employment terms: these sources bury
  "contract"/"part-time" under "About Us" boilerplate, and a plain
  head-truncation would hide the exact evidence the criteria require the
  classifier to find. HN comments get their pipe-delimited header split
  from the body first, and the role is picked out of that header by content
  rather than by position — the "Company | Role | Location | Type"
  convention holds for barely half of a real thread, so position alone
  published locations, salary bands and "YC 19" as job titles.
- **prefilter** (first_tpm lane only) — a loose, mechanical phrase filter
  that cuts the ATS candidate set before it reaches classify: an exact
  thesis phrase ("first technical program manager", …) always passes, and a
  role term near a foundation term also passes for classify to judge for
  real. Word-boundary matching on "tpm" is required, since this lane sources
  heavily from infra/security companies where "TPM" means Trusted Platform
  Module. Fractional-lane listings pass through untouched.
- **dedupe** — drops in-run duplicate URLs and anything already surfaced as
  a match on an *earlier* date. `data/seen.json` maps url → first-seen date;
  keying on the date is what makes `--force` safe to re-run (see below).
- **classify** — structured Claude calls, chunked (default 50 listings per
  call, `SCOUT_CLASSIFY_CHUNK_SIZE` to override), judging every deduped
  listing against its lane's criteria file
  ([`ROLE_CRITERIA.md`](./ROLE_CRITERIA.md) or
  [`ROLE_CRITERIA_FIRST_TPM.md`](./ROLE_CRITERIA_FIRST_TPM.md)). Every
  listing gets an explicit match/no-match verdict — not matches-only — so a
  model that silently skips listings is distinguishable from an honest zero.
  Listings are addressed by integer id rather than by echoing a url back, so
  a mangled echo can no longer silently drop a real match. A malformed
  chunk response retries; a chunk that still fails after retries doesn't
  fail the whole run — it's recorded in the manifest and heartbeat as
  unclassified listings, and per-chunk checkpoints mean a resumed run
  doesn't re-pay for chunks it already completed.
- **score** — one structured Claude call gives each match a 0-100 fit score
  + rationale, per the fit-score guide in its lane's criteria file.
- **digest** — pure Python, no LLM: renders `matches/<date>.md` with one
  section per active lane, updates `data/seen.json`, commits and pushes.
  Matches scoring below 35 go to a "Rejected on scoring" section instead of
  the main listing — the score stage disagreeing with the classify stage is
  a signal worth keeping, and those listings are deliberately *not*
  recorded as seen so they stay eligible if re-posted.

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
that weekly run, which costs recall on the fractional lane's two sources
that are rolling windows rather than archives — see
[`docs/daily-ingest-split.md`](./docs/daily-ingest-split.md) for the
proposed daily-ingest/weekly-digest split. (`fetch_ats` doesn't have this
problem — ATS endpoints return each company's current listings in full,
not a rolling window — but it's also one HTTP call per company, so it's
the slowest stage by far once `data/companies.csv` has any real size.)

## Lanes

`SCOUT_LANES` selects which lane(s) run, as a comma-separated list of
`fractional` and/or `first_tpm`. Unset, both run. To run just one for
debugging:

```
SCOUT_LANES=first_tpm python3 scripts/run_pipeline.py
```

`data/companies.csv` (not committed — hand-maintained, see
[`docs/first-tpm-lane.md`](./docs/first-tpm-lane.md)) drives the first_tpm
lane's fetch: one row per portfolio company, columns
`name,ats,token,headcount,source`. An empty `headcount` means unknown, not
zero — never guess it.

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
Claude are tested against a stubbed CLI, the six sources (three job boards,
three ATS APIs) against captured response shapes, and `digest`'s commit/push
path against a throwaway git repo with a local bare remote. Each test
redirects `paths.PROJECT_DIR` at a temp directory, so the suite never writes
to the real `data/`, `logs/`, or `matches/`.

The tests deliberately pin the behaviours that were previously *bugs* — the
snippet extraction that keeps buried "part-time"/"contract" sentences, the
first-seen-date keying that makes `--force` idempotent, the score floor, the
scoped commit, and pushing a commit stranded by a failed push — so a
regression shows up as a named failing test rather than as a quiet week with
no matches. GitHub Actions runs the suite on every push and PR against Python
3.9 (the macOS system Python the scheduled job uses), 3.11, and 3.13.
