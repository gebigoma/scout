"""Corpus: not a pipeline stage. Never add "corpus" to manifest.STAGES, and
never let it fail a run - see run().

Persists every first_tpm-lane listing from a run's `normalize` checkpoint,
together with what each downstream stage decided about it, to
data/corpus/<run_date>.jsonl - one JSON object per line. This is the only
durable copy of what the first_tpm lane saw: retention.sweep prunes
data/runs/<date>/ (and data/raw/<date>.json) to the newest 8, and a closed
ATS req cannot be re-fetched, so every week that passes without this file
destroys that week's inputs for good.

Reads `match_text` from `normalize`, never from a later checkpoint -
`prefilter` strips it before passing listings on (see prefilter.run), so
the full description this stage matched against exists only in the
normalize checkpoint.

## Schema

One row per first_tpm listing present in that run's normalize checkpoint:

  run_date                 the run this listing was seen in
  id                       the classify-stage integer id (this listing's
                           index into that run's DEDUPED listing list,
                           spanning both lanes - see classify.run) - null
                           iff the listing never reached dedupe's output
  url, company, title, location_text, location_country_code
                           carried straight from the normalize listing.
                           `headcount` was a column here until 2026-10-05
                           and is null in every corpus file that has it -
                           it was never populated for any company, which is
                           why it was dropped rather than fixed
  description              normalize's match_text - the full description
                           prefilter matched against. `match_text` did not
                           exist before d223e72 (2026-09-03) - a run from
                           before that date has no full-description field
                           in normalize.json AT ALL, not merely one
                           stripped downstream, so this falls back to
                           `snippet` (see description_source) and the text
                           is gone for good: it was never captured and the
                           source req may since have closed.
  description_source        "match_text" or "snippet" - which field
                           `description` actually came from. A reader
                           relying on `description` for anything
                           proximity-based (the way prefilter itself does)
                           must check this first: a "snippet" value is
                           stitched (see normalize._extract_snippet) and,
                           on these same pre-d223e72 runs, was salvaged
                           against EMPLOYMENT_TERMS only, not the
                           founding-language vocabulary this lane actually
                           looks for - it is worse evidence than a
                           post-fix snippet would be, not merely shorter.
  source                   normalize's "{ats}:{token}" provenance string,
                           unchanged
  ats                      source split on its first ":" - null if source
                           isn't in that shape
  portfolio                data/companies.csv's `source` column (the
                           VC-portfolio name that sourced this company),
                           joined on company name AT WRITE TIME. Nullable:
                           a missing companies.csv, a company no longer
                           listed in it, or a blank source column all
                           resolve to null rather than failing the write.
                           On a backfilled row this is TODAY's
                           companies.csv, not the mapping that was live
                           the week the listing was fetched -
                           companies.csv carries no history, so there is
                           no other mapping available.
  prefilter_passes, prefilter_tier1, prefilter_tier2, prefilter_near_miss,
  prefilter_role_fit, prefilter_us_eligible, prefilter_role_term
                           prefilter.evaluate() called fresh on this row's
                           normalize listing - a RECOMPUTATION, not a
                           replay of what prefilter actually decided that
                           week. d223e72 (2026-09-03) changed what
                           evaluate() returns (whole-description matching
                           instead of a 400-char snippet; a corrected
                           near_miss definition), so on a row backfilled
                           from before that date these fields describe
                           what CURRENT code decides, which can and does
                           disagree with what the pipeline actually did
                           that week. reached_dedupe (below) is that
                           week's real answer; these are not.
  prefilter_recomputed      true on a row written by --backfill (every
                           recomputed field above, and portfolio, reflect
                           current code / current companies.csv rather
                           than that week's); false on a row written live
                           in the same run that produced the listing.
  reached_dedupe            url is present in that run's REAL, historical
                           prefilter.json - i.e. whether prefilter
                           actually passed it that week, on the code that
                           ran that week. Ground truth; prefilter_passes
                           is a same-listing recomputation that may
                           disagree with it for pre-d223e72 runs.
  reached_classify          url is present in that run's dedupe.json
  classify_verdict         "match" / "no_match" / "unclassified" / null
                           (null iff not reached_classify). Reconstructed
                           by merging every classify_chunks/<n>.json for
                           that date with classify.json's matches, since
                           classify.json itself only ever records matches
                           - see classify.run. An id that reached classify
                           but appears in no chunk file and no match list
                           falls back to "unclassified" (this happens when
                           its chunk failed all retries and was never
                           checkpointed - see classify._validate_verdicts
                           / failed_chunk_indices); build_rows counts
                           these rather than absorbing them silently.
  classify_role_category, classify_reason
                           match verdicts only
  fit_score, score_rationale
                           from that run's score.json, if scored
  cleared_score_floor       fit_score is not null and >= digest.SCORE_FLOOR
                           - the only gate on whether _render_markdown
                           puts this match INTO matches/<date>.md.
  published                 true/false when that run's digest.json
                           exists, else null. True means cleared_score_floor
                           AND digest actually committed that run (on
                           main, git add/commit succeeded) - not just
                           floor-cleared. null covers any date whose
                           digest.run raised before writing its own
                           checkpoint - in this repo's history that's
                           2026-08-17/08-24/08-31, where a rejected push
                           stranded an already-made commit (see
                           CLAUDE.md); the file was rendered locally and a
                           commit was made, but digest.json - and
                           therefore this field - was never written, so
                           whether that commit ultimately reached origin
                           on its own or through a later run's push is
                           unknown from this checkpoint alone. Uses
                           `committed` rather than `committed AND pushed`:
                           whenever digest.json exists, a push that failed
                           already raised before the checkpoint was
                           written (see digest.run), so pushed is only
                           ever False there when there was nothing left to
                           push - i.e. already published by an earlier
                           run - which is still published.

Called from run_pipeline.run_stages after score and before digest (so
score's fields are populated) via run(); backfilled from whatever
data/runs/<date>/ remain on disk via backfill() / scripts/backfill_corpus.py.
Neither call site is wired into run_pipeline.py yet - see spec 01 CP3.
"""
import json
import sys
from datetime import date

from . import companies, digest, paths, prefilter

FIRST_TPM_LANE = "first_tpm"


def _is_run_date(name: str) -> bool:
    try:
        return date.fromisoformat(name).isoformat() == name
    except ValueError:
        return False


def _run_dates() -> list:
    """Every YYYY-MM-DD directory under data/runs/, oldest first."""
    parent = paths.PROJECT_DIR / "data" / "runs"
    if not parent.is_dir():
        return []
    return sorted(child.name for child in parent.iterdir()
                 if child.is_dir() and _is_run_date(child.name))


def _load_checkpoint(run_date: str, stage: str):
    """None for a missing or corrupt checkpoint - mirrors run_pipeline's own
    tolerance (a corrupt checkpoint costs one stage, not the whole date),
    but doesn't print: callers here need to decide what a missing
    checkpoint MEANS (skip the date? fall back to null fields?), which
    differs by stage."""
    path = paths.checkpoint_path(run_date, stage)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _classify_chunk_files(run_date: str) -> list:
    d = paths.run_dir(run_date) / "classify_chunks"
    if not d.is_dir():
        return []
    return sorted(d.glob("*.json"), key=lambda p: int(p.stem))


def _split_ats(source: str):
    ats, sep, _token = (source or "").partition(":")
    return ats if sep else None


def _portfolio_map() -> dict:
    """company name -> data/companies.csv's `source` column, or {} on any
    failure to load it - a missing/malformed companies.csv must not fail
    the corpus write (companies.load_companies already returns [] for a
    missing file; the try/except here is for anything stranger)."""
    try:
        rows = companies.load_companies()
    except Exception:
        return {}
    mapping = {c["name"]: (c.get("source") or None) for c in rows}
    # A portfolio-board listing is named after its portfolio company, which
    # has no row of its own - the row is the VC's board. Key those by the
    # listing's `source` ("ashby_portfolio:Pear-VC") so they still map to the
    # board's portfolio rather than to nothing.
    mapping.update({f"{c['ats']}:{c['token']}": (c.get("source") or None)
                    for c in rows if companies.is_portfolio_board(c["ats"])})
    return mapping


def build_rows(run_date: str, normalize_cp: dict, prefilter_cp: dict,
               dedupe_cp: dict, classify_cp: dict, score_cp: dict,
               digest_cp: dict = None, recomputed: bool = False) -> tuple:
    """Returns (rows, stats). stats currently holds classify_verdict_gaps -
    the count of first_tpm ids that reached classify but were found in no
    chunk file and no match list, which build_rows falls back to
    "unclassified" for rather than dropping."""
    listings = [l for l in normalize_cp.get("listings", [])
               if l.get("lane") == FIRST_TPM_LANE]
    if not listings:
        return [], {"classify_verdict_gaps": 0}

    prefilter_urls = {l["url"] for l in prefilter_cp.get("listings", [])}
    dedupe_listings = dedupe_cp.get("listings", [])
    id_by_url = {l["url"]: i for i, l in enumerate(dedupe_listings)}

    matches_by_url = {m["url"]: m for m in classify_cp.get("matches", [])}

    verdict_by_id = {}
    for chunk_path in _classify_chunk_files(run_date):
        chunk = json.loads(chunk_path.read_text())
        for v in chunk.get("verdicts", []):
            verdict_by_id[v["id"]] = v

    scores_by_url = {s["url"]: s for s in score_cp.get("scored", [])}

    portfolio_by_name = _portfolio_map()

    rows = []
    gap_count = 0
    for listing in listings:
        url = listing["url"]
        source = listing.get("source", "")

        row = {
            "run_date": run_date,
            "id": id_by_url.get(url),
            "url": url,
            "company": listing.get("company", ""),
            "title": listing.get("title", ""),
            "location_text": listing.get("location_text", ""),
            "location_country_code": listing.get("location_country_code", ""),
            "description": listing.get("match_text") or listing.get("snippet", ""),
            "description_source": "match_text" if "match_text" in listing else "snippet",
            "source": source,
            "ats": _split_ats(source),
            "portfolio": (portfolio_by_name.get(listing.get("company", ""))
                          or portfolio_by_name.get(source)),
        }

        pf = prefilter.evaluate(listing)
        row.update(
            prefilter_passes=pf["passes"], prefilter_tier1=pf["tier1"],
            prefilter_tier2=pf["tier2"], prefilter_near_miss=pf["near_miss"],
            prefilter_role_fit=pf["role_fit"], prefilter_us_eligible=pf["us_eligible"],
            prefilter_role_term=pf["role_term"], prefilter_recomputed=recomputed,
        )

        reached_dedupe = url in prefilter_urls
        reached_classify = url in id_by_url
        row["reached_dedupe"] = reached_dedupe
        row["reached_classify"] = reached_classify

        verdict = role_category = reason = None
        if reached_classify:
            match = matches_by_url.get(url)
            if match:
                verdict = "match"
                role_category = match.get("role_category")
                reason = match.get("reason")
            else:
                chunk_verdict = verdict_by_id.get(id_by_url[url])
                if chunk_verdict:
                    verdict = chunk_verdict.get("verdict", "no_match")
                else:
                    verdict = "unclassified"
                    gap_count += 1
        row["classify_verdict"] = verdict
        row["classify_role_category"] = role_category
        row["classify_reason"] = reason

        score = scores_by_url.get(url)
        fit_score = score["fit_score"] if score else None
        row["fit_score"] = fit_score
        row["score_rationale"] = score["rationale"] if score else None
        row["cleared_score_floor"] = fit_score is not None and fit_score >= digest.SCORE_FLOOR

        if digest_cp is None:
            row["published"] = None
        else:
            row["published"] = bool(row["cleared_score_floor"] and digest_cp.get("committed"))

        rows.append(row)

    return rows, {"classify_verdict_gaps": gap_count}


def write_corpus(run_date: str, rows: list):
    """Rewrites the whole file (temp + os.replace) every call - re-running a
    date, including --force, must not append or half-overwrite it."""
    path = paths.corpus_path(run_date)
    paths.atomic_write_jsonl(path, rows)
    return path


def run(run_date: str, normalize_cp: dict, prefilter_cp: dict, dedupe_cp: dict,
       classify_cp: dict, score_cp: dict) -> dict:
    """The live call site: run_pipeline calls this after score and before
    digest, so digest_cp is always None here and `published` is always null
    for a row written this way - only a --backfill row, written after that
    date's digest.json already exists, can ever have a non-null published.

    NOT checkpointed (never add "corpus" to manifest.STAGES) and must never
    fail a run - the caller is expected to wrap this the way
    retention.sweep is wrapped in run_pipeline.run_stages: catch, print to
    stderr, continue. Losing one week of corpus is bad; losing a published
    digest to a corpus bug is worse.
    """
    rows, stats = build_rows(run_date, normalize_cp, prefilter_cp, dedupe_cp,
                             classify_cp, score_cp, digest_cp=None, recomputed=False)
    path = write_corpus(run_date, rows)
    return {"path": str(path), "count": len(rows), **stats}


def _backfill_one(run_date: str) -> dict:
    normalize_cp = _load_checkpoint(run_date, "normalize")
    if normalize_cp is None:
        return {"run_date": run_date, "status": "skipped", "reason": "no normalize checkpoint"}

    listings = [l for l in normalize_cp.get("listings", []) if l.get("lane") == FIRST_TPM_LANE]
    if not listings:
        write_corpus(run_date, [])
        return {"run_date": run_date, "status": "ok", "count": 0, "classify_verdict_gaps": 0}

    checkpoints = {}
    for stage in ("prefilter", "dedupe", "classify", "score"):
        cp = _load_checkpoint(run_date, stage)
        if cp is None:
            return {"run_date": run_date, "status": "skipped",
                    "reason": f"first_tpm listings present but no {stage} checkpoint"}
        checkpoints[stage] = cp

    # digest.json's absence is meaningful (see the `published` schema note
    # above), not a reason to skip the date.
    digest_cp = _load_checkpoint(run_date, "digest")

    rows, stats = build_rows(run_date, normalize_cp, checkpoints["prefilter"],
                             checkpoints["dedupe"], checkpoints["classify"],
                             checkpoints["score"], digest_cp=digest_cp, recomputed=True)
    write_corpus(run_date, rows)
    return {"run_date": run_date, "status": "ok", "count": len(rows), **stats}


def backfill() -> list:
    """Best-effort per date over every data/runs/<date> still on disk. One
    date's missing/partial/malformed checkpoints get that date skipped with
    a printed reason, never abort the whole backfill - these run
    directories are the only remaining copy of ATS payloads that already
    can't be re-fetched, so an incomplete backfill beats a failed one."""
    results = []
    for run_date in _run_dates():
        try:
            result = _backfill_one(run_date)
        except Exception as e:
            result = {"run_date": run_date, "status": "skipped", "reason": str(e)}
        results.append(result)

        if result["status"] == "skipped":
            print(f"[corpus] {run_date}: skipped ({result['reason']})", file=sys.stderr)
        else:
            gaps = result.get("classify_verdict_gaps", 0)
            suffix = f", {gaps} unclassified gap(s)" if gaps else ""
            print(f"[corpus] {run_date}: {result['count']} row(s) written{suffix}")
    return results
