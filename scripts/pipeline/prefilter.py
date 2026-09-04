"""Prefilter stage (between normalize and dedupe): cuts the first-TPM
lane's candidate set before it reaches classify, since a few hundred
companies' worth of postings is a much larger pool than the three
fractional-lane job boards.

Two-tier, loose, classify confirms:
  - Tier 1: exact thesis phrases always pass.
  - Tier 2: a role term near a foundation term also passes; classify does
    the real judging.

Matching runs over `match_text` - the *whole* description, which
normalize carries on first_tpm listings for exactly this reason - not
over `snippet`. Matching the snippet is what made this lane return zero
candidates for its entire life (see the note above `_match_text`).

Word-boundary matching on "tpm" is required - this lane sources heavily
from infra/security companies where "TPM" means Trusted Platform Module
(vTPM, tpmd), not Technical Program Manager. A substring match would hit
those constantly.

Also enforces US work-location eligibility for this lane: a listing tied
to a specific non-US country is dropped even if it's otherwise a strong
role-fit match, since the user can't take a role that requires being
located elsewhere.

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

# Sentences worth rescuing from past normalize's snippet head for this
# lane. Defined here, next to the matchers they're built from, so the
# evidence normalize preserves and the evidence prefilter looks for can't
# drift apart - they are the same vocabulary by construction.
EVIDENCE_TERM = re.compile(
    ROLE_TERM.pattern + "|" + FOUNDATION_TERM.pattern, re.IGNORECASE)

PROXIMITY_WINDOW = 200


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).lower()


def _match_text(listing: dict) -> str:
    """The text tier-2 proximity is measured over.

    Must be the full, contiguous description (`match_text`), never the
    `snippet`. Two reasons, both of which this stage got wrong until
    2026-09-03:

    1. The snippet is a 400-character head, and the terms this stage
       looks for are not in the first 400 characters of a job
       description. Over the 2026-08-17 corpus, 37 of 2018 first_tpm
       listings carried a role term in the full text and only 9 carried
       one in the snippet - the stage was blind to 76% of its own input,
       which is why the lane produced zero candidates on every run.
    2. A snippet is *stitched*: `head + " […] " + salvaged sentences`.
       A character distance measured across that seam is a distance that
       does not exist in the document, in either direction - terms 6,000
       characters apart can read as adjacent, and adjacent terms can read
       as far apart. Proximity is only meaningful over contiguous text.

    Falls back to the snippet for listings with no `match_text`, which is
    every fractional-lane listing - they never reach the matchers below.
    """
    return listing.get("match_text") or listing.get("snippet", "")


# US-eligibility allowlist: keep unless a specific non-US country is named.
# lever gives an ISO-3166 alpha-2 `location_country_code`, checked directly
# and authoritatively - a non-US code disqualifies regardless of what any
# free-text field says (Armada's Greenhouse posting had location.name =
# "Australia (Remote)" *and* a custom metadata field claiming "United States
# (Remote)" for the same listing; erring toward dropping is the point, not
# a bug to reconcile). greenhouse/ashby only give free text, checked against
# this country-name list instead.
#
# Known false-positive: "Georgia" is both a country and a US state, and
# nothing here disambiguates them - a domestic listing that spells out the
# state name in full (rather than "GA") gets misfiltered. Not solved here;
# Greenhouse/Ashby overwhelmingly abbreviate US states, so this is judged a
# rare miss against the more common risk of under-filtering.
NON_US_COUNTRIES = [
    "Afghanistan", "Albania", "Algeria", "Andorra", "Angola",
    "Antigua and Barbuda", "Argentina", "Armenia", "Australia", "Austria",
    "Azerbaijan", "Bahamas", "Bahrain", "Bangladesh", "Barbados", "Belarus",
    "Belgium", "Belize", "Benin", "Bhutan", "Bolivia",
    "Bosnia and Herzegovina", "Botswana", "Brazil", "Brunei", "Bulgaria",
    "Burkina Faso", "Burundi", "Cambodia", "Cameroon", "Canada",
    "Cape Verde", "Central African Republic", "Chad", "Chile", "China",
    "Colombia", "Comoros", "Costa Rica", "Croatia", "Cuba", "Cyprus",
    "Czechia", "Czech Republic", "Denmark", "Djibouti", "Dominica",
    "Dominican Republic", "Ecuador", "Egypt", "El Salvador",
    "Equatorial Guinea", "Eritrea", "Estonia", "Eswatini", "Ethiopia",
    "Fiji", "Finland", "France", "Gabon", "Gambia", "Georgia", "Germany",
    "Ghana", "Greece", "Grenada", "Guatemala", "Guinea-Bissau", "Guinea",
    "Guyana", "Haiti", "Honduras", "Hungary", "Iceland", "India",
    "Indonesia", "Iran", "Iraq", "Ireland", "Israel", "Italy",
    "Ivory Coast", "Jamaica", "Japan", "Jordan", "Kazakhstan", "Kenya",
    "Kiribati", "Kosovo", "Kuwait", "Kyrgyzstan", "Laos", "Latvia",
    "Lebanon", "Lesotho", "Liberia", "Libya", "Liechtenstein", "Lithuania",
    "Luxembourg", "Madagascar", "Malawi", "Malaysia", "Maldives", "Mali",
    "Malta", "Marshall Islands", "Mauritania", "Mauritius", "Mexico",
    "Micronesia", "Moldova", "Monaco", "Mongolia", "Montenegro", "Morocco",
    "Mozambique", "Myanmar", "Namibia", "Nauru", "Nepal", "Netherlands",
    "New Zealand", "Nicaragua", "Niger", "Nigeria", "North Korea",
    "North Macedonia", "Norway", "Oman", "Pakistan", "Palau", "Palestine",
    "Panama", "Papua New Guinea", "Paraguay", "Peru", "Philippines",
    "Poland", "Portugal", "Qatar", "Romania", "Russia", "Rwanda",
    "Saint Kitts and Nevis", "Saint Lucia",
    "Saint Vincent and the Grenadines", "Samoa", "San Marino",
    "Sao Tome and Principe", "Saudi Arabia", "Senegal", "Serbia",
    "Seychelles", "Sierra Leone", "Singapore", "Slovakia", "Slovenia",
    "Solomon Islands", "Somalia", "South Africa", "South Korea",
    "South Sudan", "Spain", "Sri Lanka", "Sudan", "Suriname", "Sweden",
    "Switzerland", "Syria", "Taiwan", "Tajikistan", "Tanzania", "Thailand",
    "Timor-Leste", "Togo", "Tonga", "Trinidad and Tobago", "Tunisia",
    "Turkey", "Turkmenistan", "Tuvalu", "Uganda", "Ukraine",
    "United Arab Emirates", "United Kingdom", "Uruguay", "Uzbekistan",
    "Vanuatu", "Vatican City", "Venezuela", "Vietnam", "Yemen", "Zambia",
    "Zimbabwe", "U.K.", "UK",
]
NON_US_COUNTRY_TERM = re.compile(
    r"\b(" + "|".join(re.escape(c) for c in NON_US_COUNTRIES) + r")\b",
    re.IGNORECASE,
)


def _is_us_eligible(listing: dict) -> bool:
    code = (listing.get("location_country_code") or "").strip().upper()
    if code and code != "US":
        return False
    return not NON_US_COUNTRY_TERM.search(listing.get("location_text", "") or "")


def evaluate(listing: dict) -> dict:
    text = _normalize(listing.get("title", "") + " " + _match_text(listing))
    role_hits = list(ROLE_TERM.finditer(text))
    foundation_hits = list(FOUNDATION_TERM.finditer(text))

    tier1 = any(p in text for p in TIER1_PHRASES)
    tier2 = any(abs(r.start() - f.start()) <= PROXIMITY_WINDOW
               for r in role_hits for f in foundation_hits)

    role_fit = tier1 or tier2
    # A role term was present and we dropped it anyway - the cost of the
    # filter is otherwise invisible.
    #
    # This used to mean "exactly one of the two families present", which was
    # survivable while matching ran over a 400-char snippet and stopped being
    # so the moment it ran over whole descriptions: foundation vocabulary
    # ("first", "establish", "build out") is ordinary job-description filler,
    # present in 1349 of 2018 listings on the 2026-08-17 corpus. Under the old
    # definition that is 1349 "near misses" a run, one logged line each, which
    # names nothing and buries the ~30 drops actually worth reading. A role
    # term is the rare half (37 of 2018) and the half whose loss you'd want to
    # investigate, so it's the half that defines the signal.
    near_miss = bool(role_hits) and not role_fit
    us_eligible = _is_us_eligible(listing)
    return {"passes": role_fit and us_eligible, "tier1": tier1, "tier2": tier2,
            "near_miss": near_miss, "role_fit": role_fit, "us_eligible": us_eligible,
            "role_term": bool(role_hits)}


def run(run_date: str, normalize_checkpoint: dict) -> dict:
    logger = logging_setup.get_logger(run_date)
    manifest.stage_started(run_date, "prefilter")

    listings = normalize_checkpoint["listings"]
    passed = []
    filtered = 0
    near_misses = 0
    filtered_non_us = 0
    # How much raw material the lane had to work with, separate from how much
    # survived. Reported even when it's zero - a lane that passes nothing
    # because it saw no role terms and a lane that passes nothing because its
    # matching is broken produce the same empty digest, and telling them apart
    # after the fact previously took reading the checkpoints by hand.
    role_terms_seen = 0
    first_tpm_seen = 0
    for listing in listings:
        if listing.get("lane") != "first_tpm":
            passed.append(listing)
            continue
        first_tpm_seen += 1
        result = evaluate(listing)
        role_terms_seen += result["role_term"]
        if result["passes"]:
            # `match_text` is a whole job description and this is the last
            # stage that needs it; carrying it into dedupe/classify would
            # bloat those checkpoints for no reader.
            passed.append({k: v for k, v in listing.items() if k != "match_text"})
        else:
            filtered += 1
            if result["near_miss"]:
                near_misses += 1
                logging_setup.log(logger, "prefilter", "near-miss filtered",
                                   url=listing.get("url", ""))
            # Otherwise-passing but for location - logged separately from
            # role/foundation near-misses so a quiet week is diagnosable:
            # was nothing role-fit, or was it fit-but-not-workable?
            if result["role_fit"] and not result["us_eligible"]:
                filtered_non_us += 1
                logging_setup.log(logger, "prefilter", "non-US location filtered",
                                   url=listing.get("url", ""),
                                   location_text=listing.get("location_text", ""),
                                   location_country_code=listing.get("location_country_code", ""))

    checkpoint = {"listings": passed}
    paths.atomic_write_json(paths.checkpoint_path(run_date, "prefilter"), checkpoint)

    counts = dict(passed=len(passed), filtered=filtered, near_misses=near_misses,
                  filtered_non_us=filtered_non_us, role_terms_seen=role_terms_seen,
                  first_tpm_seen=first_tpm_seen)
    logging_setup.log(logger, "prefilter", "prefiltered listings", **counts)
    manifest.stage_succeeded(run_date, "prefilter", **counts)
    return checkpoint
