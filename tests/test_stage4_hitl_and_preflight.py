"""
Pruebas exhaustivas para Etapa 4:
1. Bandeja HITL de Aprobación Humana en ApiBridge (get_pending_human_approvals).
2. Firma criptográfica Ed25519 de decisiones y promoción a ADR (sign_and_approve_item).
3. Votación Humana con firma en Juicio de Honor (peso 2.0 y veredicto automático).
4. Verificación y diagnóstico pre-vuelo (verify_node_ready).
"""

import json
from pathlib import Path
import pytest

from src.app.api_bridge import ApiBridge
from src.storage.db import DatabaseManager
from src.p2p.crypto import NodeCryptoManager


@pytest.fixture
def stage4_env(tmp_path):
    db_path = tmp_path / "stage4.db"
    db_mgr = DatabaseManager(str(db_path))
    bridge = ApiBridge(db_manager=db_mgr)

    # Crear proyecto
    proj = db_mgr.create_project(name="Stage4 Live Project", base_path=str(tmp_path / "ws_stage4"), init_git=True)
    bridge.active_project_uuid = proj["project_uuid"]

    return {
        "bridge": bridge,
        "db": db_mgr,
        "project": proj,
        "root": tmp_path
    }


def test_crypto_human_approval_signature():
    """Verifica que sign_human_approval genere firmas válidas y verify_artifact_signature las valide."""
    crypto = NodeCryptoManager()
    content = "# DEC-900: Adopción de SQLite WAL\nSe aprueba el motor local-first."
    sig_block = crypto.sign_human_approval(
        artifact_id="DEC-900",
        content_text=content,
        node_uuid="node_alpha",
        developer_name="Alfa Developer",
        decision="APPROVED"
    )

    assert sig_block["artifact_id"] == "DEC-900"
    assert "signature_ed25519" in sig_block
    assert sig_block["public_key"] == crypto.public_key_base64

    # Verificación positiva
    is_valid = NodeCryptoManager.verify_artifact_signature(content, sig_block)
    assert is_valid is True

    # Verificación negativa (contenido adulterado)
    tampered_content = content + "\nLínea inyectada sin autorización"
    is_tampered_valid = NodeCryptoManager.verify_artifact_signature(tampered_content, sig_block)
    assert is_tampered_valid is False


def test_api_bridge_pending_human_approvals_and_decision_signing(stage4_env):
    """
    Verifica la bandeja de aprobación humana en ApiBridge:
    1. Lista decisiones pendientes y disputas abiertas.
    2. Firma con Ed25519 y promueve a ADR.
    """
    bridge = stage4_env["bridge"]
    proj = stage4_env["project"]
    p_uuid = proj["project_uuid"]
    gov_path = Path(proj["gov_repo_path"])

    # 1. Crear una decisión técnica pendiente en docs/okf/architecture/decisions/
    okf_adapter = stage4_env["bridge"].okf
    okf_adapter.scaffold_okf_artifact(
        gov_base_path=gov_path,
        artifact_type="decision",
        item_id="DEC-700",
        title="Uso de SQLite WAL en Producción",
        content="Se documenta la decisión de persistencia."
    )

    # 2. Consultar bandeja de pendientes
    pending = bridge.get_pending_human_approvals(p_uuid)
    assert pending["success"] is True
    assert pending["total_count"] >= 1
    found_dec = any(d["item_id"] == "DEC-700" for d in pending["pending_decisions"])
    assert found_dec is True

    # 3. Firmar y aprobar la decisión mediante el puente HITL
    approval_res = bridge.sign_and_approve_item(
        project_uuid=p_uuid,
        item_type="decision",
        item_id="DEC-700",
        decision="APPROVED",
        comment="Aprobado por el líder técnico con firma Ed25519."
    )
    assert approval_res["success"] is True
    assert approval_res["approved"] is True
    assert "ADR-" in approval_res["adr_path"]
    assert "signature" in approval_res
    assert approval_res["signature"]["decision"] == "APPROVED"


def test_api_bridge_jury_vote_signing_and_verdict(stage4_env):
    """
    Verifica la emisión de votos humanos con firma en Juicios de Honor a través de ApiBridge.
    """
    bridge = stage4_env["bridge"]
    bus = stage4_env["bridge"].bus
    p_uuid = stage4_env["project"]["project_uuid"]

    # 1. Abrir un ticket de disputa
    ticket = bus.open_dispute_ticket(
        project_uuid=p_uuid,
        title="Elección de Transporte MCP",
        collision_reason="Desacuerdo entre Stdio vs SSE",
        contender_a_node="node_alpha",
        contender_b_node="node_beta",
        proposal_a_ref="PROP-STDIO",
        proposal_b_ref="PROP-SSE"
    )
    ticket_id = ticket["ticket_id"]

    # 2. Verificar que aparece en la bandeja de pendientes
    pending = bridge.get_pending_human_approvals(p_uuid)
    assert any(d["ticket_id"] == ticket_id for d in pending["open_disputes"])

    # 3. Emitir voto humano firmado mediante ApiBridge
    vote_res = bridge.sign_and_approve_item(
        project_uuid=p_uuid,
        item_type="jury_vote",
        item_id=ticket_id,
        decision="OPTION_A",
        comment="Voto por Stdio para mayor aislamiento."
    )
    assert vote_res["success"] is True
    assert vote_res["vote"]["voted_option"] == "OPTION_A"
    assert vote_res["verdict"]["winning_option"] == "OPTION_A"
    assert vote_res["verdict"]["highest_score"] >= 2.0
