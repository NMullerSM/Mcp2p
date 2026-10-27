import tempfile
import uuid
from pathlib import Path
import pytest

from src.storage.db import DatabaseManager
from src.a2a.bus import A2ABus, A2AIntent, CoordinationTurn


@pytest.fixture
def bus_and_db(tmp_path: Path):
    db_file = tmp_path / "test_a2a.db"
    db = DatabaseManager(db_path=db_file)
    project = db.create_project(
        name="Proyecto A2A",
        base_path=str(tmp_path / "workspace_a2a"),
        init_git=False
    )
    # Registrar nodo emisor
    db.register_or_update_node(
        node_uuid="node_alpha",
        developer_name="Alice",
        agent_id="agent_alpha",
        reviewer_sla_hours=24
    )
    # Registrar nodo receptor
    db.register_or_update_node(
        node_uuid="node_beta",
        developer_name="Bob",
        agent_id="agent_beta",
        reviewer_sla_hours=48
    )
    bus = A2ABus(db=db)
    return bus, db, project["project_uuid"]


def test_informative_binary_packing():
    raw_data = {"event": "HEARTBEAT", "status": "online", "metrics": {"cpu": 12.5}}
    packed = A2ABus.pack_binary(raw_data)
    assert isinstance(packed, bytes)
    unpacked = A2ABus.unpack_binary(packed)
    assert unpacked == raw_data


def test_send_informative_persists_and_enqueues(bus_and_db):
    bus, db, project_uuid = bus_and_db
    events_received = []

    def subscriber(evt):
        events_received.append(evt)

    bus.subscribe(A2AIntent.INFORMATIVE.value, subscriber)

    packet = bus.send_informative(
        project_uuid=project_uuid,
        sender_node_uuid="node_alpha",
        signal_type="TASK_CLAIMED",
        data={"card_id": "c-101", "lease_min": 30},
        card_id="c-101"
    )

    assert packet["intent"] == "INFORMATIVE"
    assert packet["signal_type"] == "TASK_CLAIMED"
    assert len(events_received) == 1
    assert events_received[0]["message_uuid"] == packet["message_uuid"]

    # Verificar persistencia en a2a_messages
    recent = db.get_recent_a2a_messages(project_uuid)
    assert len(recent) == 1
    assert recent[0]["message_uuid"] == packet["message_uuid"]

    # Verificar encolado en outbox_events
    outbox = db.get_pending_outbox_events(project_uuid)
    assert len(outbox) == 1
    assert outbox[0]["event_uuid"] == packet["message_uuid"]
    assert outbox[0]["sync_status"] == "pending"


def test_coordination_three_turn_rfc_lifecycle(bus_and_db):
    bus, db, project_uuid = bus_and_db
    session_id = str(uuid.uuid4())

    # Turno 2 sin Turno 1 debe fallar
    with pytest.raises(ValueError, match="No existe un Turno 1"):
        bus.send_counter_proposal(
            project_uuid=project_uuid,
            sender_node_uuid="node_beta",
            session_id=session_id,
            critique="Falta modularidad",
            alternative={"component": "storage", "approach": "wal"},
            verdict_signal="REJECT"
        )

    # Turno 1: Proposal
    p1 = bus.send_proposal(
        project_uuid=project_uuid,
        sender_node_uuid="node_alpha",
        session_id=session_id,
        component="db_adapter",
        intent="Optimizar lecturas WAL",
        rationale="Reducir latencia bajo 1ms",
        diff_ref="diff-abc-123",
        impact="Alto rendimiento en UI"
    )
    assert p1["payload"]["turn"] == 1
    assert p1["payload"]["stage"] == "PROPOSAL"

    # Turno 3 sin Turno 2 debe fallar
    with pytest.raises(ValueError, match="requiere haber completado Turno 1 y Turno 2"):
        bus.send_resolution(
            project_uuid=project_uuid,
            sender_node_uuid="node_alpha",
            session_id=session_id,
            final_decision="AGREED",
            synthesis_notes="Acuerdo mutuo"
        )

    # Turno 2: Counter Proposal
    p2 = bus.send_counter_proposal(
        project_uuid=project_uuid,
        sender_node_uuid="node_beta",
        session_id=session_id,
        critique="Sugerir añadir synchronous=NORMAL para mayor throughput",
        alternative={"component": "db_adapter", "pragma": "synchronous=NORMAL"},
        verdict_signal="COMPROMISE"
    )
    assert p2["payload"]["turn"] == 2
    assert p2["payload"]["verdict_signal"] == "COMPROMISE"

    # Turno 3: Resolution
    p3 = bus.send_resolution(
        project_uuid=project_uuid,
        sender_node_uuid="node_alpha",
        session_id=session_id,
        final_decision="AGREED",
        synthesis_notes="Aceptada la sugerencia de Bob para synchronous=NORMAL",
        agreed_branch="feature/wal-optimization"
    )
    assert p3["payload"]["turn"] == 3
    assert p3["payload"]["final_decision"] == "AGREED"
    assert p3["payload"]["agreed_branch"] == "feature/wal-optimization"

    # Verificar historial completo de turnos
    turns = bus.get_session_turns(project_uuid, session_id)
    assert len(turns) == 3
    assert [t["turn"] for t in turns] == [1, 2, 3]


def test_coordination_validation_errors(bus_and_db):
    bus, db, project_uuid = bus_and_db
    session_id = str(uuid.uuid4())

    bus.send_proposal(
        project_uuid=project_uuid,
        sender_node_uuid="node_alpha",
        session_id=session_id,
        component="auth",
        intent="jwt",
        rationale="p2p",
        diff_ref="diff-1",
        impact="low"
    )

    with pytest.raises(ValueError, match="verdict_signal inválido"):
        bus.send_counter_proposal(
            project_uuid=project_uuid,
            sender_node_uuid="node_beta",
            session_id=session_id,
            critique="malo",
            alternative={},
            verdict_signal="INVALID_SIGNAL"
        )
