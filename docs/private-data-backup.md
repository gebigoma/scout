# Private data backup

Four gitignored paths have no copy anywhere else — not on GitHub, and the two
CSVs are hand-maintained with no other source of truth:

- `data/companies.csv`
- `data/known_good.csv`
- `signals/`
- `notes/`

(See `.gitignore` and `hygiene.NEVER_COMMIT` for why these stay out of git —
this is a public repo and the first three name specific companies.)

`scripts/backup_private.sh` copies whichever of these exist into a dated
folder under iCloud Drive, once a day, via a launchd job. A path missing from
the source (e.g. a fresh clone with no `data/companies.csv` yet) is skipped,
not a failure.

## First-time setup

The backup root must exist before the first run — the script does not create
it:

```
mkdir -p ~/Library/Mobile\ Documents/com~apple~CloudDocs/scout-backups
```

This is a one-time step on any given machine (see [Preflight
failure](#what-can-make-a-run-fail-and-alert) below for why it stays a manual
step rather than an automatic `mkdir -p` inside the script): after the first
run, a *missing* root almost always means iCloud Drive got unmounted, signed
out, or the path got renamed — something worth noticing, not something to
paper over by silently recreating an empty folder. Do this again on a new
machine before the launchd job's first scheduled run there.

## Where backups land

```
SCOUT_BACKUP_ROOT="${SCOUT_BACKUP_ROOT:-$HOME/Library/Mobile Documents/com~apple~CloudDocs/scout-backups}"
```

Each run writes `$SCOUT_BACKUP_ROOT/YYYY-MM-DD/`, mirroring the four paths'
relative layout (e.g. `.../2026-09-09/data/companies.csv`). A second run on
the same day replaces that day's folder contents rather than appending to
them.

Override the destination for a one-off run with the env var, e.g. to back up
somewhere other than iCloud Drive:

```
SCOUT_BACKUP_ROOT=/Volumes/external/scout-backups scripts/backup_private.sh
```

## Retention — what it recovers from, and what it doesn't

Dated folders are pruned on a tiered schedule:

- Folders dated the **1st of any month never get pruned** — a standing,
  ever-growing set of monthly snapshots.
- Of all the other (non-1st) folders, the **30 most recent are kept**; older
  ones are deleted.

So day-to-day, you can recover any of the last ~30 days' versions of these
files. Beyond that, you can only recover whichever month-start snapshots
happen to exist — there is no guaranteed daily recovery point older than 30
days. This is deliberately tiered rather than a flat 30-day window: a flat
window means a bad edit to `known_good.csv` discovered five weeks later has
nothing to roll back to. The monthly anchors trade unbounded recovery
resolution for a coarser, permanent safety net.

If iCloud Drive itself is unavailable (not mounted, not signed in) on a given
day, that day's backup is skipped and alerted on (see below) — it does not
silently no-op and does not delete anything.

## Restoring a file

Find the dated folder you want under `$SCOUT_BACKUP_ROOT` and copy the file
back, e.g.:

```
cp "$SCOUT_BACKUP_ROOT/2026-08-15/data/known_good.csv" data/known_good.csv
```

## Running a dry run

```
scripts/backup_private.sh --dry-run
```

Runs preflight, the runlock check, the copy, and verification exactly as a
real run would — the only difference is the prune step logs what it *would*
delete instead of deleting it. Nothing is deleted in dry-run mode.

## What can make a run fail (and alert)

Reuses the pipeline's existing ntfy helper (`NTFY_TOPIC`), high priority, on:

- **Preflight failure** — `$SCOUT_BACKUP_ROOT` doesn't exist or isn't
  writable. This is not auto-created; it almost always means iCloud Drive
  isn't mounted or signed in on this machine, and creating a folder anyway
  would just start writing backups nothing is actually syncing.
- **Copy failure** — an individual path failed to copy.
- **Verification failure** — verification is source-relative, so an empty
  `notes/` or a `signals/` that's legitimately empty tonight is a pass, not a
  failure. It only fails when: a path present in the source didn't land at
  the destination at all; a *non-empty* source landed empty; or (for the two
  CSVs) the line counts disagree. A verification failure blocks the prune
  step entirely for that run, so a bad backup can never cause a good older
  one to be deleted.

There is no alert on success, and no alert when the run is skipped because
the weekly pipeline's runlock is held (`logs/run.lock`) — that's an expected,
routine outcome, logged to that day's `logs/<date>.jsonl` like any other
stage, not an alert-worthy one.

## Known gap

A laptop with the lid closed at 21:30 doesn't run this job — launchd defers
it to the next time the machine is awake, same as any calendar-interval
`LaunchAgent`. In practice this makes "daily" closer to "most days"; a run
skipped this way produces no log line and no alert, since it never started.
