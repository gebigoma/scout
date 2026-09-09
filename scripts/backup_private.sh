#!/bin/sh
# Daily backup of this repo's private, gitignored data to iCloud Drive - see
# hygiene.NEVER_COMMIT and .gitignore for the authoritative list. Two of the
# four paths below (the CSVs) are hand-maintained with no other copy
# anywhere, not even on GitHub.
#
# Run manually, or on a schedule via com.scout.privatebackup.plist.
#
# --- Where this bends the spec it was written against, and why ------------
#
# "Shell script, not Python" plus "reuse the runlock/ntfy/log helpers" plus
# "don't modify pipeline code" only fit together one way: three tiny python3
# bridges below call straight into the real pipeline modules for exactly the
# things shell has no honest equivalent for on this machine, everything else
# (preflight, copy, verify, prune, the actual control flow) is plain sh.
#
#   - runlock_held: macOS ships no flock(1) (that's a Linux util-linux tool),
#     so there's no shell-native way to test the pipeline's lock without
#     reimplementing flock semantics by hand. This imports pipeline.runlock
#     and calls single_run() itself, non-blocking, same as the pipeline.
#   - alert_failure: alert.py's three public functions are all shaped for a
#     pipeline run (weekly failure, publish-skipped, success heartbeat) -
#     none fit "a backup step failed", and it can't be modified to add a
#     fourth. This calls the private _notify() the other three already share,
#     rather than duplicating the ntfy POST here.
#   - log_jsonl: writes through logging_setup.get_logger/log so a backup run
#     lands in the same logs/<date>.jsonl schema every pipeline stage uses,
#     instead of inventing a second log format.
#
# Two more additions beyond the spec's literal deliverables, both for
# testability: shell has no mocking, so `--prune-dir DIR [--dry-run]` exposes
# the prune step standalone (used by tests/test_backup_private.py) instead of
# only being reachable via a full, real backup run.
#
# And one gap worth knowing rather than working around: SCOUT_BACKUP_ROOT is
# resolved with a `:-` default below, which means by the time prune runs in
# the normal flow it can never actually be unset or empty - the guard in
# prune_dir() is real defense-in-depth (and is what `--prune-dir ""` in the
# test suite actually exercises), not a path reachable by just unsetting the
# env var.
set -eu

SCOUT_BACKUP_ROOT="${SCOUT_BACKUP_ROOT:-$HOME/Library/Mobile Documents/com~apple~CloudDocs/scout-backups}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RETAIN_BACKUPS=30
DATE_RE='^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]$'

# Relative to $ROOT. A path missing from the source (e.g. a fresh clone with
# no data/companies.csv yet) is skipped, not a failure - see CLAUDE.md on
# that file being legitimately absent.
BACKUP_PATHS="data/companies.csv data/known_good.csv signals notes"

runlock_held() {
  # Exit 0 (shell "true") if the pipeline's lock is held, 1 if it's free.
  python3 -c "
import sys
sys.path.insert(0, sys.argv[1])
from pipeline import runlock
try:
    with runlock.single_run():
        pass
    sys.exit(1)  # lock was free (and is released again already)
except runlock.AlreadyRunning:
    sys.exit(0)  # a pipeline run is in progress
" "$ROOT/scripts"
}

alert_failure() {
  # $1 = title, $2 = message.
  python3 -c "
import sys
sys.path.insert(0, sys.argv[1])
from pipeline.alert import _notify
_notify(sys.argv[3], title=sys.argv[2], priority='high')
" "$ROOT/scripts" "$1" "$2"
}

log_jsonl() {
  # $1 message, $2 paths copied (comma-separated), $3 bytes, $4 folders
  # pruned (comma-separated), $5 outcome.
  run_date="$(date +%Y-%m-%d)"
  python3 -c "
import sys
sys.path.insert(0, sys.argv[1])
from pipeline import logging_setup
run_date, message, paths_copied, nbytes, pruned, outcome = sys.argv[2:8]
logger = logging_setup.get_logger(run_date)
logging_setup.log(
    logger, 'backup', message,
    paths=[p for p in paths_copied.split(',') if p],
    bytes=int(nbytes),
    pruned=[p for p in pruned.split(',') if p],
    outcome=outcome,
)
" "$ROOT/scripts" "$run_date" "$1" "$2" "$3" "$4" "$5"
}

preflight() {
  # A missing/unwritable root almost always means iCloud Drive isn't mounted
  # or signed in on this machine - mkdir-ing past that would silently start
  # writing backups nobody is actually syncing anywhere.
  if [ ! -d "$SCOUT_BACKUP_ROOT" ] || [ ! -w "$SCOUT_BACKUP_ROOT" ]; then
    alert_failure "scout backup preflight failed" \
      "$SCOUT_BACKUP_ROOT does not exist or is not writable."
    echo "backup_private: preflight failed - $SCOUT_BACKUP_ROOT missing or not writable" >&2
    exit 1
  fi
}

do_copy() {
  mkdir -p "$DEST"
  copied=""
  for rel in $BACKUP_PATHS; do
    src="$ROOT/$rel"
    [ -e "$src" ] || continue
    # rm before cp -R: for a directory source, cp -R into an existing
    # target *merges* rather than replaces, which would leave stale files
    # behind from an earlier backup today after a real deletion upstream.
    if ! mkdir -p "$DEST/$(dirname "$rel")" \
        || ! rm -rf "${DEST:?}/$rel" \
        || ! cp -R "$src" "$DEST/$rel"; then
      alert_failure "scout backup copy failed" "copying $rel into $DEST failed"
      echo "backup_private: copy of $rel failed" >&2
      exit 1
    fi
    copied="$copied,$rel"
  done
  copied="${copied#,}"
}

do_verify() {
  problems=""
  for rel in $BACKUP_PATHS; do
    src="$ROOT/$rel"
    [ -e "$src" ] || continue
    dst="$DEST/$rel"
    if [ ! -e "$dst" ]; then
      problems="$problems
$rel did not land in the backup"
      continue
    fi
    if [ -f "$dst" ]; then
      if [ ! -s "$dst" ]; then
        problems="$problems
$rel landed empty"
        continue
      fi
      case "$rel" in
        *.csv)
          src_lines=$(wc -l < "$src" | tr -d ' ')
          dst_lines=$(wc -l < "$dst" | tr -d ' ')
          if [ "$src_lines" != "$dst_lines" ]; then
            problems="$problems
$rel line count mismatch: source $src_lines, backup $dst_lines"
          fi
          ;;
      esac
    elif [ -d "$dst" ]; then
      if [ -z "$(find "$dst" -type f -print -quit)" ]; then
        problems="$problems
$rel landed empty"
      fi
    fi
  done
  if [ -n "$problems" ]; then
    alert_failure "scout backup verification failed" "$problems"
    echo "backup_private: verification failed:$problems" >&2
    exit 1
  fi
}

prune_dir() {
  # $1 = root to prune directly under, $2 = "1" for dry-run else "0".
  # Guarded hard: this is the only part of the script that can destroy data.
  # Only ever touches "$root/$name" for a $name that passed the exact
  # YYYY-MM-DD filter below - no recursion, no loose globs.
  root="$1"
  dry_run="$2"

  if [ -z "$root" ]; then
    echo "backup_private: refusing to prune - empty backup root" >&2
    return 1
  fi

  pruned=""
  count=0
  for name in $(ls -1 "$root" 2>/dev/null | grep -E "$DATE_RE" | sort -r); do
    [ -d "$root/$name" ] || continue
    day="${name#*-*-}"
    if [ "$day" = "01" ]; then
      continue  # monthly anchor - never counted, never pruned
    fi
    count=$((count + 1))
    if [ "$count" -gt "$RETAIN_BACKUPS" ]; then
      if [ "$dry_run" = "1" ]; then
        echo "backup_private: [dry-run] would delete $root/$name" >&2
      else
        rm -rf "${root:?}/${name:?}"
      fi
      pruned="$pruned,$name"
    fi
  done
  echo "${pruned#,}"
}

run_backup() {
  dry_run="$1"
  RUN_DATE="$(date +%Y-%m-%d)"
  DEST="$SCOUT_BACKUP_ROOT/$RUN_DATE"

  preflight

  if runlock_held; then
    log_jsonl "skipped: pipeline run in progress" "" 0 "" "skipped"
    echo "backup_private: pipeline run in progress, skipping"
    exit 0
  fi

  do_copy
  do_verify

  pruned="$(prune_dir "$SCOUT_BACKUP_ROOT" "$dry_run")"
  bytes=$(find "$DEST" -type f -exec stat -f%z {} \; 2>/dev/null \
          | awk '{s+=$1} END {print s+0}')
  outcome="ok"
  [ "$dry_run" = "1" ] && outcome="dry-run"
  log_jsonl "backup complete" "$copied" "$bytes" "$pruned" "$outcome"
  echo "backup_private: wrote $DEST"
}

case "${1:-}" in
  --prune-dir)
    # Internal - exercises prune_dir() standalone. Used by
    # tests/test_backup_private.py, not part of the documented CLI.
    dry=0
    [ "${3:-}" = "--dry-run" ] && dry=1
    prune_dir "${2:-}" "$dry" >/dev/null
    ;;
  --dry-run)
    run_backup 1
    ;;
  "")
    run_backup 0
    ;;
  *)
    echo "usage: $0 [--dry-run]" >&2
    exit 2
    ;;
esac
