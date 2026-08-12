"""
sdltm_handler — read-only consult of Trados Studio .sdltm Translation Memories.

A .sdltm file is a SQLite database. We open it via a URI in mode=ro so that
opening it concurrently with Trados Studio (which keeps it in WAL mode) is
safe — readers never block writers and vice-versa. We don't write back; the
.sdltm stays the user's working copy in Trados.

Each translation_units row stores source_segment / target_segment as a small
XML document of the form:

    <Segment><Elements>
        <Tag><Type>Start|End|Standalone|Locked</Type><TagID>N</TagID>...</Tag>
        <Text><Value>literal text</Value></Text>
        ...
    </Elements><CultureName>nl-BE</CultureName></Segment>

We map those Trados tags to Supervertaler's existing inline-tag form so the
text round-trips cleanly into the grid and TM lookups:

    <Tag Type=Start  TagID=N>   →  <N>
    <Tag Type=End    TagID=N>   →  </N>
    <Tag Type=Standalone TagID=N> →  <N/>
    <Tag Type=Locked TagID=N>   →  <N/>     (treated as a standalone marker)

This is the same form produced by modules/sdlppx_handler.py for SDLXLIFF
imports, so a TM hit with `<116>` markers slots into the editor grid the
same way a directly-imported segment does.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Iterator, Optional, Tuple, Dict
from xml.etree import ElementTree as ET


class SDLTMError(Exception):
    """Raised when an .sdltm cannot be opened or parsed."""


class SDLTMReader:
    """Read-only reader for a Trados Studio `.sdltm` file.

    Lifetime is short — open, read metadata or iterate TUs, close. Safe to
    open concurrently with Trados; we use SQLite URI `mode=ro` so no write
    locks are taken.
    """

    def __init__(self, file_path: str, log_callback=None):
        self.file_path = str(Path(file_path).resolve())
        self.log = log_callback or (lambda msg: None)
        self._conn: Optional[sqlite3.Connection] = None

    # ── Connection helpers ──────────────────────────────────────────────

    def _open(self) -> sqlite3.Connection:
        if self._conn is None:
            if not os.path.exists(self.file_path):
                raise SDLTMError(f"File not found: {self.file_path}")
            # `mode=ro` lets us coexist with Trados; `nolock=1` would also
            # work but ro is the safer default. The file:// URI form is
            # required to pass mode flags.
            uri = f"file:{self.file_path}?mode=ro"
            try:
                self._conn = sqlite3.connect(uri, uri=True)
            except sqlite3.Error as e:
                raise SDLTMError(f"Could not open .sdltm: {e}") from e
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "SDLTMReader":
        self._open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ── Public API ──────────────────────────────────────────────────────

    def metadata(self) -> Dict[str, object]:
        """Return basic info about the TM stored in the file.

        The file always contains exactly one TM in practice (Trados stores
        each TM in its own .sdltm), but the schema supports several so we
        pick the first row.
        """
        conn = self._open()
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT name, source_language, target_language "
                "FROM translation_memories ORDER BY id LIMIT 1"
            )
            row = cur.fetchone()
        except sqlite3.Error as e:
            raise SDLTMError(f"Could not read translation_memories: {e}") from e
        if not row:
            raise SDLTMError("No translation_memories rows in file")
        name, src_lang, tgt_lang = row

        # Count translatable TUs (skip ones that have neither source nor target).
        cur.execute(
            "SELECT COUNT(*) FROM translation_units "
            "WHERE source_segment IS NOT NULL AND target_segment IS NOT NULL"
        )
        tu_count = cur.fetchone()[0]

        return {
            "name": name or Path(self.file_path).stem,
            "source_lang": src_lang,
            "target_lang": tgt_lang,
            "tu_count": tu_count,
            "file_path": self.file_path,
            "mtime": os.path.getmtime(self.file_path),
        }

    def iter_tus(
        self,
        since_id: int = 0,
        since_change_date: Optional[str] = None,
        batch_size: int = 1000,
    ) -> Iterator[Tuple[int, str, str, Optional[str]]]:
        """Yield (tu_id, source_text, target_text, change_date) tuples with
        tags converted to Supervertaler `<N>` form.

        With default args (`since_id=0`, `since_change_date=None`) yields
        every translatable TU in the file. Pass non-default args for an
        incremental delta sync — only TUs with `id > since_id` OR
        `change_date > since_change_date` are returned. Both filters are
        OR-ed so a re-saved TU (same id, newer change_date) is picked up
        even after the new-id check.

        The yielded `change_date` is the ISO string from the .sdltm
        (e.g. ``"2026-04-09 23:46:25"``); callers tracking high-water
        marks can compare it lexicographically.

        Skips TUs that fail to parse or come out empty; logs a warning
        per skipped row up to a small cap to avoid log spam.
        """
        conn = self._open()
        cur = conn.cursor()
        # Build the WHERE clause conditionally so an unset since_change_date
        # doesn't quietly match every row (every ISO date string is > "").
        base = (
            "SELECT id, source_segment, target_segment, change_date "
            "FROM translation_units "
            "WHERE source_segment IS NOT NULL AND target_segment IS NOT NULL"
        )
        params: list = []
        delta_clauses: list = []
        if since_id:
            delta_clauses.append("id > ?")
            params.append(since_id)
        if since_change_date:
            delta_clauses.append("(change_date IS NOT NULL AND change_date > ?)")
            params.append(since_change_date)
        if delta_clauses:
            base += " AND (" + " OR ".join(delta_clauses) + ")"
        base += " ORDER BY id"
        cur.execute(base, params)
        skipped = 0
        while True:
            rows = cur.fetchmany(batch_size)
            if not rows:
                break
            for tu_id, src_xml, tgt_xml, change_date in rows:
                try:
                    src = parse_segment_xml(src_xml)
                    tgt = parse_segment_xml(tgt_xml)
                except Exception as e:
                    skipped += 1
                    if skipped <= 5:  # cap log noise on broadly-broken files
                        self.log(f"  Skipped TU {tu_id}: parse error ({e})")
                    continue
                if not src.strip() or not tgt.strip():
                    skipped += 1
                    continue
                yield tu_id, src, tgt, change_date
        if skipped:
            self.log(f"  Skipped {skipped} TU(s) with empty/unparseable segments")


# ─── XML → Supervertaler tag-form converter ─────────────────────────────

def parse_segment_xml(xml: str) -> str:
    """Convert a Trados `<Segment>` XML doc to Supervertaler text form.

    Walks `<Elements>` children in order, mapping `<Text><Value>...</Value>`
    to its literal text and `<Tag>` blocks to `<N>`, `</N>`, or `<N/>` based
    on `<Type>`. ElementTree handles XML entity decoding for us, so input
    like `&amp;` reaches us as `&` automatically.
    """
    if not xml:
        return ""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        raise SDLTMError(f"Malformed segment XML: {e}") from e

    elements = root.find("Elements")
    if elements is None:
        return ""

    parts = []
    for child in elements:
        tag = child.tag
        if tag == "Text":
            value_el = child.find("Value")
            if value_el is not None and value_el.text:
                parts.append(value_el.text)
        elif tag == "Tag":
            type_el = child.find("Type")
            id_el = child.find("TagID")
            tag_type = type_el.text if type_el is not None else ""
            tag_id = id_el.text if id_el is not None else ""
            if not tag_id:
                continue  # Malformed tag, drop it silently
            if tag_type == "Start":
                parts.append(f"<{tag_id}>")
            elif tag_type == "End":
                parts.append(f"</{tag_id}>")
            else:
                # Standalone, Locked, or anything else we haven't seen –
                # represent as a self-closing marker. Locked tags wrap
                # protected literals (URLs, codes); preserving the marker
                # at least keeps tag count alignment with the source.
                parts.append(f"<{tag_id}/>")
        # Other element kinds (rare extensions) we skip rather than guess.

    return "".join(parts)
