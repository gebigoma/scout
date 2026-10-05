# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
python3 -m unittest discover -s tests -t .        # whole suite
python3 -m unittest tests.test_dedupe -v          # one module
python3 -m unittest tests.test_dedupe.DedupeTest.test_name   # one test

python3 scripts/run_pipeline.py                   # today's run
python3 scripts/run_pipeline.py --date 2026-08-03
python3 scripts/run_pipeline.py --force           # ignore checkpoints, redo every stage
SCOUT_LANES=first_tpm python3 scripts/run_pipeline.py        # single lane

python3 scripts/hygiene.py tracked                # same checks CI runs
sh scripts/install-hooks.sh                       # once per clone
```

Stdlib only — no venv, no requirements file, no install step. Python 3.9 is the
floor (the macOS system Python the launchd job runs on); CI also runs 3.11 and
3.13. Keep new code 3.9-compatible.

## Architecture

`scripts/run_pipeline.py` orchestrates stages under `scripts/pipeline/`:

```
fetch ─────┐
           ├→ normalize → prefilter → dedupe → classify → score → digest
fetch_ats ─┘                                        └→ company_signals ┘
```

Two **lanes** (`pipeline/lanes.py`) run by default and are threaded through the
whole run as a `lane` field on each listing: `fractional` (public job boards,
`ROLE_CRITERIA.md`) and `first_tpm` (VC-portfolio ATS endpoints,
`ROLE_CRITERIA_FIRST_TPM.md`). A lane's fetch stage is skipped entirely when
inactive; every downstream stage sees both lanes' listings together and
branches on the field. Adding a lane means touching `lanes.py`,
`paths.role_criteria_path`, and `classify.LANE_PROMPT_CONTEXT`.

**Checkpointing is the control flow.** Each stage writes
`data/runs/<date>/<stage>.json`; re-running a date resumes from the first stage
without a checkpoint. Always write through `paths.atomic_write_json` (temp +
`os.replace`) — a truncated checkpoint would wedge that date permanently. An
unreadable checkpoint is treated as missing, not fatal. `classify` checkpoints
per *chunk* under `classify_chunks/<n>.json`, so a resumed run doesn't re-pay
for completed chunks.

**Only two stages call an LLM** (`classify`, `score`), both by shelling out to
the Claude Code CLI via `pipeline/llm.py` with `--json-schema`. The model is
pinned to `sonnet` (`SCOUT_MODEL` overrides) precisely so the interactive
`/model` setting can't change what a scheduled run costs. `digest` is pure
Python — never add an LLM call there.

**Every listing gets an explicit verdict** in `classify`, not matches-only, and
listings are addressed by integer id rather than by echoing URLs back. Both are
load-bearing: matches-only makes a model that silently skips indistinguishable
from an honest zero, and a mangled URL echo silently drops real matches.
`_validate_verdicts` rejects a malformed chunk wholesale rather than partially
accepting it. A chunk that fails after retries does *not* fail the run — it
lands in `failed_chunk_indices`/`unclassified_count` in the manifest.

`company_signals` reads the **raw** `fetch_ats` checkpoint, not `normalize`'s
output, because it needs the `department` field that normalize drops. It
produces a watchlist, is not lane-keyed, and is deliberately never recorded in
`data/seen.json`. It still renders `signals/<date>.md`, but that file is
gitignored and never published — see the invariant below. `funding.py` is not
wired into the pipeline — callers invoke `enrich_companies` explicitly, keeping
the signals stage free of EDGAR's rate limits; its EDGAR contact address comes
from `SCOUT_EDGAR_CONTACT`, never hardcoded, because this repo is public.

Supporting modules: `paths.py` (all filesystem locations — never hardcode a
path elsewhere, tests repoint `PROJECT_DIR`), `manifest.py` (per-run
status/timing/counts), `logging_setup.py` (structured JSONL to `logs/<date>.jsonl`),
`retry.py`, `runlock.py`, `retention.py`, `alert.py` (ntfy.sh; topic from
`NTFY_TOPIC`, set in the launchd job, not in this repo), `textutil.py`
(`elide_middle` — the one shared, dependency-free text helper; `llm.py` and
`digest.py` both use it for failure diagnostics without either importing the
other).

## Invariants worth knowing before changing things

- **`digest` refuses to publish off `main`.** It renders `matches/<date>.md`
  either way but only commits on `PUBLISH_BRANCH`, pushes by explicit refspec,
  and scopes `git add`/`commit` to exact paths so unrelated staged work is never
  swept in. A skipped publish fires an ntfy alert — otherwise it looks like a
  quiet week.
- **`data/seen.json` maps url → *first-seen* date.** Keying on the date is what
  makes `--force` idempotent. Matches scoring below `digest.SCORE_FLOOR` (35) go
  to a "Rejected on scoring" section and are deliberately *not* recorded as
  seen, so they stay eligible if re-posted.
- **A paused role is gated in `classify`, not in the prompt or at render
  time.** `lanes.PAUSED_CATEGORIES` holds role categories that are switched
  off without deleting anything; `agentic_ai_engineer` is paused as of
  2026-10-05, and re-enabling it is removing that one entry. The gate sits
  where `classify` assembles `matches`, which is what keeps a paused role out
  of `score`, `digest` *and* `data/seen.json` — gating at render time instead
  would still have recorded those urls as seen, silently burning the backlog
  a pause is supposed to preserve. It is deliberately not a prompt change:
  `ROLE_CRITERIA.md` is interpolated into the classify prompt whole and still
  describes the paused role, so the model may still return its category and a
  deterministic Python drop is the only gate it cannot argue with. Removing
  the category from `classify.SCHEMA`'s enum would be worse than useless — the
  model would either mislabel those listings as `senior_tpm`, contaminating
  the active role, or emit a value the schema rejects, and
  `_validate_verdicts` fails a malformed chunk *wholesale*. The manifest
  records `paused_dropped` and `paused_categories` so a role dropped by
  configuration and a genuinely empty week don't produce the same green run,
  and the digest preamble names the paused role because the criteria file it
  links to still describes it.
- **The viewer's `worked` column is the number, `wall clock` is the window.**
  `scripts/viewer.py` reports both because checkpointing is the control flow:
  a date resumed days later is the normal path, so wall clock from first start
  to last finish is not how long the pipeline worked. 2026-08-31 started 08-31
  and finished 09-03 — 4787m of wall clock over about 65m of summed stage
  durations — and a single-column view misleads on exactly the runs most worth
  reading. A run whose wall clock exceeds its work by more than ten minutes is
  marked `resumed` rather than quietly reconciled, because a date that stayed
  open is itself worth seeing. A stage that failed before finishing reports no
  duration and contributes nothing, so `worked` is time accounted for, not a
  claim that every stage ran.
- **One run at a time**, via `flock` on `logs/run.lock`. A second run exits 0
  with a message — it is not a failure and must not alert like one.
- **`retention.sweep` counts runs, not days** (`RETAIN_RUNS` = 8), and runs only
  after a successful publish. This pipeline lives on a laptop that can miss
  weeks; an age-in-days rule would delete the last real runs exactly when you
  still need them. The sweep never propagates its own errors.
- **`prefilter` has three tiers, and tier 3 is title-only.** Tiers 1 and 2
  both require foundation vocabulary, which measured against the eval corpus
  dropped 12 of the 14 listings whose *title* said "Technical Program
  Manager" or "TPM" — openings at Chainguard, Deepgram, Cribl, Together AI,
  Sardine, Anyscale and Baseten, i.e. this lane's exact target profile,
  invisible because of how the ad was worded rather than what the job was.
  The two that survived, Supabase and Baseten, are the only first_tpm matches
  ever published, and both ads happened to say "first" or "Founding"; most
  don't. Tier 3 passes a role term in the title regardless of body evidence,
  because whether the role is *actually* foundational is a judgment about
  evidence that belongs to `classify` and `ROLE_CRITERIA_FIRST_TPM.md` — the
  stage's own docstring says "loose, classify confirms", and a gate dropping
  12 of 14 on-profile titles was not loose. It costs about +5 candidates per
  run. Title matching is only affordable because `ROLE_TERM` is narrow:
  "Recruiting Operations Program Manager" and "Capacity Manager, Programs"
  don't qualify, and the word-boundary `\btpm\b` keeps Trusted Platform
  Module out. `prefilter` reports `title_only` in the manifest so admissions
  on title alone stay countable.
- **`tpm_unconfirmed` is for absent evidence, never contrary evidence.** The
  first_tpm lane has two categories: `first_tpm` still requires foundation
  evidence in the posting text, and `tpm_unconfirmed` is a clear senior TPM
  role at a target-profile company whose ad says nothing either way. Anything
  in the criteria's "Not a match" list — an existing TPM org, non-technical
  program management, a Product Manager req, junior scope, Trusted Platform
  Module — is still `no_match`. A posting saying "join our team of TPMs" has
  *answered* the question and is a rejection, not an unconfirmed. It renders
  in its own digest section, because mixing unconfirmed listings in with
  evidenced ones would weaken the evidenced section's claim without telling
  the reader it had changed. The scoring guide also forbids penalising an
  unconfirmed listing for the missing evidence that defines its tier: double
  counting it would push every one under `SCORE_FLOOR` into "Rejected on
  scoring" — a different claim entirely, the pipeline disagreeing with
  itself — and quietly empty the section.
- **Word-boundary match on `\btpm\b`** in `prefilter.py` (and `\bml\b` in
  `company_signals.py`) — the first_tpm lane sources heavily from
  infra/security companies where "TPM" means Trusted Platform Module.
- **`normalize` snippets keep buried evidence sentences**, not just the
  description head. These sources bury the evidence under "About Us"
  boilerplate, and head-truncation hides the exact thing the criteria require.
  Which sentences get salvaged is *lane-specific*: the fractional lane rescues
  `EMPLOYMENT_TERMS` ("contract"/"part-time"), the first_tpm lane rescues those
  plus `prefilter.EVIDENCE_TERM` (role + foundation vocabulary), because that
  lane turns on whether the hire would establish the function, not on the
  employment terms. `EVIDENCE_TERM` is defined in `prefilter` and imported by
  `normalize` so the evidence one stage preserves and the other looks for
  cannot drift. HN headers are parsed by content, not position.
- **A snippet is stitched; `match_text` is contiguous.** `_extract_snippet`
  returns `head + " […] " + salvaged sentences`, so any *distance* measured
  across it is a distance that does not exist in the document. `normalize`
  therefore carries the whole description on first_tpm listings as
  `match_text`, `prefilter` matches on that, and `prefilter` strips it from
  everything it passes downstream — the field lives in exactly one checkpoint.
  That costs `normalize.json` roughly 2MB → 14MB on a real corpus and leaves
  every later checkpoint unchanged; `fetch_ats.json` is already 26MB, so the
  stripping is what keeps this from compounding across the retained runs.
  Getting this wrong is what made the first_tpm lane emit **zero** candidates
  on every run of its life (issue #13): `prefilter` matched the 400-char
  snippet, where 9 of 2018 listings on the 2026-08-17 corpus carried a role
  term, versus 37 in the full text. The tier-2 distances were never the
  problem — the six real candidates sit at 45–195 characters, comfortably
  inside `PROXIMITY_WINDOW` (200). Raising that window would have "fixed"
  nothing and hidden the cause.
- **`prefilter.near_miss` means a role term was seen and dropped anyway** — not
  "exactly one term family present", which is what it meant until 2026-09-03.
  Foundation vocabulary ("first", "establish", "build out") is ordinary job-ad
  filler present in ~two thirds of any real corpus, so the old definition was
  only survivable while matching ran over a truncated snippet; over whole
  descriptions it produces ~1300 logged "near misses" a run and buries the ~30
  worth reading. The manifest also records `first_tpm_seen` and
  `role_terms_seen`, so "the lane saw no role terms" and "the lane saw them and
  dropped them all" stop producing an identical empty digest and an identical
  green run.
- **Greenhouse needs `?content=true`** — the bare endpoint has no description
  field at all. A 404 from any ATS means the company isn't on it and is
  skipped, not a failure; board tokens are guesswork.
- **`data/companies.csv` is hand-maintained** (`name,ats,token,headcount,source`)
  and drives the first_tpm lane's fetch. An empty `headcount` means unknown, not
  zero — never guess it.
- **`data/known_good.csv` is the hand-kept recall log.** The pipeline can
  measure precision from what `digest` publishes, but it has no record of roles
  it never saw — recall is only observable from outside, so it gets tracked by
  hand. One row per role found manually that scout *should* have surfaced (a
  true positive by `ROLE_CRITERIA`, not everything worth a click). The
  `missed_at` column names the stage that dropped it — `universe` / `ats` /
  `prefilter` / `classify` / `score` — which is what turns "we're leaking" into
  "we're leaking *here*". Two gaps it has already caught, both upstream of any
  filtering logic: `companies.csv` is seeded from VC-firm portfolio pages, so an
  angel-funded company appears on none of them and never enters the universe at
  all; and `fetch_ats` covers only Greenhouse, Ashby and Lever, so a company on
  Workable (or any fourth ATS) 404s and is skipped as "not on this ATS".
- **`signals/`, `data/companies.csv` and `data/known_good.csv` are private**,
  gitignored *and* in `hygiene.NEVER_COMMIT` so `git add -f` can't sneak them
  back. This is a public repo and each names specific companies — being watched,
  or being applied to. The stages still run and still write their files locally;
  only publishing is off. A fresh clone has none of the three, which is why
  nothing in the suite reads the real ones. Being hand-maintained rather than
  generated is *not* the test for whether something can be committed: the test
  is whether it names companies. `data/companies.example.csv` and
  `data/known_good.example.csv` are committed in their place — same schema,
  invented rows — and `.example.csv` escapes both the gitignore patterns and
  `NEVER_COMMIT`, which match the real filenames exactly.
- **A missing or unpopulated `data/companies.csv` is no longer silent.**
  `companies.load_companies` returns `[]` rather than raising, so a clone
  without it runs the first_tpm lane over zero companies and publishes "No
  matches this week" — indistinguishable from an honest quiet week. The
  committed template does *not* fix this: its tokens are invented, so every row
  404s and is skipped as "not on this ATS", producing the same empty result by
  a different route. The template only makes the schema discoverable so the
  list can be populated for real. `companies.list_state` now names the three
  cases the empty digest used to merge — `absent` (no file), `empty` (a file
  with no data rows) and `populated` — and `fetch_ats` records that plus
  `yielded_nothing` (rows fetched, no company reachable on a supported ATS) in
  the manifest, so "there is no list", "the tokens are all wrong" and "the
  week was quiet" stop being the same green run. Each needs a different fix,
  which is why each gets a different name; only the third is an honest quiet
  week. The run-health viewer surfaces it as a `company list` column, because
  a manifest field nobody opens is a smaller improvement than it looks. What
  is *not* wired is an ntfy alert on those states — `digest` alerts on a
  skipped publish, and the same argument applies here, but adding a new alert
  path changes what the scheduled job does unprompted and is the owner's call.
  `CompaniesExampleTest` loads the template through the real loader so it
  can't drift from the schema, which is a smaller claim: it keeps the
  documentation honest, not the run.
- **`digest` commits exactly `matches/<date>.md` and `data/seen.json`**, and
  `hygiene.DIGEST_PATH_PATTERN` must match that list exactly. When it didn't,
  the 2026-08-17 run rendered and committed its digest, then had the push
  rejected as "not a digest commit" and failed. Nothing re-runs its way out of
  that: digest writes no checkpoint on failure, so every later run repeats the
  same rejection. Adding a path to either side means adding it to both.
- **The pre-push hook must accept the refspec `digest` actually pushes.**
  `digest` pushes `HEAD:{PUBLISH_BRANCH}`, and git reports that local side to
  the hook as the literal string `HEAD` rather than `refs/heads/<name>` — so
  `.githooks/pre-push` stripping `refs/heads/` left `HEAD`, which no branch
  pattern matches. `c405ca0` (2026-08-10) introduced that refspec and that
  branch check *in the same commit*, so they were never compatible, and every
  scheduled digest push from then until 2026-09-03 was rejected: the run
  committed, failed to push, stranded the commit, and reported a bare exit
  status. Three runs failed this way (08-17, 08-24, 08-31). Note the entry
  above blames 08-17 on `DIGEST_PATH_PATTERN` alone — that commit did also
  carry `signals/`, but the branch check runs *first*, so this was the
  rejection it actually hit. The attribution was reconstructed after the fact
  from an error message that had been stripped of its cause, which is the
  same defect the entry below describes. `tests/test_githooks.py` now runs
  the real hook against a real push; nothing else in the suite covers
  `.githooks/`, which is how this survived three runs.
- **A failed subprocess must report both streams.** Every stage that shells
  out (`classify`, `score`, `digest`) captures stdout and stderr, and the
  failure path has to surface whichever one carried the diagnostic. Two
  incidents came from getting this wrong. The `claude` CLI prints an expired
  login to *stdout* and exits 1 with stderr empty, so `classify` — which
  interpolated only `proc.stderr` — logged `(exit 1): ` with nothing after
  the colon, and the 2026-08-31 run discarded 397 fetched listings for a
  cause the log could not name. Separately, `subprocess.CalledProcessError`
  captures stderr but its `str()` shows only the command and exit status, so
  every failed push read as "returned non-zero exit status 1" whether the
  pre-push hook rejected the commit (2026-08-17) or the stored GitHub
  credential had expired (2026-08-24) — telling those apart afterwards took
  commit archaeology. `llm.failure_detail` and `digest.GitError` exist for
  this; use them rather than formatting `proc.stderr` directly.
  `digest.GitError` subclasses `CalledProcessError` on purpose, because
  `_current_branch` and `_unpushed_commit_count` treat a non-zero git exit as
  an expected answer and catch it.
- **A stale tracking ref crashed the hygiene check instead of just failing
  the push, and truncation can eat the cause from either direction.** The
  2026-09-28 scheduled run: PR #32 merged on GitHub 2026-09-25, advancing
  remote `main` to `7916b47`, but this laptop's clone never fetched, so its
  tracking ref (`origin/main`) stayed at the previous digest commit.
  `_unpushed_commit_count` measures `@{u}..HEAD` against that stale ref, so a
  clone that's behind still reports commits unpushed and `digest` pushed
  straight into a guaranteed rejection. `.githooks/pre-push` is handed the
  *actual* remote sha on stdin (not the tracking ref), built the range
  `7916b47..<local>`, and `hygiene._commits_in_range` ran `git rev-list` on
  it — but `7916b47` was never fetched, so it isn't in the local object
  store, and `git rev-list` exits 128 with `fatal: Invalid revision range`.
  `hygiene._git` uses `check=True`, so that raised an unhandled
  `CalledProcessError` straight through the hook: a Python traceback on
  stderr instead of a hygiene message, and the push was rejected with no
  readable cause. The cause was then hidden a second time: `GitError.__str__`'s
  `detail[:500]` is a *head* slice, and a traceback puts its cause (the final
  `subprocess.CalledProcessError: ...` line) at the *end* — the opposite end
  from where git's own `fatal:` lines land — so the one line that actually
  named the problem was exactly the part cut off. Diagnosing it required
  re-running the check by hand. Three fixes, none of which add a `git fetch`
  to `digest` (that changes the stage's network behavior and is the owner's
  call, not a diagnostics fix): `hygiene._commits_in_range` now checks the
  range's left side with `git cat-file -e <sha>^{commit}` before calling
  `rev-list`, and a missing sha becomes a `MissingRevisionError` routed
  through the normal `_report` path — a readable message naming the sha and
  the remedy (fetch, integrate, retry) — while *still blocking the push*: the
  push would be rejected as non-fast-forward regardless, the point is only
  that it says so legibly, not that it fails open or widens the range to
  something that would flag already-published commits.
  `textutil.elide_middle` replaces both head slices (`GitError.__str__`,
  `llm.failure_detail`) with a helper that keeps *both* ends of a capped
  string, because which end carries the cause depends on what produced the
  text. And `digest._git`'s push call now recognizes git's non-fast-forward
  rejection text and raises `RemoteAheadError` (a `GitError` subclass, so
  `_current_branch` and `_unpushed_commit_count`'s existing `except
  CalledProcessError` handling still catches it) stating plainly that the
  remote has moved, the commit is stranded locally, and the next run needs an
  integration before it can publish — rather than repeating the same
  rejection indefinitely, which is the same failure shape as 2026-08-17.
  `tests/test_githooks.py` reproduces the real scenario (a second clone
  advances the shared bare remote, then a digest push is attempted from the
  first, now-stale, clone) and asserts `"Traceback" not in proc.stderr` — that
  assertion is what actually pins this bug; it fails against the pre-fix hook
  with the exact traceback above.

## Repo hygiene

The scheduled job commits from this same working copy, so leaving the repo off
`main` or dirty costs a published week. A `Stop` hook
(`scripts/session-check.sh`) warns about both.

Hygiene rules live in `scripts/hygiene.py` alone, shared by `.githooks/` and CI
so the two can't drift: no files over 2MB, no generated or private output
(`data/runs/`, `logs/`, `data/raw/`, `signals/`, `data/companies.csv`,
`data/known_good.csv`), no
conflict markers, branch names must match
`(feat|fix|chore|docs|test|refactor|ci)/slug`, and only digest commits should
land on `main` directly.

**Where each rule is actually enforced** — the checks are shared, the coverage
isn't:

| Rule | `hygiene.py` command | pre-commit | pre-push | CI |
|---|---|---|---|---|
| File size, never-commit, conflict markers | `staged` / `tracked` | ✅ | — | ✅ |
| Branch name | `branch` | — | ✅ | ✅ (PRs) |
| Digest-only commits on `main` | `push-to-main` | — | ✅ | ❌ |
| Unmerged branches surfaced at session start | `branch-check.sh` | — | — | — |

The last two rows are the gap. `push-to-main` has exactly one caller,
`.githooks/pre-push`, so the rule holds only when the hooks are installed
(`sh scripts/install-hooks.sh`, once per clone) and nobody passes
`--no-verify`. **CI cannot backstop it** — by the time a workflow runs the
commit is already on `main`, so a workflow can only report the violation, not
prevent it.

`branch-check.sh` has the same shape, for a different reason: it runs as a
`SessionStart` hook, so **CI cannot backstop it either** — it has to run
before work starts, and by the time CI runs, the duplicate work it would have
prevented has already happened. Like `push-to-main`, it holds only when the
hooks are installed and running. That makes two rows in the "convention this
repo helps you keep, not one it enforces" column, not one.

Treat "everything else goes through a PR" as a convention this repo helps you
keep, not one it enforces; the only real enforcement would be a branch
protection rule on the remote, which is GitHub-side config and not visible in
this tree. The other rows genuinely do survive a bypassed or uninstalled
hook, because CI re-runs those same checks before merge.

`branch-check.sh` has two honest limits beyond that. It surfaces that a
branch exists; it cannot tell that two branches address the same task — it
converts "remember to look" into "ignore what's on screen," nothing more.
And it reports branches, not PR state: whether a branch has an open PR, and
whether that PR has since merged, lives on GitHub and is not visible in local
refs. Catching that would need a `gh` call, which reintroduces the network
dependency the no-fetch design rules out (see the script's header comment),
so it is deliberately out of scope — a real gap: PR #23's state went stale
mid-session once and was reported wrongly from a cached read.

## Tests

`tests/support.py:PipelineTestCase` repoints `paths.PROJECT_DIR` at a temp dir
per test, so the suite never touches the real `data/`, `logs/`, or `matches/`.
No network: LLM stages run against a stubbed CLI, the six sources against
captured response shapes, and `digest`'s commit/push path against a throwaway
repo with a local bare remote (`init_repo_with_remote`).

The suite deliberately pins behaviours that were previously bugs — snippet
extraction, first-seen-date keying, the score floor, the scoped commit, pushing
a commit stranded by a failed push. Treat a failure in those as a real
regression, not a stale test.

## Environment variables

`SCOUT_LANES`, `SCOUT_MODEL`, `SCOUT_CLASSIFY_CHUNK_SIZE` (50),
`SCOUT_SIGNAL_WINDOW_DAYS` (90), `SCOUT_SIGNAL_MIN_ENG` (8), `SCOUT_ATS_DELAY`,
`SCOUT_EDGAR_DELAY`, `SCOUT_EDGAR_CONTACT`, `CLAUDE_BIN`, `NTFY_TOPIC`.

`SCOUT_EDGAR_CONTACT` is the contact address EDGAR requires in the User-Agent
(`funding.py`). It defaults to a placeholder EDGAR will reject — deliberately,
so the failure is a visible HTTP error rather than someone's real address
sitting in a public repo. `NTFY_TOPIC` is likewise set in the launchd job, not
here.
