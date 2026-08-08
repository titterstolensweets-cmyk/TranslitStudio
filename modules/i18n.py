"""
Supervertaler Workbench internationalisation (i18n) support.

Added in v1.10.207 as the MVP infrastructure for translating the UI into
other languages. The MVP scope is deliberately narrow: menu bar, toolbar,
Settings tabs, and primary dialog buttons – enough to give a translated
build the "speaks your language" feel without committing to a multi-week
pass over every string in the codebase. Body text, log messages, and
non-primary tooltips remain in English for v1.

# Why XLIFF (and not .ts or .po)

Supervertaler's audience is professional translators. Every CAT tool in
regular use in the industry – Trados, memoQ, Phrase, OmegaT, Wordfast,
and Workbench itself – natively supports **XLIFF 1.2** as a source-file
format. A translator can take ``supervertaler_zh_CN.xlf``, open it in
their normal CAT environment with their normal TMs and termbases, work
through the strings, and send it back. No tool-specific dance.

Two formats we considered and rejected:

* **Qt Linguist ``.ts``** – the conventional PyQt choice, but Qt
  Linguist is essentially unknown outside Qt circles. Forcing a Trados
  user to install and learn it just for this project is friction we
  don't need.
* **GNU gettext ``.po``** – broadly supported by CAT tools and the
  default for Linux/Django/WordPress projects, but ergonomics vary
  across tools and the format is less familiar to translators than
  XLIFF. May ship as an additional Workbench *import* format in a
  separate feature (see task spawned alongside v1.10.208).

Qt's ``self.tr(...)`` machinery is unaffected by the file format choice
– the runtime contract is "a ``QTranslator`` that overrides ``translate()``
correctly". This module ships ``XliffTranslator``, a thin subclass that
reads XLIFF 1.2 directly. Strings already wrapped in ``self.tr()`` keep
working with no source changes.

# Workflow

Generate / refresh the source template (English-only XLIFF, all targets
marked ``needs-translation``)::

    python tools/extract_strings.py

Translators open the locale file (e.g. ``translations/supervertaler_zh_CN.xlf``)
in their CAT tool of choice, translate, save back as XLIFF, and PR the
file. On the next Workbench launch in that locale, translations load
automatically.

# Locale handling

Locales follow Qt's ``language[_TERRITORY]`` form for *file names* and
*settings keys*, with hyphens used inside the XLIFF metadata
(``target-language="zh-CN"``) per the XLIFF 1.2 spec:

* ``en``       – English (default, no translation file needed)
* ``zh_CN``    – Simplified Chinese
* ``zh_TW``    – Traditional Chinese
* ``pl``       – Polish

The active locale is read from the General settings dict; ``"system"``
falls back to ``QLocale.system().name()``. A missing ``.xlf`` file is
not an error – ``self.tr()`` just returns the source string (English).

A restart is required after changing the language. This is a deliberate
MVP simplification: live re-translation requires every widget to handle
``changeEvent(QEvent.LanguageChange)`` correctly, which is a non-trivial
audit on a 60k-line hand-coded UI. The Settings panel surfaces a notice
when the language is changed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET

from PyQt6.QtCore import QLocale, QTranslator


# XLIFF 1.2 namespace, used by every <element> in a valid file.
_XLIFF_NS = "urn:oasis:names:tc:xliff:document:1.2"


# Locales we explicitly know about. Used by the Settings dropdown so
# users see human-readable names rather than ISO codes. The MVP ships
# infrastructure only; the ``.xlf`` files for any locale beyond ``en``
# are added as translators contribute them (issue #178: Chinese,
# #190: Polish).
SUPPORTED_LOCALES: list[tuple[str, str]] = [
    ("en",    "English"),
    ("zh_CN", "中文 (简体) — Simplified Chinese"),
    ("zh_TW", "中文 (繁體) — Traditional Chinese"),
    ("pl",    "Polski — Polish"),
    ("de",    "Deutsch — German"),
    ("fr",    "Français — French"),
    ("es",    "Español — Spanish"),
    ("nl",    "Nederlands — Dutch"),
    ("ja",    "日本語 — Japanese"),
    ("ko",    "한국어 — Korean"),
    ("ru",    "Русский — Russian"),
]

# Extension used for translation files in this project.  XLIFF 1.2 ships
# as either ``.xlf`` or ``.xliff``; ``.xlf`` is the OASIS-recommended
# extension and what most CAT tools emit on save.
TRANSLATION_FILE_SUFFIX = ".xlf"


def translations_dir() -> Path:
    """Resolve the translations folder regardless of how Workbench is invoked.

    Looks in (in order):
        1. ``<repo_root>/translations`` for source-tree runs
        2. ``<exe_dir>/translations`` for PyInstaller ONEDIR bundles where
           the build script copies the folder next to the .exe (the most
           end-user-discoverable location – translators can drop a new
           locale's .xlf into this folder without re-running PyInstaller)
        3. ``<_MEIPASS>/translations`` for PyInstaller-bundled data
           (the spec file's ``datas`` parameter puts files here)

    Returns the first folder that exists. Falls back to the source-tree
    path even when the folder is missing so callers can ``mkdir`` against
    it without further branching.
    """
    # Source-tree path: this file is at modules/i18n.py, so parent.parent is repo root
    repo_root = Path(__file__).resolve().parent.parent
    candidate = repo_root / "translations"
    if candidate.exists():
        return candidate

    import sys

    # PyInstaller frozen-exe path
    if getattr(sys, "frozen", False):
        # 1. Next to the .exe (the build script puts a copy here for
        # end-user visibility – they can drop new .xlf files in
        # without re-extracting the bundled copies).
        exe_dir = Path(sys.executable).resolve().parent
        bundled = exe_dir / "translations"
        if bundled.exists():
            return bundled

        # 2. Inside _MEIPASS (where the spec file's ``datas`` lands).
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            internal = Path(meipass) / "translations"
            if internal.exists():
                return internal

    # Default – may not exist yet
    return candidate


def locale_to_xliff_lang(locale_code: str) -> str:
    """Convert a Qt/Python locale code to the XLIFF lang format.

    Qt and Python use underscores (``zh_CN``); XLIFF uses hyphens
    (``zh-CN``). Settings files and translation file names keep the
    underscore form for consistency with the rest of Workbench.
    """
    return locale_code.replace("_", "-")


class XliffTranslator(QTranslator):
    """A ``QTranslator`` that reads XLIFF 1.2 directly.

    Subclasses ``QTranslator`` so Qt's standard ``self.tr(...)`` plumbing
    routes lookups through ``translate()`` exactly as it would for a
    binary ``.qm`` file – no special API the rest of the codebase needs
    to know about.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        # Keyed by (context, source, disambiguation_or_None).
        # ``disambiguation`` is the optional second argument to ``tr()``
        # used when the same English source needs different translations
        # in different places.
        self._entries: dict[tuple[str, str, Optional[str]], str] = {}
        self._loaded_locale: Optional[str] = None
        self._loaded_path: Optional[Path] = None

    # ─── Loading ──────────────────────────────────────────────────────

    def load_xliff_file(self, xlf_path: Path) -> bool:
        """Load a single ``.xlf`` file. Returns True on success.

        Skips ``<target>`` elements whose ``state`` is
        ``needs-translation`` / ``needs-review-translation`` /
        ``needs-adaptation`` / ``new`` so partially-translated strings
        show the English source rather than a half-translated mess.
        Also skips entries where the target element is missing or empty.
        """
        self._entries.clear()
        try:
            tree = ET.parse(str(xlf_path))
        except (ET.ParseError, OSError):
            return False

        root = tree.getroot()
        # Iterate every <trans-unit> regardless of how many <file> elements
        # exist. The template emits a single <file> but translators may end
        # up with multi-file XLIFF when their CAT tool re-saves.
        for trans_unit in root.iter(f"{{{_XLIFF_NS}}}trans-unit"):
            src_el = trans_unit.find(f"{{{_XLIFF_NS}}}source")
            tgt_el = trans_unit.find(f"{{{_XLIFF_NS}}}target")

            if src_el is None or src_el.text is None:
                continue
            if tgt_el is None or tgt_el.text is None:
                continue

            # Skip targets marked as needing further work. "translated",
            # "signed-off", "final", or no state attribute at all all count
            # as usable.
            state = (tgt_el.get("state") or "").strip().lower()
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

            source_text = src_el.text
            target_text = tgt_el.text.strip()
            if not target_text:
                continue

            # Pull Qt context (class name) and disambiguation from the
            # context-group, falling back to "" / None.
            context_name = ""
            disambiguation: Optional[str] = None
            for ctx_group in trans_unit.findall(f"{{{_XLIFF_NS}}}context-group"):
                for ctx in ctx_group.findall(f"{{{_XLIFF_NS}}}context"):
                    ctx_type = (ctx.get("context-type") or "").strip()
                    if ctx_type == "x-qt-context" and ctx.text:
                        context_name = ctx.text.strip()

            # Disambiguation comes from a developer note with the prefix
            # "Disambiguation: " (matching what extract_strings.py emits).
            for note in trans_unit.findall(f"{{{_XLIFF_NS}}}note"):
                txt = (note.text or "").strip()
                if txt.startswith("Disambiguation: "):
                    disambiguation = txt[len("Disambiguation: "):].strip() or None
                    break

            self._entries[(context_name, source_text, disambiguation)] = target_text

        self._loaded_path = xlf_path
        return True

    def load_locale(self, locale_code: str) -> bool:
        """Load the ``.xlf`` file matching ``locale_code`` from translations/.

        Returns True if a translation file was found and loaded with at
        least one finished entry. False on missing file, parse error, or
        zero finished entries – the caller can then skip installing this
        translator entirely so Qt's ``tr()`` short-circuits to the source.
        """
        if not locale_code or locale_code.lower() == "en":
            # English is the source language – no translation file needed.
            self._loaded_locale = "en"
            return False

        xlf_path = translations_dir() / f"supervertaler_{locale_code}{TRANSLATION_FILE_SUFFIX}"
        if not xlf_path.exists():
            return False

        if not self.load_xliff_file(xlf_path):
            return False

        if not self._entries:
            return False

        self._loaded_locale = locale_code
        return True

    # ─── QTranslator overrides ────────────────────────────────────────

    def translate(  # type: ignore[override]
        self,
        context: str,
        source_text: str,
        disambiguation: Optional[str] = None,
        n: int = -1,
    ) -> str:
        """Look up a translation. Falls back to source text when not found.

        Qt's C++ contract says returning an empty QString causes
        ``QCoreApplication::translate()`` to try the next installed
        translator and ultimately fall back to the source text. **In
        PyQt6 that contract is broken**: returning Python ``""`` produces
        a QString that is *empty but not null*, and Qt's actual fallback
        check uses ``isNull()`` (or has changed behaviour between
        versions). Either way, an empirical test (v1.10.209) showed
        every untranslated string rendering as a literally empty label
        in the UI – which left the Settings tab bar half-populated when
        only some tabs had translations.

        The simplest robust fix is to fall back to the source text
        explicitly inside *our* translator instead of relying on Qt's
        internal fallback. This guarantees the displayed string is
        always non-empty regardless of Qt version / PyQt6 marshalling
        quirks. The downside (we shadow Qt's "try the next translator"
        chain) doesn't matter for our single-translator setup.
        """
        # Most specific first: (ctx, src, disambig)
        if disambiguation:
            value = self._entries.get((context, source_text, disambiguation))
            if value:
                return value
        # Then context-only
        value = self._entries.get((context, source_text, None))
        if value:
            return value
        # Then any-context fallback (handles strings translated under a
        # different context than the caller's class – e.g. menu items that
        # appear in helper functions). Cheap because the dict scan is on
        # at most a few hundred entries for the MVP.
        for (ctx, src, disambig), trans in self._entries.items():
            if src == source_text and disambig == disambiguation:
                return trans
        # Not found – return the source text so Qt displays it verbatim
        # rather than rendering an empty label.
        return source_text

    def isEmpty(self) -> bool:  # type: ignore[override]
        return not self._entries

    # ─── Introspection ─────────────────────────────────────────────────

    @property
    def loaded_locale(self) -> Optional[str]:
        """The locale code that was successfully loaded, or None."""
        return self._loaded_locale

    @property
    def loaded_path(self) -> Optional[Path]:
        """The .xlf file path that was loaded, or None."""
        return self._loaded_path

    def entry_count(self) -> int:
        """Number of finished translation entries currently loaded."""
        return len(self._entries)


# ─── Module-level helpers ──────────────────────────────────────────────


def resolve_locale(setting_value: Optional[str]) -> str:
    """Resolve the user-configured locale setting to an active locale code.

    ``setting_value`` comes from the General settings dict (or env override).
    Special values:
        - ``None`` or ``""``  → fall back to ``"system"``
        - ``"system"``        → use ``QLocale.system().name()``
        - ``"en"``            → English (no translation file loaded)
        - any other string    → used as-is (e.g. ``"zh_CN"``)
    """
    if not setting_value or setting_value.lower() == "system":
        try:
            return QLocale.system().name() or "en"
        except Exception:
            return "en"
    return setting_value


def install_translator_for_locale(
    app, locale_code: str
) -> Optional[XliffTranslator]:
    """Convenience: build an XliffTranslator, try to load, install on app if found.

    Returns the installed translator on success, None when the requested
    locale has no .xlf file or the file is empty/broken. Caller can log the
    outcome and proceed – English is the safe fallback in every branch.
    """
    if not locale_code or locale_code.lower() == "en":
        return None

    translator = XliffTranslator()
    if not translator.load_locale(locale_code):
        return None

    app.installTranslator(translator)
    return translator
