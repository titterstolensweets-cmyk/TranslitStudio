"""Guard the TM / termbase identifier conventions (see
modules/identifier_conventions.py).

These lock in the rules whose violation caused the v1.10.242 "0 TM matches"
bug: TM content is keyed by the STRING slug, termbase content by the NUMERIC
id (stored TEXT). If a future change passes the wrong id form, or breaks the
get_terms CAST, this fails.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from modules import identifier_conventions as ic
from modules.database_manager import DatabaseManager
from modules.termbase_manager import TermbaseManager


# --- convention helpers ------------------------------------------------------

def test_convention_helpers_pick_the_right_id():
    tm = {"id": 7, "tm_id": "BEIJER", "name": "BEIJER"}
    assert ic.tm_search_key(tm) == "BEIJER"     # slug for content/search filters
    assert ic.tm_registry_id(tm) == 7           # numeric for activation
    tb = {"id": 13, "name": "PATENTS"}
    assert ic.termbase_search_key(tb) == 13     # numeric for both
    assert ic.tm_search_keys([tm, {"id": 9}]) == ["BEIJER"]   # slug-less dropped


def test_looks_like_registry_id_catches_numeric_forms():
    assert ic.looks_like_registry_id(7)
    assert ic.looks_like_registry_id("7")
    assert not ic.looks_like_registry_id("BEIJER")
    assert not ic.looks_like_registry_id(None)


# --- fixture DB --------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    d = DatabaseManager(db_path=str(tmp_path / "t.db"), log_callback=lambda *a, **k: None)
    d.connect()
    cur = d.cursor
    # A TM whose units are keyed by the STRING slug 'myslug', registry id 1.
    cur.execute(
        "INSERT INTO translation_memories (id, name, tm_id, source_lang, target_lang) "
        "VALUES (1, 'My TM', 'myslug', 'nl', 'en')"
    )
    d.connection.commit()
    # Use the real batch insert so source_hash + the FTS index are populated
    # exactly as in production (and the tm_id-exists guard is satisfied).
    d.add_translation_units_batch(
        [("de inrichting werkt", "the device works"),
         ("een tweede inrichting", "a second device")],
        "nl", "en", tm_id="myslug",
    )
    # A termbase (registry id 1) whose terms store termbase_id as TEXT '1'.
    cur.execute("INSERT INTO termbases (id, name, source_lang, target_lang) "
                "VALUES (1, 'My TB', 'nl', 'en')")
    cur.execute(
        "INSERT INTO termbase_terms (source_term, target_term, source_lang, target_lang, termbase_id) "
        "VALUES ('inrichting', 'device', 'nl', 'en', '1')"
    )
    d.connection.commit()
    return d


def test_tm_search_filter_needs_the_slug_not_the_registry_id(db):
    # The slug matches; the numeric registry id matches NOTHING (the v1.10.242 bug).
    hit_slug = db.concordance_search("inrichting", tm_ids=["myslug"],
                                     source_lang="nl", target_lang="en")
    hit_numeric = db.concordance_search("inrichting", tm_ids=[1],
                                        source_lang="nl", target_lang="en")
    hit_none = db.concordance_search("inrichting", tm_ids=None,
                                     source_lang="nl", target_lang="en")
    assert len(hit_slug) >= 1, "slug filter must find the units"
    assert len(hit_numeric) == 0, "numeric registry id must NOT match string tm_id"
    assert len(hit_none) >= 1


def test_get_terms_accepts_the_numeric_registry_id(db):
    # termbase_terms.termbase_id is TEXT '1'; get_terms is called with numeric 1.
    # The CAST(? AS TEXT) fix must bridge this without relying on implicit coercion.
    tbm = TermbaseManager(db, log_callback=lambda *a, **k: None)
    terms = tbm.get_terms(1)
    assert any(t["source_term"] == "inrichting" for t in terms), \
        "numeric termbase id must resolve TEXT-stored termbase_terms.termbase_id"
