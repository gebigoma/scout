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

PROXIMITY_WINDOW = 200


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).lower()


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
    text = _normalize(listing.get("title", "") + " " + listing.get("snippet", ""))
    role_hits = list(ROLE_TERM.finditer(text))
    foundation_hits = list(FOUNDATION_TERM.finditer(text))

    tier1 = any(p in text for p in TIER1_PHRASES)
    tier2 = any(abs(r.start() - f.start()) <= PROXIMITY_WINDOW
               for r in role_hits for f in foundation_hits)
    # Exactly one of the two term families present, without proximity - the
    # cost of the filter is otherwise invisible.
    near_miss = bool(role_hits) != bool(foundation_hits)

    role_fit = tier1 or tier2
    us_eligible = _is_us_eligible(listing)
    return {"passes": role_fit and us_eligible, "tier1": tier1, "tier2": tier2,
            "near_miss": near_miss, "role_fit": role_fit, "us_eligible": us_eligible}


def run(run_date: str, normalize_checkpoint: dict) -> dict:
    logger = logging_setup.get_logger(run_date)
    manifest.stage_started(run_date, "prefilter")

    listings = normalize_checkpoint["listings"]
    passed = []
    filtered = 0
    near_misses = 0
    filtered_non_us = 0
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

    logging_setup.log(logger, "prefilter", "prefiltered listings",
                       passed=len(passed), filtered=filtered, near_misses=near_misses,
                       filtered_non_us=filtered_non_us)
    manifest.stage_succeeded(run_date, "prefilter",
                              passed=len(passed), filtered=filtered, near_misses=near_misses,
                              filtered_non_us=filtered_non_us)
    return checkpoint
