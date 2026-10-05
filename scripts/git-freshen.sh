#!/bin/sh
# Keep this clone's `main` fast-forwarded to origin/main, so the weekly
# digest run can actually publish.
#
# Run manually, or on a schedule via com.scout.gitfreshen.plist.
#
# --- Why this exists -----------------------------------------------------
#
# `digest` decides whether to push from `_unpushed_commit_count`, which
# measures `@{u}..HEAD` - the local tracking ref. When origin/main advances
# on GitHub and this laptop never fetches, the clone's `main` doesn't
# *contain* those commits, so digest commits on top of a behind-main and
# pushes into a guaranteed non-fast-forward rejection. digest writes no
# checkpoint on failure, so every later run repeats it.
#
# That cost the 2026-09-28 run its publish (PR #32 merged 09-25, never
# fetched), then recurred three days later when merging the fix for it
# advanced origin/main again. Both times the trigger was an ordinary
# GitHub-side merge. See issue #34 and the 2026-09-28 entry in CLAUDE.md.
#
# Note a bare `git fetch` does NOT prevent this: it refreshes the tracking
# ref but leaves `main` behind, and digest's push is still rejected. The
# clone has to actually fast-forward, which is what this does.
#
# --- What it deliberately does not do ------------------------------------
#
# Only ever `merge --ff-only`, and only when already on `main` with a clean
# worktree. It never merges, never rebases, never touches a feature branch,
# and never runs while a pipeline run holds the lock. Every one of those
# would mean a scheduled job mutating work in progress; a skipped freshen
# costs at most a delayed publish, which is recoverable in one command.
#
# A *diverged* main (local commits AND remote commits) is the one state it
# can't fix safely - resolving it means choosing a rebase or a merge, which
# is a judgement call about published-intent commits. It alerts instead,
# because that is exactly the state that silently costs a published week.
set -eu

# Where the pipeline package lives, for the alert bridge below. Kept separate
# from the repo being operated on (resolved from cwd, further down) for the
# same reason backup_private.sh separates $ROOT from $BACKUP_SRC: the tests
# point this script at a throwaway repo, and the bridge must still find the
# real pipeline modules.
SCRIPTS_DIR="$(cd "$(dirname "$0")" && pwd)"

usage() {
  cat <<USAGE
usage: $(basename "$0") [--dry-run] | --help

Fast-forwards this clone's main to origin/main so the weekly digest run can
publish. Runs against the repository containing the current directory.

  (no arguments)   fetch and fast-forward if possible
  --dry-run        report what would happen; makes no change at all
  --help           show this message
USAGE
}

alert() {
  # $1 = title, $2 = message. Same bridge backup_private.sh uses, and for
  # the same reason: alert.py's public helpers are all shaped for a pipeline
  # run, none fit "the clone diverged". No-ops when NTFY_TOPIC is unset,
  # which is the case in a fresh clone and in the test suite.
  python3 -c "
import sys
sys.path.insert(0, sys.argv[1])
from pipeline.alert import _notify
_notify(sys.argv[3], title=sys.argv[2], priority='high')
" "$SCRIPTS_DIR" "$1" "$2"
}

runlock_held() {
  # Exit 0 (shell true) if a pipeline run is in progress *in the repo being
  # freshened* ($1). Fast-forwarding main underneath a running pipeline would
  # change the working tree mid-run.
  #
  # The lock is the target repo's, not the one beside this script. In
  # production they are the same directory; in the test suite they are not -
  # tests run this script against temp clones - and until 2026-10-05 the
  # check read the real repo's logs/run.lock regardless. The pre-push hook
  # runs the suite during digest's push, which is exactly when a pipeline run
  # holds that lock, so seven freshen tests saw "run in progress", failed,
  # and the hook rejected every digest push. The 2026-10-05 --force run's
  # digest was stranded that way, and Sunday's scheduled run would have been.
  python3 -c "
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from pipeline import paths
paths.PROJECT_DIR = Path(sys.argv[2])
from pipeline import runlock
# No lock file means no run has ever taken the lock here, so none holds it -
# and taking it to find out would create logs/run.lock (and logs/) in a repo
# that may not ignore them, tripping the dirty-worktree guard next time.
if not (paths.PROJECT_DIR / 'logs' / 'run.lock').exists():
    sys.exit(1)
try:
    with runlock.single_run():
        pass
    sys.exit(1)  # lock was free (and is released again already)
except runlock.AlreadyRunning:
    sys.exit(0)  # a pipeline run is in progress
" "$SCRIPTS_DIR" "$1"
}

freshen() {
  dry_run="$1"

  repo="$(git rev-parse --show-toplevel 2>/dev/null)" || {
    echo "git-freshen: not a git repository" >&2
    exit 1
  }
  cd "$repo"

  branch="$(git symbolic-ref --quiet --short HEAD || echo "")"
  if [ "$branch" != "main" ]; then
    echo "git-freshen: on '${branch:-detached HEAD}', not main - nothing to do"
    exit 0
  fi

  if [ -n "$(git status --porcelain)" ]; then
    echo "git-freshen: worktree is dirty - leaving it alone"
    exit 0
  fi

  if runlock_held "$repo"; then
    echo "git-freshen: pipeline run in progress, skipping"
    exit 0
  fi

  if [ "$dry_run" = "1" ]; then
    # Deliberately does not fetch. `git fetch` writes to .git (objects and
    # remote-tracking refs), and --dry-run here performs no writes at all -
    # same rule f6454e3 established for backup_private.sh. The cost is that
    # the counts below are computed from refs that may be stale, so the
    # report says so rather than implying it checked the remote. Overstating
    # what a dry run verified is the exact bug that commit fixed.
    echo "git-freshen: [dry-run] would fetch --prune from origin (not fetched:" \
         "the state below is from local refs as they stand, and a real run may differ)"
  else
    # Bounded on purpose: a stalled transfer must not leave this job
    # running for hours. git's low-speed guard is a real stall detector,
    # unlike urllib's socket timeout - see issue #34.
    if ! git -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=15 \
         fetch --prune --quiet origin; then
      echo "git-freshen: fetch failed (offline?) - nothing changed" >&2
      exit 0
    fi
  fi

  if ! git rev-parse --verify --quiet origin/main >/dev/null; then
    echo "git-freshen: no origin/main - nothing to do" >&2
    exit 0
  fi

  # "<ahead> <behind>", tab-separated: commits on main not on origin/main,
  # and vice versa. Checked explicitly rather than inferred from merge's
  # exit code, because "ahead only" and "diverged" both fail --ff-only and
  # mean completely different things.
  counts="$(git rev-list --left-right --count main...origin/main)"
  ahead="$(printf '%s' "$counts" | cut -f1)"
  behind="$(printf '%s' "$counts" | cut -f2)"

  if [ "$behind" = "0" ] && [ "$ahead" = "0" ]; then
    if [ "$dry_run" = "1" ]; then
      echo "git-freshen: [dry-run] nothing to do on current refs"
      echo "DRY RUN - nothing was changed"
    else
      echo "git-freshen: already up to date"
    fi
    exit 0
  fi

  if [ "$behind" != "0" ] && [ "$ahead" != "0" ]; then
    # The 2026-09-28 state: a stranded local commit plus a moved remote.
    # Not auto-resolvable - picking rebase or merge here is a judgement
    # call, and this is the state that costs a published week, so say so.
    msg="main has diverged from origin/main: $ahead local commit(s), $behind remote. The weekly digest push will be rejected until this is integrated (git pull --rebase, or rebase onto origin/main)."
    echo "git-freshen: $msg" >&2
    [ "$dry_run" = "1" ] || alert "scout clone diverged from origin/main" "$msg"
    exit 1
  fi

  if [ "$ahead" != "0" ]; then
    echo "git-freshen: $ahead unpushed commit(s), nothing to fast-forward"
    exit 0
  fi

  if [ "$dry_run" = "1" ]; then
    echo "git-freshen: [dry-run] would fast-forward main $behind commit(s) to $(git rev-parse --short origin/main)"
    echo "DRY RUN - nothing was changed"
    exit 0
  fi

  git merge --ff-only --quiet origin/main
  echo "git-freshen: fast-forwarded main $behind commit(s) to $(git rev-parse --short HEAD)"
}

case "${1:-}" in
  --dry-run)
    # A bare --dry-run only. A stray trailing argument must not be silently
    # ignored - same reasoning as backup_private.sh's flag handling: that is
    # how a typo turns into an unnoticed real run.
    if [ $# -ne 1 ]; then
      usage >&2
      exit 2
    fi
    freshen 1
    ;;
  --help)
    usage
    ;;
  "")
    freshen 0
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
