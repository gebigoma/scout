#!/usr/bin/env python3
"""Parser for the published weekly digests in matches/*.md.

Pure parsing, no rendering and no HTML: scripts/viewer.py renders what this
returns. Read-only - it only reads matches/, and (like the manifest handling in
viewer.py) a digest it cannot parse becomes a skipped entry carrying an `error`
string, never an exception, so one bad week cannot hide the other ten.

Two structural generations exist and both are handled:

  * current (2026-08-10 on): `## <Lane>` wrapping `### <role>` sections.
  * legacy (2026-08-01, 2026-08-03): `## <role>` sections with no lane
    (lane is None). These predate the first_tpm lane.

Fit scores vary independently of that: 2026-08-01 has none at all, its bullets
end `(via <source>)` instead, so `score` is None there and `via` carries the
source.

Bullet anatomy (both generations):
    - **<title>** — <company><parenthetical>
      <url>
      <rationale, wrapped over several indented lines>
"""
import re

from pipeline import paths

REJECTED_HEADING = "rejected on scoring"
NO_MATCHES = "no matches this week"

_H1 = re.compile(r"^# Matches\b")
_HEADING = re.compile(r"^(#{2,3}) +(.+?) *$")
# The title is the bold span, ending at the first `** — `. Anchoring on the
# bold span rather than splitting the line on the first em dash is deliberate:
# real titles contain em dashes of their own (2026-10-05 has one that does, and
# whose company field is the same mangled string), and a first-dash split
# would cut the title in half and leave the rest of it as the company.
_BULLET = re.compile(r"^- \*\*(.+?)\*\* +— +(.*)$")
# Fallback for a bullet whose title isn't bold: first ` — ` splits, the
# remainder (em dashes and all) is the company.
_BULLET_PLAIN = re.compile(r"^- (.+?) +— +(.*)$")
_TAIL = re.compile(r"^(.*?)\s*\((?:fit:\s*(\d+)\s*/\s*100|via\s+([^()]*?))\)\s*$")
_URL = re.compile(r"^https?://\S+$")
_REVIEWED = re.compile(r"(\d[\d,]*)\s+(?:[A-Za-z-]+\s+)?listings\s+reviewed")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")


class DigestError(ValueError):
    """A digest that cannot be parsed; the message is shown to the reader."""


def _parse_bullet_line(line):
    m = _BULLET.match(line) or _BULLET_PLAIN.match(line)
    if not m:
        raise DigestError("malformed entry line: %r" % line[:80])
    title, rest = m.group(1).strip(), m.group(2).strip()
    score = via = None
    tail = _TAIL.match(rest)
    if tail:
        rest = tail.group(1)
        score = int(tail.group(2)) if tail.group(2) is not None else None
        via = tail.group(3).strip() if tail.group(3) is not None else None
    return {"title": title, "company": rest.strip(), "url": None,
            "score": score, "via": via, "rationale": ""}


def _parse_section_body(lines):
    """-> (entries, prose_lines) for the lines under one heading."""
    entries, prose, current, rationale = [], [], None, []

    def close():
        nonlocal current, rationale
        if current is not None:
            if current["url"] is None:
                raise DigestError("entry %r has no URL" % current["title"][:60])
            current["rationale"] = " ".join(rationale)
            entries.append(current)
        current, rationale = None, []

    for line in lines:
        if line.startswith("- "):
            close()
            current = _parse_bullet_line(line)
        elif not line.strip():
            continue
        elif line[0] in " \t" and current is not None:
            text = line.strip()
            if current["url"] is None:
                if not _URL.match(text):
                    raise DigestError("entry %r: expected a URL, got %r"
                                      % (current["title"][:60], text[:60]))
                current["url"] = text
            else:
                rationale.append(text)
        else:
            close()
            prose.append(line.strip())
    close()
    return entries, prose


def _split_blocks(lines):
    """Heading blocks as [level, name, body_lines]; also returns the lines
    before the first heading (the preamble)."""
    preamble, blocks = [], []
    for line in lines:
        m = _HEADING.match(line)
        if m:
            blocks.append([len(m.group(1)), m.group(2), []])
        elif blocks:
            blocks[-1][2].append(line)
        else:
            preamble.append(line)
    return preamble, blocks


def parse_digest(date, text):
    """Parse one digest's text into a per-week dict. Raises DigestError when
    the text is not recognisably a digest."""
    lines = text.splitlines()
    if not lines or not _H1.match(lines[0]):
        raise DigestError("no '# Matches' heading - not a digest, or truncated")
    preamble_lines, blocks = _split_blocks(lines[1:])
    if not blocks:
        raise DigestError("no role sections")

    preamble = _MD_LINK.sub(r"\1", " ".join(
        l.strip() for l in preamble_lines if l.strip())).replace("`", "")
    reviewed = _REVIEWED.search(preamble)

    sections, lane = [], None
    for i, (level, name, body) in enumerate(blocks):
        nxt = blocks[i + 1][0] if i + 1 < len(blocks) else None
        if level == 2 and nxt == 3:
            lane = name  # a `##` that parents `###` sections is a lane
            continue
        if level == 2:
            lane = None  # legacy / top-level section: no lane
        entries, prose = _parse_section_body(body)
        prose_text = " ".join(prose)
        if not entries and not prose_text:
            raise DigestError("section %r is empty - truncated?" % name)
        no_matches = prose_text.lower().startswith(NO_MATCHES)
        note = ""
        if no_matches:
            note = prose_text[len(NO_MATCHES):].lstrip(". ").strip()
            prose_text = ""
        sections.append({
            "role": name,
            "lane": lane if level == 3 else None,
            "rejected": name.lower().startswith(REJECTED_HEADING),
            "no_matches": no_matches,
            "no_matches_note": note,
            "intro": prose_text,
            "entries": entries,
        })
    return {
        "date": date,
        "sources": preamble,
        "reviewed": int(reviewed.group(1).replace(",", "")) if reviewed else None,
        "sections": sections,
        "error": None,
    }


def collect_digests():
    """One dict per file in matches/, newest first. A file that cannot be
    parsed still appears, with sections == [] and `error` set, so the page can
    say it was skipped rather than silently showing fewer weeks."""
    digests = []
    directory = paths.matches_dir()
    if directory.is_dir():
        for path in directory.glob("*.md"):
            date = path.stem
            try:
                digests.append(parse_digest(date, path.read_text(encoding="utf-8")))
            except (OSError, ValueError) as exc:  # DigestError and bad UTF-8 are ValueErrors
                digests.append({"date": date, "sources": "", "reviewed": None,
                                "sections": [], "error": str(exc) or type(exc).__name__})
    digests.sort(key=lambda d: d["date"], reverse=True)
    return digests


def match_entries(digest):
    """Entries that count as matches (everything outside 'Rejected on scoring')."""
    return [e for s in digest["sections"] if not s["rejected"] for e in s["entries"]]


def rejected_entries(digest):
    return [e for s in digest["sections"] if s["rejected"] for e in s["entries"]]
