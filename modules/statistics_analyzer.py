"""Statistics analyzer for Supervertaler Workbench.

Analyzes a project against one or more TMs and produces a breakdown by match
category: Repetitions, 101%, 100%, 95-99%, 85-94%, 75-84%, 50-74%, No match.
Runs in a QThread to keep the UI responsive.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from difflib import SequenceMatcher
from typing import Dict, List, Optional

from PyQt6.QtCore import QThread, pyqtSignal


CAT_REPETITION = "Repetitions"
CAT_101        = "101% (Context Match)"
CAT_100        = "100%"
CAT_95_99      = "95%-99%"
CAT_85_94      = "85%-94%"
CAT_75_84      = "75%-84%"
CAT_50_74      = "50%-74%"
CAT_NO_MATCH   = "No match"

CATEGORIES: List[str] = [
    CAT_REPETITION,
    CAT_101,
    CAT_100,
    CAT_95_99,
    CAT_85_94,
    CAT_75_84,
    CAT_50_74,
    CAT_NO_MATCH,
]

_TAG_RE = re.compile(r"<[^>]+>")

# Short, plain-language explanation of each match category, for the legend
# shown in the dialog and written into every export.
CATEGORY_HELP: Dict[str, str] = {
    CAT_REPETITION:
        "Source text that repeats earlier in the document. The first occurrence "
        "is counted under a match band; the repeats land here (translate once, reuse).",
    CAT_101:
        "Exact match whose surrounding context also matches the TM — the safest "
        "reuse, normally needs no editing.",
    CAT_100:
        "Exact match of the source text in the TM (context not checked).",
    CAT_95_99:  "Very high fuzzy match — usually a tiny edit.",
    CAT_85_94:  "High fuzzy match — minor editing expected.",
    CAT_75_84:  "Medium fuzzy match — noticeable editing expected.",
    CAT_50_74:  "Low fuzzy match — often faster to retranslate than to fix.",
    CAT_NO_MATCH:
        "No usable TM match — translate from scratch.",
}


class MatchBucket:
    """Cumulative counts for one match category."""
    __slots__ = ("segments", "words", "chars", "tags")

    def __init__(self):
        self.segments = 0
        self.words    = 0
        self.chars    = 0
        self.tags     = 0

    def add(self, words: int, chars: int, tags: int):
        self.segments += 1
        self.words    += words
        self.chars    += chars
        self.tags     += tags


class FileResult:
    """Per-file slice of a TMResult (same shape: total + category buckets)."""

    def __init__(self, name: str):
        self.name    = name
        self.total   = MatchBucket()
        self.buckets: Dict[str, MatchBucket] = {cat: MatchBucket() for cat in CATEGORIES}


class TMResult:
    """Analysis result for one TM.

    ``total`` / ``buckets`` are the combined figures across all files; ``by_file``
    holds the same breakdown per source file (for multi-file projects and the
    Quick Count tool). Single-file analyses leave ``by_file`` with one entry.
    """

    def __init__(self, tm_id: str, tm_name: str):
        self.tm_id   = tm_id
        self.tm_name = tm_name
        self.total   = MatchBucket()
        self.buckets: Dict[str, MatchBucket] = {cat: MatchBucket() for cat in CATEGORIES}
        self.by_file: "Dict[str, FileResult]" = {}

    def _file(self, name: str) -> FileResult:
        fr = self.by_file.get(name)
        if fr is None:
            fr = FileResult(name)
            self.by_file[name] = fr
        return fr

    def add_total(self, file_name: str, words: int, chars: int, tags: int):
        self.total.add(words, chars, tags)
        self._file(file_name).total.add(words, chars, tags)

    def add_to(self, category: str, file_name: str, words: int, chars: int, tags: int):
        self.buckets[category].add(words, chars, tags)
        self._file(file_name).buckets[category].add(words, chars, tags)


def _count_words(text: str) -> int:
    return len(_TAG_RE.sub(" ", text).split())


def _count_chars(text: str) -> int:
    return len(_TAG_RE.sub("", text))


def _count_tags(text: str) -> int:
    return len(_TAG_RE.findall(text))


def _normalise(text: str) -> str:
    """Strip tags, collapse whitespace, lower-case (repetition/context detection)."""
    return " ".join(_TAG_RE.sub("", text).split()).lower()


def _classify_similarity(sim: float) -> str:
    if sim >= 0.95:
        return CAT_95_99
    elif sim >= 0.85:
        return CAT_85_94
    elif sim >= 0.75:
        return CAT_75_84
    else:
        return CAT_50_74


def _batch_exact_with_context(
    conn: sqlite3.Connection,
    sources: List[str],
    tm_id: str,
    source_lang: Optional[str],
    target_lang: Optional[str],
) -> Dict[str, dict]:
    """Return {source_text -> row} for sources with an exact TM match.

    Row includes context_before, target_text, usage_count. Uses four hash
    variants for robust matching (mirrors DatabaseManager.get_exact_match) but
    also fetches context_before, which the stock batch helper omits.
    """
    if not sources:
        return {}

    source_to_hashes: Dict[str, List[str]] = {}
    all_hashes: set = set()

    def _add(src_key: str, text: str):
        h = hashlib.md5(text.encode("utf-8")).hexdigest()
        if h not in all_hashes:
            source_to_hashes[src_key].append(h)
            all_hashes.add(h)

    for src in sources:
        source_to_hashes[src] = []
        _add(src, src)
        stripped = _TAG_RE.sub("", src)
        _add(src, stripped)
        _add(src, " ".join(src.split()))
        _add(src, " ".join(stripped.split()))

    if not all_hashes:
        return {}

    extra_sql    = " AND tm_id = ?"
    extra_params: list = [tm_id]

    if source_lang:
        base = source_lang.split("-")[0]
        extra_sql    += " AND (source_lang = ? OR source_lang LIKE ?)"
        extra_params += [base, base + "-%"]
    if target_lang:
        base = target_lang.split("-")[0]
        extra_sql    += " AND (target_lang = ? OR target_lang LIKE ?)"
        extra_params += [base, base + "-%"]

    hash_list  = list(all_hashes)
    chunk_size = max(900 - len(extra_params), 50)
    rows_by_hash: Dict[str, dict] = {}
    cursor = conn.cursor()

    for i in range(0, len(hash_list), chunk_size):
        chunk = hash_list[i : i + chunk_size]
        ph    = ",".join("?" * len(chunk))
        cursor.execute(
            "SELECT source_text, target_text, context_before, source_hash, usage_count "
            "FROM translation_units INDEXED BY idx_tu_source_hash "
            "WHERE source_hash IN (" + ph + ")" + extra_sql + " ORDER BY usage_count DESC",
            chunk + extra_params,
        )
        for row in cursor.fetchall():
            h = row["source_hash"]
            if h not in rows_by_hash or row["usage_count"] > rows_by_hash[h]["usage_count"]:
                rows_by_hash[h] = dict(row)

    results: Dict[str, dict] = {}
    for src, hashes in source_to_hashes.items():
        for h in hashes:
            if h in rows_by_hash and src not in results:
                results[src] = rows_by_hash[h]
                break

    return results


_FTS_SPLIT_RE = re.compile(r"[^\w\s]")


def _fuzzy_best_similarity(
    conn: sqlite3.Connection,
    source: str,
    tm_id: str,
    threshold: float,
    source_lang: Optional[str],
    target_lang: Optional[str],
    n_terms: int = 5,
    candidate_limit: int = 30,
) -> float:
    """Best fuzzy similarity (0..1) for *source* against one TM.

    Strategy (fast on very large TMs): use the FTS5 index to fetch only the
    top-ranked candidates that share the source's most distinctive words, then
    score just those with a cheap length-ratio + quick_ratio gate before the
    O(n²) SequenceMatcher.ratio(). This avoids the in-memory brute-force scan of
    every TM row (which is ~5x slower on a 600k-entry TM) while finding the same
    high matches — a genuine fuzzy match shares the source's longest words, so
    it ranks near the top of the FTS results.

    Returns the best ratio found (>= threshold), else the best below-threshold
    ratio seen (caller decides the band). 0.0 when nothing scores.
    """
    src_norm = _normalise(source)
    if not src_norm:
        return 0.0

    # Most distinctive words = the longest ones (3+ chars), capped at n_terms.
    terms = [t for t in _FTS_SPLIT_RE.sub(" ", _TAG_RE.sub("", source)).split() if len(t) > 2]
    if not terms:
        return 0.0
    terms.sort(key=len, reverse=True)
    terms = terms[:n_terms]
    fts_query = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)

    sql = (
        "SELECT tu.source_text FROM translation_units tu "
        "JOIN translation_units_fts ON tu.id = translation_units_fts.rowid "
        "WHERE translation_units_fts MATCH ? AND tu.tm_id = ?"
    )
    params: list = [fts_query, tm_id]
    if source_lang:
        b = source_lang.split("-")[0]
        sql += " AND (tu.source_lang = ? OR tu.source_lang LIKE ?)"
        params += [b, b + "-%"]
    if target_lang:
        b = target_lang.split("-")[0]
        sql += " AND (tu.target_lang = ? OR tu.target_lang LIKE ?)"
        params += [b, b + "-%"]
    sql += " ORDER BY bm25(translation_units_fts) LIMIT " + str(int(candidate_limit))

    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        rows = cur.fetchall()
    except Exception:
        return 0.0

    src_len = len(src_norm)
    best = 0.0
    for row in rows:
        cand = _normalise(row["source_text"])
        cand_len = len(cand)
        if cand_len == 0:
            continue
        # Cheap length-ratio gate: a match can't exceed this similarity.
        if min(src_len, cand_len) / max(src_len, cand_len) < threshold:
            continue
        sm = SequenceMatcher(None, src_norm, cand)
        if sm.quick_ratio() <= best:
            continue
        ratio = sm.ratio()
        if ratio > best:
            best = ratio
            if best >= 0.999:
                break
    return best


class StatisticsWorker(QThread):
    """Background thread that runs the full statistics analysis.

    Signals: progress(current, total, message), tm_result(TMResult),
             finished(list[TMResult]), error(str)
    """

    progress  = pyqtSignal(int, int, str)
    tm_result = pyqtSignal(object)
    finished  = pyqtSignal(list)
    error     = pyqtSignal(str)

    def __init__(
        self,
        db_path: str,
        segments,
        tm_ids: List[str],
        tm_names: Dict[str, str],
        source_lang: str,
        target_lang: str,
        fuzzy_threshold: float = 0.75,
        skip_fuzzy: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.db_path         = db_path
        self.segments        = segments
        self.tm_ids          = tm_ids
        self.tm_names        = tm_names
        self.source_lang     = source_lang
        self.target_lang     = target_lang
        self.fuzzy_threshold = fuzzy_threshold
        # When True, only exact (100%/101%) matches and repetitions are computed;
        # the expensive fuzzy pass is skipped entirely (near-instant even on a
        # multi-hundred-thousand-entry TM).
        self.skip_fuzzy      = skip_fuzzy
        self._cancelled      = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            self._analyse()
        except Exception as exc:
            import traceback
            self.error.emit(str(exc) + "\n" + traceback.format_exc())

    def _analyse(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")

        segments = self.segments
        n        = len(segments)
        sources  = [seg.source for seg in segments]
        files    = [(getattr(seg, "file_name", "") or "") for seg in segments]
        metrics  = [(_count_words(s), _count_chars(s), _count_tags(s)) for s in sources]

        # Identify internal repetitions (2nd+ occurrence of identical source)
        seen_norms: Dict[str, int] = {}
        is_repetition = [False] * n
        for i, src in enumerate(sources):
            norm = _normalise(src)
            if not norm:
                continue
            if norm in seen_norms:
                is_repetition[i] = True
            else:
                seen_norms[norm] = i

        eligible_idx     = [i for i in range(n) if not is_repetition[i] and sources[i].strip()]
        eligible_sources = [sources[i] for i in eligible_idx]

        n_tms   = len(self.tm_ids)
        results = []

        # Word-count-only mode: no TM selected. Report total words/chars/tags
        # and internal repetitions; everything else falls into "No match".
        if not self.tm_ids:
            result = TMResult("", "No TM (word count only)")
            for i in range(n):
                w, c, t = metrics[i]
                result.add_total(files[i], w, c, t)
                if is_repetition[i]:
                    result.add_to(CAT_REPETITION, files[i], w, c, t)
                elif sources[i].strip():
                    result.add_to(CAT_NO_MATCH, files[i], w, c, t)
            results.append(result)
            self.tm_result.emit(result)
            conn.close()
            if not self._cancelled:
                self.finished.emit(results)
            return

        for tm_num, tm_id in enumerate(self.tm_ids):
            if self._cancelled:
                break

            tm_name = self.tm_names.get(tm_id, tm_id)
            result  = TMResult(tm_id, tm_name)

            # Total + repetitions buckets
            for i in range(n):
                w, c, t = metrics[i]
                result.add_total(files[i], w, c, t)
                if is_repetition[i]:
                    result.add_to(CAT_REPETITION, files[i], w, c, t)

            if self._cancelled:
                break

            # total=0 → busy indicator while the exact batch query runs.
            self.progress.emit(
                0, 0,
                "TM \"" + tm_name + "\" (" + str(tm_num + 1) + "/" + str(n_tms)
                + "): searching for exact matches…",
            )

            exact = _batch_exact_with_context(
                conn, eligible_sources, tm_id,
                self.source_lang, self.target_lang,
            )

            no_exact_idx: List[int] = []

            for i in eligible_idx:
                if self._cancelled:
                    break
                src     = sources[i]
                w, c, t = metrics[i]
                match   = exact.get(src)
                if match:
                    ctx_tm = _normalise(match.get("context_before") or "")
                    is_ctx = bool(ctx_tm and i > 0 and ctx_tm == _normalise(sources[i - 1]))
                    result.add_to(CAT_101 if is_ctx else CAT_100, files[i], w, c, t)
                else:
                    no_exact_idx.append(i)

            if self._cancelled:
                break

            # ---- Fuzzy pass ------------------------------------------
            # "Exact only" mode skips this entirely: the segments left over
            # after exact matching are simply No-match.
            if self.skip_fuzzy:
                for i in no_exact_idx:
                    w, c, t = metrics[i]
                    result.add_to(CAT_NO_MATCH, files[i], w, c, t)
            else:
                # Use the FTS5-indexed matcher (the same one the editor uses for
                # live lookups). On a large TM it fetches only candidates that
                # share words via the FTS index, instead of brute-forcing every
                # row in memory — dramatically faster than the in-memory batch
                # scan when the TM has hundreds of thousands of entries.
                total_fuzzy = len(no_exact_idx)
                for j, i in enumerate(no_exact_idx):
                    if self._cancelled:
                        break
                    if j % 10 == 0:
                        self.progress.emit(
                            j, max(total_fuzzy, 1),
                            "TM \"" + tm_name + "\": fuzzy match "
                            + str(j) + "/" + str(total_fuzzy) + "…",
                        )
                    src     = sources[i]
                    w, c, t = metrics[i]
                    sim = _fuzzy_best_similarity(
                        conn, src, tm_id, self.fuzzy_threshold,
                        self.source_lang, self.target_lang,
                    )
                    cat = _classify_similarity(sim) if sim >= self.fuzzy_threshold else CAT_NO_MATCH
                    result.add_to(cat, files[i], w, c, t)

            results.append(result)
            self.tm_result.emit(result)

        conn.close()
        if not self._cancelled:
            self.finished.emit(results)
