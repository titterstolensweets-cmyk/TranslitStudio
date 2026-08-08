"""Guard the unified project-termbase role (v1.10.360).

Historically there were TWO parallel representations of "project termbase"
that could silently disagree:

  * the legacy ``termbases.is_project_termbase`` flag (checked by
    create_termbase's one-per-project guard and get_project_termbase), and
  * the activation-based one the Termbases tab actually displays —
    ``termbase_activation.priority = 1`` with ``is_active = 1``.

A startup migration even flagged EVERY project-scoped termbase, so AI term
extraction refused to create a project termbase "because one already exists"
while the UI showed none. These tests lock in the v1.10.360 unification:

  1. ``termbase_activation.priority = 1`` (+ ``is_active = 1``) is
     authoritative; ``set_termbase_priority`` is the single write path;
  2. promotion auto-activates (a project termbase must be readable) and is
     exclusive per project;
  3. the legacy flag is kept in sync for project-scoped termbases and stale
     flags are repaired (cleared) at startup;
  4. a project-scoped termbase is NOT automatically "the project termbase".
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from modules.database_manager import DatabaseManager
from modules.termbase_manager import TermbaseManager

PROJECT = 42
OTHER_PROJECT = 43


@pytest.fixture
def db(tmp_path):
    d = DatabaseManager(db_path=str(tmp_path / "t.db"), log_callback=lambda *a, **k: None)
    d.connect()
    return d


@pytest.fixture
def mgr(db):
    return TermbaseManager(db, log_callback=lambda *a, **k: None)


def _flag(db, tb_id):
    db.cursor.execute("SELECT is_project_termbase FROM termbases WHERE id = ?", (tb_id,))
    return bool(db.cursor.fetchone()[0])


def _activation(db, tb_id, project_id):
    db.cursor.execute(
        "SELECT is_active, priority FROM termbase_activation "
        "WHERE termbase_id = ? AND project_id = ?", (tb_id, project_id))
    row = db.cursor.fetchone()
    return tuple(row) if row is not None else None


def test_promote_auto_activates_and_syncs_flag(db, mgr):
    tb_id = mgr.create_termbase("TB1", "en", "nl", project_id=PROJECT, is_global=False)

    # No activation row yet — promotion must create one (Read implied).
    assert mgr.set_termbase_priority(tb_id, PROJECT, 1) is True
    assert _activation(db, tb_id, PROJECT) == (1, 1)

    # Activation-based reader agrees, and the legacy flag is mirrored
    # (project-scoped termbase).
    ptb = mgr.get_project_termbase(PROJECT)
    assert ptb is not None and ptb["id"] == tb_id
    assert _flag(db, tb_id) is True


def test_promotion_is_exclusive_and_demotes_flag(db, mgr):
    a = mgr.create_termbase("TB-A", "en", "nl", project_id=PROJECT, is_global=False)
    b = mgr.create_termbase("TB-B", "en", "nl", project_id=PROJECT, is_global=False)

    mgr.set_termbase_priority(a, PROJECT, 1)
    mgr.set_termbase_priority(b, PROJECT, 1)

    # B is the project termbase; A was demoted in BOTH representations.
    assert mgr.get_project_termbase(PROJECT)["id"] == b
    assert _activation(db, a, PROJECT)[1] is None
    assert _flag(db, a) is False
    assert _flag(db, b) is True

    # Demote B → nothing holds the role.
    mgr.set_termbase_priority(b, PROJECT, None)
    assert mgr.get_project_termbase(PROJECT) is None
    assert _flag(db, b) is False


def test_global_termbase_can_hold_role_per_project(db, mgr):
    g = mgr.create_termbase("Global TB", "en", "nl", project_id=None, is_global=True)

    mgr.set_termbase_priority(g, PROJECT, 1)
    mgr.set_termbase_priority(g, OTHER_PROJECT, 1)

    # The same global termbase is the project termbase of both projects…
    assert mgr.get_project_termbase(PROJECT)["id"] == g
    assert mgr.get_project_termbase(OTHER_PROJECT)["id"] == g
    # …and the single global flag column stays 0: it cannot represent a
    # per-project role, so it is only maintained for project-scoped termbases.
    assert _flag(db, g) is False


def test_project_scoped_is_not_automatically_the_project_termbase(db, mgr):
    """The observed v1.10.359 failure: a termbase created as project-SCOPED
    must not be reported as the project termbase (extraction refused to
    create one for a role the UI said was vacant)."""
    mgr.create_termbase("test2", "en", "nl", project_id=PROJECT, is_global=False)
    assert mgr.get_project_termbase(PROJECT) is None


def test_create_with_role_promotes_and_guard_is_activation_based(db, mgr):
    tb_id = mgr.create_termbase("Role TB", "en", "nl", project_id=PROJECT,
                                is_global=False, is_project_termbase=True)
    assert _activation(db, tb_id, PROJECT) == (1, 1)
    assert mgr.get_project_termbase(PROJECT)["id"] == tb_id

    # One-per-project guard refuses while the role is held…
    assert mgr.create_termbase("Second Role TB", "en", "nl", project_id=PROJECT,
                               is_global=False, is_project_termbase=True) is None

    # …but not after the role is vacated (even though the first termbase is
    # still project-scoped).
    mgr.set_termbase_priority(tb_id, PROJECT, None)
    assert mgr.create_termbase("Second Role TB", "en", "nl", project_id=PROJECT,
                               is_global=False, is_project_termbase=True) is not None


def test_set_as_project_termbase_delegates_to_single_path(db, mgr):
    tb_id = mgr.create_termbase("Extracted TB", "en", "nl", project_id=PROJECT, is_global=False)
    assert mgr.set_as_project_termbase(tb_id, PROJECT) is True
    assert _activation(db, tb_id, PROJECT) == (1, 1)
    assert mgr.get_project_termbase(PROJECT)["id"] == tb_id
    assert _flag(db, tb_id) is True


def test_unset_clears_both_representations(db, mgr):
    tb_id = mgr.create_termbase("TB", "en", "nl", project_id=PROJECT, is_global=False)
    mgr.set_termbase_priority(tb_id, PROJECT, 1)

    assert mgr.unset_project_termbase(tb_id) is True
    assert mgr.get_project_termbase(PROJECT) is None
    assert _flag(db, tb_id) is False
    # Still readable — unsetting the role must not deactivate.
    assert _activation(db, tb_id, PROJECT)[0] == 1


def test_startup_repair_clears_stale_flags(tmp_path):
    """A flag with no backing priority=1 activation row (the pre-v1.10.360
    pollution) is cleared on the next connect; a flag WITH backing survives."""
    path = str(tmp_path / "t.db")
    d = DatabaseManager(db_path=path, log_callback=lambda *a, **k: None)
    d.connect()
    mgr = TermbaseManager(d, log_callback=lambda *a, **k: None)

    stale = mgr.create_termbase("Stale", "en", "nl", project_id=PROJECT, is_global=False)
    real = mgr.create_termbase("Real", "en", "nl", project_id=OTHER_PROJECT, is_global=False)
    mgr.set_termbase_priority(real, OTHER_PROJECT, 1)
    # Simulate the old pollution migration: flag without activation backing.
    d.cursor.execute("UPDATE termbases SET is_project_termbase = 1 WHERE id = ?", (stale,))
    d.connection.commit()
    d.connection.close()

    d2 = DatabaseManager(db_path=path, log_callback=lambda *a, **k: None)
    d2.connect()
    assert _flag(d2, stale) is False
    assert _flag(d2, real) is True
    d2.connection.close()
