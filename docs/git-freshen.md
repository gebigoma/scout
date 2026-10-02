# Keeping the clone fast-forwarded

`scripts/git-freshen.sh` fast-forwards this clone's `main` to `origin/main`
once a day, so the weekly digest run can publish. Scheduled by
`com.scout.gitfreshen.plist`.

## Why this is needed

`digest` decides whether to push from `_unpushed_commit_count`, which measures
`@{u}..HEAD` — the *local* tracking ref. When `origin/main` advances on GitHub
and this laptop never fetches, the clone's `main` does not **contain** those
commits, so `digest` commits the digest on top of a behind-`main` and pushes
into a guaranteed non-fast-forward rejection. Because `digest` writes no
checkpoint on failure, every later run repeats the same rejection.

That cost the 2026-09-28 run its publish: PR #32 merged on GitHub on 09-25,
the clone never fetched, the push was rejected. It then recurred three days
later, when merging the fix for it advanced `origin/main` again. Both times the
trigger was an ordinary GitHub-side merge, which is why this is automated
rather than left as a habit.

**A bare `git fetch` does not prevent it.** Fetching refreshes the tracking ref
but leaves `main` behind, and the push is still rejected. The clone has to
actually fast-forward.

This is a mitigation, not the fix — see issue #34 for the underlying problem
(no network call in the pipeline is bounded by total elapsed time, and
`digest` cannot tell it is behind before pushing).

## What it does, and what it refuses to do

It acts **only** when all of these hold:

- `HEAD` is on `main` (not a feature branch, not detached)
- the worktree is clean
- no pipeline run holds `logs/run.lock`

and then only `git merge --ff-only origin/main`. It never merges, never
rebases, and never touches a feature branch. A merge commit on `main` would be
rejected by `hygiene.check_main_push` anyway, so the fast-forward is load-
bearing, not a preference — `tests/test_git_freshen.py` pins that the resulting
commit has exactly one parent.

Four outcomes:

| State | What happens |
|---|---|
| Up to date | nothing |
| Behind | fast-forwarded, logged |
| Ahead only (unpushed commits) | nothing — normal between runs |
| **Diverged** (local *and* remote commits) | exits 1 and fires an ntfy alert |

Divergence is the one state it will not resolve: choosing a rebase or a merge
there is a judgement call about commits intended for publication, not a
scheduled job's decision. It is also precisely the state that silently costs a
published week — it is what 2026-09-28 left behind — so it alerts instead of
passing quietly.

The fetch is bounded with `http.lowSpeedLimit`/`http.lowSpeedTime`, git's real
stall detector. A failed fetch (offline, most likely) exits 0 and changes
nothing: a skipped freshen costs at most a delayed publish.

## `--dry-run`

Reports what it would do and performs **no writes at all** — including no
`git fetch`, which writes objects and remote-tracking refs into `.git`. The
consequence is that on an unfetched clone a dry run cannot know the remote has
moved, so it says so explicitly rather than implying it checked. Overstating
what a dry run verified is the bug `f6454e3` fixed for `backup_private.sh`;
the same rule applies here.

To see the real pending state, fetch first and then dry-run.

## First-time setup

The script needs no setup. To schedule it on a new machine:

```
cp com.scout.gitfreshen.plist ~/Library/LaunchAgents/   # see note below
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.scout.gitfreshen.plist
```

The plist is **not** committed to this repo, matching the other two launchd
jobs — it carries `NTFY_TOPIC`, which is deliberately not in a public repo
(see CLAUDE.md on environment variables). Recreate it by hand, modelled on
`com.scout.privatebackup.plist`, with:

- `ProgramArguments`: `/bin/sh`, then the absolute path to `scripts/git-freshen.sh`
- `WorkingDirectory`: the repo root — this is how the script finds the
  repository to operate on
- `StartCalendarInterval`: hour 8, minute 30, no weekday — daily, half an hour
  before the Monday 09:00 pipeline run
- its own `StandardOutPath`/`StandardErrorPath` under `logs/`, kept separate
  from `weekly_run.log` so pipeline diagnosis stays readable

Verify with:

```
launchctl kickstart -k gui/$(id -u)/com.scout.gitfreshen
cat logs/git-freshen.log
```

## Known limit

Daily at 08:30 leaves a window: a GitHub-side merge between 08:30 and the
Monday 09:00 run still reaches `digest` with a behind-`main`. The failure is at
least loud now, and recovery is one command —
`git pull --ff-only && python3 scripts/run_pipeline.py --date <date>` resumes
straight at `digest` without re-paying for `classify`.
