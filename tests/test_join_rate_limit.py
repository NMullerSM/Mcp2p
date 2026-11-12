"""
Suite de pruebas para el esquema de rate-limit de solicitudes de colaboración P2P
(join requests), keyed por clave pública Ed25519 persistente del solicitante.
"""

from datetime import datetime, timedelta, timezone

import pytest

from src.storage.db import DatabaseManager, _compute_lock_until, _hourly_cap_exceeded


# ==========================
# Fase 1: Funciones puras de lock
# ==========================

def test_no_lock_for_first_two_rejections():
    now = datetime.now(timezone.utc)
    assert _compute_lock_until(1, now, now) is None
    assert _compute_lock_until(2, now, now) is None


def test_30min_lock_on_third_and_fourth_rejection():
    now = datetime.now(timezone.utc)
    assert _compute_lock_until(3, now, now) == now + timedelta(minutes=30)
    assert _compute_lock_until(4, now, now) == now + timedelta(minutes=30)


def test_24h_lock_from_fifth_rejection_onward():
    now = datetime.now(timezone.utc)
    assert _compute_lock_until(5, now, now) == now + timedelta(hours=24)
    assert _compute_lock_until(9, now, now) == now + timedelta(hours=24)


def test_lock_expires_after_window_elapses():
    now = datetime.now(timezone.utc)
    past = now - timedelta(minutes=31)
    assert _compute_lock_until(3, past, now) is None


def test_hourly_cap_not_enforced_below_third_rejection():
    now = datetime.now(timezone.utc)
    assert _hourly_cap_exceeded(2, now, 10, now) is False


def test_hourly_cap_enforced_from_third_rejection():
    now = datetime.now(timezone.utc)
    assert _hourly_cap_exceeded(3, now, 4, now) is True
    assert _hourly_cap_exceeded(3, now, 3, now) is False


def test_hourly_cap_window_resets_after_an_hour():
    now = datetime.now(timezone.utc)
    old_window = now - timedelta(hours=1, minutes=1)
    assert _hourly_cap_exceeded(4, old_window, 4, now) is False


# ==========================
# Fase 2: Migración de esquema y métodos de DB
# ==========================

def test_migration_adds_rate_limit_columns(tmp_path):
    db = DatabaseManager(db_path=str(tmp_path / "t.db"))
    with db.get_connection() as conn:
        cols = [c["name"] for c in conn.execute("PRAGMA table_info(project_collaborators);").fetchall()]
    assert {"rejection_count", "last_rejected_at", "request_window_start", "request_window_count"} <= set(cols)


def test_migration_adds_join_status_column(tmp_path):
    db = DatabaseManager(db_path=str(tmp_path / "t.db"))
    with db.get_connection() as conn:
        cols = [c["name"] for c in conn.execute("PRAGMA table_info(projects);").fetchall()]
    assert "join_status" in cols


def test_get_collaborator_by_pubkey_finds_row(tmp_path):
    db = DatabaseManager(db_path=str(tmp_path / "t.db"))
    db.create_project(name="p", project_uuid="proj_x", init_git=False)
    db.add_or_request_collaborator("proj_x", "node_1", public_key="PK1", status="pending_approval")
    found = db.get_collaborator_by_pubkey("proj_x", "PK1")
    assert found["node_uuid"] == "node_1"
    assert db.get_collaborator_by_pubkey("proj_x", "NOPE") is None


def test_record_rejection_increments_and_locks(tmp_path):
    db = DatabaseManager(db_path=str(tmp_path / "t.db"))
    db.create_project(name="p", project_uuid="proj_x", init_git=False)
    db.add_or_request_collaborator("proj_x", "node_1", public_key="PK1", status="pending_approval")
    for _ in range(3):
        res = db.record_rejection("proj_x", "node_1")
    assert res["rejection_count"] == 3
    assert res["locked_until"] is not None
    state = db.get_join_rate_limit_state("proj_x", "PK1")
    assert state["allowed"] is False


def test_approve_resets_rejection_counters(tmp_path):
    db = DatabaseManager(db_path=str(tmp_path / "t.db"))
    db.create_project(name="p", project_uuid="proj_x", init_git=False)
    db.add_or_request_collaborator("proj_x", "node_1", public_key="PK1", status="pending_approval")
    db.record_rejection("proj_x", "node_1")
    db.record_rejection("proj_x", "node_1")
    db.record_rejection("proj_x", "node_1")
    db.approve_collaborator("proj_x", "node_1")
    state = db.get_join_rate_limit_state("proj_x", "PK1")
    assert state["allowed"] is True


def test_identity_collision_does_not_merge_two_pubkeys_under_same_node_uuid(tmp_path):
    # Review Focus: a node_uuid collision (e.g. same hostname) must not let a
    # second, unrelated public key silently take over the rate-limit state
    # of the first.
    db = DatabaseManager(db_path=str(tmp_path / "t.db"))
    db.create_project(name="p", project_uuid="proj_x", init_git=False)
    db.add_or_request_collaborator("proj_x", "node_1", public_key="PK1", status="pending_approval")
    db.record_rejection("proj_x", "node_1")
    db.record_rejection("proj_x", "node_1")
    db.record_rejection("proj_x", "node_1")  # PK1 now locked
    with pytest.raises(ValueError):
        db.add_or_request_collaborator("proj_x", "node_1", public_key="PK2", status="pending_approval")
    # PK1's lock state must be unaffected
    assert db.get_join_rate_limit_state("proj_x", "PK1")["allowed"] is False
