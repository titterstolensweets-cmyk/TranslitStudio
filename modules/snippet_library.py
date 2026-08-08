#!/usr/bin/env python3
"""
=============================================================================
MODULE: Snippet Library
=============================================================================
File-backed snippet storage for the Workbench Sidekick "Special Characters"
and "Personal Snippets" menus.

Each snippet is a single `.md` file under `<user_data>/snippet_library/`.
The filename (minus `.md`) is the label shown in the Sidekick menu, and
the file body is exactly the text inserted at the cursor when the snippet
is clicked. Folder structure maps to tree categories: a file at

    snippet_library/Special Characters/← → ↑ ↓ ⇄ ↔.md

appears under "Special Characters" with that glyph row as its label.

For backwards compatibility, optional YAML-ish front matter delimited by
`---` is still parsed off the body. The only field the loader reads from
front matter is ``name:`` – if present it overrides the displayed label,
letting power users keep an ASCII-safe filename while showing a unicode
label. New defaults (v1.9.459+) don't use this; the filename is the label.

Default snippets are defined in `DEFAULT_SNIPPETS` and written to disk on
first launch if missing. Users can edit, delete, or add their own .md files
freely – defaults are re-seeded only when absent, never overwriting edits.

Author: Michael Beijer (Claude-assisted)
=============================================================================
"""

from pathlib import Path
from typing import Optional, List, Dict, Tuple
import re


_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def _split_front_matter(text: str) -> Tuple[Dict[str, str], str]:
    """Split optional YAML-ish front matter from body. Returns (meta, body)."""
    m = _FRONT_MATTER_RE.match(text)
    if not m:
        return {}, text
    meta: Dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ':' in line:
            key, _, val = line.partition(':')
            meta[key.strip()] = val.strip().strip('"').strip("'")
    return meta, text[m.end():]


class SnippetLibrary:
    """Load snippets from a directory tree. Mirrors UnifiedPromptLibrary's pattern."""

    def __init__(self, library_dir: Optional[str] = None, log_callback=None):
        self.library_dir = Path(library_dir) if library_dir else None
        self.log = log_callback or (lambda _msg: None)
        self.snippets: List[Dict] = []  # [{label, body, category, path}]

        if self.library_dir:
            try:
                self.library_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                self.log(f"[SnippetLibrary] Cannot create {self.library_dir}: {e}")

    def load_all(self) -> int:
        """Walk the library directory and populate self.snippets. Returns count."""
        self.snippets = []
        if not self.library_dir or not self.library_dir.exists():
            return 0

        for md in sorted(self.library_dir.rglob("*.md")):
            if md.name.startswith('.'):
                continue
            try:
                raw = md.read_text(encoding='utf-8')
                meta, body = _split_front_matter(raw)
                rel = md.relative_to(self.library_dir)
                parts = list(rel.parts)
                # The top-level folder is the category; any folders BETWEEN
                # the category and the file (v1.10.350) are recorded so the
                # Clipboard Manager's Menu tree can render them as nested
                # sub-nodes. Users organise snippets simply by creating
                # subfolders on disk – same convention as the prompt library.
                category = parts[0] if len(parts) > 1 else ""
                subfolders = tuple(parts[1:-1])
                label = meta.get('name') or md.stem
                self.snippets.append({
                    'label': label,
                    'body': body.rstrip("\n"),
                    'category': category,
                    'subfolders': subfolders,
                    'path': md,
                })
            except Exception as e:
                self.log(f"[SnippetLibrary] Skip {md.name}: {e}")

        return len(self.snippets)

    def ensure_defaults(self, default_defs: List[Dict]) -> int:
        """Write any missing default snippet files. Idempotent. Returns files created.

        Before seeding, runs a one-shot migration that converts pre-v1.9.459
        default files (ASCII-safe filename + ``name:`` front-matter label,
        e.g. ``Maths symbols.md`` displayed as ``± × ÷ ≠ π ∞``) into the new
        flat layout where the filename *is* the displayed label. The
        migration preserves any body edits the user made to a default file
        and never touches snippets the user created themselves.
        """
        if not self.library_dir:
            return 0

        # Run legacy → new-default migration first so the existence check
        # below sees the new filenames already in place where appropriate.
        try:
            self._migrate_legacy_defaults(default_defs)
        except Exception as e:
            self.log(f"[SnippetLibrary] Legacy migration failed: {e}")

        created = 0
        for defn in default_defs:
            category = defn.get('category', '')
            folder = self.library_dir / category if category else self.library_dir
            try:
                folder.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                self.log(f"[SnippetLibrary] Cannot create folder {folder}: {e}")
                continue

            # Filename comes straight from the `filename` field – which is now
            # the displayed label too, since we no longer write front matter.
            raw_filename = defn.get('filename') or 'snippet'
            filename = self._safe_filename(raw_filename) + '.md'
            filepath = folder / filename

            if not filepath.exists():
                try:
                    self._write_default(filepath, defn)
                    created += 1
                except Exception as e:
                    self.log(f"[SnippetLibrary] Cannot write {filepath}: {e}")

        return created

    def _migrate_legacy_defaults(self, default_defs: List[Dict]) -> int:
        """One-shot rename of old-style default files to the new flat naming.

        Old file: ``<cat>/<legacy_filename>.md`` with ``name: "<glyphs>"`` in
        front matter and body containing the inserted text.
        New file: ``<cat>/<new_filename>.md`` (where new_filename is the
        glyph string), body only.

        For each (category, legacy_filename → new_filename) pair:
          * If old exists AND new does not: copy old body (front matter
            stripped) into new filename, then delete old file. Preserves
            any body edits the user made to the default.
          * If new already exists: leave both alone (user has likely
            already created or migrated it themselves).
          * If old doesn't exist: nothing to do.

        Returns count of migrated files.
        """
        migrated = 0
        for defn in default_defs:
            legacy_name = defn.get('legacy_filename')
            new_name = defn.get('filename')
            if not legacy_name or not new_name or legacy_name == new_name:
                continue
            category = defn.get('category', '')
            folder = self.library_dir / category if category else self.library_dir
            old_path = folder / (self._safe_filename(legacy_name) + '.md')
            new_path = folder / (self._safe_filename(new_name) + '.md')
            if not old_path.exists() or new_path.exists():
                continue
            try:
                raw = old_path.read_text(encoding='utf-8')
                _meta, body = _split_front_matter(raw)
                new_path.write_text(body.rstrip('\n') + '\n', encoding='utf-8')
                old_path.unlink()
                migrated += 1
                self.log(f"[SnippetLibrary] Migrated default: "
                         f"{old_path.name} → {new_path.name}")
            except Exception as e:
                self.log(f"[SnippetLibrary] Migration error "
                         f"{old_path} → {new_path}: {e}")
        return migrated

    @staticmethod
    def _safe_filename(name: str) -> str:
        """Strip characters that are invalid in Windows filenames."""
        invalid = '<>:"/\\|?*'
        cleaned = ''.join(c if c not in invalid else '_' for c in name).strip().strip('.')
        return cleaned or "snippet"

    @staticmethod
    def _write_default(filepath: Path, defn: Dict):
        """Write a single default snippet definition to a .md file.

        New format (v1.9.459+) is bare body – no front matter. The filename
        carries both the on-disk identity and the displayed label, so a
        single field handles both. Front-matter parsing remains in place
        (see :func:`_split_front_matter`) for backwards compatibility with
        user-authored files that still use ``name:`` to override the label.
        """
        body = defn.get('body', '')
        filepath.write_text(body.rstrip('\n') + '\n', encoding='utf-8')


# ---------------------------------------------------------------------------
# Default snippets
#
# Each entry has two fields the loader cares about:
#   `filename` – the on-disk .md file stem AND the display label shown in
#                the Sidekick menu. As of v1.9.459 these are the same so
#                you can see at a glance what an entry will insert and
#                rename it just by renaming the file.
#   `body`     – the actual text inserted at the cursor on click. Often a
#                superset of the filename (more glyphs available than the
#                six or so we show in the label).
#
# Plus one optional migration field:
#   `legacy_filename` – the previous filename used by the default before
#                       the v1.9.459 rename. The seeder uses this once
#                       per installation: if `legacy_filename` exists on
#                       disk and `filename` does not, the body is moved
#                       over to the new filename and the old file
#                       deleted. User-authored snippets are never
#                       touched – we only look for known shipped names.
#
# Users can edit, rename, or delete any of these freely; defaults are
# only seeded when missing, never overwritten.
#
# The "Personal Snippets" category ships with a single placeholder so
# the category appears in the menu with first-time guidance.
# ---------------------------------------------------------------------------

DEFAULT_SNIPPETS: List[Dict] = [
    # -- Special Characters --
    {"category": "Special Characters",
     "legacy_filename": "Misc symbols",
     "filename": "\u25A3 \u25A0 \u25EF \u25B6 \u25C6",
     "body": "\u25A3 \u25A0 \u25A1 \u25A2 \u25EF \u25B2 \u25B6 \u25BA \u25BC \u25C6 \u25E2 \u25E3 \u25E4 \u25E5"},
    {"category": "Special Characters",
     "legacy_filename": "Arrows",
     "filename": "\u2190 \u2192 \u2191 \u2193 \u21C4 \u2194",
     "body": "\u2190 \u2192 \u2191 \u2193 \u27EB \u2B07 \u2B06 \u21C4 \u2194"},
    {"category": "Special Characters",
     "legacy_filename": "Primes",
     "filename": "\u2032 \u2033 \u2034",
     "body": "\u2032 \u2033 \u2034 \u2057"},
    {"category": "Special Characters",
     "legacy_filename": "Dashes and quotes",
     "filename": "\u2013 \u2014 \u00AB \u00BB \u201C \u201D",
     "body": "\u2013 \u2014 \u00AB \u00BB \u2039 \u203A \u201C \u201D \u201E \u201A"},
    {"category": "Special Characters",
     "legacy_filename": "Currency",
     "filename": "\u20AC \u00A3 $ \u00A5 \u00A2",
     "body": "\u00A5 \u20AC $ \u00A2 \u00A3"},
    {"category": "Special Characters",
     "legacy_filename": "Legal symbols",
     "filename": "\u00A9 \u00AE \u2122 \u00B0 \u2030",
     "body": "\u00A9 \u00AE @ \u2122 \u00B0 \u2030"},
    {"category": "Special Characters",
     "legacy_filename": "Maths symbols",
     "filename": "\u00B1 \u00D7 \u00F7 \u2260 \u03C0 \u221E",
     "body": "\u00B1 \u00D7 ~ \u2248 \u00F7 \u2260 \u03C0 \u221E"},
    {"category": "Special Characters",
     "legacy_filename": "Bullets",
     "filename": "\u2022 \u25CF \u00B7 \u2026",
     "body": "\u2026 \u00B7 \u2022 \u25CF"},

    # -- Personal Snippets --
    # Placeholder so the category appears in the menu with guidance for
    # first-time users. Shipping an example here is intentional; it should
    # NOT contain anything personal or sensitive.
    {"category": "Personal Snippets",
     "filename": "Example snippet",
     "body": "This is a sample snippet. Edit this file or add new .md files "
             "under snippet_library/Personal Snippets/ to build your own "
             "library of reusable text."},
]
