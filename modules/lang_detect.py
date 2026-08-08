"""Lightweight binary language detection for QuickTrans direction.

QuickTrans always works between a project's two known languages, so this is NOT
general language identification – it just decides which of *two* candidate
languages a short piece of text is most likely in, using high-frequency
function-word ("stopword") scoring. No dependencies, no network, no cost.

If it can't decide confidently (text too short, a tie, no stopword set for a
language), it returns None / the project's own direction, so callers degrade
gracefully to the existing behaviour.
"""

import re

# Distinctive high-frequency words per language. Shared words (e.g. "in", "is")
# add to both sides equally, so the winner is decided by the distinctive ones.
_STOPWORDS = {
    "en": {"the", "of", "and", "to", "that", "for", "with", "as", "are", "be",
           "this", "was", "by", "an", "from", "which", "not", "have", "has", "or",
           "it", "at", "on"},
    "nl": {"de", "het", "een", "en", "van", "dat", "voor", "met", "op", "aan",
           "te", "zijn", "door", "niet", "wordt", "worden", "bij", "naar", "om",
           "deze", "ook", "maar", "of", "was", "dan", "meer", "als", "uit",
           "over", "heeft"},
    "de": {"der", "die", "das", "und", "zu", "den", "von", "mit", "dem", "des",
           "ein", "eine", "auf", "für", "nicht", "werden", "wird", "bei", "durch",
           "auch", "sich", "im"},
    "fr": {"le", "la", "les", "des", "et", "un", "une", "que", "dans", "pour",
           "par", "avec", "sur", "au", "aux", "qui", "ne", "pas", "plus", "ou",
           "être", "sont", "ce", "cette"},
    "es": {"el", "la", "los", "las", "de", "y", "un", "una", "que", "es", "por",
           "para", "con", "del", "se", "no", "su", "al", "como", "más", "son"},
    "it": {"il", "la", "di", "che", "un", "una", "per", "con", "del", "della",
           "è", "non", "si", "le", "dei", "come", "più", "sono", "da", "su"},
    "pt": {"o", "a", "os", "as", "de", "um", "uma", "que", "em", "do", "da",
           "para", "com", "não", "se", "por", "mais", "como", "são", "dos"},
}

# Language names / codes (as Supervertaler stores them) → canonical code.
_LANG_CODE = {
    "english": "en", "engels": "en",
    "dutch": "nl", "nederlands": "nl",
    "german": "de", "deutsch": "de", "duits": "de",
    "french": "fr", "français": "fr", "francais": "fr", "frans": "fr",
    "spanish": "es", "español": "es", "espanol": "es", "spaans": "es",
    "italian": "it", "italiano": "it", "italiaans": "it",
    "portuguese": "pt", "português": "pt", "portugues": "pt",
    "en": "en", "nl": "nl", "de": "de", "fr": "fr", "es": "es", "it": "it", "pt": "pt",
}

_WORD_RE = re.compile(r"[a-zà-ÿ']+", re.IGNORECASE)


def lang_code(lang):
    """Map a language name or code (e.g. 'English', 'en-GB', 'Nederlands') to a
    canonical two-letter code, or None if unknown."""
    if not lang:
        return None
    t = str(lang).strip().lower()
    if t in _LANG_CODE:
        return _LANG_CODE[t]
    base = re.split(r"[-_ (]", t)[0]   # 'en-gb' / 'dutch (netherlands)' → base
    return _LANG_CODE.get(base)


def detect_language_code(text, candidate_langs):
    """Return the canonical code of whichever candidate language `text` is most
    likely in, or None if undecidable (too short, a tie, or a candidate has no
    stopword set)."""
    codes = []
    for l in candidate_langs:
        c = lang_code(l)
        if c and c in _STOPWORDS and c not in codes:
            codes.append(c)
    if len(codes) < 2:
        return None

    tokens = _WORD_RE.findall((text or "").lower())
    if len(tokens) < 2:
        return None

    scores = {c: sum(1 for t in tokens if t in _STOPWORDS[c]) for c in codes}
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    # Require a clear margin, not just a 1-hit edge: a single false-friend word
    # (e.g. "was", common to both Dutch and English) must not be enough to flip
    # the caller's known direction. Below the margin we return None and the
    # caller keeps the project's own source -> target pair.
    if ranked[0][1] == 0 or (ranked[0][1] - ranked[1][1]) < 2:
        return None   # no stopword hits, or not a confident enough lead
    return ranked[0][0]


def resolve_direction(text, proj_source, proj_target):
    """Pick the QuickTrans (source, target) for `text` given a project's pair.

    If the text looks like the project's *target* language, flip the direction so
    it is translated back to the source. Otherwise (or when undecidable) keep the
    project's own source -> target direction.
    """
    detected = detect_language_code(text, [proj_source, proj_target])
    if detected is None:
        return proj_source, proj_target
    if detected == lang_code(proj_target) and detected != lang_code(proj_source):
        return proj_target, proj_source
    return proj_source, proj_target
