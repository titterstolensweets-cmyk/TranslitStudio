"""
Merge an updated XLIFF template into existing locale files.

After ``tools/extract_strings.py`` regenerates
``translations/supervertaler_template.xlf`` with whatever strings were
recently added/removed in the source, this script propagates those
changes into every existing locale file (``supervertaler_zh_CN.xlf``,
``supervertaler_pl.xlf``, etc.) while **preserving completed
translations**.

# What "merge" means

For each locale file, the script:

1. Reads existing translations into a dict keyed by source text
2. Reads the new template (the new "canonical" structure)
3. Writes a new locale file based on the template, but for every entry
   whose source text matches an entry the translator already finished,
   copies the existing translation + ``state="translated"`` over
4. Entries with source text that has no matching translation stay as
   ``state="needs-translation"`` (these are the new strings the
   translator will see in their CAT tool)
5. Old entries whose source text is no longer in the template are
   dropped (the source string was removed or changed in the code)

# When source strings *change*

If a source string changes (typo fix, rewording), the match by source
text fails for the new entry and succeeds for nothing. The old
translation is dropped, the new entry shows up as
``needs-translation``. The translator will see it as a new string in
their CAT tool and translate it.

This is the right behaviour: a meaningful source change deserves a
translator review. CAT tools' fuzzy matching catches near-duplicates
in the translator's TM so they don't start from zero.

# Usage

::

    python tools/merge_template.py             # merges into every locale file
    python tools/merge_template.py zh_CN pl    # only the listed locales

The script never overwrites a translation that is already in the
locale file – it only fills in new entries from the template. To
forcibly reset a locale, delete the file and re-copy the template by
hand.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional


REPO_ROOT = Path(__file__).resolve().parent.parent
TRANS_DIR = REPO_ROOT / "translations"
TEMPLATE_PATH = TRANS_DIR / "supervertaler_template.xlf"
XLIFF_NS = "urn:oasis:names:tc:xliff:document:1.2"


def _ns(tag: str) -> str:
    return f"{{{XLIFF_NS}}}{tag}"


def _read_existing_translations(locale_path: Path) -> dict[str, tuple[str, str]]:
    """Return {source_text: (target_text, state)} for every entry whose
    target is non-empty and not in a "needs work" state."""

    out: dict[str, tuple[str, str]] = {}
    if not locale_path.exists():
        return out

    try:
        tree = ET.parse(str(locale_path))
    except ET.ParseError as e:
        print(f"  ! Parse error in {locale_path.name}: {e}", file=sys.stderr)
        return out

    for trans_unit in tree.getroot().iter(_ns("trans-unit")):
        src_el = trans_unit.find(_ns("source"))
        tgt_el = trans_unit.find(_ns("target"))
        if src_el is None or src_el.text is None:
            continue
        if tgt_el is None or not (tgt_el.text or "").strip():
            continue
        state = (tgt_el.get("state") or "").strip().lower()
        # Skip targets that aren't actually finished translations – we
        # don't want to pollute the merged file with half-baked content.
        if state in {
            "needs-translation",
            "needs-review-translation",
            "needs-adaptation",
            "needs-l10n",
            "needs-review-adaptation",
            "needs-review-l10n",
            "new",
        }:
            continue
        out[src_el.text] = (tgt_el.text, state or "translated")

    return out


def _detect_target_language(locale_path: Path, locale_code: str) -> str:
    """Return the existing ``target-language`` attribute on the <file>
    element, or derive one from the locale code (zh_CN → zh-CN).
    """
    if locale_path.exists():
        try:
            tree = ET.parse(str(locale_path))
            file_el = tree.getroot().find(_ns("file"))
            if file_el is not None:
                existing = file_el.get("target-language")
                if existing:
                    return existing
        except ET.ParseError:
            pass
    return locale_code.replace("_", "-")


def _locales_in_translations_dir() -> list[str]:
    """Return the locale codes of every existing per-locale .xlf file."""

    if not TRANS_DIR.exists():
        return []
    codes: list[str] = []
    for path in sorted(TRANS_DIR.glob("supervertaler_*.xlf")):
        if path.name == "supervertaler_template.xlf":
            continue
        codes.append(path.stem.replace("supervertaler_", ""))
    return codes


def merge_locale(locale_code: str) -> tuple[int, int]:
    """Merge the current template into the locale's .xlf file.

    Returns ``(preserved_count, new_count)`` – preserved = entries that
    had a translation we carried over, new = entries that came from
    the template with no existing match (translator will see these as
    needs-translation).
    """

    locale_path = TRANS_DIR / f"supervertaler_{locale_code}.xlf"
    target_lang = _detect_target_language(locale_path, locale_code)
    existing = _read_existing_translations(locale_path)

    # Parse the template and clone it, then update target-language + fill
    # in matched translations.
    if not TEMPLATE_PATH.exists():
        print(f"  ! Template not found at {TEMPLATE_PATH}", file=sys.stderr)
        return (0, 0)

    ET.register_namespace("", XLIFF_NS)
    tree = ET.parse(str(TEMPLATE_PATH))
    root = tree.getroot()

    file_el = root.find(_ns("file"))
    if file_el is None:
        print(f"  ! Template has no <file> element", file=sys.stderr)
        return (0, 0)
    file_el.set("target-language", target_lang)

    preserved = 0
    new_count = 0
    for trans_unit in root.iter(_ns("trans-unit")):
        src_el = trans_unit.find(_ns("source"))
        tgt_el = trans_unit.find(_ns("target"))
        if src_el is None or tgt_el is None or src_el.text is None:
            continue
        if src_el.text in existing:
            target_text, state = existing[src_el.text]
            tgt_el.text = target_text
            tgt_el.set("state", state)
            preserved += 1
        else:
            new_count += 1

    # Pretty-print via ET.indent (Python 3.9+) – minidom's toprettyxml had a
    # well-known bug where it doubled every whitespace text node, producing
    # files with two blank lines between every element. ET.indent is correct.
    # Tricky: the template we cloned via ET.parse retains the whitespace
    # text nodes from the source. Strip those before re-indenting, or
    # ET.indent's output ends up still over-spaced. We walk every element
    # and clear .text/.tail when it's pure whitespace.
    for el in root.iter():
        if el.text is not None and not el.text.strip():
            el.text = None
        if el.tail is not None and not el.tail.strip():
            el.tail = None

    ET.indent(root, space="  ")
    xliff_text = ET.tostring(root, encoding="unicode", xml_declaration=True)
    locale_path.write_text(xliff_text.rstrip() + "\n", encoding="utf-8", newline="\n")
    return (preserved, new_count)


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]

    if argv:
        locales = argv
    else:
        locales = _locales_in_translations_dir()

    if not locales:
        print("No locale files found in translations/", file=sys.stderr)
        return 1

    if not TEMPLATE_PATH.exists():
        print(
            f"Template not found at {TEMPLATE_PATH}.\n"
            "Run: python tools/extract_strings.py",
            file=sys.stderr,
        )
        return 1

    print(f"Merging {TEMPLATE_PATH.name} into {len(locales)} locale file(s):")
    total_preserved = 0
    total_new = 0
    for code in locales:
        preserved, new = merge_locale(code)
        total_preserved += preserved
        total_new += new
        print(
            f"  - {code}: {preserved} translation(s) preserved, "
            f"{new} entrie(s) needing translation"
        )

    print(
        f"\nSummary: {total_preserved} preserved, "
        f"{total_new} needing translation across {len(locales)} locale(s)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
