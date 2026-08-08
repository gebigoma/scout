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


def run_dir(run_date: str) -> Path:
    d = PROJECT_DIR / "data" / "runs" / run_date
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


def matches_path(run_date: str) -> Path:
    return PROJECT_DIR / "matches" / f"{run_date}.md"


def signals_path(run_date: str) -> Path:
    return PROJECT_DIR / "signals" / f"{run_date}.md"


def role_criteria_path(lane: str = "fractional") -> Path:
    if lane == "first_tpm":
        return PROJECT_DIR / "ROLE_CRITERIA_FIRST_TPM.md"
    return PROJECT_DIR / "ROLE_CRITERIA.md"


def companies_csv_path() -> Path:
    return PROJECT_DIR / "data" / "companies.csv"


def prompts_dir() -> Path:
    return Path(__file__).resolve().parent / "prompts"
