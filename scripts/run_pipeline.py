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

from pipeline import (alert, classify, dedupe, digest, fetch, manifest,
                      normalize, paths, score)


def load_checkpoint(run_date: str, stage: str):
    path = paths.checkpoint_path(run_date, stage)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        # A corrupt checkpoint should cost us one stage, not wedge the date
        # permanently. Treat it as missing and re-run the stage.
        print(f"[{stage}] checkpoint unreadable ({e}); re-running stage",
              file=sys.stderr)
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=date.today().isoformat(),
                         help="run date, YYYY-MM-DD (default: today)")
    parser.add_argument("--force", action="store_true",
                         help="ignore existing checkpoints, redo every stage")
    args = parser.parse_args()
    try:
        # Guards against a typo like 2026-8-3 silently creating an
        # inconsistently-named run dir and digest file.
        run_date = date.fromisoformat(args.date).isoformat()
    except ValueError:
        parser.error(f"--date must be YYYY-MM-DD, got {args.date!r}")

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

    # Run lifecycle belongs to the orchestrator, not to digest - otherwise a
    # run whose digest came from cache would never get marked complete.
    manifest.run_succeeded(run_date)
    print(f"Pipeline succeeded: {digest_cp['matches']} matches, "
          f"committed={digest_cp['committed']}, pushed={digest_cp['pushed']}")


if __name__ == "__main__":
    main()
