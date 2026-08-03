"""Digest stage: pure Python, no LLM. Renders matches/<date>.md from the
score checkpoint, updates data/seen.json, and commits+pushes the result."""
import json
import subprocess

from . import logging_setup, manifest, paths

ROLE_LABELS = {
    "senior_tpm": "Senior Technical Program Management",
    "agentic_ai_engineer": "Agentic AI Engineer",
}

# Per the fit score guide in ROLE_CRITERIA.md, anything below this shouldn't
# have cleared the match bar in the first place - a low score here means the
# score stage disagreed with the classify stage. Publish those separately
# rather than mixing them in with real matches.
SCORE_FLOOR = 35


def _render_markdown(run_date: str, candidate_count: int,
                     scored: list, rejected: list) -> str:
    lines = [f"# Matches — {run_date}", ""]
    lines.append(
        f"Sources: RemoteOK, We Work Remotely (Programming + Product), "
        f"HN \"Who is hiring\". {candidate_count} candidate listings reviewed "
        f"against [`ROLE_CRITERIA.md`](../ROLE_CRITERIA.md)."
    )
    lines.append("")

    for category, label in ROLE_LABELS.items():
        lines.append(f"## {label}")
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
    paths.atomic_write_json(path, {"seen": dict(sorted(seen.items()))})


def _git(*args) -> str:
    return subprocess.run(["git", *args], cwd=paths.PROJECT_DIR, check=True,
                           capture_output=True, text=True).stdout.strip()


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


def run(run_date: str, dedupe_checkpoint: dict, score_checkpoint: dict) -> dict:
    logger = logging_setup.get_logger(run_date)
    manifest.stage_started(run_date, "digest")

    try:
        all_scored = score_checkpoint["scored"]
        scored = [m for m in all_scored if m["fit_score"] >= SCORE_FLOOR]
        rejected = [m for m in all_scored if m["fit_score"] < SCORE_FLOOR]
        candidate_count = len(dedupe_checkpoint["listings"])

        markdown = _render_markdown(run_date, candidate_count, scored, rejected)
        paths.matches_path(run_date).parent.mkdir(parents=True, exist_ok=True)
        paths.matches_path(run_date).write_text(markdown)
        # Only published matches are recorded as seen - a listing rejected on
        # scoring should stay eligible for reconsideration later.
        _update_seen(run_date, scored)

        commit_paths = [str(paths.matches_path(run_date)), str(paths.seen_path())]
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
            _git("push")
        committed = has_staged_changes
        pushed = bool(unpushed)
    except Exception as e:
        manifest.stage_failed(run_date, "digest", str(e))
        raise

    checkpoint = {"matches": len(scored), "rejected": len(rejected),
                  "committed": committed, "pushed": pushed}
    paths.atomic_write_json(paths.checkpoint_path(run_date, "digest"), checkpoint)

    logging_setup.log(logger, "digest", "wrote digest", matches=len(scored),
                       rejected_below_floor=len(rejected), committed=committed,
                       pushed=pushed)
    manifest.stage_succeeded(run_date, "digest", matches=len(scored),
                              classify_disagreements=len(rejected),
                              committed=committed, pushed=pushed)
    return checkpoint
