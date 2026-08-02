"""Digest stage: pure Python, no LLM. Renders matches/<date>.md from the
score checkpoint, updates data/seen.json, and commits+pushes the result."""
import json
import subprocess

from . import logging_setup, manifest, paths

ROLE_LABELS = {
    "senior_tpm": "Senior Technical Program Management",
    "agentic_ai_engineer": "Agentic AI Engineer",
}


def _render_markdown(run_date: str, candidate_count: int, scored: list[dict]) -> str:
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
            title = listing["title"]
            company = listing.get("company") or listing["source"]
            lines.append(f"- **{title}** — {company} (fit: {m['fit_score']}/100)")
            lines.append(f"  {listing['url']}")
            lines.append(f"  {m['rationale']}")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _update_seen(scored: list[dict]) -> None:
    path = paths.seen_path()
    data = {"matched_urls": []}
    if path.exists():
        data = json.loads(path.read_text())
    matched = set(data.get("matched_urls", []))
    matched.update(m["listing"]["url"] for m in scored)
    data["matched_urls"] = sorted(matched)
    path.write_text(json.dumps(data, indent=2))


def _git(*args) -> None:
    subprocess.run(["git", *args], cwd=paths.PROJECT_DIR, check=True,
                    capture_output=True, text=True)


def run(run_date: str, dedupe_checkpoint: dict, score_checkpoint: dict) -> dict:
    logger = logging_setup.get_logger(run_date)
    manifest.stage_started(run_date, "digest")

    try:
        scored = score_checkpoint["scored"]
        candidate_count = len(dedupe_checkpoint["listings"])

        markdown = _render_markdown(run_date, candidate_count, scored)
        paths.matches_path(run_date).write_text(markdown)
        _update_seen(scored)

        commit_paths = [str(paths.matches_path(run_date)), str(paths.seen_path())]
        _git("add", "--", *commit_paths)
        # Scope the diff check and the commit to exactly these two paths, so
        # any unrelated staged changes already sitting in the index (e.g.
        # from other in-progress work) are never swept into this commit.
        status = subprocess.run(
            ["git", "diff", "--cached", "--quiet", "--", *commit_paths],
            cwd=paths.PROJECT_DIR,
        ).returncode
        if status != 0:  # there are staged changes to our two paths
            _git("commit", "-m", f"Weekly matches: {run_date}", "--", *commit_paths)
            _git("push")
            committed = True
        else:
            committed = False
    except Exception as e:
        manifest.stage_failed(run_date, "digest", str(e))
        raise

    checkpoint = {"matches": len(scored), "committed": committed}
    paths.checkpoint_path(run_date, "digest").write_text(json.dumps(checkpoint, indent=2))

    logging_setup.log(logger, "digest", "wrote digest", matches=len(scored), committed=committed)
    manifest.stage_succeeded(run_date, "digest", matches=len(scored), committed=committed)
    manifest.run_succeeded(run_date)
    return checkpoint
