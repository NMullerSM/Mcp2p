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


@pytest.mark.anyio
async def test_mcp_server_resilience_aliases_and_json_blobs(mcp_handler):
    """Verifica que el servidor MCP tolere camelCase (sessionId, cardId, task_id) y objetos dict/list."""
    handler, db = mcp_handler
    server = create_mcp_server(db_path=db.db_path)
    projects = db.list_projects()
    proj_uuid = projects[0]["project_uuid"]

    # 1. create_kanban_task con task_id
    res1 = await server.call_tool("create_kanban_task", {
        "project_uuid": proj_uuid,
        "task_id": "CARD-RESILIENCE-01",
        "title": "Tarea Resiliente"
    })
    assert res1.is_error is False
    c1 = json.loads(res1.content[0].text)
    assert c1["success"] is True
    assert c1["card"]["card_id"] == "CARD-RESILIENCE-01"

    # 2. send_coordination_rfc con sessionId en camelCase y payload como dict
    res2 = await server.call_tool("send_coordination_rfc", {
        "project_uuid": proj_uuid,
        "sender_node_uuid": "node_alpha",
        "stage": "PROPOSAL",
        "sessionId": "session_camel_case_1",
        "payload_json": {"component": "audit", "intent": "Verificar tolerancia", "rationale": "Validación"}
    })
    assert res2.is_error is False
    c2 = json.loads(res2.content[0].text)
    assert c2["success"] is True

    # 3. send_coordination_rfc con session_id inferido del payload dict
    res3 = await server.call_tool("send_coordination_rfc", {
        "project_uuid": proj_uuid,
        "sender_node_uuid": "node_alpha",
        "stage": "PROPOSAL",
        "payload_json": {"session_id": "session_in_payload_dict", "component": "audit", "intent": "Inferencia", "rationale": "Validación"}
    })
    assert res3.is_error is False
    c3 = json.loads(res3.content[0].text)
    assert c3["success"] is True

    # 4. propose_or_apply_task_distribution con sessionId camelCase y dict plan
    res4 = await server.call_tool("propose_or_apply_task_distribution", {
        "project_uuid": proj_uuid,
        "sender_node_uuid": "node_alpha",
        "sessionId": "session_dist_camel",
        "distribution_plan_json": {"CARD-RESILIENCE-01": "node_alpha"},
        "apply_immediately": True
    })
    assert res4.is_error is False
    c4 = json.loads(res4.content[0].text)
    assert c4["success"] is True
    assert c4["session_id"] == "session_dist_camel"


@pytest.mark.anyio
async def test_mcp_strict_schema_validations_and_dispute_ticket(mcp_handler):
    """Valida los hallazgos de auditoría: esquemas estrictos sin defaults silenciosos y tickets de disputa."""
    handler, db = mcp_handler
    server = create_mcp_server(db_path=db.db_path)
    projects = db.list_projects()
    proj_uuid = projects[0]["project_uuid"]

    # 1. send_coordination_rfc con payload incompleto en PROPOSAL debe fallar explícitamente
    res_incomplete_prop = await server.call_tool("send_coordination_rfc", {
        "project_uuid": proj_uuid,
        "sender_node_uuid": "node_alpha",
        "stage": "PROPOSAL",
        "session_id": "sess_inval_1",
        "payload_json": {"component": "only_comp"}
    })
    err_prop = json.loads(res_incomplete_prop.content[0].text)
    assert err_prop["success"] is False
    assert "campos obligatorios" in err_prop["error"]

    # 2. send_coordination_rfc con remitente desconocido debe ser rechazado
    res_unknown_node = await server.call_tool("send_coordination_rfc", {
        "project_uuid": proj_uuid,
        "sender_node_uuid": "hacker_node_999",
        "stage": "PROPOSAL",
        "session_id": "sess_inval_2",
        "payload_json": {"component": "core", "intent": "bad", "rationale": "why"}
    })
    err_node = json.loads(res_unknown_node.content[0].text)
    assert err_node["success"] is False
    assert "no está registrado en el enjambre" in err_node["error"]

    # 3. run_okf_micro_script con promote_decision sin target_state debe fallar explícitamente
    res_promote_no_state = await server.call_tool("run_okf_micro_script", {
        "project_uuid": proj_uuid,
        "script_action": "promote_decision",
        "params_json": {"decision_id": "DEC-TEST-01"}
    })
    err_promote = json.loads(res_promote_no_state.content[0].text)
    assert err_promote["success"] is False
    assert "target_state es requerido" in err_promote["error"]

    # 4. inspect_pr_and_recommend con RECOMMEND_REJECT debe ser admitido
    res_reject_rec = await server.call_tool("inspect_pr_and_recommend", {
        "project_uuid": proj_uuid,
        "pr_id_or_branch": "pr-101",
        "reviewer_node_uuid": "node_alpha",
        "recommendation": "RECOMMEND_REJECT",
        "analysis_summary": "Rechazo por regresión crítica",
        "suggested_reply": "Favor corregir tests antes de merge"
    })
    rec_out = json.loads(res_reject_rec.content[0].text)
    assert rec_out["success"] is True

    # 5. release_kanban_task permitiendo escalado a 'disputed' por nodo no asignado
    await server.call_tool("create_kanban_task", {
        "project_uuid": proj_uuid,
        "card_id": "CARD-DISPUTE-TEST",
        "title": "Tarea en Disputa"
    })
    await server.call_tool("claim_kanban_task", {
        "project_uuid": proj_uuid,
        "card_id": "CARD-DISPUTE-TEST",
        "node_uuid": "node_alpha"
    })
    # Nodo Beta (no asignado) intenta marcar 'merged' -> denegado
    res_unauth_merged = await server.call_tool("release_kanban_task", {
        "project_uuid": proj_uuid,
        "card_id": "CARD-DISPUTE-TEST",
        "node_uuid": "node_beta",
        "target_status": "merged"
    })
    unauth_out = json.loads(res_unauth_merged.content[0].text)
    assert unauth_out["success"] is False
    assert unauth_out["code"] == "PERMISSION_DENIED"

    # Nodo Beta (no asignado) escala a 'disputed' -> PERMITIDO
    res_disputed = await server.call_tool("release_kanban_task", {
        "project_uuid": proj_uuid,
        "card_id": "CARD-DISPUTE-TEST",
        "node_uuid": "node_beta",
        "target_status": "disputed"
    })
    disputed_out = json.loads(res_disputed.content[0].text)
    assert disputed_out["success"] is True
    assert disputed_out["card"]["status"] == "disputed"

    # 6. open_dispute_ticket alcanzable vía MCP para el Tribunal de Honor
    res_ticket = await server.call_tool("open_dispute_ticket", {
        "project_uuid": proj_uuid,
        "title": "Conflicto de arquitectura en Cache",
        "collision_reason": "Divergencia entre Redis vs SQLite en memoria",
        "contender_a_node": "node_alpha",
        "contender_b_node": "node_beta",
        "proposal_a_ref": "branch-alpha",
        "proposal_b_ref": "branch-beta",
        "card_id": "CARD-DISPUTE-TEST"
    })
    ticket_out = json.loads(res_ticket.content[0].text)
    assert ticket_out["success"] is True
    assert "ticket_id" in ticket_out["ticket"]
    matrix = json.loads(ticket_out["ticket"]["jury_matrix_json"])
    assert len(matrix) == 3


@pytest.mark.anyio
async def test_mcp_v2_audited_edge_cases(mcp_handler, tmp_path):
    """
    Cubre los requerimientos V2.3, V2.4 y V2.5 del Plan de Reparación V2:
    - V2.3: manage_task_worktree vía MCP con files_scope_json doblemente codificado.
    - V2.4: send_coordination_rfc con campos faltantes o enums fuera de rango en COUNTER_PROPOSAL y RESOLUTION.
    - V2.5: run_okf_micro_script con artifact_type inválido o campos faltantes en scaffold_decision.
    """
    handler, db = mcp_handler
    server = create_mcp_server(db_path=db.db_path)

    # Crear proyecto con git para probar manage_task_worktree
    git_proj = db.create_project(
        name="V2 Edge Cases Project",
        base_path=str(tmp_path / "ws_v2_edge"),
        init_git=True
    )
    p_uuid = git_proj["project_uuid"]

    # --- V2.3: manage_task_worktree con scope doble-serializado ---
    double_encoded_scope = json.dumps(json.dumps(["src/storage/*"]))
    res_wt = await server.call_tool("manage_task_worktree", {
        "project_uuid": p_uuid,
        "card_id": "CARD-V2-01",
        "node_uuid": "node_alpha",
        "action": "create",
        "files_scope_json": double_encoded_scope
    })
    wt_out = json.loads(res_wt.content[0].text)
    assert wt_out["success"] is True
    assert Path(wt_out["worktree_path"]).exists()

    # Limpiar worktree
    res_wt_clean = await server.call_tool("manage_task_worktree", {
        "project_uuid": p_uuid,
        "card_id": "CARD-V2-01",
        "node_uuid": "node_alpha",
        "action": "remove"
    })
    wt_clean_out = json.loads(res_wt_clean.content[0].text)
    assert wt_clean_out["success"] is True

    # --- V2.4: send_coordination_rfc validaciones de esquema estricto ---
    # COUNTER_PROPOSAL sin critique
    res_cp_no_crit = await server.call_tool("send_coordination_rfc", {
        "project_uuid": p_uuid,
        "sender_node_uuid": "node_alpha",
        "stage": "COUNTER_PROPOSAL",
        "session_id": "sess_v2_1",
        "payload_json": {"verdict_signal": "ACCEPT"}
    })
    err_cp_crit = json.loads(res_cp_no_crit.content[0].text)
    assert err_cp_crit["success"] is False
    assert "critique" in err_cp_crit["error"]

    # COUNTER_PROPOSAL con verdict_signal inválido
    res_cp_bad_signal = await server.call_tool("send_coordination_rfc", {
        "project_uuid": p_uuid,
        "sender_node_uuid": "node_alpha",
        "stage": "COUNTER_PROPOSAL",
        "session_id": "sess_v2_1",
        "payload_json": {"critique": "Revisar arquitectura", "verdict_signal": "MAYBE"}
    })
    err_cp_sig = json.loads(res_cp_bad_signal.content[0].text)
    assert err_cp_sig["success"] is False
    assert "verdict_signal" in err_cp_sig["error"]

    # RESOLUTION sin synthesis_notes
    res_res_no_synth = await server.call_tool("send_coordination_rfc", {
        "project_uuid": p_uuid,
        "sender_node_uuid": "node_alpha",
        "stage": "RESOLUTION",
        "session_id": "sess_v2_1",
        "payload_json": {"final_decision": "AGREED"}
    })
    err_res_synth = json.loads(res_res_no_synth.content[0].text)
    assert err_res_synth["success"] is False
    assert "synthesis_notes" in err_res_synth["error"]

    # RESOLUTION con final_decision inválido
    res_res_bad_dec = await server.call_tool("send_coordination_rfc", {
        "project_uuid": p_uuid,
        "sender_node_uuid": "node_alpha",
        "stage": "RESOLUTION",
        "session_id": "sess_v2_1",
        "payload_json": {"final_decision": "UNDECIDED", "synthesis_notes": "No llegamos a acuerdo"}
    })
    err_res_dec = json.loads(res_res_bad_dec.content[0].text)
    assert err_res_dec["success"] is False
    assert "final_decision" in err_res_dec["error"]

    # --- V2.5: run_okf_micro_script con artifact_type inválido o campos faltantes ---
    res_scaffold_bad_type = await server.call_tool("run_okf_micro_script", {
        "project_uuid": p_uuid,
        "script_action": "scaffold_decision",
        "params_json": {
            "artifact_type": "invalid_type",
            "item_id": "XYZ-01",
            "title": "Prueba",
            "content": "Contenido"
        }
    })
    err_scaffold_type = json.loads(res_scaffold_bad_type.content[0].text)
    assert err_scaffold_type["success"] is False
    assert "artifact_type es requerido y debe ser uno de" in err_scaffold_type["error"]

    res_scaffold_missing_content = await server.call_tool("run_okf_micro_script", {
        "project_uuid": p_uuid,
        "script_action": "scaffold_decision",
        "params_json": {
            "artifact_type": "decision",
            "item_id": "DEC-V2-99",
            "title": "Decisión Incompleta"
        }
    })
    err_scaffold_content = json.loads(res_scaffold_missing_content.content[0].text)
    assert err_scaffold_content["success"] is False
    assert "scaffold_decision requiere los campos" in err_scaffold_content["error"]

