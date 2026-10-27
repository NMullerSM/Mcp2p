"""
Pruebas unitarias para las herramientas del servidor Model Context Protocol (src/mcp/tools.py y server.py).
"""

import json
import tempfile
from pathlib import Path
import pytest
from src.storage.db import DatabaseManager
from src.mcp.tools import MCPToolsHandler
from src.mcp.server import create_mcp_server


@pytest.fixture
def mcp_handler():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_mcp.db"
        db = DatabaseManager(str(db_path))
        # Registrar nodo de prueba
        db.register_or_update_node("node_alpha", "Alice", "agent_alpha", reviewer_sla_hours=48)
        db.register_or_update_node("node_beta", "Bob", "agent_beta", reviewer_sla_hours=72)
        handler = MCPToolsHandler(db_manager=db)
        yield handler, db


def test_mcp_list_projects(mcp_handler):
    """Verifica que el agente pueda listar proyectos mediante MCP."""
    handler, _ = mcp_handler
    raw = handler.list_projects()
    res = json.loads(raw)
    assert res["success"] is True
    assert len(res["projects"]) >= 1


def test_mcp_create_and_get_kanban_task(mcp_handler):
    """Verifica creación y consulta de tareas Kanban a coste 0 tokens."""
    handler, db = mcp_handler
    projects = db.list_projects()
    proj_uuid = projects[0]["project_uuid"]

    # 1. Crear tarea
    create_raw = handler.create_kanban_task(
        project_uuid=proj_uuid,
        card_id="CARD-MCP-1",
        title="Implementar Endpoint MCP",
        description="Crear handler de tareas"
    )
    create_res = json.loads(create_raw)
    assert create_res["success"] is True
    assert create_res["card"]["card_id"] == "CARD-MCP-1"

    # 2. Consultar tablero
    board_raw = handler.get_kanban_board(project_uuid=proj_uuid)
    board_res = json.loads(board_raw)
    assert board_res["success"] is True
    assert board_res["count"] == 1
    assert board_res["cards"][0]["title"] == "Implementar Endpoint MCP"


def test_mcp_claim_and_collision_prevention(mcp_handler):
    """Verifica adquisición con lease y prevención estricta de colisiones."""
    handler, db = mcp_handler
    projects = db.list_projects()
    proj_uuid = projects[0]["project_uuid"]

    handler.create_kanban_task(proj_uuid, "CARD-MCP-2", "Refactorizar Tokens")

    # Nodo Alpha reclama la tarea
    claim_raw = handler.claim_kanban_task(
        project_uuid=proj_uuid,
        card_id="CARD-MCP-2",
        node_uuid="node_alpha",
        duration_minutes=30
    )
    claim_res = json.loads(claim_raw)
    assert claim_res["success"] is True
    assert claim_res["card"]["status"] == "in_progress"
    assert claim_res["card"]["assignee_node_uuid"] == "node_alpha"

    # Nodo Beta intenta reclamar la misma tarea -> Debe ser rechazado por colisión
    collision_raw = handler.claim_kanban_task(
        project_uuid=proj_uuid,
        card_id="CARD-MCP-2",
        node_uuid="node_beta",
        duration_minutes=30
    )
    collision_res = json.loads(collision_raw)
    assert collision_res["success"] is False
    assert collision_res["code"] == "LEASE_COLLISION"
    assert "bloqueada por el nodo node_alpha" in collision_res["error"]


def test_mcp_release_kanban_task(mcp_handler):
    """Verifica la transición y liberación de una tarea con referencia de rama."""
    handler, db = mcp_handler
    projects = db.list_projects()
    proj_uuid = projects[0]["project_uuid"]

    handler.create_kanban_task(proj_uuid, "CARD-MCP-3", "Test Release")
    handler.claim_kanban_task(proj_uuid, "CARD-MCP-3", "node_alpha", duration_minutes=20)

    # Liberar a in_review con branch_ref
    release_raw = handler.release_kanban_task(
        project_uuid=proj_uuid,
        card_id="CARD-MCP-3",
        node_uuid="node_alpha",
        target_status="in_review",
        branch_ref="feature/mcp-test"
    )
    release_res = json.loads(release_raw)
    assert release_res["success"] is True
    assert release_res["card"]["status"] == "in_review"
    assert release_res["card"]["branch_ref"] == "feature/mcp-test"


def test_mcp_project_context(mcp_handler):
    """Verifica que el agente pueda inspeccionar el contexto de doble repositorio de su proyecto."""
    handler, db = mcp_handler
    projects = db.list_projects()
    proj_uuid = projects[0]["project_uuid"]

    context_raw = handler.get_project_context(proj_uuid)
    context_res = json.loads(context_raw)
    assert context_res["success"] is True
    assert context_res["orphan_branch"] == f"orphan-conversa-memory-{proj_uuid}"
    assert "code_repo_path" in context_res["project"]
    assert "gov_repo_path" in context_res["project"]


def test_mcp_server_initialization():
    """Verifica que el servidor MCPServer registre todas las herramientas sin errores."""
    server = create_mcp_server()
    assert server.name == "mcp2p-server"
    tool_names = [t.name for t in server._tool_manager.list_tools()]
    assert "list_projects" in tool_names
    assert "get_kanban_board" in tool_names
    assert "claim_kanban_task" in tool_names
    assert "release_kanban_task" in tool_names
    assert "create_kanban_task" in tool_names
    assert "get_project_context" in tool_names
    assert "register_agent_node" in tool_names
    assert "send_coordination_rfc" in tool_names
    assert "get_coordination_session" in tool_names
    assert "inspect_okf_governance" in tool_names


def test_mcp_coordination_and_okf_tools(mcp_handler):
    """Verifica las herramientas MCP de coordinación dialéctica e inspección OKF."""
    handler, db = mcp_handler
    projects = db.list_projects()
    proj_uuid = projects[0]["project_uuid"]

    # 1. Enviar turno 1: PROPOSAL
    proposal_data = {
        "component": "cache",
        "intent": "Implementar LRU en memoria",
        "rationale": "Reducir I/O",
        "diff_ref": "diff-cache-1",
        "impact": "Bajo"
    }
    raw_p1 = handler.send_coordination_rfc(
        project_uuid=proj_uuid,
        sender_node_uuid="node_alpha",
        stage="PROPOSAL",
        session_id="session-rfc-1",
        payload_json=json.dumps(proposal_data)
    )
    res_p1 = json.loads(raw_p1)
    assert res_p1["success"] is True

    # 2. Consultar turnos de la sesión
    raw_sess = handler.get_coordination_session(proj_uuid, "session-rfc-1")
    res_sess = json.loads(raw_sess)
    assert res_sess["success"] is True
    assert res_sess["turns_count"] == 1

    # 3. Inspeccionar gobernanza OKF
    raw_gov = handler.inspect_okf_governance(proj_uuid)
    res_gov = json.loads(raw_gov)
    assert res_gov["success"] is True
    assert "validation" in res_gov
