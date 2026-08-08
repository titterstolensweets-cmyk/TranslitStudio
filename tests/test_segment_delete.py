"""Tests for segment deletion (segment_split_merge.deletable / delete_segments).

The gate is the safety-critical part: deleting whole segments is only allowed
for paste / plain-text / Start-Empty projects. On DOCX / Okapi / bilingual-CAT
projects it would leave a gap in the merged-back export, so it must be refused.
"""
from types import SimpleNamespace

from modules import segment_split_merge as ssm


def _proj(**attrs):
    """A minimal project stand-in. Missing source-path attrs default to None."""
    base = dict(import_engine="", original_docx_path=None, original_txt_path=None,
                trados_source_path=None, memoq_source_path=None,
                mqxliff_source_path=None, cafetran_source_path=None,
                sdlppx_source_path=None, dejavu_source_path=None,
                po_source_path=None, sdlxliff_source_paths=None)
    base.update(attrs)
    return SimpleNamespace(**base)


def _seg(i, locked=False):
    return SimpleNamespace(id=i, source=f"s{i}", target="", status="not_started",
                           locked=locked)


def test_deletable_allows_paste_text_and_empty():
    assert ssm.deletable(_proj()) is True                       # paste / start empty
    assert ssm.deletable(_proj(original_txt_path="a.txt")) is True  # plain txt/md


def test_deletable_blocks_roundtrip_projects():
    assert ssm.deletable(_proj(import_engine="okapi")) is False
    assert ssm.deletable(_proj(original_docx_path="a.docx")) is False
    assert ssm.deletable(_proj(memoq_source_path="a")) is False
    assert ssm.deletable(_proj(trados_source_path="a")) is False
    assert ssm.deletable(_proj(sdlxliff_source_paths=["a"])) is False
    assert ssm.deletable(None) is False


def test_delete_segments_removes_and_skips_locked():
    segs = [_seg(1), _seg(2, locked=True), _seg(3), _seg(4)]
    removed = ssm.delete_segments(segs, [0, 1, 3])   # try to delete #1, locked #2, #4
    assert removed == 2                              # locked one skipped
    assert [s.id for s in segs] == [2, 3]            # #2 (locked) and #3 remain
    ssm.renumber_ids(segs)
    assert [s.id for s in segs] == [2, 3]            # contiguous from the base


def test_delete_segments_out_of_range_and_empty():
    segs = [_seg(1), _seg(2)]
    assert ssm.delete_segments(segs, [5, -1]) == 0
    assert len(segs) == 2
    assert ssm.delete_segments(segs, []) == 0
