"""AutoCorrect engine — typographic auto-conversion while typing.

Added in v1.10.230. Tracking issue: #213.

Per-target-language rule profiles that fire after the user types a single
character in a target cell. Each rule sees the full text and the index of
the just-typed character, and returns either ``None`` (no fire) or a
:class:`Conversion` describing the edit to make. The engine itself is
stateless — Backspace-undo memory lives on the editor widget that invokes
the engine.

Currently shipped rules:

* :class:`SmartDoubleQuoteRule`  — straight ``"`` → language-correct
  typographic double quote, choosing open vs close shape based on what
  preceded the typed character.
* :class:`SmartSingleQuoteRule`  — same, for straight ``'``. Also handles
  apostrophe-in-word (don't → don’t).
* :class:`EllipsisRule`          — ``...`` → ``…``.
* :class:`EnDashRule`            — ``--`` → ``–``.
* :class:`EmDashRule`            — ``---`` → ``—`` (opt-in; off by
  default to match the Supervertaler house style which prefers en-dashes).
* :class:`FrenchNbspRule`        — insert a NARROW NO-BREAK SPACE (U+202F)
  before ``:`` ``;`` ``!`` ``?`` and adjacent to French guillemets when
  the target language is French. Auto-enabled for ``fr*`` targets.

Languages currently profiled (quote shapes):

    en, de, fr, es, it, nl, pl, ru, pt, cs, da, sv, no, fi, hu, ro, tr, uk

Any language not listed falls back to English shapes. (No-op for CJK and
Arabic for now — those have their own conventions and need dedicated rules
rather than a quote-shape table.)

Notes on tag-awareness: the engine skips its rules when the just-typed
character sits inside a tag marker — ``{1}``, ``[2}``, ``<seg>``, etc. —
so autocorrect can't corrupt tag boundaries. The check is a lightweight
backward scan; it doesn't need to share state with TagHighlighter.

Notes on dictation / paste / programmatic edits: this module is invoked
exclusively from the editor's ``keyPressEvent`` after a single printable
character is typed. ``setPlainText``, paste, and dictation that goes
through a different insertion path are not affected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, List, Set, Dict


# ---------------------------------------------------------------------------
# Per-language typographic shapes
# ---------------------------------------------------------------------------
#
# Each entry maps the abstract "open/close double/single quote" to the
# concrete Unicode character(s) for that language. For French we bake the
# narrow non-breaking space (U+202F) directly into the open/close pair so
# the smart-quote rule gets the spacing right on the first pass.
#
# Sources: Wikipedia "Quotation mark" article, cross-checked against
# typographic style guides for each language.

QUOTE_SHAPES: Dict[str, Dict[str, str]] = {
    # English: " ... " and ' ... '
    "en": {
        "double_open": "“", "double_close": "”",
        "single_open": "‘", "single_close": "’",
    },
    # German: „ ... " and ‚ ... '
    "de": {
        "double_open": "„", "double_close": "“",
        "single_open": "‚", "single_close": "‘",
    },
    # French: « ... » with narrow no-break spaces inside
    "fr": {
        "double_open": "« ", "double_close": " »",
        "single_open": "‹ ", "single_close": " ›",
    },
    # Spanish: « ... » (also accepts " ... ")
    "es": {
        "double_open": "«", "double_close": "»",
        "single_open": "‘", "single_close": "’",
    },
    # Italian: « ... »
    "it": {
        "double_open": "«", "double_close": "»",
        "single_open": "‘", "single_close": "’",
    },
    # Dutch: " ... " (modern) and ' ... '
    "nl": {
        "double_open": "“", "double_close": "”",
        "single_open": "‘", "single_close": "’",
    },
    # Polish: „ ... " and ‚ ... '
    "pl": {
        "double_open": "„", "double_close": "”",
        "single_open": "‘", "single_close": "’",
    },
    # Russian: « ... » (and German-style „...“ as inner quotes, not
    # auto-applied — too context-dependent)
    "ru": {
        "double_open": "«", "double_close": "»",
        "single_open": "‘", "single_close": "’",
    },
    # Portuguese (PT and BR both use " ... " in modern usage; PT also
    # accepts « ... » but " ... " is by far more common in software UI)
    "pt": {
        "double_open": "“", "double_close": "”",
        "single_open": "‘", "single_close": "’",
    },
    # Czech, Slovak, Slovenian, Croatian: „ ... "
    "cs": {
        "double_open": "„", "double_close": "“",
        "single_open": "‚", "single_close": "‘",
    },
    "sk": {
        "double_open": "„", "double_close": "“",
        "single_open": "‚", "single_close": "‘",
    },
    "sl": {
        "double_open": "„", "double_close": "“",
        "single_open": "‚", "single_close": "‘",
    },
    "hr": {
        "double_open": "„", "double_close": "“",
        "single_open": "‚", "single_close": "‘",
    },
    # Danish: " ... " (or » ... « for some publishers — using " ... " here
    # as the modern default)
    "da": {
        "double_open": "“", "double_close": "”",
        "single_open": "‘", "single_close": "’",
    },
    # Swedish: " ... " (closing-shape on both sides per typographic
    # tradition is also accepted, but " ... " is the modern norm)
    "sv": {
        "double_open": "”", "double_close": "”",
        "single_open": "’", "single_close": "’",
    },
    # Norwegian: « ... »
    "no": {
        "double_open": "«", "double_close": "»",
        "single_open": "‘", "single_close": "’",
    },
    "nb": {
        "double_open": "«", "double_close": "»",
        "single_open": "‘", "single_close": "’",
    },
    "nn": {
        "double_open": "«", "double_close": "»",
        "single_open": "‘", "single_close": "’",
    },
    # Finnish: " ... " (closing-shape on both sides)
    "fi": {
        "double_open": "”", "double_close": "”",
        "single_open": "’", "single_close": "’",
    },
    # Hungarian: „ ... "
    "hu": {
        "double_open": "„", "double_close": "”",
        "single_open": "»", "single_close": "«",
    },
    # Romanian: „ ... "
    "ro": {
        "double_open": "„", "double_close": "”",
        "single_open": "‘", "single_close": "’",
    },
    # Turkish: " ... "
    "tr": {
        "double_open": "“", "double_close": "”",
        "single_open": "‘", "single_close": "’",
    },
    # Ukrainian: « ... » (Russian-style)
    "uk": {
        "double_open": "«", "double_close": "»",
        "single_open": "‘", "single_close": "’",
    },
}


def normalize_lang(lang_code: Optional[str]) -> str:
    """Normalise a BCP-47-ish language code to its base form for quote lookup."""
    if not lang_code:
        return "en"
    base = lang_code.strip().lower().split("-")[0].split("_")[0]
    return base if base in QUOTE_SHAPES else "en"


# ---------------------------------------------------------------------------
# Tag-awareness
# ---------------------------------------------------------------------------

# Open and close characters of tag markers we want to protect:
#   {1}    memoQ-style numbered tag
#   [2}    memoQ alternative bracket
#   <seg>  HTML/XML tag
# We scan a short window backwards from the typed position; if we find an
# opener (one of "{[<") before any closer ("}]>"), we're inside a tag.
_TAG_OPENERS = frozenset("{[<")
_TAG_CLOSERS = frozenset("}]>")
_TAG_LOOKBACK_CHARS = 40


# Characters the engine should treat as whitespace for the purposes of
# smart-quote opening/closing context. Includes the on-screen markers
# Supervertaler uses when "Show Invisibles" is enabled — without these,
# typing  Hallo " with the space marker on would yield the closing quote
# shape because the engine would see `·` (middle dot) before the quote
# rather than a real space. The set mirrors the marker chars wired into
# the main editor's invisible-display machinery.
_WHITESPACE_LIKE = frozenset(
    " \t\n\r"        # real whitespace
    " "         # NBSP
    " "         # narrow NBSP (used inside French guillemets)
    "​"         # zero-width space
    "·"              # space marker (Show Invisibles)
    "→"              # tab marker
    "↵"              # newline marker
)


def _is_whitespace_like(ch: str) -> bool:
    """Whitespace, NBSPs, or any of Supervertaler's invisible-display markers."""
    if not ch:
        return False
    return ch in _WHITESPACE_LIKE or ch.isspace()


def is_inside_tag(text: str, pos: int) -> bool:
    """Return True if position ``pos`` in ``text`` lies inside a tag marker.

    Used to suppress autocorrect rules from firing inside ``{1}``, ``[2}``,
    ``<seg>`` and similar — autocorrecting inside a tag would corrupt it
    and break the round-trip back to the original file format.
    """
    if pos <= 0 or pos > len(text):
        return False
    start = max(0, pos - _TAG_LOOKBACK_CHARS)
    for i in range(pos - 1, start - 1, -1):
        ch = text[i]
        if ch in _TAG_OPENERS:
            return True
        if ch in _TAG_CLOSERS:
            return False
    return False


# ---------------------------------------------------------------------------
# Conversion data class
# ---------------------------------------------------------------------------

@dataclass
class Conversion:
    """One text-replacement operation produced by an autocorrect rule.

    Attributes:
        start: Index in the editor text where the replacement begins.
        end: Index where the replacement ends (exclusive).
        replacement: What to put in ``[start, end)``.
        new_cursor_pos: Where to leave the cursor after applying.
        undo_text: The text that originally occupied ``[start, end)`` — used
            by the Backspace-undo path to restore the user's typing.
        rule_id: Identifier of the rule that fired. Useful for telemetry
            and for the per-rule enable/disable settings.
    """
    start: int
    end: int
    replacement: str
    new_cursor_pos: int
    undo_text: str
    rule_id: str


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

class Rule:
    """Base class for an autocorrect rule.

    Subclasses override :meth:`applies_to_char` for a fast pre-filter and
    :meth:`apply` for the actual work. Rules are evaluated in order; the
    first one to return a non-None :class:`Conversion` wins.
    """

    rule_id: str = "base"
    # Whether this rule is on by default when the engine boots without any
    # per-rule overrides. Overrides per-language defaults from
    # default_enabled_rules() can still flip it.
    default_on: bool = True

    def applies_to_char(self, ch: str) -> bool:
        return False

    def apply(self, text: str, pos: int, lang: str) -> Optional[Conversion]:
        raise NotImplementedError


def _char_before_skipping_tags(text: str, pos: int) -> str:
    """Return the character immediately before ``pos``, skipping past any
    fully-closed tag marker (``{...}``, ``[...}``, ``<...>``). Empty string
    if ``pos`` is at the start.

    Tags should be functionally invisible to the smart-quote open/close
    decision so that ``{1}"foo"`` (a quoted string after an opening
    inline-formatting tag) gets the opening shape, not the closing shape.
    """
    i = pos - 1
    while i >= 0:
        ch = text[i]
        # If we're at a tag closer, scan back to its matching opener and
        # treat the whole tag as not-present.
        if ch in _TAG_CLOSERS:
            j = i - 1
            opener_idx = -1
            while j >= max(0, i - _TAG_LOOKBACK_CHARS):
                if text[j] in _TAG_OPENERS:
                    opener_idx = j
                    break
                if text[j] in _TAG_CLOSERS:
                    # Another closer first — give up; treat current char as content.
                    break
                j -= 1
            if opener_idx >= 0:
                i = opener_idx - 1
                continue
            # Unbalanced closer — treat as content.
            return ch
        return ch
    return ""


def _is_opening_context(text: str, pos: int) -> bool:
    """A typed quote at ``pos`` is in an opening context iff the character
    immediately before it (after skipping over any tag markers) is
    whitespace, an opening bracket, start-of-text, or another opening
    quote shape (so nested quotes nest correctly)."""
    prev = _char_before_skipping_tags(text, pos)
    if not prev:
        return True
    if _is_whitespace_like(prev):
        return True
    if prev in "([{":
        return True
    # Treat the explicit OPEN shapes as opening context so nested quotes
    # work; closing-side shapes count as content.
    if prev in "«‹„‚":
        return True
    return False


class SmartDoubleQuoteRule(Rule):
    rule_id = "smart_double_quote"

    def applies_to_char(self, ch: str) -> bool:
        return ch == '"'

    def apply(self, text: str, pos: int, lang: str) -> Optional[Conversion]:
        if pos < 0 or pos >= len(text) or text[pos] != '"':
            return None
        shapes = QUOTE_SHAPES.get(lang, QUOTE_SHAPES["en"])
        if _is_opening_context(text, pos):
            replacement = shapes["double_open"]
        else:
            replacement = shapes["double_close"]
        if replacement == '"':
            return None  # nothing to do
        return Conversion(
            start=pos,
            end=pos + 1,
            replacement=replacement,
            new_cursor_pos=pos + len(replacement),
            undo_text='"',
            rule_id=self.rule_id,
        )


class SmartSingleQuoteRule(Rule):
    rule_id = "smart_single_quote"

    def applies_to_char(self, ch: str) -> bool:
        return ch == "'"

    def apply(self, text: str, pos: int, lang: str) -> Optional[Conversion]:
        if pos < 0 or pos >= len(text) or text[pos] != "'":
            return None
        shapes = QUOTE_SHAPES.get(lang, QUOTE_SHAPES["en"])
        # Apostrophe-in-word case: letter ' letter → letter ’ letter
        # We can only see the LEFT side at the moment the apostrophe is
        # typed (the next letter hasn't been pressed yet). Heuristic: if
        # the previous character is a letter, treat it as an apostrophe,
        # otherwise as a single opening quote.
        prev = text[pos - 1] if pos > 0 else ""
        if prev and prev.isalpha():
            # Curly right-single-quote, which is the standard apostrophe
            replacement = "’"
        elif _is_opening_context(text, pos):
            replacement = shapes["single_open"]
        else:
            replacement = shapes["single_close"]
        if replacement == "'":
            return None
        return Conversion(
            start=pos,
            end=pos + 1,
            replacement=replacement,
            new_cursor_pos=pos + len(replacement),
            undo_text="'",
            rule_id=self.rule_id,
        )


class EllipsisRule(Rule):
    rule_id = "ellipsis"

    def applies_to_char(self, ch: str) -> bool:
        return ch == "."

    def apply(self, text: str, pos: int, lang: str) -> Optional[Conversion]:
        if pos < 2:
            return None
        # Require the just-typed char to be the THIRD dot in a run.
        if text[pos] != "." or text[pos - 1] != "." or text[pos - 2] != ".":
            return None
        # If we're already in a longer run of dots, don't fire (the user
        # is explicitly typing more than three).
        if pos > 2 and text[pos - 3] == ".":
            return None
        return Conversion(
            start=pos - 2,
            end=pos + 1,
            replacement="…",
            new_cursor_pos=pos - 2 + 1,
            undo_text="...",
            rule_id=self.rule_id,
        )


class EnDashRule(Rule):
    rule_id = "en_dash"

    def applies_to_char(self, ch: str) -> bool:
        # Fire on whatever comes AFTER the second hyphen — that disambiguates
        # double-hyphen (→ en-dash) from triple-hyphen (→ em-dash, handled
        # by EmDashRule). We pick space as the trigger.
        return ch == " "

    def apply(self, text: str, pos: int, lang: str) -> Optional[Conversion]:
        # Pattern: "word-- " → "word– " (note the trailing space is kept).
        # We want the two hyphens immediately before the typed space, and
        # NOT three (which would be the em-dash territory).
        if pos < 3:
            return None
        if text[pos] != " ":
            return None
        if not (text[pos - 1] == "-" and text[pos - 2] == "-"):
            return None
        if pos >= 3 and text[pos - 3] == "-":
            return None  # triple hyphen → leave for EmDashRule
        return Conversion(
            start=pos - 2,
            end=pos,  # replace just the "--", leave the typed space
            replacement="–",
            new_cursor_pos=pos - 2 + 1 + 1,  # after dash, after space
            undo_text="--",
            rule_id=self.rule_id,
        )


class EmDashRule(Rule):
    rule_id = "em_dash"
    default_on = False  # opt-in: house style is en-dashes (see CLAUDE.md)

    def applies_to_char(self, ch: str) -> bool:
        return ch == " "

    def apply(self, text: str, pos: int, lang: str) -> Optional[Conversion]:
        # Pattern: "word--- " → "word— "
        if pos < 4:
            return None
        if text[pos] != " ":
            return None
        if not (text[pos - 1] == "-" and text[pos - 2] == "-" and text[pos - 3] == "-"):
            return None
        if pos >= 4 and text[pos - 4] == "-":
            return None  # don't fire on quadruple+ hyphens
        return Conversion(
            start=pos - 3,
            end=pos,
            replacement="—",
            new_cursor_pos=pos - 3 + 1 + 1,
            undo_text="---",
            rule_id=self.rule_id,
        )


class FrenchNbspRule(Rule):
    """Insert a narrow no-break space (U+202F) before French two-part
    punctuation marks (``:`` ``;`` ``!`` ``?``) — modern French typographic
    convention. Only active when the target language is French."""

    rule_id = "french_nbsp"

    _FRENCH_NBSP_BEFORE = frozenset(":;!?")

    def applies_to_char(self, ch: str) -> bool:
        return ch in self._FRENCH_NBSP_BEFORE

    def apply(self, text: str, pos: int, lang: str) -> Optional[Conversion]:
        if lang != "fr":
            return None
        if pos < 1:
            return None
        ch = text[pos]
        if ch not in self._FRENCH_NBSP_BEFORE:
            return None
        prev = text[pos - 1]
        # Already correctly spaced? Skip. (Treat the space marker · as
        # equivalent to a real space so Show-Invisibles users still get
        # the upgrade to NBSP.)
        if prev in (" ", " ", " ", "·", ""):
            # If the previous char is a regular space (' '), upgrade it to
            # a narrow non-breaking space so line breaks don't separate the
            # mark from the word it follows.
            if prev == " " or prev == "·":
                return Conversion(
                    start=pos - 1,
                    end=pos,
                    replacement=" ",
                    new_cursor_pos=pos + 1,  # cursor stays after the typed mark
                    undo_text=" ",
                    rule_id=self.rule_id,
                )
            return None
        # Insert NBSP between prev and the typed mark.
        return Conversion(
            start=pos,
            end=pos,
            replacement=" ",
            new_cursor_pos=pos + 2,  # past the inserted NBSP and the typed mark
            undo_text="",
            rule_id=self.rule_id,
        )


# Default rule order. The double-quote rule MUST come before the single
# quote rule (they don't overlap, but lower indices win on equal applies).
_DEFAULT_RULES: List[Rule] = [
    SmartDoubleQuoteRule(),
    SmartSingleQuoteRule(),
    EllipsisRule(),
    EnDashRule(),
    EmDashRule(),
    FrenchNbspRule(),
]


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

@dataclass
class AutoCorrectSettings:
    """Snapshot of user-visible AutoCorrect settings.

    Carried around by the engine on each call so that the engine itself
    stays stateless and the editor can pass per-segment settings (e.g.
    a future "AutoCorrect off for this segment" toggle) without engine
    re-instantiation.
    """
    enabled: bool = True
    # Per-rule enable map. Missing keys → use rule's default_on.
    rule_overrides: Dict[str, bool] = field(default_factory=dict)

    def is_rule_enabled(self, rule: Rule) -> bool:
        if rule.rule_id in self.rule_overrides:
            return bool(self.rule_overrides[rule.rule_id])
        return rule.default_on


def default_settings_for_language(lang_code: Optional[str]) -> AutoCorrectSettings:
    """Reasonable default settings for a fresh user, parameterised by the
    target language. French gets the NBSP rule on by default; everyone
    gets em-dash off by default; everything else on."""
    overrides: Dict[str, bool] = {}
    norm = normalize_lang(lang_code)
    if norm == "fr":
        overrides["french_nbsp"] = True
    return AutoCorrectSettings(enabled=True, rule_overrides=overrides)


class AutoCorrectEngine:
    """Apply autocorrect rules after a single typed character.

    Stateless. Construct once on app startup; pass per-call settings and
    language. The editor widget is responsible for capturing the typed
    character and applying the returned :class:`Conversion`.
    """

    def __init__(self, rules: Optional[List[Rule]] = None):
        self.rules: List[Rule] = list(rules) if rules is not None else list(_DEFAULT_RULES)

    def all_rules(self) -> List[Rule]:
        return list(self.rules)

    def apply_after_typed_char(
        self,
        text: str,
        pos: int,
        lang_code: Optional[str],
        settings: AutoCorrectSettings,
    ) -> Optional[Conversion]:
        """Find the first rule that fires for the character at ``text[pos]``.

        Args:
            text: Full text of the editor at the moment after Qt inserted
                the typed character.
            pos: Index of the just-typed character. The cursor sits at
                ``pos + 1``.
            lang_code: Target language code (e.g. ``"de"``, ``"fr-CH"``).
                Used to look up quote shapes and gate locale-specific rules.
            settings: User-visible settings snapshot.

        Returns:
            A :class:`Conversion` describing what to replace, or ``None``
            if no rule fires.
        """
        if not settings.enabled:
            return None
        if pos < 0 or pos >= len(text):
            return None
        # Cheap early-out: if the typed character is inside a tag marker,
        # don't autocorrect — that would break the round-trip.
        if is_inside_tag(text, pos):
            return None
        lang = normalize_lang(lang_code)
        ch = text[pos]
        for rule in self.rules:
            if not settings.is_rule_enabled(rule):
                continue
            if not rule.applies_to_char(ch):
                continue
            conversion = rule.apply(text, pos, lang)
            if conversion is not None:
                return conversion
        return None
