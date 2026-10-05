"""Lane selection: which sourcing lane(s) are active for this run.

Two lanes exist - the original fractional lane (RemoteOK/WWR/HN,
ROLE_CRITERIA.md) and the first-TPM lane (VC-portfolio ATS endpoints,
ROLE_CRITERIA_FIRST_TPM.md). Both run by default; SCOUT_LANES overrides to
a comma-separated subset for debugging a single lane."""
import os

FRACTIONAL = "fractional"
FIRST_TPM = "first_tpm"
ALL = [FRACTIONAL, FIRST_TPM]
DEFAULT = list(ALL)


def active_lanes() -> list:
    raw = os.environ.get("SCOUT_LANES")
    if not raw:
        return list(DEFAULT)
    lanes = [l.strip() for l in raw.split(",") if l.strip()]
    unknown = [l for l in lanes if l not in ALL]
    if unknown:
        raise ValueError(f"unknown lane(s) in SCOUT_LANES: {unknown}")
    return lanes


# Role categories paused without deleting their criteria or their code.
# ROLE_CRITERIA.md still describes a paused role and digest.LANES still carries
# its label, so re-enabling one is removing its entry from this set.
#
# The gate is deterministic and lives in classify (not in the prompt and not in
# digest's rendering): the criteria file is interpolated into the classify
# prompt whole, so the model is still told about a paused role and may still
# return its category. Dropping those verdicts in Python means a pause cannot
# be argued out of by a model, and it keeps ROLE_CRITERIA.md byte-identical.
#
# Nothing paused is scored, published, or recorded in data/seen.json - a
# listing passed over during a pause stays eligible when the role comes back,
# the same way a match below digest.SCORE_FLOOR does.
PAUSED_CATEGORIES = {"agentic_ai_engineer"}


def is_paused(category) -> bool:
    return category in PAUSED_CATEGORIES
