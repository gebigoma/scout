"""Prefilter stage (between normalize and dedupe): cuts the first-TPM
lane's candidate set before it reaches classify, since a few hundred
companies' worth of postings is a much larger pool than the three
fractional-lane job boards.

Two-tier, loose, classify confirms:
  - Tier 1: exact thesis phrases always pass.
  - Tier 2: a role term near a foundation term also passes; classify does
    the real judging.

Word-boundary matching on "tpm" is required - this lane sources heavily
from infra/security companies where "TPM" means Trusted Platform Module
(vTPM, tpmd), not Technical Program Manager. A substring match would hit
those constantly.

Only applies to first_tpm-lane listings; fractional-lane listings pass
through untouched."""
import re

from . import logging_setup, manifest, paths

TIER1_PHRASES = [
    "first technical program manager",
    "our second tpm",
    "establish the program management function",
    "build out our tpm practice",
    "first tpm hire",
]

ROLE_TERM = re.compile(r"technical program manager|program management|\btpm\b", re.IGNORECASE)
FOUNDATION_TERM = re.compile(
    r"\bfirst\b|\bsecond\b|\bfounding\b|\bestablish\b|\bbuild out\b|\bstand up\b|"
    r"\bfrom scratch\b|\b0 to 1\b|\bno one in this role\b", re.IGNORECASE)

PROXIMITY_WINDOW = 200


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).lower()


def evaluate(listing: dict) -> dict:
    text = _normalize(listing.get("title", "") + " " + listing.get("snippet", ""))
    role_hits = list(ROLE_TERM.finditer(text))
    foundation_hits = list(FOUNDATION_TERM.finditer(text))

    tier1 = any(p in text for p in TIER1_PHRASES)
    tier2 = any(abs(r.start() - f.start()) <= PROXIMITY_WINDOW
               for r in role_hits for f in foundation_hits)
    # Exactly one of the two term families present, without proximity - the
    # cost of the filter is otherwise invisible.
    near_miss = bool(role_hits) != bool(foundation_hits)

    return {"passes": tier1 or tier2, "tier1": tier1, "tier2": tier2, "near_miss": near_miss}


def run(run_date: str, normalize_checkpoint: dict) -> dict:
    logger = logging_setup.get_logger(run_date)
    manifest.stage_started(run_date, "prefilter")

    listings = normalize_checkpoint["listings"]
    passed = []
    filtered = 0
    near_misses = 0
    for listing in listings:
        if listing.get("lane") != "first_tpm":
            passed.append(listing)
            continue
        result = evaluate(listing)
        if result["passes"]:
            passed.append(listing)
        else:
            filtered += 1
            if result["near_miss"]:
                near_misses += 1
                logging_setup.log(logger, "prefilter", "near-miss filtered",
                                   url=listing.get("url", ""))

    checkpoint = {"listings": passed}
    paths.atomic_write_json(paths.checkpoint_path(run_date, "prefilter"), checkpoint)

    logging_setup.log(logger, "prefilter", "prefiltered listings",
                       passed=len(passed), filtered=filtered, near_misses=near_misses)
    manifest.stage_succeeded(run_date, "prefilter",
                              passed=len(passed), filtered=filtered, near_misses=near_misses)
    return checkpoint
