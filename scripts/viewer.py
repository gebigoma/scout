#!/usr/bin/env python3
"""Static viewer: writes two cross-linked, self-contained pages under viewer/
(open them with `open viewer/index.html`, no server needed):

  index.html    run health, from the retained per-run manifests
  digests.html  an archive of every published matches/<date>.md
                (parsing lives in digest_archive.py; this module renders)

Read-only with respect to pipeline data: no LLM, no git, no network, and
nothing is written under data/runs/. The output is local-only and gitignored -
manifests can carry private company names (fetch_ats.failed), so this file is
as private as data/runs/ itself.

A missing or malformed manifest is a skipped row, never a crash, and every
field is treated as possibly absent (the pre-prefilter-counter runs lack
first_tpm_seen / role_terms_seen entirely). Absent renders as an em dash.
A digest that cannot be parsed is likewise a visibly skipped entry.
"""
import html
import json
from datetime import datetime

import digest_archive
from pipeline import digest as digest_stage
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
nav { margin: 0 0 1.25rem; }
nav a, nav strong { margin-right: 1rem; }
nav a { color: #1d4ed8; }
h2 { font-size: 1.1rem; margin: 2rem 0 .25rem; }
h3 { font-size: .95rem; margin: 1.1rem 0 .4rem; }
.lane { font-size: .75rem; font-weight: 600; color: #555; background: #eee; border-radius: 3px; padding: .1rem .4rem; margin-left: .4rem; }
.lead { max-width: 60rem; background: #f4f4f4; border-left: 3px solid #999; padding: .6rem .9rem; margin: 0 0 1.25rem; }
.src { color: #555; max-width: 60rem; margin: 0 0 .5rem; }
.empty { color: #555; margin: .2rem 0; }
.skipped { background: #fadadd; color: #7f1d1d; padding: .5rem .8rem; max-width: 60rem; }
.entry { border-bottom: 1px solid #ddd; padding: .5rem 0; max-width: 60rem; }
.entry.rejected { opacity: .65; }
.entry .head { font-weight: 600; }
.entry .co { color: #555; }
.entry .rat { margin: .25rem 0 0; color: #333; }
.entry .link { word-break: break-all; font-size: .85rem; }
.score { display: inline-block; min-width: 3.2rem; text-align: center; font-weight: 700; border-radius: 3px; padding: .05rem .4rem; margin-right: .5rem; }
.score.top { background: #b7e4c0; color: #14532d; }
.score.strong { background: #d9f2de; color: #14532d; }
.score.marginal { background: #fdf0c4; color: #713f12; }
.score.low { background: #fadadd; color: #7f1d1d; }
.score.none { background: #eee; color: #555; font-weight: 400; }
.tag { font-size: .7rem; font-weight: 700; color: #7f1d1d; border: 1px solid #7f1d1d; border-radius: 3px; padding: 0 .3rem; margin-left: .4rem; }
"""


def _nav(current):
    """Links between the two pages; the current one is plain bold text."""
    items = [("index", "index.html", "Run health"), ("digests", "digests.html", "Digests")]
    return "<nav>%s</nav>" % "".join(
        "<strong>%s</strong>" % label if key == current
        else '<a href="%s">%s</a>' % (href, label)
        for key, href, label in items)


def _page(title, nav_html, body_html):
    """The one page wrapper both pages share, so CSS is defined once."""
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>%s</title>
<style>%s</style>
</head>
<body>
%s
%s
</body>
</html>
""" % (html.escape(title), CSS, nav_html, body_html)


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

    body_html = """<h1>scout run health</h1>
<p class="note">%d run(s), newest first. %s means the manifest has no such field.
Local only - generated from data/runs/, never committed.</p>
<div class="wrap">
<table>
<thead><tr>%s</tr></thead>
<tbody>%s</tbody>
</table>
</div>""" % (len(runs), MISSING, "".join(th(h) for h in head), body)
    return _page("scout run health", _nav("index"), body_html)


def _score_class(entry, rejected):
    """Bands follow the fit score guide in ROLE_CRITERIA.md: 85+ ideal, 60-84
    strong, 35-59 marginal; below SCORE_FLOOR is the rejected band."""
    score = entry["score"]
    if score is None:
        return "none"
    if rejected or score < digest_stage.SCORE_FLOOR:
        return "low"
    if score >= 85:
        return "top"
    if score >= 60:
        return "strong"
    return "marginal"


def _score_pill(entry, rejected):
    score = entry["score"]
    label = MISSING if score is None else str(score)
    title = "no fit score recorded" if score is None else "fit score %d/100" % score
    return '<span class="score %s" title="%s">%s</span>' % (
        _score_class(entry, rejected), title, label)


def _entry_html(entry, rejected):
    url = html.escape(entry["url"], quote=True)
    company = ""
    if entry["company"]:
        company = ' <span class="co">&mdash; %s</span>' % html.escape(entry["company"])
    via = ""
    if entry.get("via"):
        via = ' <span class="co">(via %s)</span>' % html.escape(entry["via"])
    tag = '<span class="tag">REJECTED</span>' if rejected else ""
    rationale = ""
    if entry["rationale"]:
        rationale = '<p class="rat">%s</p>' % html.escape(entry["rationale"])
    return ('<div class="entry%s"><div class="head">%s%s%s%s%s</div>'
            '<div class="link"><a href="%s" rel="noopener noreferrer">%s</a></div>%s</div>'
            % (" rejected" if rejected else "", _score_pill(entry, rejected),
               html.escape(entry["title"]), company, via, tag, url, url, rationale))


def _section_html(section):
    rejected = section["rejected"]
    lane = ""
    if section["lane"]:
        lane = '<span class="lane">%s</span>' % html.escape(section["lane"])
    parts = ["<h3>%s%s</h3>" % (html.escape(section["role"]), lane)]
    if section["intro"]:
        parts.append('<p class="empty">%s</p>' % html.escape(section["intro"]))
    if section["no_matches"]:
        note = ""
        if section["no_matches_note"]:
            note = " " + html.escape(section["no_matches_note"])
        parts.append('<p class="empty">No matches this week.%s</p>' % note)
    parts.extend(_entry_html(e, rejected) for e in section["entries"])
    return "".join(parts)


def _mean(values):
    return "%.0f" % (sum(values) / len(values)) if values else MISSING


def _summary_row(d):
    date = html.escape(d["date"])
    link = '<a href="#d-%s">%s</a>' % (date, date)
    if d["error"]:
        return ('<tr><td class="txt">%s</td><td class="txt skipped" colspan="6">'
                'skipped: unparseable digest</td></tr>' % link)
    matches = digest_archive.match_entries(d)
    scores = [e["score"] for e in matches if e["score"] is not None]
    reviewed = MISSING if d["reviewed"] is None else str(d["reviewed"])
    skipped = len(d["skipped"])
    skipped_cell = '<td class="skipped">%d</td>' % skipped if skipped else "<td>0</td>"
    return ("<tr><td class=\"txt\">%s</td><td>%s</td><td>%d</td><td>%d</td><td>%s</td><td>%s</td>%s</tr>"
            % (link, reviewed, len(matches), len(digest_archive.rejected_entries(d)),
               max(scores) if scores else MISSING, _mean(scores), skipped_cell))


def _skipped_html(d):
    """A visible note for entries the parser could not read: a dropped entry
    must never be invisible."""
    if not d["skipped"]:
        return ""
    items = "".join(
        "<li>%s: <code>%s</code> (%s)</li>" % (html.escape(k["section"]),
                                               html.escape(k["excerpt"]),
                                               html.escape(k["reason"]))
        for k in d["skipped"])
    return ('<div class="skipped"><strong>%d entr%s in this digest could not be '
            'parsed and %s not shown below.</strong> The rest of the week is '
            'unaffected; see matches/%s.md.<ul>%s</ul></div>'
            % (len(d["skipped"]), "y" if len(d["skipped"]) == 1 else "ies",
               "is" if len(d["skipped"]) == 1 else "are", html.escape(d["date"]), items))


def _week_html(d):
    date = html.escape(d["date"])
    if d["error"]:
        return ('<h2 id="d-%s">%s</h2><p class="skipped">Skipped: matches/%s.md could not '
                'be parsed (%s). The other weeks are unaffected.</p>'
                % (date, date, date, html.escape(d["error"])))
    reviewed = ""
    if d["reviewed"] is not None:
        reviewed = " &mdash; %d listings reviewed" % d["reviewed"]
    src = '<p class="src">%s</p>' % html.escape(d["sources"]) if d["sources"] else ""
    return '<h2 id="d-%s">%s%s</h2>%s%s%s' % (
        date, date, reviewed, src, _skipped_html(d),
        "".join(_section_html(s) for s in d["sections"]))


def render_digests(digests):
    if not digests:
        body = '<p class="empty">No digests found under matches/.</p>'
    else:
        rows = "".join(_summary_row(d) for d in digests)
        body = """<table>
<thead><tr><th>week</th><th>reviewed</th><th>matches</th><th>rejected</th><th>best fit</th><th>mean fit</th><th>unparsed</th></tr></thead>
<tbody>%s</tbody>
</table>
%s""" % (rows, "".join(_week_html(d) for d in digests))
    body_html = """<h1>scout digest archive</h1>
<p class="lead">Quiet weeks are published as zeros on purpose. A week with no
matches is an honest result, and it must never look the same as a run that
broke, so every week appears here whether or not it found anything. Scores
below %d are listed as rejected: they cleared the match bar but the score stage
disagreed.</p>
<p class="note">%d digest(s), newest first. %s means no fit score was recorded
(the earliest digest predates scoring). Local only - generated from matches/.</p>
%s""" % (digest_stage.SCORE_FLOOR, len(digests), MISSING, body)
    return _page("scout digest archive", _nav("digests"), body_html)


def main():
    out = paths.viewer_index_path()
    out.write_text(render(collect_runs()))
    digests_out = paths.viewer_digests_path()
    digests_out.write_text(render_digests(digest_archive.collect_digests()))
    print(out)
    print(digests_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
