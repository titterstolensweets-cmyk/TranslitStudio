"""
Voice dictation vocabulary biasing + replacement table.

Solves the "Whisper mistranscribes brand and technical names" problem
through two complementary techniques:

1. **Initial-prompt biasing**: Whisper accepts a short text "prompt"
   that biases the decoder's language model toward the vocabulary it
   contains. Words that appear in the prompt are more likely to be
   transcribed when they're spoken. Both faster-whisper
   (``transcribe(initial_prompt=...)``) and the OpenAI API
   (``transcriptions.create(prompt=...)``) support this.

2. **Post-transcription replacements**: a simple table of
   ``heard → meant`` pairs applied as a case-insensitive whole-word
   regex pass after Whisper returns. Catches whatever the biasing
   didn't.

The two techniques are stacked: biasing reduces the number of
mistakes that happen in the first place, replacements catch the
stubborn cases.

Settings shape (stored in user_data settings JSON under the
``voice_vocabulary`` key):

    {
        "voice_vocabulary": {
            "custom_terms": ["MyClient", "TechWord", ...],
            "replacements": [
                {"heard": "supervertile", "meant": "Supervertaler"},
                ...
            ],
            "use_termbase": true
        }
    }

The defaults live here as ``DEFAULT_VOCABULARY`` and
``DEFAULT_REPLACEMENTS`` and are *always* applied – the user-supplied
``custom_terms`` and ``replacements`` are appended to (not replacing)
the defaults.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional


# ---------- Built-in defaults --------------------------------------

# Words Whisper consistently misses if you say them out loud. These
# always go into the initial_prompt regardless of user settings, so
# even a fresh install gets "Supervertaler" right out of the box.
DEFAULT_VOCABULARY: List[str] = [
    # Product names
    "Supervertaler",
    "Supervertaler Workbench",
    "Supervertaler for Trados",
    "Workbench",
    "TermLens",
    "SuperLookup",
    "QuickTrans",
    "QuickLauncher",
    "AutoPrompt",
    "SuperMemory",
    # CAT tools the user works with daily
    "Trados Studio",
    "memoQ",
    "CafeTran",
    "Phrase",
    "Memsource",
    # AI providers / models
    "OpenAI",
    "ChatGPT",
    "Claude",
    "Anthropic",
    "Gemini",
    "Mistral",
    "DeepSeek",
    "Ollama",
    "GPT-4",
    "GPT-4o",
    # Tech terms commonly mistranscribed
    "TMX",
    "SDLTM",
    "MultiTerm",
    "Markdown",
    "XLIFF",
    "Okapi",
]

# Common Whisper mistranscriptions of Supervertaler-ecosystem terms.
# Map is case-insensitive, applied as a whole-word regex.
DEFAULT_REPLACEMENTS: Dict[str, str] = {
    # The bug report that started this feature
    "supervertile": "Supervertaler",
    "supervertaller": "Supervertaler",
    "supervirtual": "Supervertaler",
    "super vertaler": "Supervertaler",
    "super vertile": "Supervertaler",
    "super virtual": "Supervertaler",
    # Common compound mishears
    "work bench": "Workbench",
    "quick trans": "QuickTrans",
    "quick launcher": "QuickLauncher",
    "term lens": "TermLens",
    "super lookup": "SuperLookup",
    "super memory": "SuperMemory",
    "auto prompt": "AutoPrompt",
    # CAT-tool mishears
    "memo q": "memoQ",
    "memo cue": "memoQ",
    "cafe tran": "CafeTran",
    "trade os": "Trados",
    # AI providers
    "claude.ai": "Claude",
    "chat gpt": "ChatGPT",
    "gpt 4": "GPT-4",
    "gpt 4o": "GPT-4o",
    "gpt four": "GPT-4",
    "gpt 4-o": "GPT-4o",
}


# ---------- Initial-prompt builder ---------------------------------

# Whisper's initial_prompt has an implicit token budget. Empirically,
# faster-whisper and the OpenAI API both accept up to ~224 tokens of
# prompt context before they start truncating. A safe character budget
# is ~800 chars (≈1 token per 4 chars for English). We trim to this if
# the user's custom + termbase terms blow past it.
_MAX_PROMPT_CHARS = 800


def build_initial_prompt(
    custom_terms: Optional[Iterable[str]] = None,
    termbase_terms: Optional[Iterable[str]] = None,
) -> str:
    """Return the ``initial_prompt`` string to pass to Whisper.

    Combines the built-in :data:`DEFAULT_VOCABULARY` with optional
    user-supplied ``custom_terms`` and ``termbase_terms``. Trimmed to
    fit within Whisper's prompt-context budget so the prompt itself
    never crowds out the actual transcription.

    Args:
        custom_terms: extra brand / technical terms from the user's
            voice-settings "Custom dictation vocabulary" textarea.
            Whitespace-only entries and duplicates are dropped.
        termbase_terms: source-language entries from the active
            project's termbase, when the "Bias from active termbase"
            toggle is on. Same de-duplication.

    Returns:
        A single-paragraph string ready to pass as
        ``transcribe(initial_prompt=...)`` to faster-whisper or
        ``transcriptions.create(prompt=...)`` to the OpenAI API.
        Empty string if everything would be empty (which Whisper
        treats as "no prompt").
    """
    seen: set[str] = set()
    parts: List[str] = []
    for source in (DEFAULT_VOCABULARY, custom_terms or (), termbase_terms or ()):
        for term in source:
            if not term:
                continue
            term = str(term).strip()
            if not term:
                continue
            key = term.lower()
            if key in seen:
                continue
            seen.add(key)
            parts.append(term)
    if not parts:
        return ""
    # Punctuate as a list of short noun phrases. Whisper's tokeniser
    # handles either commas or periods; commas are more compact.
    prompt = ", ".join(parts) + "."
    if len(prompt) > _MAX_PROMPT_CHARS:
        # Greedy trim from the end so the built-in vocab always
        # makes the cut – the user's longest custom list and the
        # termbase tail are the things to drop first.
        # Find a clean comma split close to the budget.
        cutoff = prompt.rfind(", ", 0, _MAX_PROMPT_CHARS)
        if cutoff > 0:
            prompt = prompt[:cutoff] + "."
        else:
            prompt = prompt[:_MAX_PROMPT_CHARS]
    return prompt


# ---------- Replacement post-processor -----------------------------

def apply_replacements(
    text: str,
    user_replacements: Optional[Iterable[dict]] = None,
) -> str:
    """Apply default + user-supplied ``heard → meant`` replacements.

    Each replacement matches whole-word, case-insensitively. Multi-
    word heard phrases match across whitespace (so ``"super
    vertaler"`` catches a slow speaker who says it as two words).

    Args:
        text: the transcript Whisper returned.
        user_replacements: a list of dicts with ``heard`` and
            ``meant`` keys – the user-editable "Replacements" table
            in the Voice settings. Applied AFTER the defaults so the
            user can override (or extend) any default mapping.

    Returns:
        The text with replacements applied. Whitespace is preserved
        otherwise.
    """
    if not text:
        return text

    # Build the merged replacement list. User entries that share a
    # ``heard`` key with a default overwrite the default (so users
    # can fix a default they disagree with by simply re-listing it
    # with a different ``meant``).
    merged: Dict[str, str] = dict(DEFAULT_REPLACEMENTS)
    for entry in user_replacements or ():
        try:
            heard = (entry.get("heard") or "").strip()
            meant = (entry.get("meant") or "").strip()
        except AttributeError:
            # Tolerate (heard, meant) tuple form as well.
            try:
                heard, meant = entry
                heard = (heard or "").strip()
                meant = (meant or "").strip()
            except (ValueError, TypeError):
                continue
        if not heard or not meant:
            continue
        merged[heard.lower()] = meant

    # Apply longest-first so multi-word heards win over single-word
    # ones that overlap. E.g. "super vertaler" should be replaced
    # before "super" alone would have a chance.
    for heard in sorted(merged.keys(), key=len, reverse=True):
        meant = merged[heard]
        # Whole-word, case-insensitive. \b doesn't work cleanly when
        # the heard form contains hyphens or apostrophes, so we
        # fall back to a "surrounded by non-word or string-edge"
        # pattern. Spaces inside `heard` match any whitespace run.
        pattern_text = re.escape(heard).replace(r"\ ", r"\s+")
        pattern = re.compile(
            r"(?<![A-Za-z0-9])" + pattern_text + r"(?![A-Za-z0-9])",
            re.IGNORECASE,
        )
        text = pattern.sub(meant, text)
    return text


# ---------- Convenience: end-to-end post-process -------------------

def post_process_transcript(
    text: str,
    user_replacements: Optional[Iterable[dict]] = None,
) -> str:
    """Alias for :func:`apply_replacements`, kept under a more
    descriptive name for call-site clarity. Future post-processing
    steps (punctuation normalisation, common spacing fixes, etc.)
    will land here too.
    """
    return apply_replacements(text, user_replacements=user_replacements)
