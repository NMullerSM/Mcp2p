"""
Regresión end-to-end del ciclo de vida Kanban/Tribunal a través de los adaptadores
primarios reales (`MCPToolsHandler` y `mcp2p fast`) delegando en el CoreKernel
(Tarea 2.3 de `23_`, documentada en `24_`).

Cubre además:
- EGM-49: un agente no puede "blanquear" un estado reservado al humano escribiendo un
  YAML OKF y llamando a `run_okf_micro_script(reconcile_kanban)`.
- Defecto latente de `KanbanGovernanceService.promote_decision` (kwargs incompatibles con
  `OKFAdapter.move_decision_lifecycle` y retorno `Path` tratado como `dict`).
- Guarda estructural: ningún adaptador primario vuelve a definir su propia lista de
  estados reservados (patrón de bug EGM-04 / EGM-36).
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest
import yaml

from src.app import fast_cli
from src.core.domain.context import ACTOR_AGENT_MCP, ActorContext
from src.core.factory import ApplicationFactory
from src.core.services.kanban_service import KanbanGovernanceService
from src.mcp.tools import MCPToolsHandler
from src.storage.db import DatabaseManager

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def lifecycle_env(tmp_path: Path):
    db_file = tmp_path / "lifecycle.db"
    db = DatabaseManager(db_path=str(db_file))
    proj = db.create_project(name="HexLifecycle", base_path=str(tmp_path / "workspace"))
    p_uuid = proj["project_uuid"]
    for node in ("agent_a", "agent_b"):
        db.register_or_update_node(node, node.upper(), f"ag_{node}", status="online", node_kind="agent")
        db.add_or_request_collaborator(p_uuid, node, public_key="", role="collaborator", status="approved")
    handler = MCPToolsHandler(db_manager=db)
    events: List[Any] = []
    handler.kernel.event_bus.subscribe("*", events.append)
    try:
        yield {
            "db": db,
            "db_path": str(db_file),
            "project": proj,
            "p_uuid": p_uuid,
            "gov_path": Path(proj["gov_repo_path"]),
            "handler": handler,
            "events": events,
        }
    finally:
        db.close()


def _j(raw: str) -> Dict[str, Any]:
    return json.loads(raw)


def test_mcp_tools_handler_is_a_thin_adapter_over_core_kernel(lifecycle_env) -> None:
    handler: MCPToolsHandler = lifecycle_env["handler"]
    assert isinstance(handler.kernel.kanban, KanbanGovernanceService)
    # Comparte DB y A2ABus con el núcleo (EGM-28: un solo bus por proceso MCP)
    assert handler.db is handler.kernel.db
    assert handler.bus is handler.kernel.bus
    assert handler.okf is handler.kernel.okf_adapter
    # El proceso MCP no carga la clave Ed25519 humana ni la malla P2P
    assert handler.kernel.crypto is None
    assert handler.kernel.mesh_connector is None
    assert handler.kernel.profile == "mcp_server"


def test_mcp_full_lifecycle_through_kernel(lifecycle_env) -> None:
    h: MCPToolsHandler = lifecycle_env["handler"]
    db: DatabaseManager = lifecycle_env["db"]
    p_uuid = lifecycle_env["p_uuid"]
    gov_path: Path = lifecycle_env["gov_path"]
    events: List[Any] = lifecycle_env["events"]

    # 1. Crear tarea
    created = _j(h.create_kanban_task(p_uuid, "CARD-HEX-1", "Ciclo hexagonal", "desc"))
    assert created["success"] is True
    assert created["card"]["card_id"] == "CARD-HEX-1"
    assert created["card"]["status"] == "backlog"
    created_evt = [e for e in events if e.event_type == "kanban.card_created"]
    assert created_evt and created_evt[-1].payload["actor_kind"] == ACTOR_AGENT_MCP

    # 2. Reclamar
    claim = _j(h.claim_kanban_task(p_uuid, "CARD-HEX-1", "agent_a", duration_minutes=20))
    assert claim["success"] is True
    assert claim["lease_expires_at"]
    assert claim["card"]["assignee_node_uuid"] == "agent_a"
    assert "message" in claim

    # 3. Colisión de lease
    collision = _j(h.claim_kanban_task(p_uuid, "CARD-HEX-1", "agent_b"))
    assert collision["success"] is False
    assert collision["code"] == "LEASE_COLLISION"

    # 3b. Un no titular no puede mover la tarjeta (salvo disputed)
    denied = _j(h.release_kanban_task(p_uuid, "CARD-HEX-1", "agent_b", target_status="in_review"))
    assert denied["success"] is False
    assert denied["code"] == "PERMISSION_DENIED"

    # 4. Release a in_review por el titular
    rel = _j(h.release_kanban_task(p_uuid, "CARD-HEX-1", "agent_a", target_status="in_review", branch_ref="feat/hex-1"))
    assert rel["success"] is True
    assert rel["card"]["status"] == "in_review"
    assert rel["card"]["branch_ref"] == "feat/hex-1"
    assert "dispute_ticket" not in rel
    assert any(e.event_type == "kanban.lease_released" for e in events)

    # 5. Forjar merged vía release_kanban_task -> rechazado
    for forbidden in ("merged", "rejected", "discarded"):
        forged = _j(h.release_kanban_task(p_uuid, "CARD-HEX-1", "agent_a", target_status=forbidden))
        assert forged["success"] is False
        assert forged["code"] == "HUMAN_SIGNATURE_REQUIRED"
    invalid = _j(h.release_kanban_task(p_uuid, "CARD-HEX-1", "agent_a", target_status="consolidated"))
    assert invalid["success"] is False and "target_status inválido" in invalid["error"]

    # 6. Forjar status vía run_okf_micro_script(update_card) -> rechazado
    forged_upd = _j(h.run_okf_micro_script(
        p_uuid, "update_card", json.dumps({"card_data": {"card_id": "CARD-HEX-1", "status": "merged"}})
    ))
    assert forged_upd["success"] is False
    assert forged_upd["code"] == "USE_LEASE_WORKFLOW"
    # update_card sin status sigue funcionando (crea o edita)
    ok_upd = _j(h.run_okf_micro_script(
        p_uuid, "update_card", {"card_data": {"card_id": "CARD-HEX-1", "title": "Ciclo hexagonal v2"}}
    ))
    assert ok_upd["success"] is True and ok_upd["card"]["title"] == "Ciclo hexagonal v2"
    new_upd = _j(h.run_okf_micro_script(
        p_uuid, "update_card", {"card_data": {"id": "CARD-HEX-NEW", "title": "Nueva desde OKF"}}
    ))
    assert new_upd["success"] is True and new_upd["card"]["status"] == "backlog"
    assert db.get_card_by_id(p_uuid, "CARD-HEX-1")["status"] == "in_review"

    # 6b. EGM-49: escribir status: merged en el YAML y reconciliar no lo blanquea
    yaml_path = gov_path / "docs" / "okf" / "kanban" / "cards" / "CARD-HEX-1.yaml"
    assert yaml_path.exists()
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    data["status"] = "merged"
    yaml_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    rec = _j(h.run_okf_micro_script(p_uuid, "reconcile_kanban", "{}"))
    assert rec["success"] is True
    blocked = rec["reconciled"].get("blocked_human_only_transitions", [])
    assert any(b["card_id"] == "CARD-HEX-1" and b["yaml_status"] == "merged" for b in blocked)
    assert db.get_card_by_id(p_uuid, "CARD-HEX-1")["status"] == "in_review"

    # 6c. promote_decision(approved) bloqueado; discarded funciona vía servicio
    scaf = _j(h.run_okf_micro_script(p_uuid, "scaffold_decision", {
        "item_id": "DEC-HEX-01", "title": "Puerto unico", "content": "Contexto de prueba"
    }))
    assert scaf["success"] is True
    appr = _j(h.run_okf_micro_script(p_uuid, "promote_decision", {
        "decision_id": "DEC-HEX-01", "target_state": "approved",
        "signature_block": {"signature_ed25519": "forged", "node_uuid": "agent_a"},
    }))
    assert appr["success"] is False and appr["code"] == "HUMAN_SIGNATURE_REQUIRED"
    disc = _j(h.run_okf_micro_script(p_uuid, "promote_decision", {
        "decision_id": "DEC-HEX-01", "target_state": "discarded", "resolution_notes": "no aplica"
    }))
    assert disc["success"] is True, disc
    assert Path(disc["promoted_path"]).exists()

    # 7. disputed por un revisor no titular abre ticket automático
    disp = _j(h.release_kanban_task(
        p_uuid, "CARD-HEX-1", "agent_b", target_status="disputed", dispute_reason="Diseño incompatible"
    ))
    assert disp["success"] is True
    assert disp["card"]["status"] == "disputed"
    auto_ticket = disp["dispute_ticket"]
    assert auto_ticket["card_id"] == "CARD-HEX-1"
    assert auto_ticket["contender_a_node"] == "agent_a"
    assert auto_ticket["contender_b_node"] == "agent_b"
    assert auto_ticket["ticket_id"] in disp["message"]
    # Re-escalar reutiliza el ticket abierto (sin disputas duplicadas)
    disp2 = _j(h.release_kanban_task(p_uuid, "CARD-HEX-1", "agent_b", target_status="disputed"))
    assert disp2["dispute_ticket"]["ticket_id"] == auto_ticket["ticket_id"]

    # 8. open_dispute_ticket directo
    direct = _j(h.open_dispute_ticket(
        project_uuid=p_uuid,
        title="Conflicto directo",
        collision_reason="Dos caches",
        contender_a_node="agent_a",
        contender_b_node="agent_b",
        proposal_a_ref="feat/a",
        proposal_b_ref="feat/b",
    ))
    assert direct["success"] is True
    direct_id = direct["ticket"]["ticket_id"]
    assert any(e.event_type == "tribunal.dispute_opened" for e in events)

    # 9. list / get
    listed = _j(h.list_dispute_tickets(p_uuid))
    assert listed["success"] is True and listed["count"] == 2
    assert {t["ticket_id"] for t in listed["tickets"]} == {auto_ticket["ticket_id"], direct_id}
    by_card = _j(h.list_dispute_tickets(p_uuid, card_id="CARD-HEX-1"))
    assert by_card["count"] == 1
    got = _j(h.get_dispute_ticket(p_uuid, ticket_id=direct_id))
    assert got["success"] is True and got["ticket"]["ticket_id"] == direct_id
    got_card = _j(h.get_dispute_ticket(p_uuid, card_id="CARD-HEX-1"))
    assert got_card["success"] is True and got_card["ticket"]["ticket_id"] == auto_ticket["ticket_id"]
    assert got_card["votes_summary"]["scores_hidden_until_human_vote"] is True

    # La tarjeta jamás llegó a un estado reservado
    assert db.get_card_by_id(p_uuid, "CARD-HEX-1")["status"] == "disputed"


def test_fast_cli_lifecycle_through_kernel(lifecycle_env, capsys: pytest.CaptureFixture[str]) -> None:
    h: MCPToolsHandler = lifecycle_env["handler"]
    db: DatabaseManager = lifecycle_env["db"]
    db_path = lifecycle_env["db_path"]
    p_uuid = lifecycle_env["p_uuid"]
    base = ["--db-path", db_path, "--project", p_uuid]

    def run(*argv: str) -> tuple[int, Dict[str, Any]]:
        rc = fast_cli.run_fast_cli([*base, *argv])
        return rc, json.loads(capsys.readouterr().out)

    assert _j(h.create_kanban_task(p_uuid, "CARD-CLI-HEX", "CLI"))["success"] is True

    rc, out = run("--node", "agent_a", "card-claim", "CARD-CLI-HEX", "--minutes", "15")
    assert rc == 0 and out["success"] is True and out["lease_expires_at"]

    # card-claim colisionando
    rc, out = run("--node", "agent_b", "card-claim", "CARD-CLI-HEX")
    assert rc == 1 and out["code"] == "LEASE_COLLISION"

    # card-move ... merged rechazado (por el propio titular)
    rc, out = run("--node", "agent_a", "card-move", "CARD-CLI-HEX", "merged")
    assert rc == 1 and out["code"] == "HUMAN_SIGNATURE_REQUIRED"
    # mismo orden de validación que MCP: no titular -> PERMISSION_DENIED
    rc, out = run("--node", "agent_b", "card-move", "CARD-CLI-HEX", "in_review")
    assert rc == 1 and out["code"] == "PERMISSION_DENIED"

    rc, out = run("--node", "agent_a", "card-move", "CARD-CLI-HEX", "in_review")
    assert rc == 0 and out["card"]["status"] == "in_review"

    # disputed por fast CLI también abre ticket (antes quedaba huérfano)
    rc, out = run("--node", "agent_b", "card-release", "CARD-CLI-HEX", "--status", "disputed")
    assert rc == 0 and out["dispute_ticket"]["card_id"] == "CARD-CLI-HEX"

    # decision promote approved bloqueado; discarded funciona
    assert _j(h.run_okf_micro_script(p_uuid, "scaffold_decision", {
        "item_id": "DEC-CLI-01", "title": "CLI decision", "content": "x"
    }))["success"] is True
    rc, out = run("decision", "promote", "DEC-CLI-01", "--state", "approved")
    assert rc == 1 and out["code"] == "HUMAN_SIGNATURE_REQUIRED"
    rc, out = run("decision", "promote", "DEC-CLI-01", "--state", "discarded", "--notes", "obsoleta")
    assert rc == 0 and out["success"] is True and Path(out["promoted_path"]).exists()

    assert db.get_card_by_id(p_uuid, "CARD-CLI-HEX")["status"] == "disputed"


def test_kernel_promote_decision_reaches_okf_adapter(lifecycle_env) -> None:
    """Defecto latente: promote_decision llamaba a OKFAdapter con kwargs inexistentes."""
    p_uuid = lifecycle_env["p_uuid"]
    h: MCPToolsHandler = lifecycle_env["handler"]
    assert _j(h.run_okf_micro_script(p_uuid, "scaffold_decision", {
        "item_id": "DEC-KER-01", "title": "Kernel decision", "content": "x"
    }))["success"] is True
    human = ActorContext(kind="human_ui", node_uuid="owner", human_presence_verified=True)
    res = h.kernel.kanban.promote_decision(p_uuid, "DEC-KER-01", "approved", human, reason="ok")
    assert res["success"] is True, res
    assert Path(res["promoted_path"]).exists()
    missing = h.kernel.kanban.promote_decision(p_uuid, "DEC-NOPE", "discarded", human)
    assert missing["success"] is False and "DEC-NOPE" in missing["error"]


def _string_collections(path: Path) -> List[set[str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: List[set[str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            vals = {e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}
            if vals:
                found.append(vals)
    return found


@pytest.mark.parametrize("rel_path", ["src/mcp/tools.py", "src/app/fast_cli.py"])
def test_primary_adapters_do_not_redefine_human_only_states(rel_path: str) -> None:
    """Una sola definición de 'qué requiere firma humana': src/core/services/kanban_service.py."""
    for vals in _string_collections(REPO_ROOT / rel_path):
        assert not ({"merged", "discarded"} <= vals), (
            f"{rel_path} redefine una lista de estados reservados {sorted(vals)}; "
            "use HUMAN_ONLY_CARD_STATES/KanbanGovernanceService."
        )
        assert not ({"approved", "discarded"} <= vals), (
            f"{rel_path} redefine estados de decisión {sorted(vals)}; use DECISION_TARGET_STATES."
        )
