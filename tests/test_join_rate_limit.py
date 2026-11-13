"""
Suite de pruebas para el esquema de rate-limit de solicitudes de colaboración P2P
(join requests), keyed por clave pública Ed25519 persistente del solicitante.
"""

import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

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


def _old_two_step_record_rejection(db, project_uuid, node_uuid, pause_event=None, resume_event=None):
    """
    Reconstrucción FIEL de la implementación ANTERIOR (previa a la ronda de fix de
    Task 11) de record_rejection: el UPDATE de status (vía reject_collaborator, que
    adquiere y LIBERA su propio _write_lock al retornar) y el incremento del contador
    de rechazos corrían como DOS adquisiciones SEPARADAS de _write_lock, con una
    ventana real -- sin el lock sostenido -- entre ambas. Existe únicamente para
    reproducir el bug TOCTOU en un test (ver test_old_two_step_record_rejection_...
    abajo); NO es código de producción -- la producción (db.record_rejection) ya no
    tiene esta estructura.

    pause_event/resume_event, si se proveen, pausan la ejecución JUSTO en esa ventana
    -- después de que reject_collaborator() ya devolvió (lock liberado) y antes de que
    arranque la segunda transacción -- para que un test pueda forzar deliberadamente
    una operación concurrente a correr ahí.
    """
    now = datetime.now(timezone.utc)
    now_str = now.isoformat()
    db.reject_collaborator(project_uuid, node_uuid)
    if pause_event is not None:
        pause_event.set()
        if resume_event is not None:
            resume_event.wait(timeout=2)
    with db._write_lock, db.get_connection() as conn:
        with conn:
            conn.execute(
                """
                UPDATE project_collaborators
                SET rejection_count = rejection_count + 1, last_rejected_at = ?
                WHERE project_uuid = ? AND node_uuid = ?;
                """,
                (now_str, project_uuid, node_uuid)
            )
            row = conn.execute(
                "SELECT rejection_count, last_rejected_at FROM project_collaborators "
                "WHERE project_uuid = ? AND node_uuid = ?;",
                (project_uuid, node_uuid)
            ).fetchone()
    return dict(row) if row else None


def test_old_two_step_record_rejection_is_vulnerable_to_concurrent_rerequest_demotion(tmp_path):
    """
    Review finding (test-quality, Task 11 fix round 2): the previous version of this
    test paused INSIDE the status-update SQL execute() call, which -- for BOTH the old
    two-step structure and the new merged one -- happens while the enclosing `with
    self._write_lock:` block is still active (Python doesn't exit a `with` block until
    the paused statement inside it returns). So that pause point never actually opened
    the lock-released gap the old bug depended on, and the test passed against a
    reconstructed old implementation too -- a false sense of coverage.

    This test fixes that: `_old_two_step_record_rejection` pauses via an explicit
    Python-level hook placed AFTER reject_collaborator() has fully returned (and so
    genuinely released _write_lock) and BEFORE the second transaction acquires it
    again -- the literal historical gap. It proves the bug reproduces: with the lock
    truly free during the pause, a concurrent add_or_request_collaborator for the same
    node_uuid runs to completion immediately (not blocked), re-inserting the row as
    'pending_approval' before the counter increment lands on top of it -- silently
    undoing the owner's rejection.
    """
    db = DatabaseManager(db_path=str(tmp_path / "t.db"))
    db.create_project(name="p", project_uuid="proj_x", init_git=False)
    db.add_or_request_collaborator("proj_x", "node_1", public_key="PK1", status="pending_approval")

    paused = threading.Event()
    resume = threading.Event()
    result = {}

    def do_old_rejection():
        result["row"] = _old_two_step_record_rejection(db, "proj_x", "node_1", paused, resume)

    rejecter = threading.Thread(target=do_old_rejection)
    rejecter.start()
    assert paused.wait(timeout=2), "old-style record_rejection never reached its pause point"

    rerequest_done = threading.Event()

    def do_rerequest():
        db.add_or_request_collaborator("proj_x", "node_1", public_key="PK1", status="pending_approval")
        rerequest_done.set()

    rerequester = threading.Thread(target=do_rerequest)
    rerequester.start()
    # The whole point: with the OLD structure, _write_lock is genuinely free during this
    # pause, so the concurrent re-request is NOT blocked -- it must complete promptly.
    assert rerequest_done.wait(timeout=2), (
        "the concurrent re-request was blocked -- this reconstruction no longer "
        "reproduces the historical released-lock gap"
    )

    resume.set()
    rejecter.join(timeout=2)
    rerequester.join(timeout=2)

    row = db.get_collaborator("proj_x", "node_1")
    # The bug: the owner's rejection is silently lost -- status ends up back at
    # 'pending_approval' even though the rejection counter DID get incremented.
    assert row["status"] == "pending_approval"
    assert row["rejection_count"] == 1


def test_record_rejection_is_atomic_against_a_concurrent_rerequest(tmp_path, monkeypatch):
    """
    Review finding (TOCTOU, Task 11 fix round): record_rejection used to perform the
    status update (via a separate call to reject_collaborator, acquiring and releasing
    its own _write_lock) and the rejection-counter increment as TWO separate _write_lock
    acquisitions. A concurrent add_or_request_collaborator landing in the gap between
    them (e.g. a live join_request arriving over the P2P transport) could flip the row
    back to 'pending_approval' while the counter still held its PRE-increment value,
    silently undoing the owner's rejection -- reproduced directly, against a faithful
    reconstruction of that old structure, by
    test_old_two_step_record_rejection_is_vulnerable_to_concurrent_rerequest_demotion
    above.

    This test exercises the REAL, fixed record_rejection and proves the gap is closed:
    it pauses mid-transaction, immediately after the status-update statement executes
    but still inside the single unbroken _write_lock acquisition that now wraps both
    statements, and, while paused, starts a concurrent add_or_request_collaborator for
    the same node_uuid. That concurrent call must BLOCK (unlike the paired old-code
    test above, where the equivalent operation completes immediately) until
    record_rejection's entire transaction completes, never observing or writing the
    intermediate state.
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


def test_record_rejection_opens_exactly_one_connection_per_call(tmp_path):
    """
    Deterministic, non-timing-dependent structural regression guard, complementing the
    two threading tests above (which rely on real thread scheduling and a specific
    pause point). The fixed record_rejection wraps its status-update and
    rejection-counter-increment statements in a SINGLE `with self._write_lock,
    self.get_connection() as conn:` block -- i.e. exactly one get_connection() /
    _write_lock acquisition per call. The old, vulnerable two-step structure called
    get_connection() TWICE per call (once inside the nested reject_collaborator(),
    once in record_rejection's own second block) -- that second, independent
    acquisition is exactly what let a concurrent write land in between. This test
    fails immediately, with no race window needed, if record_rejection is ever
    restructured back into two separate acquisitions -- verified empirically below by
    running the identical assertion against a reconstruction of the old structure.
    """
    db = DatabaseManager(db_path=str(tmp_path / "t.db"))
    db.create_project(name="p", project_uuid="proj_x", init_git=False)
    db.add_or_request_collaborator("proj_x", "node_1", public_key="PK1", status="pending_approval")

    with patch.object(db, "get_connection", wraps=db.get_connection) as spy:
        db.record_rejection("proj_x", "node_1")
        assert spy.call_count == 1, (
            f"record_rejection opened {spy.call_count} connections/_write_lock "
            "acquisitions, not 1 -- the status-update and counter-increment are no "
            "longer a single atomic transaction, reopening the TOCTOU gap"
        )


def test_old_two_step_record_rejection_opens_two_connections_per_call(tmp_path):
    """
    Proof, demanded by the Task 11 review, that the assertion in
    test_record_rejection_opens_exactly_one_connection_per_call above actually
    DISTINGUISHES the fixed structure from the vulnerable one -- run against
    _old_two_step_record_rejection (the faithful reconstruction of the pre-fix
    implementation), the identical spy-based assertion observes 2 connections, not 1.
    If someone reverted db.record_rejection to this structure, the other test would
    fail with exactly this count.
    """
    db = DatabaseManager(db_path=str(tmp_path / "t.db"))
    db.create_project(name="p", project_uuid="proj_x", init_git=False)
    db.add_or_request_collaborator("proj_x", "node_1", public_key="PK1", status="pending_approval")

    with patch.object(db, "get_connection", wraps=db.get_connection) as spy:
        _old_two_step_record_rejection(db, "proj_x", "node_1")
        assert spy.call_count == 2
