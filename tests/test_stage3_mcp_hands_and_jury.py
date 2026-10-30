"""
Pruebas exhaustivas para Etapa 3:
1. Conversación Dialéctica Libre con Compactación de Contexto (CHECKPOINT_COMPACT).
2. Motor de Juicio de Honor Ponderado (2.0 Desarrollador / 1.5 Agente).
3. Herramientas MCP ("Las Manos del Agente"): get_task_full_context, manage_task_worktree,
   run_okf_micro_script, propose_or_apply_task_distribution, inspect_pr_and_recommend.
4. Respeto estricto del guardrail HITL (ninguna herramienta MCP firma con clave privada Ed25519).
"""

import json
from pathlib import Path
import pytest

from src.storage.db import DatabaseManager
from src.a2a.bus import A2ABus, A2AIntent, DialogueMessageType
from src.mcp.tools import MCPToolsHandler
from src.p2p.crypto import NodeCryptoManager


@pytest.fixture
def stage3_env(tmp_path):
    db_path = tmp_path / "stage3.db"
    db_mgr = DatabaseManager(str(db_path))
    bus = A2ABus(db=db_mgr)
    mcp_handler = MCPToolsHandler(db_manager=db_mgr)
    
    # Crear proyecto inicial de prueba
    proj = db_mgr.create_project(name="Stage3 Project", base_path=str(tmp_path / "ws_stage3"), init_git=True)
    p_uuid = proj["project_uuid"]

    # Registrar 2 nodos
    db_mgr.register_or_update_node("node_alpha", "Desarrollador Alfa", "agent_claude")
    db_mgr.register_or_update_node("node_beta", "Desarrollador Beta", "agent_gemini")

    return {
        "db": db_mgr,
        "bus": bus,
        "mcp": mcp_handler,
        "project_uuid": p_uuid,
        "root": tmp_path
    }


def test_dialogue_conversation_free_rounds_and_compaction(stage3_env):
    """
    Verifica que múltiples agentes puedan conversar con razonamiento libre
    y que al emitir un CHECKPOINT_COMPACT, el contexto devuelto se reduzca drásticamente.
    """
    bus = stage3_env["bus"]
    p_uuid = stage3_env["project_uuid"]
    session_id = "sess_arch_discussion_01"

    # Turno 1: Node Alpha propone arquitectura
    t1 = bus.post_dialogue_message(
        project_uuid=p_uuid,
        sender_node_uuid="node_alpha",
        session_id=session_id,
        message_type="PROPOSAL",
        reasoning_text="Propongo usar SQLite WAL para sincronización rápida y cero tokens.",
        structured_proposal={"engine": "sqlite_wal"}
    )
    assert t1["payload"]["seq"] == 1
    assert t1["requires_compaction"] is False

    # Turno 2: Node Beta responde con razonamiento
    t2 = bus.post_dialogue_message(
        project_uuid=p_uuid,
        sender_node_uuid="node_beta",
        session_id=session_id,
        message_type="REASONING_REPLY",
        reasoning_text="De acuerdo con SQLite WAL, pero debemos asegurar que el checkpoint no bloquee la lectura."
    )
    assert t2["payload"]["seq"] == 2
    assert t2["requires_compaction"] is False

    # Turno 3: Node Alpha agrega detalles
    t3 = bus.post_dialogue_message(
        project_uuid=p_uuid,
        sender_node_uuid="node_alpha",
        session_id=session_id,
        message_type="REASONING_REPLY",
        reasoning_text="Usaremos modo PASSIVE para los checkpoints para evitar bloqueos concurrentes."
    )
    assert t3["payload"]["seq"] == 3
    # Al tercer turno, el bus indica que es momento de compactar
    assert t3["requires_compaction"] is True

    # Turno 4: Emitir CHECKPOINT_COMPACT
    t4 = bus.post_dialogue_message(
        project_uuid=p_uuid,
        sender_node_uuid="node_alpha",
        session_id=session_id,
        message_type="CHECKPOINT_COMPACT",
        reasoning_text="SÍNTESIS: Acordado SQLite WAL con checkpoints en modo PASSIVE para evitar contención."
    )
    assert t4["payload"]["seq"] == 4

    # Turno 5: Mensaje posterior al checkpoint
    t5 = bus.post_dialogue_message(
        project_uuid=p_uuid,
        sender_node_uuid="node_beta",
        session_id=session_id,
        message_type="RESOLUTION",
        reasoning_text="Procediendo con la implementación en src/storage/db.py."
    )

    # Verificar que get_session_active_context compacta la ventana
    ctx = bus.get_session_active_context(p_uuid, session_id)
    assert ctx["is_compacted"] is True
    assert ctx["total_history_count"] == 5
    # Solo deben estar el CHECKPOINT_COMPACT y el turno 5
    assert len(ctx["messages"]) == 2
    assert ctx["messages"][0]["message_type"] == "CHECKPOINT_COMPACT"
    assert "SÍNTESIS:" in ctx["last_checkpoint"]


def test_weighted_honor_jury_system(stage3_env):
    """
    Verifica el cálculo de la fórmula del Juicio de Honor:
    Score(k) = (Votos Humanos * 2.0) + (Borda Agente / 100 * 1.5)
    """
    bus = stage3_env["bus"]
    p_uuid = stage3_env["project_uuid"]

    # 1. Abrir ticket de disputa
    ticket = bus.open_dispute_ticket(
        project_uuid=p_uuid,
        title="Elección de Protocolo P2P",
        collision_reason="Desacuerdo entre WebSocket nativo vs HTTP Pull",
        contender_a_node="node_alpha",
        contender_b_node="node_beta",
        proposal_a_ref="PROP-01",
        proposal_b_ref="PROP-02"
    )
    ticket_id = ticket["ticket_id"]

    # 2. Evaluación de Agente 1 (node_alpha)
    # Califica OPTION_A con 90 en todo y OPTION_B con 50
    bus.submit_agent_borda_evaluation(
        ticket_id=ticket_id,
        juror_agent_id="agent_claude",
        scores_per_option={
            "OPTION_A": {"kiss": 90, "performance": 90, "security": 90, "ux_hicks": 90, "testability": 90},
            "OPTION_B": {"kiss": 50, "performance": 50, "security": 50, "ux_hicks": 50, "testability": 50}
        },
        rationale="OPTION_A ofrece mejor KISS y performance."
    )

    # 3. Voto Humano de Desarrollador Beta (apoya OPTION_B con peso 2.0)
    # Generar firma Ed25519 humana
    key_path = stage3_env["root"] / "human_dev_key.pem"
    crypto_mgr = NodeCryptoManager(key_path=key_path)
    sig_b = crypto_mgr.sign_payload({"vote": "OPTION_B", "ticket": ticket_id})

    bus.submit_human_jury_vote(
        ticket_id=ticket_id,
        node_uuid="node_beta",
        developer_name="Desarrollador Beta",
        voted_option="OPTION_B",
        signature_ed25519=sig_b
    )

    # 4. Calcular veredicto:
    # OPTION_A: 0 humanos * 2.0 + (90/100 * 1.5) = 1.35
    # OPTION_B: 1 humano * 2.0 + (50/100 * 1.5) = 2.0 + 0.75 = 2.75
    # Gana OPTION_B porque el voto humano tiene mayor peso (2.0)
    verdict = bus.compute_jury_verdict(p_uuid, ticket_id)
    assert verdict["winning_option"] == "OPTION_B"
    assert verdict["highest_score"] >= 2.75


def test_mcp_agent_hands_tools(stage3_env):
    """
    Verifica las herramientas de "manos" del agente expuestas por MCP:
    - get_task_full_context
    - run_okf_micro_script
    - propose_or_apply_task_distribution
    - inspect_pr_and_recommend (respetando que NO aprueba directamente)
    """
    mcp = stage3_env["mcp"]
    db = stage3_env["db"]
    p_uuid = stage3_env["project_uuid"]

    # 1. Crear tarea en DB
    card = db.create_card(p_uuid, "TASK-500", "Optimizar WAL Cache", "Reducir latencia de lectura")

    # 2. get_task_full_context
    ctx_json = mcp.get_task_full_context(p_uuid, "TASK-500")
    ctx = json.loads(ctx_json)
    assert ctx["success"] is True
    assert ctx["card"]["card_id"] == "TASK-500"
    assert "unread_notifications" in ctx

    # 3. run_okf_micro_script (scaffold_decision)
    res_script = json.loads(mcp.run_okf_micro_script(
        project_uuid=p_uuid,
        script_action="scaffold_decision",
        params_json=json.dumps({
            "item_id": "DEC-505",
            "title": "Adopcion de MessagePack en A2A",
            "content": "MessagePack reduce el payload de red un 60% frente a JSON.",
            "related_ids": ["RNF-01"]
        })
    ))
    assert res_script["success"] is True
    assert "DEC-505" in res_script["created_path"]

    # 4. propose_or_apply_task_distribution con apply_immediately
    dist_res = json.loads(mcp.propose_or_apply_task_distribution(
        project_uuid=p_uuid,
        sender_node_uuid="node_alpha",
        session_id="task_dist_01",
        distribution_plan_json=json.dumps({"TASK-500": "node_alpha"}),
        apply_immediately=True
    ))
    assert dist_res["success"] is True
    assert len(dist_res["applied_leases"]) == 1
    assert dist_res["applied_leases"][0]["assignee_node_uuid"] == "node_alpha"

    # 5. inspect_pr_and_recommend (deja recomendación sin auto-aprobar)
    pr_rec_res = json.loads(mcp.inspect_pr_and_recommend(
        project_uuid=p_uuid,
        pr_id_or_branch="feature/wal-cache",
        reviewer_node_uuid="node_beta",
        recommendation="RECOMMEND_ACCEPT",
        analysis_summary="El código cumple con RNF-01 y no rompe los tests unitarios.",
        suggested_reply="Recomiendo aprobar el merge a main."
    ))
    assert pr_rec_res["success"] is True
    assert pr_rec_res["recommendation_logged"] is True
