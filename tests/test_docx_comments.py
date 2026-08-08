"""Guard DOCX comment extraction (modules/docx_comments.py).

Locks in the fix for the bug where importing a .docx with several Word
comments attached them all to segment 1: the anchor parser used to capture
only the highlighted run text, which is often a single mid-sentence word
(e.g. "werkwijze") that also appears in the document title — so the matcher
could not tell the title segment from the body segment.

The parser now captures the run text from the highlight to the END of its
paragraph, giving enough trailing context ("werkwijze voor het sorteren…")
to pin the comment to the right segment. These tests assert that context is
captured (past the highlight, bounded by the paragraph) for every comment.
"""

from __future__ import annotations

import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.docx_comments import (
    match_comments_to_segments,
    parse_docx_comments,
)

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

_DOCUMENT_XML = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="{_W}"><w:body>
  <w:p><w:r><w:t>Een werkwijze voor het scheiden van afval</w:t></w:r></w:p>
  <w:p>
    <w:r><w:t xml:space="preserve">De uitvinding betreft een </w:t></w:r>
    <w:commentRangeStart w:id="0"/>
    <w:r><w:t>werkwijze</w:t></w:r>
    <w:commentRangeEnd w:id="0"/>
    <w:r><w:t xml:space="preserve"> voor het sorteren van zware kunststof materialen.</w:t></w:r>
    <w:r><w:commentReference w:id="0"/></w:r>
  </w:p>
  <w:p>
    <w:r><w:t xml:space="preserve">In </w:t></w:r>
    <w:commentRangeStart w:id="1"/>
    <w:r><w:t>Europa</w:t></w:r>
    <w:commentRangeEnd w:id="1"/>
    <w:r><w:t xml:space="preserve"> worden jaarlijks miljoenen voertuigen afgedankt.</w:t></w:r>
    <w:r><w:commentReference w:id="1"/></w:r>
  </w:p>
</w:body></w:document>"""

_COMMENTS_XML = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:comments xmlns:w="{_W}">
  <w:comment w:id="0" w:author="Tester" w:date="2026-01-01T00:00:00Z" w:initials="T">
    <w:p><w:r><w:t>First comment</w:t></w:r></w:p>
  </w:comment>
  <w:comment w:id="1" w:author="Tester" w:date="2026-01-01T00:00:00Z" w:initials="T">
    <w:p><w:r><w:t>Second comment</w:t></w:r></w:p>
  </w:comment>
</w:comments>"""


def _make_docx(tmp_path) -> str:
    path = os.path.join(str(tmp_path), "sample.docx")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types '
            'xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        z.writestr("word/document.xml", _DOCUMENT_XML)
        z.writestr("word/comments.xml", _COMMENTS_XML)
    return path


def test_all_comments_extracted(tmp_path):
    comments = parse_docx_comments(_make_docx(tmp_path))
    assert [c["id"] for c in comments] == ["0", "1"]
    assert [c["text"] for c in comments] == ["First comment", "Second comment"]
    assert all(c["author"] == "Tester" for c in comments)


def test_anchor_captures_context_past_the_highlight(tmp_path):
    """The crux of the bug fix: the anchor must include the text that FOLLOWS
    the highlighted word, so an ambiguous word can be disambiguated."""
    comments = {c["id"]: c for c in parse_docx_comments(_make_docx(tmp_path))}

    a0 = comments["0"]["anchor_text"]
    assert a0.startswith("werkwijze")
    # Must reach past the highlight into the rest of the paragraph — this is
    # what distinguishes the body ("…voor het sorteren") from the title
    # ("…voor het scheiden"), both of which start with "werkwijze".
    assert "voor het sorteren" in a0

    a1 = comments["1"]["anchor_text"]
    assert a1.startswith("Europa")
    assert "worden jaarlijks" in a1


def test_anchor_is_bounded_by_its_paragraph(tmp_path):
    """Context must not bleed into the next paragraph's text."""
    comments = {c["id"]: c for c in parse_docx_comments(_make_docx(tmp_path))}
    # Comment 0 lives in the second paragraph; the third paragraph's "Europa"
    # text must not appear in its anchor.
    assert "Europa" not in comments["0"]["anchor_text"]


def test_no_comments_file_returns_empty(tmp_path):
    path = os.path.join(str(tmp_path), "plain.docx")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", _DOCUMENT_XML)
    assert parse_docx_comments(path) == []


def test_char_offset_is_recorded_in_document_order(tmp_path):
    comments = {c["id"]: c for c in parse_docx_comments(_make_docx(tmp_path))}
    # Comment 0 (2nd paragraph) sits before comment 1 (3rd paragraph).
    assert comments["0"]["char_offset"] < comments["1"]["char_offset"]
    assert comments["0"]["doc_chars"] > 0


# ── match_comments_to_segments ───────────────────────────────────────────────

def test_match_basic_containment():
    """Mid-sentence anchors map to the segment that contains them, not the
    title that merely shares the highlighted word."""
    segments = [
        "Een werkwijze voor het scheiden van afval",                 # 0 title
        "De uitvinding betreft een werkwijze voor het sorteren.",    # 1 body
        "In Europa worden jaarlijks miljoenen voertuigen afgedankt.",  # 2
    ]
    comments = [
        {"anchor_text": "werkwijze voor het sorteren.", "char_offset": 50, "doc_chars": 120},
        {"anchor_text": "Europa worden jaarlijks miljoenen", "char_offset": 95, "doc_chars": 120},
    ]
    assert match_comments_to_segments(comments, segments) == [1, 2]


def test_match_identical_segments_use_position():
    """Five identical segments; the comment on the 4th must NOT collapse to the
    first — document position picks the right occurrence."""
    dup = "The quick brown fox jumps over the lazy dog every single day."
    segments = ["Intro paragraph number one here.", dup, "A divider line.",
                dup, dup, dup, "Closing paragraph at the very end here."]
    # doc_chars ~ sum of segment lengths; place the anchor near the 4th 'dup'
    # (index 4). Cumulative offset of index 4 ≈ start of its text.
    seg_lens = [len(s) for s in segments]
    offset_to_idx4 = sum(seg_lens[:4])
    total = sum(seg_lens)
    comments = [{"anchor_text": dup, "char_offset": offset_to_idx4, "doc_chars": total}]
    assert match_comments_to_segments(comments, segments) == [4]


def test_match_unplaced_returns_none():
    segments = ["Totally unrelated content about widgets and gadgets."]
    comments = [{"anchor_text": "something that does not appear anywhere here at all",
                 "char_offset": 0, "doc_chars": 100}]
    assert match_comments_to_segments(comments, segments) == [None]
