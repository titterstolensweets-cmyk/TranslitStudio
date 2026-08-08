"""Guard the SuperLookup inclusion flag (v1.10.247).

SuperLookup inclusion is controlled by the dedicated `superlookup_enabled`
column on translation_memories / termbases — NOT the Read flag
(tm_activation.is_active / termbase_activation.is_active). These tests lock in:

  1. the columns exist and default to 1 (included) on a fresh DB;
  2. the managers surface the flag (default True) and persist toggles;
  3. the flag is INDEPENDENT of Read — toggling SuperLookup off doesn't
     touch activation, and a TM/termbase that is not Read-active is still
     reported as SuperLookup-enabled.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from modules.database_manager import DatabaseManager
from modules.tm_metadata_manager import TMMetadataManager
from modules.termbase_manager import TermbaseManager


@pytest.fixture
def db(tmp_path):
    d = DatabaseManager(db_path=str(tmp_path / "t.db"), log_callback=lambda *a, **k: None)
    d.connect()
    cur = d.cursor
    cur.execute(
        "INSERT INTO translation_memories (id, name, tm_id, source_lang, target_lang) "
        "VALUES (1, 'My TM', 'myslug', 'nl', 'en')"
    )
    cur.execute(
        "INSERT INTO termbases (id, name, source_lang, target_lang) "
        "VALUES (1, 'My TB', 'nl', 'en')"
    )
    d.connection.commit()
    return d


def _columns(cur, table):
    cur.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def test_columns_exist_and_default_included(db):
    assert "superlookup_enabled" in _columns(db.cursor, "translation_memories")
    assert "superlookup_enabled" in _columns(db.cursor, "termbases")

    tm = TMMetadataManager(db, log_callback=lambda *a, **k: None)
    tb = TermbaseManager(db, log_callback=lambda *a, **k: None)

    # Default: every existing TM / termbase is INCLUDED in SuperLookup.
    assert tm.get_all_tms()[0]["superlookup_enabled"] is True
    assert tb.get_all_termbases()[0]["superlookup_enabled"] is True


def test_setters_persist(db):
    tm = TMMetadataManager(db, log_callback=lambda *a, **k: None)
    tb = TermbaseManager(db, log_callback=lambda *a, **k: None)

    assert tm.set_superlookup_enabled(1, False) is True
    assert tb.set_termbase_superlookup_enabled(1, False) is True
    assert tm.get_all_tms()[0]["superlookup_enabled"] is False
    assert tb.get_all_termbases()[0]["superlookup_enabled"] is False

    # And back on.
    assert tm.set_superlookup_enabled(1, True) is True
    assert tb.set_termbase_superlookup_enabled(1, True) is True
    assert tm.get_all_tms()[0]["superlookup_enabled"] is True
    assert tb.get_all_termbases()[0]["superlookup_enabled"] is True


def test_independent_of_read(db):
    """SuperLookup toggle must not touch the Read/activation state, and a
    not-Read-active resource is still SuperLookup-enabled."""
    tm = TMMetadataManager(db, log_callback=lambda *a, **k: None)
    tb = TermbaseManager(db, log_callback=lambda *a, **k: None)

    # No activation record exists for project 0 → TM is NOT Read-active
    # (TMs default inactive). It must still report SuperLookup-enabled.
    assert tm.is_tm_active(1, 0) is False
    assert tm.get_all_tms()[0]["superlookup_enabled"] is True

    # Toggling SuperLookup off leaves Read/activation untouched.
    tm.set_superlookup_enabled(1, False)
    assert tm.is_tm_active(1, 0) is False  # unchanged
    # read_only (the Write flag) is also untouched.
    assert tm.get_all_tms()[0]["read_only"] is True

    # Same independence for termbases: superlookup off, activation untouched.
    tb.set_termbase_superlookup_enabled(1, False)
    assert tb.get_all_termbases()[0]["superlookup_enabled"] is False
    assert tb.get_all_termbases()[0]["read_only"] is True
