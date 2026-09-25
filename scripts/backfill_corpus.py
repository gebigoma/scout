#!/usr/bin/env python3
"""Build data/corpus/<date>.jsonl for every data/runs/<date> still on disk.

See pipeline.corpus's module docstring for the schema and for why this
exists: retention.sweep prunes data/runs/ to the newest 8 runs, ATS payloads
are not re-fetchable once a req closes, and every week that passes destroys
that week's first_tpm inputs for eval purposes. This is a one-time recovery
of whatever's still on disk, not something the scheduled job runs - going
forward, pipeline.corpus.run() (wired in separately) writes each week's
corpus as part of the normal run.

Best-effort per date: a run whose checkpoints are missing, partial, or
predate the first-TPM lane entirely is skipped with a printed reason rather
than aborting the whole backfill.
"""
import argparse
import sys

from pipeline import corpus


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backfill", action="store_true", required=True,
                        help="build data/corpus/<date>.jsonl for every "
                             "data/runs/<date> on disk")
    parser.parse_args()

    results = corpus.backfill()
    ok = [r for r in results if r["status"] == "ok"]
    skipped = [r for r in results if r["status"] == "skipped"]
    total_rows = sum(r["count"] for r in ok)
    total_gaps = sum(r.get("classify_verdict_gaps", 0) for r in ok)
    print(f"\nbackfill_corpus: {len(ok)} date(s) written ({total_rows} row(s) "
         f"total, {total_gaps} unclassified gap(s)), {len(skipped)} skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
