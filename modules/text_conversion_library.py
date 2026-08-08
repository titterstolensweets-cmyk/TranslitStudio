#!/usr/bin/env python3
"""
=============================================================================
MODULE: Text Conversion Library
=============================================================================
File-backed text-conversion storage for the Workbench Clipboard Manager's
"Text Conversions" menu.

Each conversion is a single ``.md`` file under
``<user_data>/text_conversion_library/``.  The YAML frontmatter declares
what the conversion does; the optional body holds free-form human notes
(ignored by the loader).

Folder structure inside the library is purely organisational — the loader
walks ``rglob("*.md")`` and surfaces every conversion in a flat list in the
Menu, ordered by category then label.  Move files between folders to
re-organise without breaking anything.

Supported ``type`` values (one per file):

* ``case``           — change case of the whole clipboard text.
                      Requires ``mode``: one of ``upper``, ``lower``,
                      ``title``, ``sentence``, ``swap``, ``camel``,
                      ``snake``, ``kebab``.
* ``wrap``           — glue ``prefix`` and ``suffix`` around the text.
* ``regex_replace``  — find/replace.  Required: ``find``, ``replace``.
                      Optional: ``regex`` (default ``true``),
                      ``case_sensitive`` (default ``true``).
* ``strip_chars``    — remove every occurrence of any character listed
                      in ``chars``.

Optional metadata (every type):

* ``label``        — display label in the Menu.  Defaults to the filename
                    stem if missing.  Use this when the label needs
                    characters that aren't valid in filenames (``: " / \\``
                    etc.).
* ``category``     — sub-grouping shown in the Menu.  Defaults to the
                    name of the .md file's parent folder.  Set to an
                    empty string to surface the conversion at the top
                    level of the Text Conversions list.
* ``enabled``      — defaults to ``true``.  Set to ``false`` to hide the
                    conversion without deleting the file.

Default conversions are defined in :data:`DEFAULT_CONVERSIONS` and written
to disk on first launch if missing.  Users can edit, rename, delete, or
add their own ``.md`` files freely — defaults are re-seeded only when
absent, never overwriting user edits.

This module mirrors :mod:`snippet_library`'s structure deliberately so
the two libraries behave consistently and the Clipboard Manager's
🔄 Refresh button reloads both with the same pattern.

Author: Michael Beijer (Claude-assisted)
=============================================================================
"""

import re
from pathlib import Path
from typing import Optional, List, Dict

import yaml


_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)

_VALID_TYPES = ("case", "wrap", "regex_replace", "strip_chars")
_VALID_CASE_MODES = ("upper", "lower", "title", "sentence", "swap",
                     "camel", "snake", "kebab")

# Word-splitter used by camelCase / snake_case / kebab-case helpers.
# Splits on whitespace, underscores, and hyphens.
_WORD_SPLIT_RE = re.compile(r"[\s_\-]+")


# ── Case-conversion helpers (used by TextConversion._apply_case) ──────────

def _to_sentence_case(text: str) -> str:
    """Lowercase the string, then capitalise the first letter of each sentence.

    Cheap heuristic — mirrors the original ``_to_sentence_case`` from
    ``clipboard_manager_widget.py`` (now removed in favour of this helper).
    """
    if not text:
        return text
    lowered = text.lower()
    out = []
    capitalise_next = True
    for ch in lowered:
        if capitalise_next and ch.isalpha():
            out.append(ch.upper())
            capitalise_next = False
        else:
            out.append(ch)
            if ch in ".!?":
                capitalise_next = True
    return "".join(out)


def _to_camel(text: str) -> str:
    """``hello world foo`` → ``helloWorldFoo``."""
    words = [w for w in _WORD_SPLIT_RE.split(text) if w]
    if not words:
        return text
    head = words[0].lower()
    tail = "".join(w.capitalize() for w in words[1:])
    return head + tail


def _to_snake(text: str) -> str:
    """``Hello World Foo`` → ``hello_world_foo``."""
    words = [w for w in _WORD_SPLIT_RE.split(text) if w]
    return "_".join(w.lower() for w in words)


def _to_kebab(text: str) -> str:
    """``Hello World Foo`` → ``hello-world-foo``."""
    words = [w for w in _WORD_SPLIT_RE.split(text) if w]
    return "-".join(w.lower() for w in words)


def _safe_filename(name: str) -> str:
    """Strip characters that are invalid in Windows filenames."""
    invalid = '<>:"/\\|?*'
    cleaned = "".join(c if c not in invalid else "_" for c in name).strip().strip(".")
    return cleaned or "conversion"


# ── Public API ────────────────────────────────────────────────────────────

class TextConversion:
    """A single file-backed text transformation.

    Holds the parsed YAML metadata plus the apply() entry point that
    actually performs the transformation when the user clicks the
    corresponding Menu item.  Failures during apply() are swallowed — a
    broken conversion file returns the input text unchanged so the
    clipboard flow doesn't break; the error is logged by the loader.
    """

    __slots__ = ("path", "label", "category", "type", "meta", "enabled")

    def __init__(self, path: Path, label: str, category: str,
                 type_: str, meta: Dict, enabled: bool = True):
        self.path = path
        self.label = label
        self.category = category
        self.type = type_
        self.meta = meta
        self.enabled = enabled

    def apply(self, text: str) -> str:
        """Run the conversion against ``text``.  Returns transformed text.

        Returns the input unchanged on any failure — the Menu item should
        feel "safe to click on garbage" even if the underlying YAML has
        a typo.  Errors are not raised; the user fixes the file and hits
        🔄 Refresh.
        """
        if text is None:
            return text
        try:
            if self.type == "case":
                return self._apply_case(text)
            if self.type == "wrap":
                prefix = str(self.meta.get("prefix", "") or "")
                suffix = str(self.meta.get("suffix", "") or "")
                return f"{prefix}{text}{suffix}"
            if self.type == "regex_replace":
                return self._apply_replace(text)
            if self.type == "strip_chars":
                chars = str(self.meta.get("chars", "") or "")
                if not chars:
                    return text
                char_set = set(chars)
                return "".join(c for c in text if c not in char_set)
        except Exception:
            return text
        return text

    def _apply_case(self, text: str) -> str:
        mode = str(self.meta.get("mode", "upper")).lower()
        if mode == "upper":
            return text.upper()
        if mode == "lower":
            return text.lower()
        if mode == "title":
            return text.title()
        if mode == "sentence":
            return _to_sentence_case(text)
        if mode == "swap":
            return text.swapcase()
        if mode == "camel":
            return _to_camel(text)
        if mode == "snake":
            return _to_snake(text)
        if mode == "kebab":
            return _to_kebab(text)
        return text

    def _apply_replace(self, text: str) -> str:
        find = self.meta.get("find", "")
        replace = self.meta.get("replace", "")
        # Booleans may arrive as Python bools (PyYAML safe_load) or as
        # strings if the file was hand-edited oddly; coerce.
        use_regex = self.meta.get("regex", True)
        if isinstance(use_regex, str):
            use_regex = use_regex.strip().lower() in ("true", "1", "yes")
        case_sensitive = self.meta.get("case_sensitive", True)
        if isinstance(case_sensitive, str):
            case_sensitive = case_sensitive.strip().lower() in ("true", "1", "yes")

        if find is None or find == "":
            return text

        find_str = str(find)
        replace_str = str(replace) if replace is not None else ""

        if use_regex:
            flags = 0 if case_sensitive else re.IGNORECASE
            return re.sub(find_str, replace_str, text, flags=flags)
        if case_sensitive:
            return text.replace(find_str, replace_str)
        # Case-insensitive literal replace: route through regex with re.escape
        return re.sub(re.escape(find_str), replace_str, text, flags=re.IGNORECASE)


class TextConversionLibrary:
    """Load text-conversion definitions from a directory of ``.md`` files.

    Mirrors :class:`snippet_library.SnippetLibrary`'s pattern: file-backed,
    refreshable, with a single ``ensure_defaults`` pass that seeds shipped
    defaults on first launch without ever overwriting user edits.
    """

    def __init__(self, library_dir: Optional[str] = None, log_callback=None):
        self.library_dir = Path(library_dir) if library_dir else None
        self.log = log_callback or (lambda _msg: None)
        self.conversions: List[TextConversion] = []

        if self.library_dir:
            try:
                self.library_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                self.log(f"[TextConversionLibrary] Cannot create {self.library_dir}: {e}")

    def load_all(self) -> int:
        """Walk the library directory and populate ``self.conversions``.

        Returns the count of conversions loaded.  Files with no YAML
        frontmatter or with an invalid / missing ``type`` field are
        skipped and logged but do not raise.
        """
        self.conversions = []
        if not self.library_dir or not self.library_dir.exists():
            return 0

        for md in sorted(self.library_dir.rglob("*.md")):
            if md.name.startswith("."):
                continue
            try:
                raw = md.read_text(encoding="utf-8")
                meta = self._parse_front_matter(raw)
                if not meta:
                    self.log(f"[TextConversionLibrary] Skip {md.name}: no YAML frontmatter")
                    continue
                type_ = str(meta.get("type", "")).strip().lower()
                if type_ not in _VALID_TYPES:
                    self.log(
                        f"[TextConversionLibrary] Skip {md.name}: "
                        f"invalid or missing `type` (got {type_!r}, "
                        f"expected one of {_VALID_TYPES})"
                    )
                    continue
                rel = md.relative_to(self.library_dir)
                parts = list(rel.parts)
                folder_category = parts[0] if len(parts) > 1 else ""
                # YAML category overrides folder; explicit empty string
                # ("") means "top-level, no sub-grouping".
                cat_val = meta.get("category", folder_category)
                category = str(cat_val) if cat_val is not None else folder_category
                label = str(meta.get("label") or md.stem)
                enabled = bool(meta.get("enabled", True))
                self.conversions.append(TextConversion(
                    path=md, label=label, category=category,
                    type_=type_, meta=meta, enabled=enabled,
                ))
            except Exception as e:
                self.log(f"[TextConversionLibrary] Skip {md.name}: {e}")

        return len(self.conversions)

    @staticmethod
    def _parse_front_matter(text: str) -> Dict:
        """Extract the YAML frontmatter from a markdown file.

        Returns the parsed mapping or ``{}`` if no frontmatter is present
        or the YAML is invalid.  Uses :func:`yaml.safe_load` so Unicode
        escapes inside double-quoted strings (``"\\u00AD"``) are
        processed correctly — important for ``strip_chars`` and
        ``regex_replace`` entries targeting invisible characters.
        """
        m = _FRONT_MATTER_RE.match(text)
        if not m:
            return {}
        try:
            loaded = yaml.safe_load(m.group(1))
        except yaml.YAMLError:
            return {}
        return loaded if isinstance(loaded, dict) else {}

    def ensure_defaults(self, default_defs: List[Dict]) -> int:
        """Write any missing default conversion files.  Idempotent.

        Returns the count of files created.  Existing files are never
        touched — once a default is on disk the user owns it, even if a
        later release ships an updated version.  Recovery from "I broke
        the file": delete it, restart Workbench, and the default is
        re-seeded.
        """
        if not self.library_dir:
            return 0

        created = 0
        for defn in default_defs:
            category = defn.get("category", "")
            folder = self.library_dir / category if category else self.library_dir
            try:
                folder.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                self.log(f"[TextConversionLibrary] Cannot create folder {folder}: {e}")
                continue

            raw_filename = defn.get("filename") or "conversion"
            filename = _safe_filename(raw_filename) + ".md"
            filepath = folder / filename

            if not filepath.exists():
                try:
                    self._write_default(filepath, defn)
                    created += 1
                except Exception as e:
                    self.log(f"[TextConversionLibrary] Cannot write {filepath}: {e}")

        return created

    @staticmethod
    def _write_default(filepath: Path, defn: Dict):
        """Write a single default conversion to a ``.md`` file with YAML frontmatter.

        Builds the YAML block from the ``defn`` dict, omitting the
        loader-only keys (``category`` since it's folder-derived, and
        ``filename`` since the filename is the file's identity).  An
        optional ``notes`` field becomes the markdown body after the
        closing ``---`` so users get an in-file explanation of what the
        default does.
        """
        yaml_data = {
            k: v for k, v in defn.items()
            if k not in ("category", "filename", "notes")
        }
        yaml_text = yaml.safe_dump(
            yaml_data, allow_unicode=True, sort_keys=False,
            default_flow_style=False,
        )
        notes = defn.get("notes", "")
        body = f"---\n{yaml_text}---\n"
        if notes:
            body += f"\n{notes.rstrip()}\n"
        filepath.write_text(body, encoding="utf-8")


# ---------------------------------------------------------------------------
# Default conversions
#
# These are seeded on first launch so the Text Conversions menu has the
# same eleven entries that used to be hardcoded in the Clipboard Manager
# widget.  Users can edit, rename, delete, or extend any of these freely
# — defaults are only re-seeded when absent.
#
# The list below intentionally exercises every supported `type` so the
# files serve as worked examples for users who want to add their own.
# ---------------------------------------------------------------------------

DEFAULT_CONVERSIONS: List[Dict] = [
    # ─── Case ────────────────────────────────────────────────────────
    {"category": "Case", "filename": "Uppercase",
     "type": "case", "label": "Uppercase",
     "mode": "upper"},
    {"category": "Case", "filename": "Lowercase",
     "type": "case", "label": "Lowercase",
     "mode": "lower"},
    {"category": "Case", "filename": "Title Case",
     "type": "case", "label": "Title Case",
     "mode": "title"},
    {"category": "Case", "filename": "Sentence case",
     "type": "case", "label": "Sentence case",
     "mode": "sentence"},

    # ─── Wrap ────────────────────────────────────────────────────────
    {"category": "Wrap", "filename": "Single curly quotes",
     "type": "wrap",
     "label": "Single curly quotes: ‘Example’",
     "prefix": "‘", "suffix": "’"},
    {"category": "Wrap", "filename": "Double curly quotes",
     "type": "wrap",
     "label": "Double curly quotes: “Example”",
     "prefix": "“", "suffix": "”"},
    {"category": "Wrap", "filename": "Round brackets",
     "type": "wrap",
     "label": "Round brackets: (Example)",
     "prefix": "(", "suffix": ")"},
    {"category": "Wrap", "filename": "Square brackets",
     "type": "wrap",
     "label": "Square brackets: [Example]",
     "prefix": "[", "suffix": "]"},
    {"category": "Wrap", "filename": "HTML bold",
     "type": "wrap",
     "label": "Make <b>bold</b>",
     "prefix": "<b>", "suffix": "</b>"},

    # ─── Strip ───────────────────────────────────────────────────────
    {"category": "Strip", "filename": "Soft hyphens",
     "type": "strip_chars",
     "label": "Remove soft hyphens (U+00AD)",
     "chars": "­",
     "notes": "Strip the invisible soft-hyphen character (U+00AD) that "
              "Word and some PDFs sprinkle through running text.  "
              "Add more characters to `chars` to strip them in one pass, "
              "e.g. `chars: \"\\u00AD\\u00A0\\u2007\"` strips soft "
              "hyphens, non-breaking spaces, and figure spaces."},

    # ─── Replace ─────────────────────────────────────────────────────
    {"category": "Replace", "filename": "Double to single quotes",
     "type": "regex_replace",
     "label": "Double quotes → single quotes",
     "find": "\"", "replace": "'", "regex": False,
     "notes": "Literal (non-regex) replacement of ASCII double quotes "
              "with ASCII single quotes.  For smart-quote handling "
              "(“ ” ‘ ’) add separate conversion "
              "files using regex character classes."},
]
