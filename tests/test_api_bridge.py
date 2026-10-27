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


def test_api_bridge_a2a_and_memory(bridge):
    """Verifica señales A2A y persistencia de memoria huérfana vía ApiBridge."""
    # 1. Enviar señal informativa A2A
    sig_res = bridge.send_a2a_signal(
        signal_type="METRIC_PULSE",
        data={"memory_mb": 42, "cpu_pct": 5.0}
    )
    assert sig_res["success"] is True
    assert sig_res["packet"]["signal_type"] == "METRIC_PULSE"

    # 2. Consultar feed A2A
    feed_res = bridge.get_a2a_feed()
    assert feed_res["success"] is True
    assert len(feed_res["messages"]) >= 1

    # 3. Snapshot de memoria en rama huérfana
    snap_res = bridge.write_memory_snapshot({
        "summary": "Sesión iniciada con éxito",
        "turn": 1
    })
    assert snap_res["success"] is True

    read_res = bridge.read_memory_snapshot()
    assert read_res["success"] is True
    assert read_res["snapshot"]["summary"] == "Sesión iniciada con éxito"


def test_api_bridge_p2p_invite_and_join_flow(bridge):
    """Verifica la generación de enlace P2P y la conexión automática con replicación de UUID."""
    # 1. Crear proyecto inicial
    p_orig = bridge.create_project(name="Proyecto Distribuidor")
    assert p_orig["success"] is True
    orig_uuid = p_orig["project"]["project_uuid"]

    # 2. Generar invitación P2P
    invite_res = bridge.generate_p2p_invite(orig_uuid)
    assert invite_res["success"] is True
    assert "mcp2p://join?token=" in invite_res["invite_url"]
    assert invite_res["token"]

    # 3. Unirse al proyecto desde otra "instancia" o contexto
    join_res = bridge.join_p2p_project(invite_res["invite_url"])
    assert join_res["success"] is True
    assert join_res["project"]["project_uuid"] == orig_uuid

    # 4. Eliminar proyecto
    del_res = bridge.delete_project(orig_uuid)
    assert del_res["success"] is True
    # Confirmar que ya no existe
    proj_check = bridge.db.get_project_by_uuid(orig_uuid)
    assert proj_check is None


def test_api_bridge_github_pat_storage(bridge, monkeypatch):
    """Verifica el flujo dual de GitHub (PAT y gh CLI fallback)."""
    # 1. Sin PAT configurado y simulando sin gh cli
    bridge.github.gh_bin = None
    status = bridge.get_github_status()
    assert status["success"] is True

    # 2. Guardar PAT simulando respuesta exitosa de GitHub API
    from unittest.mock import MagicMock
    import io
    import urllib.request

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = b'{"login": "octodev", "name": "Octo Developer"}'
    mock_resp.__enter__.return_value = mock_resp

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=8: mock_resp)

    save_res = bridge.save_github_pat("ghp_test1234567890abcdef")
    assert save_res["success"] is True
    assert save_res["username"] == "octodev"

    # Verificar que el status reporta PAT
    status_pat = bridge.get_github_status()
    assert status_pat["success"] is True
    assert status_pat["github"]["authenticated"] is True
    assert status_pat["github"]["provider"] == "pat"
    assert status_pat["github"]["username"] == "octodev"

    # 3. Eliminar PAT
    clear_res = bridge.clear_github_pat()
    assert clear_res["success"] is True
    status_cleared = bridge.get_github_status()
    assert status_cleared["github"]["provider"] != "pat"


def test_api_bridge_okf_repair(bridge):
    """Verifica que repair_project_okf restaure la estructura conforme."""
    p_res = bridge.create_project(name="Proyecto Auto Repair")
    p_uuid = p_res["project"]["project_uuid"]

    repair_res = bridge.repair_project_okf(p_uuid)
    assert repair_res["success"] is True
    assert repair_res["report"]["valid"] is True


def test_api_bridge_branches_and_consensus_and_okf_tree(bridge):
    """Verifica la consulta de ramas Git, sesiones de consenso y árbol OKF."""
    p_res = bridge.create_project(name="Proyecto Git & Consenso")
    p_uuid = p_res["project"]["project_uuid"]

    # 1. Consultar ramas y consenso
    bc_res = bridge.get_branches_and_consensus(p_uuid)
    assert bc_res["success"] is True
    assert "git" in bc_res
    assert "sessions" in bc_res

    # 2. Consultar árbol de archivos OKF
    tree_res = bridge.get_okf_raw_tree(p_uuid)
    assert tree_res["success"] is True
    assert len(tree_res["tree"]) >= 2
    assert any("01_ARQUITECTURA_Y_GOBERNANZA.md" in f["path"] for f in tree_res["tree"])

    # 3. Leer archivo OKF
    file_res = bridge.read_okf_file(p_uuid, "01_ARQUITECTURA_Y_GOBERNANZA.md")
    assert file_res["success"] is True
    assert "OKF-01" in file_res["content"]

    # 4. Actualizar tarjeta con criterios de aceptación
    card_res = bridge.create_kanban_card("TASK-TEST-CRIT", "Tarea con Criterios", "Desc")
    assert card_res["success"] is True

    update_res = bridge.update_kanban_card_details(
        card_id="TASK-TEST-CRIT",
        title="Tarea con Criterios Actualizada",
        acceptance_criteria="1. Tests pasando\n2. Linting OK",
        status="in_review"
    )
    assert update_res["success"] is True
    assert update_res["card"]["acceptance_criteria"] == "1. Tests pasando\n2. Linting OK"
    assert update_res["card"]["status"] == "in_review"


