# Design: split daily ingest from the weekly digest

**Status:** proposed, not implemented
**Date:** 2026-08-03

## Problem

Everything runs weekly. A single `launchd` job
(`~/Library/LaunchAgents/com.scout.weeklyfetch.plist`, Mondays 09:00) runs
all six stages of `scripts/run_pipeline.py`, so `fetch` only ever runs when
the digest runs.

Two of the three sources are **rolling windows**, not archives:

| source | shape | consequence of weekly polling |
| --- | --- | --- |
| RemoteOK | hard 100 items, ~3-day span | Tue–Thu postings are gone by Monday |
| We Work Remotely | RSS, ~77 items, no date parameter | same, less severe |
| HN "Who is hiring" | monthly thread, accumulates | little to no loss |

Measured on two captured snapshots:

```
data/raw/2026-08-01.json   remoteok n=100   posted 2026-07-29 → 2026-08-01
data/raw/2026-08-02.json   remoteok n=100   posted 2026-07-29 → 2026-08-01
```

Both days return exactly 100 RemoteOK items spanning roughly three days. A
Monday-only fetch therefore *structurally* cannot see most of the week's
RemoteOK postings — and the pipeline reports success on such a run, because
nothing failed. It is a silent recall loss, which is the failure mode this
project has been most careful about elsewhere.

## Proposal

Split the pipeline at the LLM boundary, which is also the cost boundary:

```
daily    fetch → normalize → dedupe        (pure Python, no LLM, no usage cost)
                     ↓
                data/pool.json
                     ↓
weekly   classify → score → digest         (the two Claude calls, then publish)
```

Ingest runs every morning and accumulates candidates. The digest runs Monday
over everything accumulated since the last digest.

## Work items

### 1. Orchestrator

Add `--mode ingest|digest|full` to `scripts/run_pipeline.py` (default
`full`, preserving today's behaviour and the manual-run instructions in the
README). The existing `stage_output()` checkpoint/resume machinery needs no
change — only the stage list it walks.

### 2. The pool

New `data/pool.json`: `url → {listing, first_ingested}`. Daily ingest merges
into it; the weekly digest reads it.

Prefer this over having the digest glob `data/runs/<date>/` for every date
since the last digest. Globbing makes "which days are unconsumed" implicit
and degrades silently if a day is missing (laptop asleep, source outage) —
the digest would simply see less and still report success.

This file is a real deliverable-adjacent artifact, not scratch: unlike
`data/runs/`, it should **not** be gitignored if you want the pool to
survive a machine rebuild. Decide deliberately.

### 3. Consume, don't clear

The digest must mark pool entries `consumed_by: <digest-date>` rather than
deleting them.

If it clears the pool, a `--force` digest re-run finds it empty, publishes
an empty digest over the real one, and reports success. That is precisely
the `--force`/`seen.json` back-edge bug that was already found and fixed
once (see `dedupe.py`'s module docstring); do not reintroduce it in a new
place.

### 4. Dedupe's date guard

`scripts/pipeline/dedupe.py`:

```python
seen_urls = {url for url, first_seen in seen.items() if first_seen != run_date}
```

This exists so a `--force` re-run doesn't drop its own previously-published
matches. It assumes ingest and digest share one run date. Once they diverge
— ingest on Thursday, digest on Monday — reconsider what "this run's own
date" means. Likely it becomes "not first-seen within the current digest
window."

Also decide where `seen.json` filtering belongs. Keeping it in the daily
pass keeps the pool small; the tradeoff is that a listing rejected on
scoring (deliberately *not* recorded as seen) will be re-ingested daily
until it rolls off the source.

### 5. Digest rendering

`scripts/pipeline/digest.py`:

- `_render_markdown` hardcodes one `run_date` in the H1 and prints
  "No matches this week." Both need a window, e.g.
  `# Matches — 2026-08-04 → 2026-08-10`.
- `candidate_count` currently comes from the dedupe checkpoint; it becomes
  the pool size for the window.
- The commit message `Weekly matches: <date>` and the path-scoped
  `git commit -- <paths>` are fine as-is.

### 6. Scheduling

Add `com.scout.dailyingest.plist` — same `ProgramArguments` plus
`--mode ingest`, `StartCalendarInterval` with `Hour 9` and **no** `Weekday`
key. Move the digest job to **09:30 Monday**.

Stagger them deliberately. If both fire at 09:00 they race on `pool.json`;
`paths.atomic_write_json` prevents a *torn file*, not a lost
read-modify-write.

Note `NTFY_TOPIC` must be set in the new plist too — it lives only in the
launchd environment, never in this repo.

### 7. Alerting

Do not page on a single failed ingest day; with seven chances a week, one
miss is noise. Either alert only on consecutive failures, or have the weekly
digest report "ingested N of the last 7 days" so a degrading schedule is
visible in the published output.

This also partly covers the dead-man's-switch gap: a digest that reports
2 of 7 says something the current success heartbeat cannot.

### 8. Tests

The existing suite pins `dedupe` and `digest` signatures, so those tests
change with the refactor. Add coverage for:

- pool merge idempotency (ingesting the same day twice is a no-op)
- digest over a multi-day window
- consumed-marker plus `--force` re-run (the §3 regression)

## Sequencing note

The classify prompt is already ~48K tokens for a single day's listings.
Accumulating seven days makes the other open item — the single monolithic
classify call, which should become chunked batches with a verdict required
for every input id — considerably more urgent. Daily ingest *without*
chunking will likely degrade classify recall, which defeats the purpose of
ingesting more.

Sequence chunking first, or do the two together.

## Non-goals

Loosening `ROLE_CRITERIA.md`. Every recall problem found in this project so
far has been an engineering defect, and thin results are not a reason to
lower the matching bar.
