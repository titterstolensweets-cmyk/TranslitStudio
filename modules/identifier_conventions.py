"""TM & Termbase identifier conventions — the single authoritative rule.

Years of "no results" bugs (e.g. v1.10.242, where SuperLookup passed numeric
TM ids into a filter that expects string slugs) came from confusing the
*several* identifiers each TM / termbase carries. This module states the rule
once and gives the few helpers needed so call sites stop hand-rolling it.

================================  THE RULE  ================================

Every TM and termbase has up to three identifiers. Use the RIGHT one:

  • `id`  (INTEGER, registry primary key)
        → the REGISTRY key. Use it for: activation (tm_activation /
          termbase_activation), registry CRUD, and the UI's
          select-which-termbase logic. Never changes, even on rename.

  • the CONTENT key — what child/content rows reference:
        - TMs:       the string slug `tm_id`  ('BEIJER', 'patents', …).
                     `translation_units.tm_id` holds THIS. All TM search
                     filters (get_exact_match / search_fuzzy_matches /
                     concordance_search) take a list of these SLUGS.
        - Termbases: the numeric `id`, stored in `termbase_terms.termbase_id`
                     as TEXT ('13'). Compare with an explicit cast — either
                     `CAST(termbase_terms.termbase_id AS INTEGER) = termbases.id`
                     or `termbase_terms.termbase_id = CAST(? AS TEXT)` — never
                     rely on implicit coercion.

  • `name` (TEXT)  → DISPLAY ONLY. Never use a name as an identifier
                     (names duplicate and get renamed).

Gotcha that has bitten us: the column literally named `tm_activation.tm_id`
holds the numeric REGISTRY id (FK → translation_memories.id), NOT the string
slug that `translation_units.tm_id` holds. Same name, different meaning. When
you read an id out of an activation table, it is a REGISTRY id; translate it
to the content key before using it in a content/search filter.

The helpers below make the translation explicit. A TM record is a dict from
TMMetadataManager.get_all_tms(); a termbase record is a dict from
TermbaseManager.get_all_termbases().
"""

from __future__ import annotations

from typing import Iterable, List


def tm_search_key(tm_record: dict):
    """The id to use when FILTERING TM content (translation_units): the string
    slug. Returns None if the record has no slug (such a TM cannot match any
    translation_units row — callers should skip it, never fall back to the
    numeric id)."""
    return tm_record.get("tm_id")


def tm_registry_id(tm_record: dict):
    """The id to use for ACTIVATION / registry ops: the numeric primary key."""
    return tm_record.get("id")


def termbase_search_key(tb_record: dict):
    """The id to use when filtering termbase content (termbase_terms) AND for
    activation / UI selection: the numeric registry id. (Termbases, unlike TMs,
    key their content by the numeric id — stored as TEXT in termbase_terms, so
    compare with an explicit CAST.)"""
    return tb_record.get("id")


def tm_search_keys(tm_records: Iterable[dict]) -> List:
    """Slugs for a set of TM records, dropping any that lack one."""
    return [s for s in (tm_search_key(t) for t in tm_records) if s is not None]


def looks_like_registry_id(value) -> bool:
    """Heuristic guard: True if `value` looks like a numeric registry id rather
    than a string content slug. Useful in asserts at TM-search boundaries to
    catch a numeric id being passed where a slug is required."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, str) and value.isdigit():
        return True
    return False
