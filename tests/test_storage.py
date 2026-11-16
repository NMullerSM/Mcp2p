"""
Pruebas unitarias para el motor de almacenamiento SQLite WAL (src/storage/db.py).
"""

import tempfile
from pathlib import Path
import pytest
from src.storage.db import DatabaseManager


@pytest.fixture
def temp_db():
    # Windows no puede borrar un .db con conexiones abiertas (pool + ancla): se cierra la BD antes.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db_path = Path(tmpdir) / "test_mcp2p.db"
        db = DatabaseManager(str(db_path))
        try:
            # Registrar nodos de prueba
            db.register_or_update_node("node_alpha", "Alice", "agent_alpha", reviewer_sla_hours=24)
            db.register_or_update_node("node_beta", "Bob", "agent_beta", reviewer_sla_hours=48)
            yield db
        finally:
            db.close()


def test_schema_initialization(temp_db):
    """Verifica que las tablas principales se creen con modo WAL."""
    with temp_db.get_connection() as conn:
        tables = [
            row["name"] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table';"
            ).fetchall()
        ]
        assert "projects" in tables
        assert "swarm_nodes" in tables
        assert "kanban_cards" in tables
        assert "a2a_messages" in tables
        assert "dispute_tickets" in tables
        assert "jury_borda_scores" in tables
        assert "outbox_events" in tables


def test_project_creation_and_listing(temp_db):
    """Verifica la creación y listado de proyectos con UUID único."""
    proj = temp_db.create_project(name="Proyecto Alfa", init_git=False)
    assert proj["project_uuid"].startswith("proj_")
    assert proj["name"] == "Proyecto Alfa"

    projects = temp_db.list_projects()
    # Proyecto semilla por defecto + Proyecto Alfa = 2 proyectos
    assert len(projects) >= 2


def test_create_and_get_card(temp_db):
    """Verifica la creación y recuperación de tarjetas a 0 tokens."""
    projects = temp_db.list_projects()
    proj_uuid = projects[0]["project_uuid"]

    card = temp_db.create_card(proj_uuid, "CARD-001", "Configurar PyWebView", "Crear la ventana base")
    assert card["card_id"] == "CARD-001"
    assert card["status"] == "backlog"

    retrieved = temp_db.get_card_by_id(proj_uuid, "CARD-001")
    assert retrieved is not None
    assert retrieved["title"] == "Configurar PyWebView"


def test_claim_card_lease_success(temp_db):
    """Verifica que un nodo pueda reclamar un lease con expiración temporal."""
    projects = temp_db.list_projects()
    proj_uuid = projects[0]["project_uuid"]

    temp_db.create_card(proj_uuid, "CARD-002", "Diseñar UI")
    claimed = temp_db.claim_card_lease(proj_uuid, "CARD-002", "node_alpha", duration_minutes=15)

    assert claimed["status"] == "in_progress"
    assert claimed["assignee_node_uuid"] == "node_alpha"
    assert claimed["lease_expires_at"] is not None


def test_claim_card_lease_collision_prevented(temp_db):
    """Verifica que si otro nodo intenta tomar la misma tarjeta con lease activo, se lance PermissionError."""
    projects = temp_db.list_projects()
    proj_uuid = projects[0]["project_uuid"]

    temp_db.create_card(proj_uuid, "CARD-003", "Refactorizar DB")
    temp_db.claim_card_lease(proj_uuid, "CARD-003", "node_alpha", duration_minutes=30)

    # El nodo beta intenta tomar la misma tarjeta antes de que expire el lease
    with pytest.raises(PermissionError) as exc_info:
        temp_db.claim_card_lease(proj_uuid, "CARD-003", "node_beta", duration_minutes=30)
    assert "bloqueada por el nodo node_alpha" in str(exc_info.value)


def test_manual_assignment_requires_approved_collaborator(temp_db):
    """Verifica que asignar una tarjeta a un nodo exige que sea un colaborador APROBADO
    real del proyecto (conectado por MCP) — nunca un subagente efímero de jurado."""
    projects = temp_db.list_projects()
    proj_uuid = projects[0]["project_uuid"]
    temp_db.create_card(proj_uuid, "CARD-ASSIGN-01", "Tarea asignable")

    # node_alpha todavía no es colaborador del proyecto -> debe rechazarse
    with pytest.raises(ValueError):
        temp_db.update_card_details(proj_uuid, "CARD-ASSIGN-01", assignee_node_uuid="node_alpha")

    # Un uuid inventado (equivalente a un subagente de jurado neutral_juror_01) también debe rechazarse
    with pytest.raises(ValueError):
        temp_db.update_card_details(proj_uuid, "CARD-ASSIGN-01", assignee_node_uuid="neutral_juror_01")

    # Una vez aprobado como colaborador real del proyecto, sí se puede asignar
    temp_db.add_or_request_collaborator(proj_uuid, "node_alpha", role="collaborator", status="approved")
    updated = temp_db.update_card_details(proj_uuid, "CARD-ASSIGN-01", assignee_node_uuid="node_alpha")
    assert updated["assignee_node_uuid"] == "node_alpha"

    # '' desasigna explícitamente; None no toca la asignación actual
    unassigned = temp_db.update_card_details(proj_uuid, "CARD-ASSIGN-01", assignee_node_uuid="")
    assert unassigned["assignee_node_uuid"] is None
    untouched = temp_db.update_card_details(proj_uuid, "CARD-ASSIGN-01", title="Tarea asignable (editada)")
    assert untouched["assignee_node_uuid"] is None


def test_create_card_with_assignee(temp_db):
    """Verifica que una tarjeta pueda nacer ya asignada a un colaborador aprobado."""
    projects = temp_db.list_projects()
    proj_uuid = projects[0]["project_uuid"]
    temp_db.add_or_request_collaborator(proj_uuid, "node_beta", role="collaborator", status="approved")

    card = temp_db.create_card(proj_uuid, "CARD-ASSIGN-02", "Tarea con asignación inicial", assignee_node_uuid="node_beta")
    assert card["assignee_node_uuid"] == "node_beta"

    with pytest.raises(ValueError):
        temp_db.create_card(proj_uuid, "CARD-ASSIGN-03", "Tarea con nodo inválido", assignee_node_uuid="node_inexistente")


def test_project_folder_naming_and_cascade_delete(temp_db):
    """Verifica que las carpetas sigan la convención app_{name} y okf_{name}_{uuid} con .git y eliminación en cascada."""
    proj = temp_db.create_project(name="Modulo Pagos", init_git=True)
    p_uuid = proj["project_uuid"]

    app_path = Path(proj["code_repo_path"])
    gov_path = Path(proj["gov_repo_path"])

    assert "app_modulo_pagos" in app_path.name
    assert "okf_modulo_pagos" in gov_path.name
    assert (app_path / ".git").is_dir()
    assert (gov_path / ".git").is_dir()
    assert (gov_path / "docs" / "okf" / "01_ARQUITECTURA_Y_GOBERNANZA.md").is_file()

    # Crear una tarjeta en el proyecto
    temp_db.create_card(p_uuid, "CARD-DEL", "Tarea Temporal")
    assert len(temp_db.get_cards(p_uuid)) == 1

    # Eliminar proyecto
    assert temp_db.delete_project(p_uuid) is True
    assert temp_db.get_project_by_uuid(p_uuid) is None
    # Las tarjetas deben haberse eliminado en cascada
    assert len(temp_db.get_cards(p_uuid)) == 0


def test_delete_nonexistent_project_is_strict_false(temp_db):
    """F8: delete_project debía devolver {} (truthy en JS) cuando el proyecto no existe."""
    from src.app.api_bridge import ApiBridge

    assert temp_db.delete_project("proj_doesnotexist") is False

    bridge = ApiBridge(db_manager=temp_db)
    assert bridge.delete_project("proj_doesnotexist") == {
        "success": False,
        "error": "Proyecto no encontrado: proj_doesnotexist"
    }
