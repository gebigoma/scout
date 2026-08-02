#!/usr/bin/env python3
"""Orchestrator for the weekly scout pipeline:
fetch -> normalize -> dedupe -> classify -> score -> digest

Each stage checkpoints its own output under data/runs/<date>/<stage>.json.
Re-running for the same date resumes from the first stage that doesn't
already have a checkpoint (i.e. the first stage that previously failed) -
a failure at, say, classify doesn't force re-fetching. Pass --force to
ignore checkpoints and redo every stage.
"""
import argparse
import json
import sys
from datetime import date

from pipeline import alert, classify, dedupe, digest, fetch, normalize, paths, score


def load_checkpoint(run_date: str, stage: str):
    path = paths.checkpoint_path(run_date, stage)
    if path.exists():
        return json.loads(path.read_text())
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=date.today().isoformat(),
                         help="run date, YYYY-MM-DD (default: today)")
    parser.add_argument("--force", action="store_true",
                         help="ignore existing checkpoints, redo every stage")
    args = parser.parse_args()
    run_date = args.date

    def stage_output(stage_name, run_fn, *inputs):
        if not args.force:
            cached = load_checkpoint(run_date, stage_name)
            if cached is not None:
                print(f"[{stage_name}] using cached checkpoint")
                return cached
        print(f"[{stage_name}] running...")
        return run_fn(run_date, *inputs)

    try:
        fetch_cp = stage_output("fetch", fetch.run)
        normalize_cp = stage_output("normalize", normalize.run, fetch_cp)
        dedupe_cp = stage_output("dedupe", dedupe.run, normalize_cp)
        classify_cp = stage_output("classify", classify.run, dedupe_cp)
        score_cp = stage_output("score", score.run, classify_cp)
        digest_cp = stage_output("digest", digest.run, dedupe_cp, score_cp)
    except Exception as e:
        print(f"Pipeline failed: {e}", file=sys.stderr)
        alert.send_failure_alert(run_date, str(e))
        sys.exit(1)

    print(f"Pipeline succeeded: {digest_cp['matches']} matches, "
          f"committed={digest_cp['committed']}")


if __name__ == "__main__":
    main()
