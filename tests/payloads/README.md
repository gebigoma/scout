# Captured ATS payloads

Real `fetch_ats` entries (`{status, company, jobs}`), one posting each, taken
from retained runs between 2026-08-31 and 2026-10-05. The structure and
markup are real; the prose has been rewritten for a public repo. `tests/test_prefilter.py` runs them through `normalize` and then
`prefilter`. Hand-written fixtures only exercise each stage on its own, and
issue #13 was a bug in what `normalize` hands `prefilter`.

| File | What it pins |
|---|---|
| `ashby/founding_tpm` | tier 1: "first Technical Program Manager" |
| `greenhouse/title_only_tpm` | tier 3: TPM title, evidence too far apart for tier 2 |
| `greenhouse/evidence_past_snippet_cap` | role term ~7,500 chars in, invisible to the snippet (#13's direction) |
| `greenhouse/stitched_false_adjacency` | "not a program management role"; the stitched snippet fakes tier-2 proximity |
| `ashby/program_manager_not_tpm` | a program-manager title `ROLE_TERM` must not match |
| `greenhouse/non_us_tpm` | `location.name` says Australia, a metadata field says US |
| `ashby/us_and_canada_tpm` | open in US *and* Canada, passes (#46) |
| `lever/no_role_term` | `country` code; the role lives in `lists`, which normalize must read (#44) |

## Kept from the real payload

- JSON structure, field names and nesting, and every non-description field:
  locations, dates, titles, departments, flags.
- The markup each ATS uses. That means Greenhouse's entity-escaped `content`
  with its wrapper divs, pay-transparency block, Word-export spans and
  `&nbsp;` runs. Ashby sends a `descriptionHtml`/`descriptionPlain` pair, and
  Lever an opening plus a `lists` array.
- The section layout of each ad (about, role, responsibilities,
  requirements, benefits, legal), and its mix of role terms, foundation
  filler ("first", "establish", "build out") and employment terms.

## Rewritten

- **Prose.** Every description was rewritten from scratch, not edited.
  Seven-word runs shared with the original are limited to legal boilerplate,
  location lines and section headings. Each rewrite keeps the term placement
  its tests depend on, so it is shorter than the original.
- **Names and ids.** Company names and board tokens are replaced with
  fictional ones (Initech, Vandelay, Hooli, Wonka, Soylent, Umbrella,
  Tyrell). Numeric ids and UUIDs are replaced consistently, including inside
  posting URLs.
- **Links.** Every link off an ATS host now points to `example.com`.

Each file was checked after rewriting. `prefilter.evaluate` gives the same
result on the original and the rewritten posting, both over `match_text` and
over the snippet alone. None of the names or tokens in the private
`data/companies.csv` or `data/known_good.csv` appears in any file.

## Adding one

Start from `data/runs/<date>/fetch_ats.json` (gitignored). Before
committing, re-run both checks above against the original. CI can only run
`PayloadFilesTest`'s link check, because the watchlist isn't in the repo, so
the name check is yours to run.
