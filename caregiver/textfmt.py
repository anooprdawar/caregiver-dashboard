"""Render clinical note text as readable HTML.

Notes arrive as three different things wearing the same clothes: markdown-ish text, HTML that has
already been stripped to plain text, and dictated clinical prose with ALL-CAPS headings and
"LABEL:" run-ins. This renders all three legibly. Input is escaped first, so a note can never
inject markup.
"""
from __future__ import annotations

import html
import re

# "IMPRESSION:", "ASSESSMENT AND PLAN:", "Discharge Medications:" at the start of a line.
_RUN_IN = re.compile(r"^([A-Z][A-Za-z0-9 /&'\-()]{2,60}):\s*(.*)$")
_ALLCAPS = re.compile(r"^[A-Z][A-Z0-9 /&'\-().,]{3,70}$")
_BULLET = re.compile(r"^\s*(?:[-*•·]|\d{1,2}[.)])\s+(.*)$")
_MD_HEAD = re.compile(r"^(#{1,4})\s+(.*)$")
_KEY_HEADINGS = re.compile(
    r"^(impression|findings|diagnosis|assessment|plan|assessment and plan|conclusion|"
    r"history of present illness|hpi|chief complaint|indication|comparison|technique|"
    r"medications|allergies|discharge medications|follow.?up|recommendations?|"
    r"hospital course|physical exam|review of systems|labs?|imaging|procedure)\b", re.I)


def _inline(s: str) -> str:
    """Bold, italics and bare URLs, applied to already-escaped text."""
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![\w*])\*([^*\n]+?)\*(?![\w*])", r"<em>\1</em>", s)
    s = re.sub(r"(?<![\w/])_([^_\n]+?)_(?![\w/])", r"<em>\1</em>", s)
    s = re.sub(r"(https?://[^\s<]+)", r'<a href="\1" rel="noreferrer noopener">\1</a>', s)
    return s


def render(text: str | None) -> str:
    """Return HTML for a clinical note. Safe to mark as trusted: the input is escaped up front."""
    if not text or not text.strip():
        return '<p class="muted">No text.</p>'

    lines = [ln.rstrip() for ln in html.escape(text).replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    out: list[str] = []
    para: list[str] = []
    bullets: list[str] = []

    def flush_para():
        if para:
            out.append("<p>" + _inline(" ".join(para)) + "</p>")
            para.clear()

    def flush_bullets():
        if bullets:
            out.append("<ul>" + "".join(f"<li>{_inline(b)}</li>" for b in bullets) + "</ul>")
            bullets.clear()

    def flush():
        flush_para()
        flush_bullets()

    for raw in lines:
        line = raw.strip()
        if not line:
            flush()
            continue

        md = _MD_HEAD.match(line)
        if md:
            flush()
            level = min(len(md.group(1)) + 2, 5)
            out.append(f"<h{level}>{_inline(md.group(2))}</h{level}>")
            continue

        bul = _BULLET.match(raw)
        if bul:
            flush_para()
            bullets.append(bul.group(1).strip())
            continue
        flush_bullets()

        if _ALLCAPS.match(line) and len(line.split()) <= 10:
            flush_para()
            out.append(f'<h4 class="sec">{_inline(line.title() if line.isupper() else line)}</h4>')
            continue

        run = _RUN_IN.match(line)
        if run and (_KEY_HEADINGS.match(run.group(1)) or run.group(1).isupper()):
            flush_para()
            label, rest = run.group(1), run.group(2).strip()
            out.append(f'<h4 class="sec">{_inline(label.title() if label.isupper() else label)}</h4>')
            if rest:
                para.append(rest)
            continue
        if run and len(run.group(1)) <= 40 and run.group(2).strip():
            flush_para()
            out.append(f'<p class="kv"><b>{_inline(run.group(1))}:</b> {_inline(run.group(2).strip())}</p>')
            continue

        para.append(line)

    flush()
    return "\n".join(out)


def summarize(text: str | None, limit: int = 240) -> str:
    """A one-line gist for list views: prefer the impression, else the first real sentence."""
    if not text:
        return ""
    m = re.search(r"(?:IMPRESSION|DIAGNOSIS|CONCLUSION|ASSESSMENT)\s*:?\s*(.+)", text, re.I | re.S)
    body = m.group(1) if m else text
    body = re.sub(r"\s+", " ", body).strip()
    return body[:limit] + ("…" if len(body) > limit else "")
