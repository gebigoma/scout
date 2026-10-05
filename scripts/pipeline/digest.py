"""Digest stage: pure Python, no LLM. Renders matches/<date>.md from the
score checkpoint, updates data/seen.json, and commits+pushes the result."""
import json
import re
import subprocess

from . import alert, lanes, logging_setup, manifest, paths, textutil

# The scheduled job runs against whatever the working copy happens to be
# checked out on, which for a repo that's also actively developed in is not
# reliably main. Publishing from a feature branch put two "Weekly matches"
# commits on branches that then had to be reverted, so the branch is checked
# rather than assumed.
PUBLISH_BRANCH = "main"

# One top-level heading per lane, so a paused lane renders no heading at all
# and it's obvious at a glance which matches came from which lane.
LANES = {
    lanes.FRACTIONAL: {
        "label": "Fractional Roles",
        "sources": "RemoteOK, We Work Remotely (Programming + Product), HN \"Who is hiring\"",
        "criteria": "ROLE_CRITERIA.md",
        "categories": {
            "senior_tpm": "Senior Technical Program Management",
            "agentic_ai_engineer": "Agentic AI Engineer",
        },
    },
    lanes.FIRST_TPM: {
        "label": "First TPM",
        "sources": "Greenhouse, Ashby, and Lever boards of VC-portfolio companies",
        "criteria": "ROLE_CRITERIA_FIRST_TPM.md",
        "categories": {
            "first_tpm": "First Technical Program Manager",
            # Role fit is clear, foundation status isn't stated either way.
            # Its own section on purpose: mixing unconfirmed listings in with
            # evidenced ones would make the evidenced section's claim weaker
            # without telling the reader it had changed.
            "tpm_unconfirmed": "TPM role — foundation status unstated",
        },
    },
}

# Per the fit score guide in ROLE_CRITERIA.md, anything below this shouldn't
# have cleared the match bar in the first place - a low score here means the
# score stage disagreed with the classify stage. Publish those separately
# rather than mixing them in with real matches.
SCORE_FLOOR = 35


def active_categories(lane: str) -> dict:
    """A lane's categories minus any paused role. Paused entries stay in LANES
    so the label survives for the preamble and for re-enabling."""
    return {category: label
            for category, label in LANES[lane]["categories"].items()
            if not lanes.is_paused(category)}


def paused_categories(lane: str) -> dict:
    return {category: label
            for category, label in LANES[lane]["categories"].items()
            if lanes.is_paused(category)}


def _render_markdown(run_date: str, candidate_count: int,
                     scored: list, rejected: list, active_lanes: list = None) -> str:
    active_lanes = active_lanes if active_lanes is not None else [lanes.FRACTIONAL]
    lines = [f"# Matches — {run_date}", ""]
    sources = "; ".join(LANES[lane]["sources"] for lane in active_lanes)
    criteria_links = " and ".join(
        f"[`{LANES[lane]['criteria']}`](../{LANES[lane]['criteria']})" for lane in active_lanes
    )
    lines.append(
        f"Sources: {sources}. {candidate_count} candidate listings reviewed "
        f"against {criteria_links}."
    )
    lines.append("")

    # The criteria files are linked above and still describe every role,
    # including paused ones. Without this line a reader would take the
    # criteria at face value and read a paused role's absence as a quiet
    # week for it, which is the one thing this digest is supposed never to
    # be ambiguous about.
    paused_labels = [label for lane in active_lanes
                     for label in paused_categories(lane).values()]
    if paused_labels:
        lines.append(
            f"Paused: {', '.join(paused_labels)} — criteria retained, not "
            f"matched this run."
        )
        lines.append("")

    for lane in active_lanes:
        lane_info = LANES[lane]
        lines.append(f"## {lane_info['label']}")
        lines.append("")
        for category, label in active_categories(lane).items():
            lines.append(f"### {label}")
            lines.append("")
            category_matches = sorted(
                (m for m in scored if m["role_category"] == category),
                key=lambda m: m["fit_score"], reverse=True,
            )
            if not category_matches:
                lines.append("No matches this week.")
                lines.append("")
                continue
            for m in category_matches:
                listing = m["listing"]
                company = listing.get("company") or listing["source"]
                lines.append(f"- **{listing['title']}** — {company} (fit: {m['fit_score']}/100)")
                lines.append(f"  {listing['url']}")
                lines.append(f"  {m['rationale']}")
                lines.append("")

    if rejected:
        lines.append("## Rejected on scoring")
        lines.append("")
        lines.append(
            f"Cleared the initial match bar but scored below {SCORE_FLOOR}/100 — "
            f"kept here for auditability, and deliberately *not* recorded as "
            f"seen, so they can resurface if re-posted."
        )
        lines.append("")
        for m in sorted(rejected, key=lambda m: m["fit_score"], reverse=True):
            listing = m["listing"]
            company = listing.get("company") or listing["source"]
            lines.append(f"- **{listing['title']}** — {company} (fit: {m['fit_score']}/100)")
            lines.append(f"  {listing['url']}")
            lines.append(f"  {m['rationale']}")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _update_seen(run_date: str, scored: list) -> None:
    """Record published matches as seen, keyed by the date they were first
    surfaced (see dedupe.load_seen for why the date matters)."""
    path = paths.seen_path()
    seen = {}
    if path.exists():
        data = json.loads(path.read_text())
        seen = data.get("seen") or {url: "" for url in data.get("matched_urls", [])}
    for m in scored:
        seen.setdefault(m["listing"]["url"], run_date)
    # data/ exists in practice only because fetch's run dir created it; don't
    # make publishing depend on that side effect.
    path.parent.mkdir(parents=True, exist_ok=True)
    paths.atomic_write_json(path, {"seen": dict(sorted(seen.items()))})


class GitError(subprocess.CalledProcessError):
    """A failed git command that says what git actually printed.

    The base class captures stderr but its str() shows only the command and
    the exit status, so every failed push reached the log as the same
    "returned non-zero exit status 1" - identical text whether the pre-push
    hook rejected the commit or the stored credential had expired. Those were
    the real causes on 2026-08-17 and 2026-08-24 respectively, and telling
    them apart afterwards took commit archaeology rather than a log read.

    Subclassing rather than raising a fresh type keeps `except
    CalledProcessError` callers below working unchanged: for them a non-zero
    git exit is an expected answer (no branch, no upstream), not a failure.
    """

    def __str__(self) -> str:
        detail = (self.stderr or "").strip() or (self.output or "").strip()
        base = super().__str__()
        # rstrip the base's trailing period so the appended detail doesn't
        # read as ".: fatal: ...". elide_middle (not a head slice) so a
        # traceback-shaped detail - whose cause is its LAST line - doesn't
        # lose that line the way the 2026-09-28 incident did; see
        # textutil.elide_middle's docstring.
        return f"{base.rstrip('.')}: {textutil.elide_middle(detail)}" if detail else base


class RemoteAheadError(GitError):
    """Raised when `git push` is rejected because origin/main has moved past
    what this clone knows about.

    On 2026-09-28 a PR merged on GitHub without this laptop's clone ever
    fetching, so the tracking ref (`@{u}`) `_unpushed_commit_count` checks
    stayed stale: it still reported the digest commit as unpushed, so the run
    pushed straight into a guaranteed non-fast-forward rejection. Because
    digest writes no checkpoint on failure, every later run would repeat that
    rejection identically until someone intervened - the same shape as the
    2026-08-17 incident. This turns that outcome into a named, readable one
    instead of a bare "returned non-zero exit status 1".

    Subclasses GitError (itself a CalledProcessError) rather than composing a
    plain exception, because _current_branch and _unpushed_commit_count both
    catch CalledProcessError and treat a non-zero git exit as an expected
    answer, not a crash - a fresh, unrelated exception type would break that.
    """

    def __str__(self) -> str:
        detail = (self.stderr or self.output or "").strip()
        return (
            "origin/main has moved past this clone - the push was rejected "
            "as non-fast-forward. The commit this run just made is stranded "
            "locally; fetch and integrate origin/main (git fetch origin && "
            "git rebase origin/main, or merge) before the next scheduled run "
            "publishes, or it will hit this same rejection every time."
            + (f" git said: {textutil.elide_middle(detail)}" if detail else "")
        )


# Substrings git uses across its various "the remote has commits I don't"
# rejection messages - the hint text differs by git version, but these are
# stable. Checked against stderr only: `_looks_like_non_fast_forward` is
# meant to recognise "the push itself was rejected because the remote moved",
# not any git failure that happens to mention "rejected" elsewhere.
_NON_FAST_FORWARD_SIGNATURE = re.compile(
    r"\[rejected\]|non-fast-forward|fetch first|fetch again", re.IGNORECASE)


def _looks_like_non_fast_forward(stderr: str) -> bool:
    return bool(_NON_FAST_FORWARD_SIGNATURE.search(stderr or ""))


def _git(*args) -> str:
    proc = subprocess.run(["git", *args], cwd=paths.PROJECT_DIR,
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise GitError(proc.returncode, ["git", *args],
                       output=proc.stdout, stderr=proc.stderr)
    return proc.stdout.strip()


def _current_branch() -> str:
    """The checked-out branch name, or "" when detached or not a repo. Both
    non-branch states are treated the same way by the caller: don't publish."""
    try:
        return _git("symbolic-ref", "--quiet", "--short", "HEAD")
    except subprocess.CalledProcessError:
        return ""


def _unpushed_commit_count() -> int:
    """How many commits are on HEAD but not on its upstream. Used instead of
    the index state to decide whether a push is needed: if a previous run
    committed but failed to push, re-running finds nothing staged, and
    checking the index alone would wrongly report success and leave the
    commit stranded."""
    try:
        return int(_git("rev-list", "--count", "@{u}..HEAD") or 0)
    except subprocess.CalledProcessError:
        return 0  # no upstream configured - nothing we can reason about


def run(run_date: str, dedupe_checkpoint: dict, score_checkpoint: dict,
       active_lanes: list = None) -> dict:
    logger = logging_setup.get_logger(run_date)
    manifest.stage_started(run_date, "digest")
    active_lanes = active_lanes if active_lanes is not None else lanes.active_lanes()

    try:
        all_scored = score_checkpoint["scored"]
        scored = [m for m in all_scored if m["fit_score"] >= SCORE_FLOOR]
        rejected = [m for m in all_scored if m["fit_score"] < SCORE_FLOOR]
        candidate_count = len(dedupe_checkpoint["listings"])

        markdown = _render_markdown(run_date, candidate_count, scored, rejected, active_lanes)
        paths.matches_path(run_date).parent.mkdir(parents=True, exist_ok=True)
        paths.matches_path(run_date).write_text(markdown)
        # Only published matches are recorded as seen - a listing rejected on
        # scoring should stay eligible for reconsideration later.
        _update_seen(run_date, scored)

        # company_signals still renders signals/<date>.md, but it is never
        # committed: the watchlist names which companies are being watched and
        # why, which is local-only by decision. signals/ is gitignored and
        # hygiene.NEVER_COMMIT blocks it, so this list must stay exactly
        # matches + seen.
        commit_paths = [str(paths.matches_path(run_date)), str(paths.seen_path())]
        branch = _current_branch()
        if branch != PUBLISH_BRANCH:
            # Leave the rendered files on disk - the digest is still the useful
            # output of the run - but don't create a commit that lands on
            # whatever feature branch was left checked out and has to be
            # reverted afterwards.
            committed = pushed = False
            publish_skipped = branch or "a detached HEAD"
            alert.send_publish_skipped_alert(run_date, publish_skipped)
        else:
            publish_skipped = None
            _git("add", "--", *commit_paths)
            # Scope the diff check and the commit to exactly these two paths, so
            # any unrelated staged changes already sitting in the index (e.g.
            # from other in-progress work) are never swept into this commit.
            has_staged_changes = subprocess.run(
                ["git", "diff", "--cached", "--quiet", "--", *commit_paths],
                cwd=paths.PROJECT_DIR,
            ).returncode != 0
            if has_staged_changes:
                _git("commit", "-m", f"Weekly matches: {run_date}", "--", *commit_paths)

            unpushed = _unpushed_commit_count()
            if unpushed:
                # Name the destination explicitly instead of a bare `git push`,
                # which would follow push.default and send every unpushed commit
                # on the branch wherever main happens to track.
                try:
                    _git("push", "origin", f"HEAD:{PUBLISH_BRANCH}")
                except GitError as e:
                    # _unpushed_commit_count measures @{u}..HEAD - the STALE
                    # tracking ref, not what origin/main actually is now. A
                    # clone that never fetched still sees unpushed>=1 and
                    # pushes straight into this rejection; say so plainly
                    # instead of letting it read as an ordinary push failure.
                    if _looks_like_non_fast_forward(e.stderr or ""):
                        raise RemoteAheadError(e.returncode, e.cmd,
                                               output=e.output, stderr=e.stderr) from e
                    raise
            committed = has_staged_changes
            pushed = bool(unpushed)
    except Exception as e:
        manifest.stage_failed(run_date, "digest", str(e))
        raise

    checkpoint = {"matches": len(scored), "rejected": len(rejected),
                  "committed": committed, "pushed": pushed,
                  "publish_skipped": publish_skipped}
    paths.atomic_write_json(paths.checkpoint_path(run_date, "digest"), checkpoint)

    logging_setup.log(logger, "digest", "wrote digest", matches=len(scored),
                       rejected_below_floor=len(rejected), committed=committed,
                       pushed=pushed, publish_skipped=publish_skipped)
    manifest.stage_succeeded(run_date, "digest", matches=len(scored),
                              classify_disagreements=len(rejected),
                              committed=committed, pushed=pushed)
    return checkpoint
