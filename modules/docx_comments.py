"""
DOCX comment extraction
========================

Reads Word review comments out of a .docx so they can be imported into
Supervertaler as *actual comments* (anchored, author-tagged) rather than
as translatable segments.

A Word comment has three relevant pieces, spread across two XML parts:

* ``word/comments.xml`` – the comment body, author, date and id:
      <w:comment w:id="3" w:author="Jane Roe" w:date="..." w:initials="JR">
          <w:p><w:r><w:t>comment text</w:t></w:r></w:p>
      </w:comment>

* ``word/document.xml`` – where the comment anchors, as a run range:
      <w:commentRangeStart w:id="3"/> ...runs... <w:commentRangeEnd w:id="3"/>
  The text of the runs between start and end is the highlighted span the
  reviewer attached the comment to. We use that span to find the matching
  Supervertaler segment.

This module is read-only and has no Okapi/Qt dependency.
"""

from __future__ import annotations

import re
import zipfile
from typing import Any, Dict, List, Tuple
from xml.etree import ElementTree as ET

# WordprocessingML main namespace
_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NS = {"w": _W}


def _q(tag: str) -> str:
    return f"{{{_W}}}{tag}"


def _comment_text(comment_el: ET.Element) -> str:
    """Concatenate the visible text of a <w:comment>, joining paragraphs
    with newlines."""
    paras = []
    for p in comment_el.iter(_q("p")):
        runs = [t.text or "" for t in p.iter(_q("t"))]
        paras.append("".join(runs))
    return "\n".join(part for part in paras).strip()


def _parse_comments_xml(data: bytes) -> Dict[str, Dict[str, str]]:
    """Return {comment_id: {author, date, initials, text}}."""
    out: Dict[str, Dict[str, str]] = {}
    root = ET.fromstring(data)
    for c in root.findall(_q("comment")):
        cid = c.get(_q("id"))
        if cid is None:
            continue
        out[cid] = {
            "author": c.get(_q("author"), ""),
            "date": c.get(_q("date"), ""),
            "initials": c.get(_q("initials"), ""),
            "text": _comment_text(c),
        }
    return out


# How much context to capture from the START of a comment range. We capture
# from the comment's start through the end of its paragraph (capped), NOT just
# the highlighted span: the highlighted word alone is often ambiguous — e.g. a
# word that also appears in the document title — whereas the run of text that
# follows it uniquely identifies the segment. Capturing the whole range is
# unreliable anyway (Word duplicates drawing text via mc:AlternateContent, and
# reviewers sometimes select huge blocks).
_ANCHOR_MAX = 100


def _parse_anchor_spans(data: bytes) -> Tuple[Dict[str, Dict[str, Any]], int]:
    """Walk ``document.xml`` and return ``(spans, total_chars)``.

    ``spans`` maps ``comment_id -> {"anchor": <text>, "offset": <int>}``:

    * ``anchor`` is the run text from the START of the comment range to the end
      of that paragraph (capped at ~100 chars). When a comment id's range opens
      we accumulate run text and keep going *past* commentRangeEnd until the cap
      or the end of the start paragraph — so even a one-word highlight yields
      enough following context to locate its segment. Consecutive exact-duplicate
      runs are skipped to absorb Word's mc:AlternateContent (Choice/Fallback)
      doubling.
    * ``offset`` is the number of body-text characters seen *before* the
      comment's range start, in document order — a position fingerprint used to
      tell apart segments that have identical text.

    ``total_chars`` is the total body-text length, so a caller can compare a
    comment's offset to a segment's cumulative offset as a fraction of the whole.
    """
    spans: Dict[str, List[str]] = {}
    offsets: Dict[str, int] = {}
    done: set = set()
    capturing: set = set()
    running = 0  # body-text characters seen so far, in document order

    for _evt, el in ET.iterparse(_BytesReader(data), events=("end",)):
        tag = el.tag
        if tag == _q("commentRangeStart"):
            cid = el.get(_q("id"))
            if cid is not None and cid not in done:
                capturing.add(cid)
                spans.setdefault(cid, [])
                offsets.setdefault(cid, running)
        elif tag == _q("t"):
            txt = el.text or ""
            if not txt:
                continue
            for cid in list(capturing):
                if cid in done:
                    continue
                buf = spans[cid]
                # Skip a run that exactly repeats what we already have
                # (mc:Choice/Fallback duplication of the same drawing text).
                if buf and "".join(buf).endswith(txt):
                    continue
                buf.append(txt)
                if sum(len(p) for p in buf) >= _ANCHOR_MAX:
                    done.add(cid)
            running += len(txt)
        elif tag == _q("p"):
            # End of a paragraph: stop capturing anything that started in it so
            # the anchor context never bleeds into unrelated later text.
            capturing.clear()

    spans_out = {cid: {"anchor": "".join(parts).strip()[:_ANCHOR_MAX],
                       "offset": offsets.get(cid, 0)}
                 for cid, parts in spans.items()}
    return spans_out, running


class _BytesReader:
    """Minimal file-like wrapper so ET.iterparse can read a bytes blob."""

    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            chunk = self._data[self._pos:]
            self._pos = len(self._data)
            return chunk
        chunk = self._data[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk


def parse_docx_comments(docx_path: str) -> List[Dict[str, Any]]:
    """Extract Word comments from a .docx.

    Returns a list of dicts (in comment-id order) with keys:
        id, author, date, initials, text, anchor_text, char_offset, doc_chars

    ``anchor_text`` is the run text from the highlighted span to the end of its
    paragraph (may be empty for a comment with no range). ``char_offset`` is the
    body-text position of the comment's anchor and ``doc_chars`` the total body
    length — together a document-order fingerprint used by
    :func:`match_comments_to_segments` to disambiguate identical segments.
    Returns [] if the document has no comments.
    """
    try:
        with zipfile.ZipFile(docx_path) as z:
            names = set(z.namelist())
            if "word/comments.xml" not in names:
                return []
            comments = _parse_comments_xml(z.read("word/comments.xml"))
            anchors: Dict[str, Dict[str, Any]] = {}
            doc_chars = 0
            if "word/document.xml" in names:
                try:
                    anchors, doc_chars = _parse_anchor_spans(z.read("word/document.xml"))
                except Exception:
                    anchors, doc_chars = {}, 0
    except (zipfile.BadZipFile, KeyError, OSError):
        return []

    result: List[Dict[str, Any]] = []
    # Sort by numeric id when possible so they import in document order.
    def _key(cid: str):
        m = re.match(r"\d+", cid or "")
        return (0, int(m.group())) if m else (1, cid)

    for cid in sorted(comments.keys(), key=_key):
        info = comments[cid]
        if not info["text"]:
            continue
        a = anchors.get(cid, {})
        result.append({
            "id": cid,
            "author": info["author"],
            "date": info["date"],
            "initials": info["initials"],
            "text": info["text"],
            "anchor_text": a.get("anchor", ""),
            "char_offset": a.get("offset", 0),
            "doc_chars": doc_chars,
        })
    return result


# ── Matching comments to Supervertaler segments ──────────────────────────────
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _norm_for_match(s: str) -> str:
    """Tag-stripped, whitespace-collapsed, casefolded form for text matching."""
    return _WS_RE.sub(" ", _TAG_RE.sub("", s or "")).strip().casefold()


def match_comments_to_segments(comments: List[Dict[str, Any]],
                               segment_sources: List[str]) -> List[Any]:
    """Map each parsed comment to the index of its best segment.

    ``comments`` is the list from :func:`parse_docx_comments`; ``segment_sources``
    is the segment source strings in document order. Returns a list the same
    length as ``comments`` of 0-based segment indices, or ``None`` where no
    confident match was found (the caller decides the fallback, e.g. top).

    A comment is matched by *containment* of its anchor context (longest prefix
    first, so a paragraph Okapi split into several segments still matches on its
    first segment). When several segments match — identical or boilerplate text —
    the tie is broken by **document position**: the comment's character offset
    vs each candidate segment's cumulative offset, each as a fraction of its
    own total, so the comment lands on the occurrence the reviewer actually
    highlighted rather than always the first.
    """
    norm_sources = [_norm_for_match(s) for s in segment_sources]
    # Cumulative tag-stripped length before each segment, for positional ties.
    seg_cum: List[int] = []
    acc = 0
    for s in segment_sources:
        seg_cum.append(acc)
        acc += len(_TAG_RE.sub("", s or ""))
    seg_total = acc or 1

    return [_match_one(_norm_for_match(c.get("anchor_text", "")),
                       c.get("char_offset"), c.get("doc_chars"),
                       norm_sources, seg_cum, seg_total)
            for c in comments]


def _match_one(anchor, char_offset, doc_chars, norm_sources, seg_cum, seg_total):
    if len(anchor) < 6:
        return None
    target_frac = None
    if isinstance(char_offset, int) and isinstance(doc_chars, int) and doc_chars > 0:
        target_frac = char_offset / doc_chars
    for length in (len(anchor), 60, 40, 24, 12):
        if length > len(anchor):
            continue
        needle = anchor[:length]
        if len(needle) < 6:
            break
        hits = [i for i, src in enumerate(norm_sources)
                if len(src) >= 6 and needle in src]
        if not hits:
            continue
        if len(hits) == 1 or target_frac is None:
            return hits[0]
        # Several matches (identical/boilerplate text): pick the one closest in
        # document position to where the comment's anchor actually sits.
        return min(hits, key=lambda i: abs((seg_cum[i] / seg_total) - target_frac))
    return None
