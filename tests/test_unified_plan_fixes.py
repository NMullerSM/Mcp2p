import json
import pytest
from pathlib import Path
from src.mcp.server import create_mcp_server
from src.app.api_bridge import ApiBridge
from src.p2p.crypto import NodeCryptoManager
from src.storage.db import DatabaseManager
from src.storage.okf_adapter import OKFAdapter
from src.a2a.bus import A2ABus


@pytest.fixture
def mcp_setup(tmp_path):
    db_path = str(tmp_path / "test_unified.db")
    db = DatabaseManager.get_instance(db_path=db_path)
    server = create_mcp_server(db_path=db_path)
    proj = db.create_project("Unified Test Project", str(tmp_path / "ws_unified"), init_git=True)
    return server, db, proj, tmp_path


@pytest.mark.anyio
async def test_edit_kanban_task_and_update_card_micro_script(mcp_setup):
    server, db, proj, tmp_path = mcp_setup
    p_uuid = proj["project_uuid"]

    # 1. Crear card en backlog
    create_res = await server.call_tool("create_kanban_task", {
        "project_uuid": p_uuid,
        "card_id": "CARD-EDIT-01",
        "title": "Título Original",
        "description": "Descripción Original"
    })
    c_out = json.loads(create_res.content[0].text)
    assert c_out["success"] is True

    # 2. Reclamar lease
    db.register_or_update_node("node_worker_1", "Worker 1", "agent_worker_1")
    claim_res = await server.call_tool("claim_kanban_task", {
        "project_uuid": p_uuid,
        "card_id": "CARD-EDIT-01",
        "node_uuid": "node_worker_1"
    })
    assert json.loads(claim_res.content[0].text)["success"] is True

    # 3. Llamar edit_kanban_task para modificar descripción y criterios sin alterar lease ni estado
    edit_res = await server.call_tool("edit_kanban_task", {
        "project_uuid": p_uuid,
        "card_id": "CARD-EDIT-01",
        "title": "Título Editado",
        "description": "Nueva descripción ampliada",
        "acceptance_criteria": "- Test 1\n- Test 2"
    })
    edit_out = json.loads(edit_res.content[0].text)
    assert edit_out["success"] is True
    card_updated = edit_out["card"]
    assert card_updated["title"] == "Título Editado"
    assert card_updated["description"] == "Nueva descripción ampliada"
    assert card_updated["acceptance_criteria"] == "- Test 1\n- Test 2"
    assert card_updated["status"] == "in_progress"
    assert card_updated["assignee_node_uuid"] == "node_worker_1"

    # Verificar que la DB SQLite viva tiene los cambios
    db_card = db.get_card_by_id(p_uuid, "CARD-EDIT-01")
    assert db_card["title"] == "Título Editado"

    # 4. Probar run_okf_micro_script con 'update_card' para escribir en la base de datos viva
    micro_res = await server.call_tool("run_okf_micro_script", {
        "project_uuid": p_uuid,
        "script_action": "update_card",
        "params_json": json.dumps({
            "card_data": {
                "card_id": "CARD-EDIT-01",
                "title": "Título Vía MicroScript",
                "description": "Descripción Vía MicroScript",
                "acceptance_criteria": "- Criterio MicroScript"
            }
        })
    })
    micro_out = json.loads(micro_res.content[0].text)
    assert micro_out["success"] is True
    db_card_after = db.get_card_by_id(p_uuid, "CARD-EDIT-01")
    assert db_card_after["title"] == "Título Vía MicroScript"
    assert db_card_after["description"] == "Descripción Vía MicroScript"


@pytest.mark.anyio
async def test_edit_kanban_task_reassignment_via_mcp(mcp_setup):
    """Un agente conectado por MCP puede reasignar manualmente una tarjeta, pero solo a
    otro colaborador aprobado real del proyecto — nunca a un subagente efímero de jurado."""
    server, db, proj, tmp_path = mcp_setup
    p_uuid = proj["project_uuid"]

    await server.call_tool("create_kanban_task", {
        "project_uuid": p_uuid,
        "card_id": "CARD-REASSIGN-01",
        "title": "Tarea a reasignar"
    })

    # Intento de asignar a un subagente de jurado (nunca es colaborador) -> falla
    bad_res = await server.call_tool("edit_kanban_task", {
        "project_uuid": p_uuid,
        "card_id": "CARD-REASSIGN-01",
        "assignee_node_uuid": "neutral_juror_01"
    })
    bad_out = json.loads(bad_res.content[0].text)
    assert bad_out["success"] is False

    # node_worker_2 aún no es colaborador aprobado -> también falla
    db.register_or_update_node("node_worker_2", "Worker 2", "agent_worker_2")
    not_collab_res = await server.call_tool("edit_kanban_task", {
        "project_uuid": p_uuid,
        "card_id": "CARD-REASSIGN-01",
        "assignee_node_uuid": "node_worker_2"
    })
    assert json.loads(not_collab_res.content[0].text)["success"] is False

    # Una vez aprobado como colaborador real, la reasignación funciona y queda en la DB viva
    db.add_or_request_collaborator(p_uuid, "node_worker_2", role="collaborator", status="approved")
    ok_res = await server.call_tool("edit_kanban_task", {
        "project_uuid": p_uuid,
        "card_id": "CARD-REASSIGN-01",
        "assignee_node_uuid": "node_worker_2"
    })
    ok_out = json.loads(ok_res.content[0].text)
    assert ok_out["success"] is True
    assert ok_out["card"]["assignee_node_uuid"] == "node_worker_2"
    assert db.get_card_by_id(p_uuid, "CARD-REASSIGN-01")["assignee_node_uuid"] == "node_worker_2"

    # '' desasigna explícitamente
    unassign_res = await server.call_tool("edit_kanban_task", {
        "project_uuid": p_uuid,
        "card_id": "CARD-REASSIGN-01",
        "assignee_node_uuid": ""
    })
    assert json.loads(unassign_res.content[0].text)["card"]["assignee_node_uuid"] is None


@pytest.mark.anyio
async def test_release_kanban_task_rejected(mcp_setup):
    server, db, proj, tmp_path = mcp_setup
    p_uuid = proj["project_uuid"]

    await server.call_tool("create_kanban_task", {
        "project_uuid": p_uuid,
        "card_id": "CARD-REJ-01",
        "title": "Tarea para rechazar",
        "description": "Prueba de estado rejected"
    })

    # Transicionar a rejected: MCP debe bloquearlo por EGM-35; la UI HITL humana sí puede transicionar
    db.register_or_update_node("node_reviewer", "Reviewer", "agent_reviewer")
    rel_res = await server.call_tool("release_kanban_task", {
        "project_uuid": p_uuid,
        "card_id": "CARD-REJ-01",
        "node_uuid": "node_reviewer",
        "target_status": "rejected"
    })
    rel_out = json.loads(rel_res.content[0].text)
    assert rel_out["success"] is False
    assert rel_out["code"] == "HUMAN_SIGNATURE_REQUIRED"

    bridge = ApiBridge(db_manager=db)
    bridge.active_project_uuid = p_uuid
    ui_res = bridge.update_task_status("CARD-REJ-01", "rejected")
    assert ui_res["success"] is True

    card = db.get_card_by_id(p_uuid, "CARD-REJ-01")
    assert card["status"] == "rejected"

    # Comprobar que el YAML se movió a kanban/discarded/
    gov_path = Path(proj["gov_repo_path"])
    discarded_yaml = gov_path / "docs" / "okf" / "kanban" / "discarded" / "CARD-REJ-01.yaml"
    assert discarded_yaml.exists()


@pytest.mark.anyio
async def test_open_dispute_ticket_escalation_recommendation_and_staleness(mcp_setup):
    server, db, proj, tmp_path = mcp_setup
    p_uuid = proj["project_uuid"]

    # Abrir ticket con recomendación de escalamiento
    open_res = await server.call_tool("open_dispute_ticket", {
        "project_uuid": p_uuid,
        "title": "Disputa con recomendación",
        "collision_reason": "Divergencia entre ORM y raw SQL",
        "contender_a_node": "node_alpha",
        "contender_b_node": "node_beta",
        "proposal_a_ref": "branch-orm",
        "proposal_b_ref": "branch-sql",
        "escalation_recommendation": "sugiero jurado: impacto arquitectónico mayor en persistencia"
    })
    open_out = json.loads(open_res.content[0].text)
    assert open_out["success"] is True
    t_id = open_out["ticket"]["ticket_id"]

    # Consultar ticket
    get_res = await server.call_tool("get_dispute_ticket", {
        "project_uuid": p_uuid,
        "ticket_id": t_id
    })
    get_out = json.loads(get_res.content[0].text)
    assert get_out["success"] is True
    assert get_out["escalation_recommendation"] == "sugiero jurado: impacto arquitectónico mayor en persistencia"
    assert get_out["is_outdated_matrix"] is False

    # Simular matriz obsoleta sin directivas de clean room (Gap #2)
    db.update_dispute_synthesis_and_matrix(
        ticket_id=t_id,
        jury_matrix=[{"juror_id": "legacy_juror_01", "role": "LEGACY_JUROR"}]
    )
    get_outdated = await server.call_tool("get_dispute_ticket", {
        "project_uuid": p_uuid,
        "ticket_id": t_id
    })
    outdated_res = json.loads(get_outdated.content[0].text)
    assert outdated_res["is_outdated_matrix"] is True
    assert outdated_res.get("matrix_status") == "outdated"

    # Actualizar la matriz mediante upgrade_if_outdated
    get_upgraded = await server.call_tool("get_dispute_ticket", {
        "project_uuid": p_uuid,
        "ticket_id": t_id,
        "upgrade_if_outdated": True
    })
    upgraded_res = json.loads(get_upgraded.content[0].text)
    assert upgraded_res["is_outdated_matrix"] is False
    assert len(upgraded_res["mayeutic_discussions"]) > 0


def test_remove_collaborator(mcp_setup):
    _, db, proj, _ = mcp_setup
    p_uuid = proj["project_uuid"]

    db.add_or_request_collaborator(p_uuid, "node_collab_temp", role="collaborator")
    collabs = db.get_project_collaborators(p_uuid)
    assert any(c["node_uuid"] == "node_collab_temp" for c in collabs)

    # Remover colaborador
    removed = db.remove_collaborator(p_uuid, "node_collab_temp")
    assert removed is True
    collabs_after = db.get_project_collaborators(p_uuid)
    assert not any(c["node_uuid"] == "node_collab_temp" for c in collabs_after)


def test_ruling_and_precedents_atomic_commit(mcp_setup):
    _, db, proj, _ = mcp_setup
    p_uuid = proj["project_uuid"]
    bus = A2ABus(db=db)

    # Abrir disputa y votar
    ticket = bus.open_dispute_ticket(
        project_uuid=p_uuid,
        title="Disputa Atomic Precedents",
        collision_reason="Colisión de precedentes",
        contender_a_node="node_a",
        contender_b_node="node_b",
        proposal_a_ref="prop_a",
        proposal_b_ref="prop_b"
    )
    t_id = ticket["ticket_id"]

    # Voto humano del desarrollador registrado en el proyecto (EGM-05 anti-Sybil) con firma Ed25519 real
    owner_node = db.get_project_collaborators(p_uuid)[0]["node_uuid"]
    crypto = NodeCryptoManager()
    db.add_or_request_collaborator(
        p_uuid,
        owner_node,
        public_key=crypto.public_key_base64,
        role="owner",
        status="approved",
    )
    sig_block = crypto.sign_human_approval(
        artifact_id=t_id,
        content_text=f"VOTE:OPTION_A:TICKET:{t_id}:PROJECT:{p_uuid}",
        node_uuid=owner_node,
        developer_name="Human Dev",
        decision="OPTION_A",
    )
    bus.submit_human_jury_vote(
        ticket_id=t_id,
        node_uuid=owner_node,
        developer_name="Human Dev",
        voted_option="OPTION_A",
        signature_ed25519=sig_block["signature_ed25519"],
        public_key=sig_block["public_key"],
        signed_at=sig_block["signed_at"],
        content_sha256=sig_block["content_sha256"],
        project_uuid=p_uuid,
    )

    verdict = bus.compute_jury_verdict(p_uuid, t_id)
    assert verdict["consensus_reached"] is True

    # Verificar que el ruling YAML existe y precedents_index.yaml fue actualizado y comiteado
    gov_path = Path(proj["gov_repo_path"])
    ruling_path = gov_path / "docs" / "okf" / "architecture" / "rulings" / f"RULING-{t_id}.yaml"
    precedents_path = gov_path / "docs" / "okf" / "architecture" / "precedents_index.yaml"
    assert ruling_path.exists()
    assert precedents_path.exists()

    # Verificar con git status que no quedan cambios sin comitear en el repo de gobernanza
    import subprocess
    proc = subprocess.run(["git", "status", "--porcelain"], cwd=gov_path, capture_output=True, text=True)
    # Ni el ruling ni precedents_index.yaml deben quedar sin comitear
    assert "precedents_index.yaml" not in proc.stdout
    assert f"RULING-{t_id}.yaml" not in proc.stdout


def test_api_bridge_sign_and_approve_rejected_and_remove_collaborator(mcp_setup):
    from src.app.api_bridge import ApiBridge
    _, db, proj, _ = mcp_setup
    p_uuid = proj["project_uuid"]
    bridge = ApiBridge(db_manager=db)

    # 1. Crear card en in_review
    db.create_card(p_uuid, "TASK-REV-01", "Tarea para rechazar", "Desc")
    db.update_card_status(p_uuid, "TASK-REV-01", "in_review")

    # 2. Rechazar mediante sign_and_approve_item
    rej_res = bridge.sign_and_approve_item(p_uuid, "pr_merge", "TASK-REV-01", decision="REJECTED")
    assert rej_res["success"] is True
    assert rej_res["rejected"] is True
    card = db.get_card_by_id(p_uuid, "TASK-REV-01")
    assert card["status"] == "rejected"

    # 3. Probar remove_collaborator desde ApiBridge
    db.add_or_request_collaborator(p_uuid, "node_collab_api", role="collaborator")
    remove_res = bridge.remove_collaborator(p_uuid, "node_collab_api")
    assert remove_res["success"] is True
    collabs = db.get_project_collaborators(p_uuid)
    assert not any(c["node_uuid"] == "node_collab_api" for c in collabs)


