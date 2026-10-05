#!/usr/bin/env python3
"""Static run-health viewer: reads the retained per-run manifests and writes a
self-contained viewer/index.html (open it with `open viewer/index.html`).

Read-only with respect to pipeline data: no LLM, no git, no network, and
nothing is written under data/runs/. The output is local-only and gitignored -
manifests can carry private company names (fetch_ats.failed), so this file is
as private as data/runs/ itself.

A missing or malformed manifest is a skipped row, never a crash, and every
field is treated as possibly absent (the pre-prefilter-counter runs lack
first_tpm_seen / role_terms_seen entirely). Absent renders as an em dash.
"""
import html
import json
from datetime import datetime

from pipeline import manifest, paths

MISSING = "—"

# Counter columns: (header, stage, key). role_terms_seen is its own column on
# purpose - it is what separates an honest quiet week from a silently broken
# lane (issue #13), and its recent baseline (34-39) should be eyeball-able.
COUNTERS = [
    ("fetch total", "fetch", "total_count"),
    ("ATS ok", "fetch_ats", "succeeded"),
    ("ATS skipped", "fetch_ats", "skipped"),
    ("normalized", "normalize", "count"),
    ("first_tpm seen", "prefilter", "first_tpm_seen"),
    ("role terms seen", "prefilter", "role_terms_seen"),
    ("unclassified", "classify", "unclassified_count"),
    ("failed chunks", "classify", "failed_chunk_indices"),
]


def _stage(run, name):
    stages = run.get("stages")
    if not isinstance(stages, dict):
        return {}
    st = stages.get(name)
    return st if isinstance(st, dict) else {}


def _duration_s(run):
    try:
        start = datetime.fromisoformat(run["started_at"])
        end = datetime.fromisoformat(run["finished_at"])
        return round((end - start).total_seconds(), 1)
    except (KeyError, TypeError, ValueError):
        return None


def _summarize(dir_name, run):
    stage_status = {s: _stage(run, s).get("status") for s in manifest.STAGES}
    counters = {(stage, key): _stage(run, stage).get(key) for _, stage, key in COUNTERS}
    date = run.get("date")
    return {
        "date": date if isinstance(date, str) and date else dir_name,
        "status": run.get("status"),
        "duration_s": _duration_s(run),
        "stages": stage_status,
        "counters": counters,
        "digest_status": stage_status["digest"],
    }


def collect_runs():
    """One plain dict per readable manifest, newest first."""
    runs = []
    for path in paths.runs_dir().glob("*/manifest.json"):
        try:
            raw = json.loads(path.read_text())
        except (OSError, ValueError):  # JSONDecodeError and bad UTF-8 are ValueErrors
            continue
        if not isinstance(raw, dict):
            continue
        runs.append(_summarize(path.parent.name, raw))
    runs.sort(key=lambda r: r["date"], reverse=True)
    return runs


def _text(value):
    """Escaped display text; absent (None) is an em dash."""
    if value is None:
        return MISSING
    if isinstance(value, (list, tuple)):
        return html.escape(", ".join(str(v) for v in value)) if value else "none"
    return html.escape(str(value))


def _status_class(status):
    if status == "success":
        return "ok"
    if status == "failed":
        return "bad"
    return "other"


def _status_cell(status):
    return '<td class="%s">%s</td>' % (_status_class(status), _text(status))


def _duration_text(seconds):
    if seconds is None:
        return MISSING
    minutes, secs = divmod(int(round(seconds)), 60)
    return "%dm %02ds" % (minutes, secs) if minutes else "%ds" % secs


CSS = """
body { font: 14px/1.4 -apple-system, system-ui, sans-serif; margin: 2rem; color: #1a1a1a; }
h1 { font-size: 1.3rem; margin: 0 0 .25rem; }
p.note { color: #555; margin: 0 0 1.25rem; }
.wrap { overflow-x: auto; }
table { border-collapse: collapse; }
th, td { padding: .35rem .7rem; border-bottom: 1px solid #ddd; text-align: right; white-space: nowrap; }
th { background: #f4f4f4; font-weight: 600; }
th:first-child, td:first-child, td.txt { text-align: left; }
td.ok { background: #d9f2de; color: #14532d; font-weight: 600; text-align: left; }
td.bad { background: #fadadd; color: #7f1d1d; font-weight: 600; text-align: left; }
td.other { color: #555; text-align: left; }
th.key, td.key { border-left: 2px solid #999; border-right: 2px solid #999; font-weight: 600; }
"""


def render(runs):
    head = ["date", "run status", "duration"]
    head += [h for h, _, _ in COUNTERS]
    head += list(manifest.STAGES)  # includes digest, so no separate digest column

    def th(h):
        cls = ' class="key"' if h == "role terms seen" else ""
        return "<th%s>%s</th>" % (cls, html.escape(h))

    rows = []
    for r in runs:
        cells = ['<td class="txt">%s</td>' % _text(r["date"]),
                 _status_cell(r["status"]),
                 "<td>%s</td>" % _duration_text(r["duration_s"])]
        for header, stage, key in COUNTERS:
            cls = ' class="key"' if header == "role terms seen" else ""
            cells.append("<td%s>%s</td>" % (cls, _text(r["counters"][(stage, key)])))
        cells += [_status_cell(r["stages"][s]) for s in manifest.STAGES]
        rows.append("<tr>%s</tr>" % "".join(cells))

    if rows:
        body = "".join(rows)
    else:
        body = '<tr><td class="txt" colspan="%d">No readable manifests under data/runs/.</td></tr>' % len(head)

    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>scout run health</title>
<style>%s</style>
</head>
<body>
<h1>scout run health</h1>
<p class="note">%d run(s), newest first. %s means the manifest has no such field.
Local only - generated from data/runs/, never committed.</p>
<div class="wrap">
<table>
<thead><tr>%s</tr></thead>
<tbody>%s</tbody>
</table>
</div>
</body>
</html>
""" % (CSS, len(runs), MISSING, "".join(th(h) for h in head), body)


def main():
    out = paths.viewer_index_path()
    out.write_text(render(collect_runs()))
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
