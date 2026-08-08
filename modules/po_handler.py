"""
GNU gettext .po / .pot file handler.

Parses and writes GNU gettext message catalogues, preserving:
  - msgctxt (context)
  - translator comments  (#  ...)
  - extracted comments    (#. ...)
  - source references     (#: file:line)
  - flags                 (#, fuzzy, c-format, ...)
  - previous-string refs  (#| ...)
  - plural forms          (msgid_plural / msgstr[0..N])
  - header entry          (empty msgid with Project-Id-Version, Language, etc.)

Each plural msgstr[N] is exposed as its own bilingual segment so the
translator can edit them independently inside the Workbench grid; the
handler reassembles them on save.

The header entry (empty msgid) is preserved verbatim through round-trip
but is NOT exposed as a translatable segment.

No external dependencies — pure Python, mirrors the API of MQXLIFFHandler.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def _decode_po_string(s: str) -> str:
    """Decode a .po-quoted string body (without the surrounding quotes)."""
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == '\\' and i + 1 < len(s):
            nxt = s[i + 1]
            if nxt == 'n':
                out.append('\n')
            elif nxt == 't':
                out.append('\t')
            elif nxt == 'r':
                out.append('\r')
            elif nxt == '"':
                out.append('"')
            elif nxt == '\\':
                out.append('\\')
            elif nxt == '0':
                out.append('\0')
            else:
                out.append(nxt)
            i += 2
        else:
            out.append(c)
            i += 1
    return ''.join(out)


def _encode_po_string(s: str) -> str:
    """Encode a Python string for use inside a .po `"..."` literal."""
    out = []
    for c in s:
        if c == '\\':
            out.append('\\\\')
        elif c == '"':
            out.append('\\"')
        elif c == '\n':
            out.append('\\n')
        elif c == '\t':
            out.append('\\t')
        elif c == '\r':
            out.append('\\r')
        else:
            out.append(c)
    return ''.join(out)


def _format_po_field(keyword: str, text: str) -> List[str]:
    """
    Format a msgid/msgstr/msgctxt field with proper multi-line wrapping.

    If the string contains \\n, emit one `""` continuation line per
    logical line so the file stays diff-friendly (the convention every
    .po tool uses).
    """
    encoded = _encode_po_string(text)
    if '\\n' not in encoded:
        return [f'{keyword} "{encoded}"']

    # Split on encoded \n so each piece keeps its terminating \n
    parts = encoded.split('\\n')
    pieces = [p + '\\n' for p in parts[:-1]]
    if parts[-1]:
        pieces.append(parts[-1])

    lines = [f'{keyword} ""']
    lines.extend(f'"{p}"' for p in pieces)
    return lines


class POEntry:
    """A single logical entry in a .po file."""

    __slots__ = (
        'translator_comments',
        'extracted_comments',
        'references',
        'flags',
        'previous_lines',
        'msgctxt',
        'msgid',
        'msgid_plural',
        'msgstrs',          # List[str]; len 1 for singular, len N for plural
        'is_header',
        'is_obsolete',
    )

    def __init__(self) -> None:
        self.translator_comments: List[str] = []
        self.extracted_comments: List[str] = []
        self.references: List[str] = []
        self.flags: List[str] = []
        self.previous_lines: List[str] = []
        self.msgctxt: Optional[str] = None
        self.msgid: str = ''
        self.msgid_plural: Optional[str] = None
        self.msgstrs: List[str] = ['']
        self.is_header: bool = False
        self.is_obsolete: bool = False

    @property
    def is_fuzzy(self) -> bool:
        return 'fuzzy' in self.flags

    def serialise(self) -> List[str]:
        """Render this entry as a list of .po file lines (no trailing blank)."""
        prefix = '#~ ' if self.is_obsolete else ''
        lines: List[str] = []

        for c in self.translator_comments:
            lines.append(f'# {c}' if c else '#')
        for c in self.extracted_comments:
            lines.append(f'#. {c}')
        for r in self.references:
            lines.append(f'#: {r}')
        if self.flags:
            lines.append('#, ' + ', '.join(self.flags))
        for p in self.previous_lines:
            lines.append(f'#| {p}')

        if self.msgctxt is not None:
            lines.extend(prefix + l for l in _format_po_field('msgctxt', self.msgctxt))
        lines.extend(prefix + l for l in _format_po_field('msgid', self.msgid))

        if self.msgid_plural is not None:
            lines.extend(prefix + l for l in _format_po_field('msgid_plural', self.msgid_plural))
            for idx, s in enumerate(self.msgstrs):
                lines.extend(prefix + l for l in _format_po_field(f'msgstr[{idx}]', s))
        else:
            lines.extend(prefix + l for l in _format_po_field('msgstr', self.msgstrs[0] if self.msgstrs else ''))

        return lines


class POHandler:
    """Handler for parsing and generating GNU gettext .po / .pot files."""

    _QUOTED_RE = re.compile(r'^\s*"(.*)"\s*$')

    def __init__(self) -> None:
        self.file_path: Optional[str] = None
        self.entries: List[POEntry] = []
        self.source_lang: str = 'unknown'
        self.target_lang: str = 'unknown'
        self.is_template: bool = False  # True for .pot
        # Encoding declared in the header; preserved on save.
        self.encoding: str = 'utf-8'

    # ------------------------------------------------------------------ load

    def load(self, file_path: str) -> bool:
        try:
            self.file_path = file_path
            self.is_template = Path(file_path).suffix.lower() == '.pot'

            # Try UTF-8 first; fall back to latin-1 if the header declares another
            # encoding (rare, but legal). We re-decode with the declared charset
            # after we've parsed the header.
            with open(file_path, 'rb') as f:
                raw = f.read()

            try:
                text = raw.decode('utf-8')
            except UnicodeDecodeError:
                text = raw.decode('latin-1')

            self.entries = self._parse(text)

            # Resolve charset from header and re-decode if needed
            header = self._find_header()
            if header is not None:
                charset = self._extract_header_value(header.msgstrs[0], 'Content-Type')
                if charset:
                    m = re.search(r'charset=([^\s;]+)', charset, re.IGNORECASE)
                    if m:
                        declared = m.group(1).strip().lower()
                        self.encoding = declared
                        if declared not in ('utf-8', 'utf8', 'us-ascii', 'ascii'):
                            try:
                                text = raw.decode(declared)
                                self.entries = self._parse(text)
                                header = self._find_header()
                            except (LookupError, UnicodeDecodeError):
                                pass

                # Target language comes from the header's Language: field if present
                lang = self._extract_header_value(header.msgstrs[0], 'Language')
                if lang:
                    self.target_lang = lang.strip()

            # Source language is conventionally English for gettext catalogues,
            # but we look for an X-Source-Language header as a courtesy.
            if header is not None:
                src = self._extract_header_value(header.msgstrs[0], 'X-Source-Language')
                if src:
                    self.source_lang = src.strip()
                else:
                    self.source_lang = 'en'

            return True
        except Exception as e:
            print(f"[PO] Error loading file: {e}")
            return False

    @staticmethod
    def _extract_header_value(header_msgstr: str, key: str) -> Optional[str]:
        for raw_line in header_msgstr.split('\n'):
            line = raw_line.strip()
            if not line:
                continue
            if ':' in line:
                k, _, v = line.partition(':')
                if k.strip().lower() == key.lower():
                    return v.strip()
        return None

    def _find_header(self) -> Optional[POEntry]:
        for e in self.entries:
            if e.is_header:
                return e
        return None

    def _parse(self, text: str) -> List[POEntry]:
        entries: List[POEntry] = []
        current: Optional[POEntry] = None
        # last_field: which keyword the next `"..."` continuation belongs to
        # one of: msgctxt, msgid, msgid_plural, msgstr, msgstr[N]
        last_field: Optional[str] = None
        last_index: int = 0

        def commit() -> None:
            nonlocal current, last_field
            if current is not None:
                # First entry with empty msgid (and no context) is the header
                if (
                    not current.msgid
                    and current.msgctxt is None
                    and not entries  # only the very first qualifying entry
                ):
                    current.is_header = True
                entries.append(current)
            current = None
            last_field = None

        lines = text.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            if not stripped:
                # Blank line separates entries
                commit()
                i += 1
                continue

            if current is None:
                current = POEntry()

            # Obsolete entries — `#~` prefix. Strip it and parse normally,
            # but mark the entry as obsolete so it's written back the same way.
            obsolete = stripped.startswith('#~')
            if obsolete:
                current.is_obsolete = True
                stripped = stripped[2:].lstrip()
                if not stripped:
                    i += 1
                    continue

            if stripped.startswith('#'):
                if stripped.startswith('#.'):
                    current.extracted_comments.append(stripped[2:].lstrip())
                elif stripped.startswith('#:'):
                    # One #: line can hold multiple refs separated by whitespace;
                    # keep them as a single string for fidelity.
                    current.references.append(stripped[2:].strip())
                elif stripped.startswith('#,'):
                    flags = [f.strip() for f in stripped[2:].split(',') if f.strip()]
                    current.flags.extend(flags)
                elif stripped.startswith('#|'):
                    current.previous_lines.append(stripped[2:].lstrip())
                else:
                    # Plain translator comment: `# foo` or bare `#`
                    body = stripped[1:]
                    if body.startswith(' '):
                        body = body[1:]
                    current.translator_comments.append(body)
                i += 1
                continue

            # Keyword line or continuation
            m_cont = self._QUOTED_RE.match(line)
            if m_cont and last_field is not None:
                addition = _decode_po_string(m_cont.group(1))
                self._append_to_field(current, last_field, last_index, addition)
                i += 1
                continue

            keyword, payload = self._split_keyword(stripped)
            if keyword is None:
                # Unknown line — skip silently rather than blow up
                i += 1
                continue

            decoded = _decode_po_string(payload) if payload is not None else ''

            if keyword == 'msgctxt':
                current.msgctxt = decoded
                last_field = 'msgctxt'
                last_index = 0
            elif keyword == 'msgid':
                current.msgid = decoded
                last_field = 'msgid'
                last_index = 0
            elif keyword == 'msgid_plural':
                current.msgid_plural = decoded
                last_field = 'msgid_plural'
                last_index = 0
                # Pre-allocate msgstrs list for plurals; size grows as msgstr[N] lines appear
                current.msgstrs = []
            elif keyword == 'msgstr':
                current.msgstrs = [decoded]
                last_field = 'msgstr'
                last_index = 0
            elif keyword.startswith('msgstr['):
                idx_match = re.match(r'msgstr\[(\d+)\]', keyword)
                if idx_match:
                    idx = int(idx_match.group(1))
                    while len(current.msgstrs) <= idx:
                        current.msgstrs.append('')
                    current.msgstrs[idx] = decoded
                    last_field = 'msgstr'
                    last_index = idx
            i += 1

        commit()
        return entries

    @staticmethod
    def _split_keyword(line: str) -> Tuple[Optional[str], Optional[str]]:
        """Split a line like `msgid "hello"` into ('msgid', 'hello')."""
        m = re.match(r'^([A-Za-z_]+(?:\[\d+\])?)\s+"(.*)"\s*$', line)
        if m:
            return m.group(1), m.group(2)
        m = re.match(r'^([A-Za-z_]+(?:\[\d+\])?)\s*$', line)
        if m:
            return m.group(1), ''
        return None, None

    @staticmethod
    def _append_to_field(entry: POEntry, field: str, index: int, addition: str) -> None:
        if field == 'msgctxt':
            entry.msgctxt = (entry.msgctxt or '') + addition
        elif field == 'msgid':
            entry.msgid += addition
        elif field == 'msgid_plural':
            entry.msgid_plural = (entry.msgid_plural or '') + addition
        elif field == 'msgstr':
            while len(entry.msgstrs) <= index:
                entry.msgstrs.append('')
            entry.msgstrs[index] += addition

    # -------------------------------------------------------------- extract

    def extract_bilingual_segments(self) -> List[Dict]:
        """
        Yield one segment per translatable msgstr. For plural entries this
        produces N segments (one per msgstr[i]).

        Each segment dict contains:
            id         — composite key 'entry_index[:plural_index]'
            source     — msgid (or msgid_plural for i>=1)
            target     — msgstr (or msgstr[i])
            status     — 'not_started' / 'pre_translated' / 'draft'
            notes      — joined comments + msgctxt for translator context
            msgctxt    — raw context (None if absent)
            references — '#:' source-code references joined with newlines
            flags      — comma-joined flags
            is_fuzzy   — bool
            plural_index — None or int
        """
        segments: List[Dict] = []
        for ent_idx, entry in enumerate(self.entries):
            if entry.is_header or entry.is_obsolete:
                continue

            base_notes = self._build_notes(entry)
            flags_str = ', '.join(entry.flags)
            refs_str = '\n'.join(entry.references)

            if entry.msgid_plural is not None:
                # Ensure msgstrs has at least singular+plural slots
                if not entry.msgstrs:
                    entry.msgstrs = ['', '']
                elif len(entry.msgstrs) < 2:
                    entry.msgstrs.append('')
                for p_idx, msgstr in enumerate(entry.msgstrs):
                    source = entry.msgid if p_idx == 0 else entry.msgid_plural
                    notes = base_notes
                    if entry.msgid_plural is not None:
                        plural_hint = (
                            f"[plural form {p_idx} — singular]" if p_idx == 0
                            else f"[plural form {p_idx}]"
                        )
                        notes = (plural_hint + '\n' + notes) if notes else plural_hint
                    segments.append({
                        'id': f'{ent_idx}:{p_idx}',
                        'source': source,
                        'target': msgstr,
                        'status': self._status_for(entry, msgstr),
                        'notes': notes,
                        'msgctxt': entry.msgctxt,
                        'references': refs_str,
                        'flags': flags_str,
                        'is_fuzzy': entry.is_fuzzy,
                        'plural_index': p_idx,
                    })
            else:
                msgstr = entry.msgstrs[0] if entry.msgstrs else ''
                segments.append({
                    'id': str(ent_idx),
                    'source': entry.msgid,
                    'target': msgstr,
                    'status': self._status_for(entry, msgstr),
                    'notes': base_notes,
                    'msgctxt': entry.msgctxt,
                    'references': refs_str,
                    'flags': flags_str,
                    'is_fuzzy': entry.is_fuzzy,
                    'plural_index': None,
                })

        return segments

    @staticmethod
    def _status_for(entry: POEntry, msgstr: str) -> str:
        if not msgstr.strip():
            return 'not_started'
        if entry.is_fuzzy:
            return 'draft'
        return 'pre_translated'

    @staticmethod
    def _build_notes(entry: POEntry) -> str:
        parts: List[str] = []
        if entry.msgctxt is not None:
            parts.append(f'Context: {entry.msgctxt}')
        if entry.extracted_comments:
            parts.append('\n'.join(entry.extracted_comments))
        if entry.translator_comments:
            parts.append('\n'.join(entry.translator_comments))
        if entry.references:
            parts.append('References: ' + ' '.join(entry.references))
        if entry.flags:
            parts.append('Flags: ' + ', '.join(entry.flags))
        return '\n'.join(p for p in parts if p)

    # ---------------------------------------------------------------- save

    def update_target_segments(self, translations: List[str]) -> int:
        """
        Apply translations back to entries in the order produced by
        extract_bilingual_segments() — i.e. one translation per yielded segment.

        Returns the number of msgstrs updated. Also clears the 'fuzzy' flag
        on any entry where every msgstr is now non-empty (matches msgmerge's
        convention: a translator who's finished editing wants the fuzzy mark
        gone).
        """
        # Walk entries in the same order extract_bilingual_segments() used
        i = 0
        updated = 0
        for entry in self.entries:
            if entry.is_header or entry.is_obsolete:
                continue
            if entry.msgid_plural is not None:
                needed = max(2, len(entry.msgstrs))
                while len(entry.msgstrs) < needed:
                    entry.msgstrs.append('')
                for p_idx in range(needed):
                    if i < len(translations):
                        new_val = translations[i]
                        if new_val != entry.msgstrs[p_idx]:
                            entry.msgstrs[p_idx] = new_val
                            updated += 1
                    i += 1
            else:
                if i < len(translations):
                    new_val = translations[i]
                    if not entry.msgstrs:
                        entry.msgstrs = ['']
                    if new_val != entry.msgstrs[0]:
                        entry.msgstrs[0] = new_val
                        updated += 1
                i += 1

            # Clear fuzzy flag if all msgstrs are now filled in. We leave
            # fuzzy in place when any plural form is still empty so the
            # translator doesn't get a false "done" signal.
            if 'fuzzy' in entry.flags and all(s.strip() for s in entry.msgstrs):
                entry.flags = [f for f in entry.flags if f != 'fuzzy']

        return updated

    def save(self, output_path: str) -> bool:
        try:
            lines: List[str] = []
            for idx, entry in enumerate(self.entries):
                if idx > 0:
                    lines.append('')
                lines.extend(entry.serialise())
            content = '\n'.join(lines) + '\n'

            # Write in the declared encoding so non-UTF-8 catalogues round-trip
            try:
                encoded = content.encode(self.encoding)
            except (LookupError, UnicodeEncodeError):
                encoded = content.encode('utf-8')

            with open(output_path, 'wb') as f:
                f.write(encoded)
            return True
        except Exception as e:
            print(f"[PO] Error saving file: {e}")
            return False

    # ------------------------------------------------------------- helpers

    def get_segment_count(self) -> int:
        n = 0
        for entry in self.entries:
            if entry.is_header or entry.is_obsolete:
                continue
            n += max(1, len(entry.msgstrs) if entry.msgid_plural is not None else 1)
        return n
