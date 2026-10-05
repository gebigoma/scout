import json
import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent


def atomic_write_json(path: Path, obj) -> None:
    """Write JSON via a temp file + os.replace, so an interrupted run (laptop
    sleeps, launchd kills the job) can never leave a truncated file behind.
    A half-written checkpoint would otherwise wedge that date permanently -
    and a half-written seen.json would wedge every future run."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2))
    os.replace(tmp, path)


def atomic_write_jsonl(path: Path, rows) -> None:
    """Same discipline as atomic_write_json, for line-delimited JSON: a temp
    file + os.replace so a corpus file is never observed half-written. A
    sibling of atomic_write_json rather than a bent version of it, since
    JSONL wants one compact object per line (hand-appendable later) instead
    of one indented document."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("".join(json.dumps(row) + "\n" for row in rows))
    os.replace(tmp, path)


def runs_dir() -> Path:
    """The parent of every run directory. Deliberately does NOT mkdir, unlike
    run_dir: a read-only consumer (the viewer) listing runs must never create
    data/runs/ as a side effect."""
    return PROJECT_DIR / "data" / "runs"


def run_dir(run_date: str) -> Path:
    d = runs_dir() / run_date
    d.mkdir(parents=True, exist_ok=True)
    return d


def checkpoint_path(run_date: str, stage: str) -> Path:
    return run_dir(run_date) / f"{stage}.json"


def classify_chunks_dir(run_date: str) -> Path:
    d = run_dir(run_date) / "classify_chunks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def classify_chunk_path(run_date: str, chunk_index: int) -> Path:
    return classify_chunks_dir(run_date) / f"{chunk_index}.json"


def manifest_path(run_date: str) -> Path:
    return run_dir(run_date) / "manifest.json"


def logs_dir() -> Path:
    d = PROJECT_DIR / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def seen_path() -> Path:
    return PROJECT_DIR / "data" / "seen.json"


def matches_dir() -> Path:
    """The directory of published digests. Like runs_dir, deliberately does NOT
    mkdir: a read-only consumer (the viewer's digest archive) must not create
    matches/ as a side effect."""
    return PROJECT_DIR / "matches"


def matches_path(run_date: str) -> Path:
    return matches_dir() / f"{run_date}.md"


def signals_path(run_date: str) -> Path:
    return PROJECT_DIR / "signals" / f"{run_date}.md"


def corpus_dir() -> Path:
    d = PROJECT_DIR / "data" / "corpus"
    d.mkdir(parents=True, exist_ok=True)
    return d


def corpus_path(run_date: str) -> Path:
    return corpus_dir() / f"{run_date}.jsonl"


def role_criteria_path(lane: str = "fractional") -> Path:
    if lane == "first_tpm":
        return PROJECT_DIR / "ROLE_CRITERIA_FIRST_TPM.md"
    return PROJECT_DIR / "ROLE_CRITERIA.md"


def companies_csv_path() -> Path:
    return PROJECT_DIR / "data" / "companies.csv"


def company_candidates_path() -> Path:
    """Review queue written by scripts/discover_companies.py. Private for the
    same reason companies.csv is: it names companies."""
    return PROJECT_DIR / "data" / "company_candidates.csv"


def company_candidates_dismissed_path() -> Path:
    """Boards a person has reviewed and rejected - agencies, wrong-stage
    companies - so the regenerated review queue stops offering them. Hand
    maintained, and private for the same reason: it names companies."""
    return PROJECT_DIR / "data" / "company_candidates_dismissed.csv"


def prompts_dir() -> Path:
    return Path(__file__).resolve().parent / "prompts"


def viewer_dir() -> Path:
    d = PROJECT_DIR / "viewer"
    d.mkdir(parents=True, exist_ok=True)
    return d


def viewer_index_path() -> Path:
    return viewer_dir() / "index.html"


def viewer_digests_path() -> Path:
    return viewer_dir() / "digests.html"
