"""
Suite de pruebas para el esquema de rate-limit de solicitudes de colaboración P2P
(join requests), keyed por clave pública Ed25519 persistente del solicitante.
"""

import sqlite3
import threading
import time
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


# ==========================
# Fase 3: Hallazgos de revisión de Task 11 (owner-side UI)
# ==========================

def test_get_collaborator_by_pubkey_prefers_row_with_rejection_history_over_a_clean_duplicate(tmp_path):
    """
    Review finding (Important, Task 11 fix round): the SAME public key can end up
    spread across more than one row -- add_or_request_collaborator only rejects a
    collision when the node_uuid ALREADY exists under a DIFFERENT public_key; a
    requester retrying from a new node_uuid (different hostname/device, or a changed
    MCP2P_NODE_UUID) while NOT YET locked inserts a brand-new row under the SAME
    public_key instead of updating the old one. Without a deterministic ORDER BY,
    get_collaborator_by_pubkey's bare fetchone() could return either row -- including
    the clean, 0-rejection one -- silently hiding the other row's rejection history and
    defeating the rate limiter. This test reproduces that two-row state through the
    real API and asserts the row with rejection history is always the one returned.
    """
    db = DatabaseManager(db_path=str(tmp_path / "t.db"))
    db.create_project(name="p", project_uuid="proj_x", init_git=False)

    # First attempt: node_uuid "node_A", two rejections (below the lock threshold, so
    # the requester is still "allowed" and can retry from elsewhere).
    db.add_or_request_collaborator("proj_x", "node_A", public_key="PK1", status="pending_approval")
    db.record_rejection("proj_x", "node_A")
    db.record_rejection("proj_x", "node_A")

    # Second attempt, same persistent public key, but a different node_uuid (e.g. a
    # different device) -- add_or_request_collaborator's node_uuid-keyed collision
    # guard does not fire here, so this creates a SECOND, clean row for the same PK1.
    db.add_or_request_collaborator("proj_x", "node_B", public_key="PK1", status="pending_approval")

    row = db.get_collaborator_by_pubkey("proj_x", "PK1")
    assert row is not None
    assert row["node_uuid"] == "node_A"
    assert row["rejection_count"] == 2

    # The rate-limit state computed from this must reflect node_A's real history, not
    # node_B's clean slate.
    state = db.get_join_rate_limit_state("proj_x", "PK1")
    assert state["allowed"] is True  # 2 rejections is still below the lock threshold...

    db.record_rejection("proj_x", "node_A")  # ...a 3rd rejection on node_A crosses it.
    row2 = db.get_collaborator_by_pubkey("proj_x", "PK1")
    assert row2["node_uuid"] == "node_A"
    assert row2["rejection_count"] == 3
    assert db.get_join_rate_limit_state("proj_x", "PK1")["allowed"] is False


def test_get_collaborator_by_pubkey_ignores_an_approved_row_in_favor_of_an_unresolved_one(tmp_path):
    """An 'approved' row for a public key must never shadow another unresolved
    (pending/rejected) row for that same key when both somehow exist."""
    db = DatabaseManager(db_path=str(tmp_path / "t.db"))
    db.create_project(name="p", project_uuid="proj_x", init_git=False)
    db.add_or_request_collaborator("proj_x", "node_A", public_key="PK1", status="approved")
    db.add_or_request_collaborator("proj_x", "node_B", public_key="PK1", status="pending_approval")
    db.record_rejection("proj_x", "node_B")
    db.record_rejection("proj_x", "node_B")
    db.record_rejection("proj_x", "node_B")

    row = db.get_collaborator_by_pubkey("proj_x", "PK1")
    assert row["node_uuid"] == "node_B"
    assert row["status"] == "rejected"


def test_record_rejection_is_atomic_against_a_concurrent_rerequest(tmp_path, monkeypatch):
    """
    Review finding (TOCTOU, Task 11 fix round): record_rejection used to perform the
    status update (via a separate call to reject_collaborator, acquiring and releasing
    its own _write_lock) and the rejection-counter increment as TWO separate _write_lock
    acquisitions. A concurrent add_or_request_collaborator landing in the gap between
    them (e.g. a live join_request arriving over the P2P transport) could flip the row
    back to 'pending_approval' while the counter still held its PRE-increment value,
    silently undoing the owner's rejection.

    This test forces exactly that interleaving attempt: it pauses record_rejection
    immediately after its status-update statement (simulating the old gap) and, while
    paused, starts a concurrent add_or_request_collaborator for the same node_uuid.
    With the fix (both statements under one unbroken _write_lock acquisition), that
    concurrent call must BLOCK until record_rejection's entire transaction completes,
    never observing or writing the intermediate state.
    """
    db = DatabaseManager(db_path=str(tmp_path / "t.db"))
    db.create_project(name="p", project_uuid="proj_x", init_git=False)
    db.add_or_request_collaborator("proj_x", "node_1", public_key="PK1", status="pending_approval")

    # sqlite3.Connection is an immutable C type -- its methods can't be monkeypatched
    # directly (`monkeypatch.setattr(sqlite3.Connection, "execute", ...)` raises
    # TypeError). Instead, inject the pause via a Connection subclass passed as
    # `factory=` to sqlite3.connect (DatabaseManager.get_connection() calls
    # sqlite3.connect() with no factory, so patching the module-level sqlite3.connect
    # to always supply ours works transparently for every connection in this test).
    paused = threading.Event()
    resume = threading.Event()

    class _DelayedConnection(sqlite3.Connection):
        def execute(self, sql, params=()):
            result = super().execute(sql, params)
            if "SET status = 'rejected'" in sql:
                paused.set()
                resume.wait(timeout=2)
            return result

    original_connect = sqlite3.connect

    def patched_connect(*args, **kwargs):
        kwargs["factory"] = _DelayedConnection
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", patched_connect)

    rejection_result = {}

    def do_rejection():
        rejection_result["res"] = db.record_rejection("proj_x", "node_1")

    rejecter = threading.Thread(target=do_rejection)
    rejecter.start()
    assert paused.wait(timeout=2), "record_rejection never reached its status-update pause point"

    rerequest_completed = []

    def do_rerequest():
        db.add_or_request_collaborator("proj_x", "node_1", public_key="PK1", status="pending_approval")
        rerequest_completed.append(True)

    rerequester = threading.Thread(target=do_rerequest)
    rerequester.start()
    time.sleep(0.15)
    assert rerequester.is_alive(), "add_or_request_collaborator ran while record_rejection was mid-transaction"
    assert rerequest_completed == []

    resume.set()
    rejecter.join(timeout=2)
    rerequester.join(timeout=2)
    assert not rejecter.is_alive() and not rerequester.is_alive()
    assert rerequest_completed == [True]

    # record_rejection's own result must reflect the fully-incremented counter.
    assert rejection_result["res"]["rejection_count"] == 1
    # The re-request legitimately ran AFTER record_rejection fully committed, so the row
    # is pending_approval again -- but it must carry the fully-incremented count (1),
    # never a stale pre-increment value that a mid-transaction interleaving would leave.
    row = db.get_collaborator("proj_x", "node_1")
    assert row["status"] == "pending_approval"
    assert row["rejection_count"] == 1
