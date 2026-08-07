# Design: first-TPM lane (VC-portfolio ATS sourcing)

**Status:** designed, not implemented
**Date:** 2026-08-07

## Goal

Find **full-time** roles where the hire would be the first (or near-first)
Technical Program Manager at a startup of roughly 50-150 people, responsible
for establishing the TPM function. Criteria live in
[`ROLE_CRITERIA_FIRST_TPM.md`](../ROLE_CRITERIA_FIRST_TPM.md).

This is a **second lane**, not a replacement. The fractional lane
(`ROLE_CRITERIA.md`, RemoteOK/WWR/HN sources) is **paused, not deleted** —
its criteria, sources, and tests stay in place behind a flag so it can be
switched back on without a rebuild.

## Why this sourcing works

VC portfolio boards pre-filter for funded and vetted, which is most of the
sourcing work. But the boards themselves are not the fetch target — the
portfolio companies' **ATS endpoints** are. Those are first-party and
daily-fresh; the boards are only a source for the *company list*.

## Verified endpoint facts

All probed 2026-08-07. No auth, no key, no login on any of them.

| Source | Endpoint | Descriptions? |
|---|---|---|
| Greenhouse | `boards-api.greenhouse.io/v1/boards/<token>/jobs?content=true` | **Only with `?content=true`** — the bare list endpoint has no description field at all |
| Ashby | `api.ashbyhq.com/posting-api/job-board/<token>` | Yes — `descriptionPlain` included by default |
| Lever | `api.lever.co/v0/postings/<token>?mode=json` | Yes |

Measured: Greenhouse returned 388 jobs for one token (~9KB of content each);
Ashby returned 122 jobs (~4.5KB each). Phrase matching is impossible without
the description, so **the `?content=true` flag on Greenhouse is load-bearing**
— omitting it produces a silently empty filter rather than an error.

Lever board tokens are guesswork and many companies have migrated off it
(`netflix`, `brexhq`, `pinterest` all 404; `matchgroup` works). Treat a 404 as
"this company isn't on this ATS", not as an error.

## The company list — and why harvesting is harder than it looks

The VC boards (`jobs.lsvp.com`, `jobs.bvp.com`) are **Consider.com**-powered
(`config.considerDomain = consider.com`), not Getro. Findings from probing:

- The page shell is ~21KB and contains a `window.serverInitialData` blob with
  board metadata — for Lightspeed: **513 companies, 15,195 jobs**.
- That blob does **not** contain the company list. `/companies` fetches it
  client-side.
- The HTML references **no JS bundle at all**. Scripts load through a
  `/mendel/<base64>` bot-management endpoint, so the XHR paths aren't
  greppable from the served HTML.
- Path guessing against the same-origin API failed (`/api/v1/companies`,
  `/api/search/companies`, `/companies.json`, … all 404, including with the
  page's own `csrfToken` and `X-Requested-With`). `api.consider.com` does not
  resolve.

**Conclusion: automated harvesting needs a headless browser.** That is a real
architectural conflict — the entire pipeline is deliberately stdlib-only so it
runs on the macOS system Python under launchd with no venv, and CI runs with
no install step.

**Recommended resolution:** keep the weekly pipeline stdlib-only and make
harvesting a *separate, optional, manually-run* tool. The company list changes
slowly (a 513-company portfolio does not churn week to week), so a
hand-maintained YAML refreshed occasionally is not a meaningful burden, and it
keeps a Playwright dependency out of the scheduled job entirely. Do **not**
add a browser dependency to `scripts/pipeline/`.

## Work items

### 1. Company list

`data/companies.csv` (hand-maintained), one row per company:

```csv
name,ats,token,headcount,source
Example Corp,greenhouse,examplecorp,85,lightspeed
Another Inc,ashby,anotherinc,,bessemer
```

- `ats` — `greenhouse` | `ashby` | `lever`
- `token` — the **board token**, not the company name
- `headcount` — optional; leave empty when unknown, never guess
- `source` — which portfolio it came from

**CSV, not YAML or JSON.** The repo is stdlib-only and `yaml` is not in the
standard library. CSV wins over JSON here for a hand-maintained list of a few
hundred flat rows: `csv` is stdlib, the file opens in a spreadsheet for bulk
editing, and it has none of JSON's hand-editing hazards (trailing commas,
quoting every key, one malformed brace breaking the whole file). The schema is
flat with no nesting, so JSON's structure buys nothing.

Empty `headcount` must parse as "unknown", not `0` — a `0` would score as
badly out-of-band rather than as missing data. Worth a test.

Seed it by hand from the infra/dev-tools-heavy portfolios first — Lightspeed,
Bessemer, Accel, Index. Board tokens do not map 1:1 to company names and have
to be found per company (usually visible in the company's careers-page URL).

### 2. Fetch stage

New source module following the existing `fetch.py` contract: per-source
retry+backoff, a single source failing is a partial failure, all failing is a
stage failure. One HTTP call per company (not per job). With a few hundred
companies this is the slowest stage by far — add a small delay between calls
and expect the run to take minutes.

### 3. Phrase prefilter (new stage, between normalize and dedupe)

Purpose: cut the candidate set before it reaches the LLM, since the classify
prompt is already ~48K tokens for the fractional lane alone.

**Two-tier, loose, LLM confirms.** Normalize first: strip HTML, lowercase,
collapse whitespace.

- **Tier 1 — exact phrases.** The five thesis phrases (`first technical
  program manager`, `our second TPM`, `establish the program management
  function`, `build out our TPM practice`, `first TPM hire`). High confidence;
  always passes through.
- **Tier 2 — proximity.** A role term (`technical program manager`, `program
  management`, word-boundary `tpm`) within ~200 characters of a foundation
  term (`first`, `second`, `founding`, `establish`, `build out`, `stand up`,
  `from scratch`, `0 to 1`, `no one in this role`). Lower confidence; also
  passes through, and classify does the real judging.

Proximity, not whole-document co-occurrence — `first` appears in nearly every
posting ("first-class", "first and foremost") and a document-wide match would
pass almost everything.

**Word-boundary match on `tpm` is required.** "TPM" also means Trusted
Platform Module, and this lane sources heavily from infra and security
companies. Expect this false positive; the criteria doc tells classify to
reject it, but a substring match would also hit `tpmd`, `vtpm`, and similar.

**Log near-misses** (one tier condition met, not both) to the structured log
so the cost of the filter is visible rather than invisible.

### 4. Classify / score

- Extend the `role_category` enum in `classify.py`'s `SCHEMA` — currently
  `["senior_tpm", "agentic_ai_engineer"]`.
- The prompt template loads `{role_criteria}` from `paths.role_criteria_path()`;
  that needs to become lane-aware.
- **Keep the untrusted-data fence.** `classify_prompt.md` already wraps
  listings in BEGIN/END markers with explicit anti-injection instructions.
  Preserve it verbatim — ATS descriptions are third-party text with the same
  risk profile as job-board text.
- Score against the new rubric, including headcount as a signal.

### 5. Digest

Own section per lane, so a paused lane doesn't render an empty heading.
Rationales must carry the quoted foundation evidence (per the criteria doc) —
that is what makes a false positive visible at a glance.

### 6. Lane flag

Something like `SCOUT_LANES=first_tpm` (default) selecting criteria file,
source set, and digest sections. The fractional lane stays wired up and
tested, just not run.

### 7. Tests

Follow the existing pattern — stdlib `unittest`, no network, captured
response shapes. Minimum coverage:

- Greenhouse `?content=true` parsing, Ashby `descriptionPlain`, Lever
- Prefilter: each of the five exact phrases; a proximity hit; a proximity
  near-miss; **a Trusted Platform Module posting that must not match**
- 404 from one ATS is a per-company skip, not a stage failure
- Lane flag selects the right criteria file

## Sequencing note

This lane makes the chunked-classify work
([`daily-ingest-split.md`](./daily-ingest-split.md) → Sequencing note) more
urgent, not less: a few hundred companies' worth of postings is a much larger
candidate pool than three job boards. The prefilter is what keeps this
tractable in the interim — if it turns out to be too permissive, chunk classify
before loosening anything else.

## Non-goals

- Adding a browser dependency to the scheduled pipeline (see above).
- Loosening the foundation-laying requirement to increase volume. Thin results
  from this lane mean the company list is too small or the prefilter is too
  tight — both are engineering problems, not criteria problems.
