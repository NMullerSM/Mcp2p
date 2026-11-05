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


@pytest.mark.anyio
async def test_mcp_v3_dispute_discovery_and_versioning(mcp_handler):
    """
    Valida los requerimientos del Plan de Reparación V3:
    - V3.2: release_kanban_task(target_status="disputed") dispara automáticamente open_dispute_ticket.
    - V3.3: Herramientas de descubrimiento: get_dispute_ticket (por ticket_id y card_id) y list_dispute_tickets.
    - V3.4: get_server_version retorna versión, build y herramientas registradas.
    """
    handler, db = mcp_handler
    server = create_mcp_server(db_path=db.db_path)
    projects = db.list_projects()
    proj_uuid = projects[0]["project_uuid"]

    # 1. V3.4: get_server_version
    res_ver = await server.call_tool("get_server_version", {})
    ver_out = json.loads(res_ver.content[0].text)
    assert ver_out["success"] is True
    assert ver_out["server_version"] == "0.3.0"
    assert ver_out["tools_count"] == 26
    assert "edit_kanban_task" in ver_out["tools"]

    # 2. V3.2: Crear card, asignarla y transicionarla a disputed con creación automática de ticket
    card_id = "CARD-V3-DISPUTE"
    await server.call_tool("create_kanban_task", {
        "project_uuid": proj_uuid,
        "card_id": card_id,
        "title": "Refactor de Conexiones P2P"
    })
    await server.call_tool("claim_kanban_task", {
        "project_uuid": proj_uuid,
        "card_id": card_id,
        "node_uuid": "node_alpha"
    })

    # Nodo revisor (node_beta) escala a 'disputed' con motivo
    res_dispute = await server.call_tool("release_kanban_task", {
        "project_uuid": proj_uuid,
        "card_id": card_id,
        "node_uuid": "node_beta",
        "target_status": "disputed",
        "dispute_reason": "Divergencia entre WebRTC vs HTTP polling"
    })
    disp_out = json.loads(res_dispute.content[0].text)
    assert disp_out["success"] is True
    assert disp_out["card"]["status"] == "disputed"
    assert "dispute_ticket" in disp_out
    ticket_data = disp_out["dispute_ticket"]
    t_id = ticket_data["ticket_id"]
    assert ticket_data["card_id"] == card_id
    assert "Divergencia entre WebRTC vs HTTP polling" in ticket_data["collision_reason"]

    # 3. V3.3: Descubrimiento con get_dispute_ticket por card_id
    res_get_by_card = await server.call_tool("get_dispute_ticket", {
        "project_uuid": proj_uuid,
        "card_id": card_id
    })
    card_ticket_out = json.loads(res_get_by_card.content[0].text)
    assert card_ticket_out["success"] is True
    assert card_ticket_out["ticket"]["ticket_id"] == t_id
    assert card_ticket_out["ticket"]["contender_a_node"] == "node_alpha"
    assert card_ticket_out["ticket"]["contender_b_node"] == "node_beta"

    # 4. V3.3: Descubrimiento con get_dispute_ticket por ticket_id
    res_get_by_id = await server.call_tool("get_dispute_ticket", {
        "project_uuid": proj_uuid,
        "ticket_id": t_id
    })
    id_ticket_out = json.loads(res_get_by_id.content[0].text)
    assert id_ticket_out["success"] is True
    assert id_ticket_out["ticket"]["card_id"] == card_id

    # 5. V3.3: list_dispute_tickets
    res_list = await server.call_tool("list_dispute_tickets", {
        "project_uuid": proj_uuid,
        "status": "open"
    })
    list_out = json.loads(res_list.content[0].text)
    assert list_out["success"] is True
    assert list_out["count"] >= 1
    found = any(t["ticket_id"] == t_id for t in list_out["tickets"])
    assert found is True


@pytest.mark.anyio
async def test_mcp_v4_dynamic_version_and_transport(mcp_handler):
    """
    Valida el requerimiento V4.1 del Plan de Reparación V4:
    - transport dinámico reportado según la configuración del servidor (stdio vs sse).
    - tools_count calculado dinámicamente según las tools registradas en tiempo de ejecución.
    - listado dinámico de herramientas en el payload sin literales hardcodeados.
    """
    handler, db = mcp_handler

    # 1. Servidor iniciado con transport="sse"
    server_sse = create_mcp_server(db_path=db.db_path, transport="sse")
    res_sse = await server_sse.call_tool("get_server_version", {})
    sse_out = json.loads(res_sse.content[0].text)
    assert sse_out["success"] is True
    assert sse_out["transport"] == "sse"
    assert sse_out["tools_count"] == 26
    assert "edit_kanban_task" in sse_out["tools"]
    assert isinstance(sse_out.get("tools"), list)
    assert len(sse_out["tools"]) == 26

    # 2. Servidor estándar con transport="stdio" por defecto
    server_stdio = create_mcp_server(db_path=db.db_path)
    res_stdio = await server_stdio.call_tool("get_server_version", {})
    stdio_out = json.loads(res_stdio.content[0].text)
    assert stdio_out["success"] is True
    assert stdio_out["transport"] == "stdio"
    assert stdio_out["tools_count"] == 26

    # 3. Llamada directa a MCPToolsHandler.get_server_version() sin servidor envolvente
    # Debe calcular tools_count inspeccionando los métodos de herramientas de la clase dinámicamente
    direct_out = json.loads(handler.get_server_version())
    assert direct_out["success"] is True
    assert direct_out["tools_count"] >= 20


@pytest.mark.anyio
async def test_mcp_dynamic_jury_and_orphan_memory(mcp_handler, tmp_path):
    """
    Valida las mejoras de Jurado Dinámico y Memoria Huérfana (Plan de Reparación Jurado Dinámico):
    1. Catálogo extendido FOCUS_CATALOG (10 vectores técnicos con tags de dominio).
    2. Apertura de disputa con cálculo de nodos reales y sesgo de dominio.
    3. get_jury_evaluation_prompt retornando paquetes Clean-Room para subagentes evaluadores.
    4. Ocultamiento de puntuaciones numéricas en get_dispute_ticket antes del voto humano (anti-sesgo cognitivo).
    5. sync_coordination_memory volcando outbox events a la rama huérfana sin interfaz gráfica.
    """
    handler, db = mcp_handler
    server = create_mcp_server(db_path=db.db_path)

    # 1. Verificar catálogo FOCUS_CATALOG
    from src.a2a.jury_engine import FOCUS_CATALOG
    assert len(FOCUS_CATALOG) == 10
    assert "TESTABILIDAD_Y_QA" in FOCUS_CATALOG
    assert "UX_HICKS_LAW_ACCESIBILIDAD" in FOCUS_CATALOG
    assert "INTEGRIDAD_DATOS_Y_ACID" in FOCUS_CATALOG
    assert "testing" in FOCUS_CATALOG["TESTABILIDAD_Y_QA"]["domains"]

    # Crear proyecto de prueba con git para soportar rama huérfana
    git_proj = db.create_project(
        name="Jury Dynamic Project",
        base_path=str(tmp_path / "ws_jury_dyn"),
        init_git=True
    )
    p_uuid = git_proj["project_uuid"]

    # Registrar 3 colaboradores para activar regla de N>=3
    db.add_or_request_collaborator(p_uuid, "node_alpha", role="owner")
    db.approve_collaborator(p_uuid, "node_alpha")
    db.add_or_request_collaborator(p_uuid, "node_beta", role="collaborator")
    db.approve_collaborator(p_uuid, "node_beta")
    db.add_or_request_collaborator(p_uuid, "node_gamma", role="collaborator")
    db.approve_collaborator(p_uuid, "node_gamma")

    # 2. Abrir disputa técnica sobre testing / QA
    res_ticket = await server.call_tool("open_dispute_ticket", {
        "project_uuid": p_uuid,
        "title": "Disputa sobre fixtures y tests unitarios",
        "collision_reason": "Desacuerdo en mockeo de red vs tests deterministas",
        "contender_a_node": "node_alpha",
        "contender_b_node": "node_beta",
        "proposal_a_ref": "branch-alpha-unit-tests",
        "proposal_b_ref": "branch-beta-integration"
    })
    ticket_out = json.loads(res_ticket.content[0].text)
    assert ticket_out["success"] is True
    t_id = ticket_out["ticket"]["ticket_id"]
    matrix = json.loads(ticket_out["ticket"]["jury_matrix_json"])
    assert len(matrix) >= 3

    # 3. get_jury_evaluation_prompt
    res_prompt = await server.call_tool("get_jury_evaluation_prompt", {
        "project_uuid": p_uuid,
        "ticket_id": t_id
    })
    prompt_out = json.loads(res_prompt.content[0].text)
    assert prompt_out["success"] is True
    assert prompt_out["evaluators_count"] == len(matrix)
    first_pkg = prompt_out["evaluation_packages"][0]
    assert "clean_room_system_prompt" in first_pkg
    assert first_pkg["clean_room_system_prompt"] is not None
    assert len(first_pkg["clean_room_system_prompt"].strip()) > 0
    for pkg in prompt_out["evaluation_packages"]:
        assert pkg.get("clean_room_system_prompt") is not None
        assert "CÁMARA LIMPIA" in pkg["clean_room_system_prompt"].upper() or "CAMARA LIMPIA" in pkg["clean_room_system_prompt"].upper()
    assert "anonymized_dispute" in first_pkg
    assert first_pkg["anonymized_dispute"]["thesis_option_a"] == "branch-alpha-unit-tests"

    # Emitir voto de agente jurado
    await server.call_tool("submit_agent_jury_evaluation", {
        "project_uuid": p_uuid,
        "ticket_id": t_id,
        "agent_id": first_pkg["juror_id"],
        "evaluations_json": json.dumps({
            "OPTION_A": {"kiss": 90, "performance": 85, "security": 80, "ux_hicks": 85, "testability": 95},
            "OPTION_B": {"kiss": 70, "performance": 75, "security": 80, "ux_hicks": 70, "testability": 75},
            "THIRD_WAY_C": {"kiss": 85, "performance": 80, "security": 85, "ux_hicks": 80, "testability": 90}
        }),
        "rationale": "La opción A tiene mayor testabilidad."
    })

    # 4. get_dispute_ticket con protección anti-sesgo (§2.6)
    # Nodo gamma (humano que no ha votado) consulta el ticket abierto
    res_get_unvoted = await server.call_tool("get_dispute_ticket", {
        "project_uuid": p_uuid,
        "ticket_id": t_id,
        "caller_node_uuid": "node_gamma"
    })
    unvoted_out = json.loads(res_get_unvoted.content[0].text)
    assert unvoted_out["success"] is True
    # agent_scores numéricos deben estar ocultos
    assert unvoted_out["votes_summary"]["scores_hidden_until_human_vote"] is True
    assert len(unvoted_out["votes_summary"]["agent_scores"]) == 0
    # Pero las discusiones mayéuticas cualitativas sí deben estar visibles
    assert len(unvoted_out["mayeutic_discussions"]) >= 3

    # 5. sync_coordination_memory (§2.9)
    # Encolar un evento de outbox
    db.enqueue_outbox_event(
        event_uuid="evt_test_001",
        project_uuid=p_uuid,
        node_uuid="node_alpha",
        event_type="DISPUTE_MESSAGE",
        payload_raw=json.dumps({"msg": "Debate mayéutico"})
    )
    res_sync = await server.call_tool("sync_coordination_memory", {
        "project_uuid": p_uuid
    })
    sync_out = json.loads(res_sync.content[0].text)
    assert sync_out["success"] is True
    assert sync_out["flushed_count"] >= 1
    assert "orphan-conversa-memory" in sync_out["orphan_branch"]



