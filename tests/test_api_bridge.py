"""
Pruebas unitarias para el puente JavaScript API (src/app/api_bridge.py).
"""

import tempfile
from pathlib import Path
import pytest
from src.storage.db import DatabaseManager
from src.app.api_bridge import ApiBridge


@pytest.fixture
def bridge():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_bridge.db"
        db = DatabaseManager(str(db_path))
        bridge_instance = ApiBridge(db_manager=db)
        yield bridge_instance


def test_api_bridge_system_info(bridge):
    """Verifica que el puente retorne la información del sistema y del proyecto activo."""
    info = bridge.get_system_info()
    assert info["success"] is True
    assert "os" in info
    assert info["wal_mode"] is True
    assert info["active_project"] is not None


def test_api_bridge_project_lifecycle(bridge):
    """Verifica la creación y selección de proyectos."""
    create_res = bridge.create_project(name="Microservicio Auth")
    assert create_res["success"] is True
    assert create_res["project"]["name"] == "Microservicio Auth"

    projects_res = bridge.list_projects()
    assert projects_res["success"] is True
    assert len(projects_res["projects"]) >= 2


def test_api_bridge_kanban_flow(bridge):
    """Verifica creación, consulta y actualización de tarjetas a través del puente."""
    # 1. Crear tarjeta en proyecto activo
    create_res = bridge.create_kanban_card("CARD-100", "Probar Bridge", "Prueba de integración")
    assert create_res["success"] is True
    assert create_res["card"]["card_id"] == "CARD-100"

    # 2. Consultar tablero
    board_res = bridge.get_kanban_board()
    assert board_res["success"] is True
    assert len(board_res["cards"]) == 1

    # 3. Adquirir lease
    node_id = bridge.get_system_info()["active_node"]
    claim_res = bridge.claim_task_lease("CARD-100", node_id, duration_minutes=20)
    assert claim_res["success"] is True
    assert claim_res["card"]["status"] == "in_progress"

    # 4. Actualizar estado
    update_res = bridge.update_task_status("CARD-100", "in_review", branch_ref="feature/bridge-test")
    assert update_res["success"] is True
    assert update_res["card"]["status"] == "in_review"
    assert update_res["card"]["branch_ref"] == "feature/bridge-test"
