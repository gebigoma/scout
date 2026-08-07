# Design: chunked classify with a verdict per listing

**Status:** designed, not implemented
**Date:** 2026-08-07

## Problem

`classify` makes **one** Claude call over every deduped listing and returns
**matches only** (`scripts/pipeline/classify.py`). Two consequences:

1. **Low recall and an honest zero are indistinguishable.** If the model
   silently overlooks 30 listings, the output is identical in shape to a week
   where nothing genuinely matched. There is no accounting of what was
   considered.
2. **URLs round-trip through the model.** Matches are reconciled by echoing
   the URL back (`by_url.get(m["url"])`). A mangled URL means
   `unresolved += 1` and the match is **silently dropped** — a real match,
   found by the model, discarded by a string mismatch. It's counted in the
   manifest but nothing fails.

The prompt is already ~48K tokens for ~450 listings from three job boards.
The [first-TPM lane](./first-tpm-lane.md) sources from a few hundred companies,
which makes both problems worse.

## Design

Three changes, and they're coupled — do them together:

1. Split the single call into **chunks** of listings.
2. Address listings by **integer id** instead of URL.
3. Require a **verdict for every id** in the chunk, not just the matches.

### Reversal of an earlier decision — stated deliberately

`classify.py`'s docstring says matches-only "keeps output small," and that was
a considered choice. **This spec reverses it.** The saving bought
recall-blindness: a model that returns nothing looks exactly like a model that
found nothing.

The cost is contained by keeping non-matches minimal — a `no_match` verdict is
just `{id, verdict}` with no `reason` field. The expensive part of the output
(the rationale) is still paid only for matches.

### Schema

```python
SCHEMA = {
  "type": "object",
  "properties": {
    "verdicts": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "id": {"type": "integer"},
          "verdict": {"type": "string", "enum": ["match", "no_match"]},
          "role_category": {"type": "string", "enum": [...]},
          "reason": {"type": "string"},
        },
        "required": ["id", "verdict"],
      },
    },
  },
  "required": ["verdicts"],
}
```

`role_category` and `reason` are required **iff** `verdict == "match"` — JSON
Schema can't express that conditional cleanly here, so validate it in Python
after parsing.

### Integer ids

Assign each deduped listing a **run-global** id (`0..N-1`, its index in the
deduped list). Chunks carry a sparse set of those ids; reconciliation is a
single flat lookup rather than `(chunk_index, local_id)` arithmetic.

Store the id → listing mapping in the checkpoint so a resumed run reconciles
against the same assignment.

### Validation — the whole point

After each chunk call, compare the returned id set against the chunk's input
id set. Any of these means a **malformed response, so retry the chunk**:

- a missing id (the model skipped a listing)
- a duplicate id
- an id not in this chunk (hallucinated, or leaked from another chunk)
- `verdict == "match"` without `role_category` or `reason`

This is a behavior change worth being explicit about: **today an unknown URL
silently increments `unresolved` and drops the match.** Under this design an
unknown id is a hard validation failure that triggers a retry. Silently
dropping a found match is exactly the failure mode this project keeps fixing;
don't preserve it.

If a chunk still fails validation after retries, treat it as a failed chunk
(below) — do not partially accept it.

### Chunk size

A module constant, overridable by env for experimentation. **Start at 50.**

The tuning trade is not obvious, so here's the math: the template plus
`ROLE_CRITERIA*.md` is fixed overhead re-sent on *every* chunk, since each is
an independent CLI call. At ~450 listings and ~3K tokens of fixed overhead,
chunk=40 means 12 calls and ~36K tokens of duplicated overhead — roughly
doubling input tokens versus the single 48K call. Chunk=100 means 5 calls and
~15K duplicated.

So smaller chunks buy per-listing attention and pay for it in duplicated
overhead. 50 is a starting point, not a tuned value — measure before moving it.

### Per-chunk checkpoints

Write each chunk's validated verdicts to
`data/runs/<date>/classify_chunks/<n>.json`.

A re-run resumes from the first chunk without a checkpoint rather than
re-calling the LLM for work already done. This is the same resume philosophy
the stage-level checkpoints already use, and classify is the expensive stage —
this is the highest-value part of the change for cost.

Use `paths.atomic_write_json` (interrupted writes must not wedge a chunk).

### Partial-chunk failure

Mirror `fetch.py`'s per-source semantics:

- **All chunks fail** → stage failure, **no checkpoint written**, so a re-run
  actually retries rather than treating the failure as cached.
- **Some chunks fail** → write the stage checkpoint with the verdicts that
  succeeded, record the failed chunk indices and the count of unclassified
  listings in the manifest, and **surface it in the digest** ("classified 380
  of 450 listings; 2 of 9 chunks failed"). A partially-classified run must not
  read as a clean one.
- Fire the failure alert on partial failure too, or at minimum carry it in the
  success heartbeat. A silently thin week is the thing to prevent.

**Listings in a failed chunk must not be recorded as seen.** This is already
correct by construction — `digest._update_seen` writes only scored matches, and
an unclassified listing never becomes one, so it stays eligible next run.
Stated here so nobody "fixes" it into recording them.

### Preserve the untrusted-data fence

`classify_prompt.md` wraps listings in `--- BEGIN/END UNTRUSTED LISTINGS ---`
with explicit anti-injection instructions. **Each chunk's prompt keeps the
fence.** Chunking multiplies the number of prompts; it must not divide the
number of fences.

### Score stage

Leave `score` unchunked. It runs only over matches — a handful per run, not
hundreds. Revisit if a single run ever produces enough matches to approach the
same prompt size, but don't pre-build it.

## Tests

Follow the existing pattern — stdlib `unittest`, no network, stubbed CLI.

- Validation rejects: a missing id, a duplicate id, an out-of-chunk id, a
  `match` verdict missing `role_category` / `reason`
- A chunk that fails validation once and succeeds on retry produces correct
  output
- Chunk boundary: N not evenly divisible by chunk size (e.g. 45 listings at
  chunk=20) classifies all 45
- Partial failure publishes the successful verdicts **and** records the gap in
  the manifest
- All chunks failing raises and writes **no** stage checkpoint
- Per-chunk checkpoint resume skips already-completed chunks (assert the
  stubbed CLI is called only for the incomplete ones)
- A listing whose URL contains characters that previously round-tripped badly
  still resolves — ids make this structural, and the test pins it

## Sequencing

Land this **before** the first-TPM lane. It changes existing code with tests
already around it, so it can be verified in isolation; building the lane first
means writing lane code against a contract that's about to change, then
reworking it. Doing both at once conflates two independent sets of failures.
