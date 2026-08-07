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
