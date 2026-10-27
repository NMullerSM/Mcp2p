"""
Pruebas unitarias para el motor de almacenamiento SQLite WAL (src/storage/db.py).
"""

import tempfile
from pathlib import Path
import pytest
from src.storage.db import DatabaseManager


@pytest.fixture
def temp_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_mcp2p.db"
        db = DatabaseManager(str(db_path))
        yield db


def test_schema_initialization(temp_db):
    """Verifica que las tablas principales se creen con modo WAL."""
    with temp_db.get_connection() as conn:
        tables = [
            row["name"] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table';"
            ).fetchall()
        ]
        assert "swarm_nodes" in tables
        assert "kanban_cards" in tables
        assert "a2a_messages" in tables
        assert "dispute_tickets" in tables
        assert "jury_borda_scores" in tables
        assert "outbox_events" in tables


def test_create_and_get_card(temp_db):
    """Verifica la creación y recuperación de tarjetas a 0 tokens."""
    card = temp_db.create_card("CARD-001", "Configurar PyWebView", "Crear la ventana base")
    assert card["card_id"] == "CARD-001"
    assert card["status"] == "backlog"

    retrieved = temp_db.get_card_by_id("CARD-001")
    assert retrieved is not None
    assert retrieved["title"] == "Configurar PyWebView"


def test_claim_card_lease_success(temp_db):
    """Verifica que un nodo pueda reclamar un lease con expiración temporal."""
    temp_db.create_card("CARD-002", "Diseñar UI")
    claimed = temp_db.claim_card_lease("CARD-002", "node_alpha", duration_minutes=15)

    assert claimed["status"] == "in_progress"
    assert claimed["assignee_node_uuid"] == "node_alpha"
    assert claimed["lease_expires_at"] is not None


def test_claim_card_lease_collision_prevented(temp_db):
    """Verifica que si otro nodo intenta tomar la misma tarjeta con lease activo, se lance PermissionError."""
    temp_db.create_card("CARD-003", "Refactorizar DB")
    temp_db.claim_card_lease("CARD-003", "node_alpha", duration_minutes=30)

    # El nodo beta intenta tomar la misma tarjeta antes de que expire el lease
    with pytest.raises(PermissionError) as exc_info:
        temp_db.claim_card_lease("CARD-003", "node_beta", duration_minutes=30)
    assert "bloqueada por el nodo node_alpha" in str(exc_info.value)


def test_swarm_nodes_registration(temp_db):
    """Verifica el registro de nodos con SLA del revisor autodeclarado."""
    node = temp_db.register_or_update_node(
        node_uuid="node_alpha",
        developer_name="Nathaniel",
        agent_id="agent_alpha_v1",
        reviewer_sla_hours=48,
        status="online"
    )
    assert node["node_uuid"] == "node_alpha"
    assert node["reviewer_sla_hours"] == 48

    nodes = temp_db.get_swarm_nodes()
    assert len(nodes) == 1
    assert nodes[0]["developer_name"] == "Nathaniel"
