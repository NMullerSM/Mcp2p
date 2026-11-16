"""
Hito 3.3 — Suite adversarial contra CoreKernel, ExtensionSecurityGate, OKF y MultiPeerMeshConnector.

Cubre:
- EGM-35 por MCP (`MCPToolsHandler`), `fast_cli` y `CoreKernel.kanban_service`
- EGM-05-R / EGM-37 / EGM-01-R por `A2ABus` y `ApiBridge`
- `ExtensionSecurityGate` bloqueado por defecto, rechazo a agentes y firmas adulteradas
- Bloqueo SSRF en `OutboundRestConnector` (127.0.0.1, 169.254.169.254, IPv6 ::1, dominio no autorizado, DNS rebinding)
- Path traversal en `add_okf_folder` / `remove_okf_folder` / `rename_okf_folder`
- Replay, tamper, stale timestamp, nodo no confiable y flood en `MultiPeerMeshConnector`
"""

from __future__ import annotations

import sys
import json
import time
from pathlib import Path
from typing import Any, Dict

import pytest

from src.a2a.bus import A2ABus
from src.adapters.outbound.extension_gate import (
    ExtensionSecurityGate,
    NullAgentTerminalConnector,
    OutboundRestConnector,
    SignedConnectorManifest,
)
from src.app import fast_cli
from src.app.api_bridge import ApiBridge
from src.core.domain.context import ActorContext
from src.core.factory import ApplicationFactory
from src.mcp.tools import MCPToolsHandler
from src.p2p.crypto import NodeCryptoManager
from src.p2p.mesh_adapter import MultiPeerMeshConnector
from src.storage.db import DatabaseManager


def _setup_project_with_nodes(tmp_path: Path, name: str = "AdvProj") -> Dict[str, Any]:
    db_file = tmp_path / "adv.db"
    db = DatabaseManager(db_path=str(db_file))
    proj = db.create_project(name=name)
    p_uuid = proj["project_uuid"]
    db.register_or_update_node("agent_adv", "Adv Agent", "ag_adv", status="online", node_kind="agent")
    db.add_or_request_collaborator(p_uuid, "agent_adv", public_key="", role="collaborator", status="approved")
    return {"db": db, "db_path": str(db_file), "project": proj, "project_uuid": p_uuid}


# =========================================================================
# 1. EGM-35 por MCP, fast_cli y CoreKernel
# =========================================================================


def test_egm35_mcp_blocks_terminal_card_transitions_and_decision_approval(tmp_path: Path) -> None:
    ctx = _setup_project_with_nodes(tmp_path)
    db: DatabaseManager = ctx["db"]
    p_uuid = ctx["project_uuid"]
    gov_path = Path(ctx["project"]["gov_repo_path"])

    try:
        db.create_card(p_uuid, "CARD-EGM35", "Implement gate")
        handler = MCPToolsHandler(db_manager=db)

        # Crear decisión pendiente para intentar promoverla vía MCP
        json.loads(
            handler.run_okf_micro_script(
                p_uuid,
                "create_decision",
                {"title": "Usar Redis para locks", "context": "Prueba EGM-35", "decision": "Redis"},
            )
        )
        dec_files = list((gov_path / "docs" / "okf" / "architecture" / "decisions").glob("DEC-*.md"))
        dec_id = dec_files[0].stem if dec_files else "DEC-001"

        # 1a. promote_decision(target_state='approved') debe bloquear incluso con signature_block falsificado
        res_dec = json.loads(
            handler.run_okf_micro_script(
                p_uuid,
                "promote_decision",
                {
                    "decision_id": dec_id,
                    "target_state": "approved",
                    "signature_block": {"signature_ed25519": "forged_sig", "node_uuid": "agent_adv"},
                },
            )
        )
        assert res_dec["success"] is False
        assert res_dec["code"] == "HUMAN_SIGNATURE_REQUIRED"

        # 1b. update_card con status directo debe exigir lease workflow
        res_upd = json.loads(
            handler.run_okf_micro_script(
                p_uuid,
                "update_card",
                {"card_data": {"card_id": "CARD-EGM35", "status": "merged"}},
            )
        )
        assert res_upd["success"] is False
        assert res_upd["code"] == "USE_LEASE_WORKFLOW"

        # 1c. release_kanban_task hacia estados terminales debe devolver HUMAN_SIGNATURE_REQUIRED
        claim_res = json.loads(handler.claim_kanban_task(p_uuid, "CARD-EGM35", "agent_adv"))
        assert claim_res["success"] is True

        for forbidden_status in ("merged", "rejected", "discarded"):
            rel = json.loads(
                handler.release_kanban_task(
                    project_uuid=p_uuid,
                    card_id="CARD-EGM35",
                    node_uuid="agent_adv",
                    target_status=forbidden_status,
                )
            )
            assert rel["success"] is False
            assert rel["code"] == "HUMAN_SIGNATURE_REQUIRED"

        # Verificar que la tarjeta no pasó a merged
        card = db.get_card_by_id(p_uuid, "CARD-EGM35")
        assert card is not None and card["status"] == "in_progress"
    finally:
        db.close()


def test_egm35_fast_cli_and_kernel_block_terminal_transitions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ctx = _setup_project_with_nodes(tmp_path)
    db: DatabaseManager = ctx["db"]
    db_path = ctx["db_path"]
    p_uuid = ctx["project_uuid"]

    try:
        db.create_card(p_uuid, "CARD-CLI-1", "CLI EGM-35 test")
        db.claim_card_lease(p_uuid, "CARD-CLI-1", "agent_adv", duration_minutes=15)

        for forbidden_status in ("merged", "rejected", "discarded"):
            rc = fast_cli.run_fast_cli([
                "--db-path", db_path,
                "--project", p_uuid,
                "--node", "agent_adv",
                "kanban", "release", "CARD-CLI-1", "--status", forbidden_status,
            ])
            assert rc == 1
            out = json.loads(capsys.readouterr().out)
            assert out["success"] is False
            assert out["code"] == "HUMAN_SIGNATURE_REQUIRED"

        rc_dec = fast_cli.run_fast_cli([
            "--db-path", db_path,
            "--project", p_uuid,
            "decision", "promote", "DEC-001", "--state", "approved",
        ])
        assert rc_dec == 1
        out_dec = json.loads(capsys.readouterr().out)
        assert out_dec["success"] is False
        assert out_dec["code"] == "HUMAN_SIGNATURE_REQUIRED"

        # CoreKernel KanbanGovernanceService con ActorContext no humano
        kernel = ApplicationFactory.create_test_kernel(db_manager=db)
        for kind in ("mcp_agent", "fast_cli"):
            actor = ActorContext(kind=kind, node_uuid="agent_adv")
            res_t = kernel.kanban.transition_card_status(p_uuid, "CARD-CLI-1", "merged", actor)
            assert res_t["success"] is False
            assert res_t["code"] == "HUMAN_SIGNATURE_REQUIRED"
            res_p = kernel.kanban.promote_decision(p_uuid, "DEC-001", "approved", actor)
            assert res_p["success"] is False
            assert res_p["code"] == "HUMAN_SIGNATURE_REQUIRED"
    finally:
        db.close()


# =========================================================================
# 2. EGM-05-R, EGM-37 y EGM-01-R por A2ABus y ApiBridge
# =========================================================================


def test_egm05r_egm37_egm01r_bus_and_bridge_reject_forged_and_rogue_votes(tmp_path: Path) -> None:
    db_file = tmp_path / "tribunal.db"
    db = DatabaseManager(db_path=str(db_file))
    try:
        proj = db.create_project(name="TribunalAdv")
        p_uuid = proj["project_uuid"]

        crypto_human = NodeCryptoManager(key_path=tmp_path / "human.pem")
        crypto_arbiter = NodeCryptoManager(key_path=tmp_path / "arbiter.pem")
        crypto_attacker = NodeCryptoManager(key_path=tmp_path / "attacker.pem")

        for nid, name, pk in (
            ("node_a", "Alice", crypto_attacker.public_key_base64),
            ("node_b", "Bob", crypto_attacker.public_key_base64),
            ("human_juror", "Human Juror", crypto_human.public_key_base64),
            ("arbiter_node", "External Arbiter", crypto_arbiter.public_key_base64),
        ):
            db.register_or_update_node(nid, name, f"ag_{nid}", status="online", node_kind="human")
            db.add_or_request_collaborator(p_uuid, nid, public_key=pk, role="collaborator", status="approved")

        db.set_project_arbiter(p_uuid, "arbiter_node")

        bus = A2ABus(db=db)
        ticket = bus.open_dispute_ticket(
            project_uuid=p_uuid,
            title="Storage Engine Dispute",
            collision_reason="Conflicto de arquitectura",
            contender_a_node="node_a",
            contender_b_node="node_b",
            proposal_a_ref="Propuesta SQLite",
            proposal_b_ref="Propuesta DuckDB",
        )
        t_id = ticket["ticket_id"]

        # 2a. Bus rechaza firma falsificada
        bad_vote = bus.submit_human_jury_vote(
            ticket_id=t_id,
            node_uuid="human_juror",
            developer_name="Human Juror",
            voted_option="OPTION_A",
            signature_ed25519="forged_base64_sig==",
            public_key=crypto_human.public_key_base64,
            project_uuid=p_uuid,
        )
        assert bad_vote["success"] is False
        assert bad_vote["code"] in (401, 403)

        # 2b. Bus rechaza intento de inyectar una clave pública distinta a la registrada en BD
        spoof_sig = crypto_attacker.sign_human_approval(
            artifact_id=t_id,
            content_text=f"VOTE:OPTION_A:TICKET:{t_id}:PROJECT:{p_uuid}",
            node_uuid="human_juror",
            developer_name="Attacker",
            decision="OPTION_A",
        )
        spoof_res = bus.submit_human_jury_vote(
            ticket_id=t_id,
            node_uuid="human_juror",
            developer_name="Human Juror",
            voted_option="OPTION_A",
            signature_ed25519=spoof_sig["signature_ed25519"],
            public_key=crypto_attacker.public_key_base64,
            signed_at=spoof_sig["signed_at"],
            content_sha256=spoof_sig["content_sha256"],
            project_uuid=p_uuid,
        )
        assert spoof_res["success"] is False
        assert spoof_res["code"] in (401, 403)

        # 2c. EGM-37: ApiBridge.sign_and_approve_item con nodo no autorizado no debe recalcular veredicto
        bridge = ApiBridge(db_manager=db)
        res_bridge_bad = bridge.sign_and_approve_item(
            project_uuid=p_uuid,
            item_type="jury_vote",
            item_id=t_id,
            decision="ALFA",
            voter_node="unregistered_rogue_node",
        )
        assert res_bridge_bad["success"] is False
        assert "verdict" not in res_bridge_bad
        ticket_after = db.get_dispute_ticket(t_id)
        assert ticket_after is not None and ticket_after["status"] == "open"

        # 2d. EGM-01-R: Contendiente o nodo no árbitro no puede emitir laudo arbitral
        contender_ruling = bus.submit_external_arbiter_ruling(
            ticket_id=t_id,
            arbiter_node_uuid="node_a",
            winning_option="OPTION_A",
            signature_ed25519="sig",
            project_uuid=p_uuid,
        )
        assert contender_ruling["success"] is False
        assert contender_ruling["code"] == 403

        # Firma falsificada del árbitro designado
        bad_arbiter_ruling = bus.submit_external_arbiter_ruling(
            ticket_id=t_id,
            arbiter_node_uuid="arbiter_node",
            winning_option="OPTION_B",
            signature_ed25519="forged_ruling_sig",
            public_key=crypto_arbiter.public_key_base64,
            project_uuid=p_uuid,
        )
        assert bad_arbiter_ruling["success"] is False
        assert bad_arbiter_ruling["code"] == 401

        # Verificar que desde ApiBridge también rechaza si el nodo firmante local no tiene pubkey coincidente
        bridge.crypto = crypto_attacker
        res_bridge_ruling_bad = bridge.sign_and_approve_item(
            project_uuid=p_uuid,
            item_type="arbiter_ruling",
            item_id=t_id,
            decision="OPTION_B",
            comment="Laudo no autorizado",
            voter_node="arbiter_node",
        )
        assert res_bridge_ruling_bad["success"] is False

        # Con la clave real del árbitro registrado, EGM-01-R debe ratificar correctamente
        bridge.crypto = crypto_arbiter
        res_bridge_ruling_ok = bridge.sign_and_approve_item(
            project_uuid=p_uuid,
            item_type="arbiter_ruling",
            item_id=t_id,
            decision="OPTION_B",
            comment="Laudo vinculante válido",
            voter_node="arbiter_node",
        )
        assert res_bridge_ruling_ok["success"] is True
        assert res_bridge_ruling_ok["status"] == "ratified"
        assert res_bridge_ruling_ok["resolution_mode"] == "external_arbiter_ruling"
    finally:
        db.close()


# =========================================================================
# 3. ExtensionSecurityGate y OutboundRestConnector anti-SSRF
# =========================================================================


def test_extension_gate_default_deny_rejects_agents_and_forged_manifests(tmp_path: Path) -> None:
    crypto = NodeCryptoManager(key_path=tmp_path / "owner.pem")
    other_crypto = NodeCryptoManager(key_path=tmp_path / "other.pem")
    gate = ExtensionSecurityGate()

    # Default deny
    with pytest.raises(PermissionError, match="EXTENSION_GATE_LOCKED"):
        gate.require_unlocked("outbound_rest")
    with pytest.raises(PermissionError, match="EXTENSION_GATE_LOCKED"):
        gate.require_unlocked("agent_terminal_matrix")

    terminal = NullAgentTerminalConnector(gate)
    assert terminal.is_available() is False
    # EGM-62 / RF-STM-12: NullAgentTerminalConnector degrada sin lanzar PermissionError
    pre_unlock_resp = terminal.spawn_session()
    assert pre_unlock_resp["success"] is False
    assert pre_unlock_resp["degraded"] is True
    # En Windows no hay conector PTY: el codigo es TERMINAL_PLATFORM_UNSUPPORTED (ver test_terminal_windows_degradation).
    assert pre_unlock_resp["code"] == (
        "TERMINAL_PLATFORM_UNSUPPORTED" if sys.platform == "win32" else "TERMINAL_ADAPTER_DEGRADED"
    )
    assert pre_unlock_resp.get("manual_command")
    assert isinstance(pre_unlock_resp.get("mcp_config"), dict)

    manifest = SignedConnectorManifest.create_signed(
        crypto_manager=crypto,
        connector_id="outbound_rest",
        owner_node_uuid="owner_1",
        allowed_domains=["api.github.com"],
        allow_private_ips=False,
    )

    # Agentes y procesos no humanos nunca pueden abrir la puerta
    for non_human in ("mcp_agent", "fast_cli", "system", "agent"):
        with pytest.raises(PermissionError, match="EXTENSION_GATE_LOCKED"):
            gate.unlock_connector(manifest, crypto.public_key_base64, actor_kind=non_human)
        assert gate.is_unlocked("outbound_rest") is False

    # Firma de otra llave pública debe fallar
    with pytest.raises(PermissionError, match="EXTENSION_GATE_LOCKED"):
        gate.unlock_connector(manifest, other_crypto.public_key_base64, actor_kind="human_ui")

    # Manifiesto adulterado tras firmar debe fallar
    tampered = SignedConnectorManifest.from_dict({
        **manifest.to_dict(),
        "allowed_domains": ["*"],
        "allow_private_ips": True,
    })
    with pytest.raises(PermissionError, match="EXTENSION_GATE_LOCKED"):
        gate.unlock_connector(tampered, crypto.public_key_base64, actor_kind="human_ui")

    # Desbloqueo legítimo humano sí abre, y NullAgentTerminalConnector sigue degradando seguro
    term_manifest = SignedConnectorManifest.create_signed(
        crypto_manager=crypto,
        connector_id="agent_terminal_matrix",
        owner_node_uuid="owner_1",
    )
    gate.unlock_connector(term_manifest, crypto.public_key_base64, actor_kind="human_ui")
    term_resp = terminal.spawn_session()
    assert term_resp["success"] is False
    assert term_resp["degraded"] is True
    assert term_resp["code"] == "TERMINAL_ADAPTER_DEGRADED"
    assert term_resp.get("manual_command")
    assert isinstance(term_resp.get("mcp_config"), dict)


def test_outbound_rest_connector_blocks_ssrf_and_unauthorized_domains(tmp_path: Path) -> None:
    crypto = NodeCryptoManager(key_path=tmp_path / "owner_rest.pem")
    gate = ExtensionSecurityGate()
    manifest = SignedConnectorManifest.create_signed(
        crypto_manager=crypto,
        connector_id="outbound_rest",
        owner_node_uuid="owner_1",
        allowed_domains=["api.github.com", "trusted.example"],
        allow_private_ips=False,
    )
    gate.unlock_connector(manifest, crypto.public_key_base64, actor_kind="human_ui")

    called_urls = []
    rest = OutboundRestConnector(
        gate=gate,
        connector_id="outbound_rest",
        dns_resolver=lambda host: ["93.184.216.34"] if host == "trusted.example" else ["140.82.121.6"],
        transport_fn=lambda url, _method, _headers, _body, _timeout: (
            called_urls.append(url) or {"status_code": 200, "body": "ok", "headers": {}}
        ),
    )

    for ssrf_url in (
        "http://127.0.0.1:8080/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]:8000/secret",
        "http://10.0.0.1/internal",
        "https://evil.attacker.com/exfil",
    ):
        with pytest.raises((PermissionError, ValueError)):
            rest.request(ssrf_url)
    assert called_urls == []

    # DNS Rebinding: dominio permitido que resuelve a IP loopback o metadata
    rebinding_rest = OutboundRestConnector(
        gate=gate,
        connector_id="outbound_rest",
        dns_resolver=lambda _host: ["169.254.169.254"],
        transport_fn=lambda *_args, **_kwargs: {"status_code": 200, "body": "leak", "headers": {}},
    )
    with pytest.raises(PermissionError, match="SSRF_BLOCKED_IP"):
        rebinding_rest.request("https://api.github.com/zen")

    # También vía ApiBridge.test_outbound_rest_gate
    db = DatabaseManager(db_path=str(tmp_path / "rest_bridge.db"))
    try:
        bridge = ApiBridge(db_manager=db)
        bridge.unlock_extension_gate("outbound_rest", ["api.github.com"], allow_private_ips=False)
        for bad_url in (
            "http://127.0.0.1:9000/",
            "http://169.254.169.254/latest/meta-data/",
            "https://unauthorized.domain.invalid/api",
        ):
            r = bridge.test_outbound_rest_gate("outbound_rest", bad_url)
            assert r["success"] is False
            assert r["blocked_reason"]
    finally:
        db.close()


# =========================================================================
# 4. Path Traversal en add_okf_folder / remove_okf_folder / rename_okf_folder
# =========================================================================


@pytest.mark.parametrize(
    "evil_path",
    [
        "../outside",
        "../../etc/passwd",
        "/tmp/absolute_escape",
        "nested/../../escape",
        "bad\x00null",
        "C:\\Windows\\System32",
        "",
    ],
)
def test_okf_folder_operations_block_path_traversal(tmp_path: Path, evil_path: str) -> None:
    db = DatabaseManager(db_path=str(tmp_path / "okf_sec.db"))
    try:
        bridge = ApiBridge(db_manager=db)
        proj = bridge.create_project("OkfTraversalTest")["project"]
        p_uuid = proj["project_uuid"]
        gov_path = Path(proj["gov_repo_path"])
        docs_okf = (gov_path / "docs" / "okf").resolve()

        # Crear carpeta legítima para probar rename/remove
        ok_add = bridge.add_okf_folder(p_uuid, "custom_safe", "custom", "Safe folder")
        assert ok_add["success"] is True

        r_add = bridge.add_okf_folder(p_uuid, evil_path)
        assert r_add["success"] is False

        r_rem = bridge.remove_okf_folder(p_uuid, evil_path)
        assert r_rem["success"] is False

        r_ren1 = bridge.rename_okf_folder(p_uuid, "custom_safe", evil_path)
        assert r_ren1["success"] is False

        r_ren2 = bridge.rename_okf_folder(p_uuid, evil_path, "custom_safe_2")
        assert r_ren2["success"] is False

        # Ningún directorio fuera de docs/okf pudo ser creado
        assert not (docs_okf.parent / "outside").exists()
        assert not Path("/tmp/absolute_escape").exists()
    finally:
        db.close()


# =========================================================================
# 5. Replay, Tamper, Stale Timestamp y Flood en MultiPeerMeshConnector
# =========================================================================


def test_multipeer_mesh_blocks_untrusted_tamper_replay_expired_and_flood(tmp_path: Path) -> None:
    c_alice = NodeCryptoManager(key_path=tmp_path / "alice.pem")
    c_bob = NodeCryptoManager(key_path=tmp_path / "bob.pem")
    c_rogue = NodeCryptoManager(key_path=tmp_path / "rogue.pem")

    alice = MultiPeerMeshConnector(node_uuid="alice", crypto_manager=c_alice)
    bob = MultiPeerMeshConnector(
        node_uuid="bob",
        crypto_manager=c_bob,
        replay_window_s=60.0,
        rate_limit_max=3,
        rate_limit_window_s=10.0,
    )
    rogue = MultiPeerMeshConnector(node_uuid="rogue", crypto_manager=c_rogue)

    try:
        bob.register_peer("alice", public_key=c_alice.public_key_base64, endpoint="lan://alice")

        # 5a. Par no registrado rechazado
        rogue_env = rogue.build_signed_envelope({"msg": "spoof"}, target_node_uuid="bob")
        res_rogue = bob.ingest_envelope(rogue_env)
        assert res_rogue["accepted"] is False

        # 5b. Adulteración de payload (tamper) rechazada
        valid_env = alice.build_signed_envelope({"msg": "legit", "seq": 1}, target_node_uuid="bob")
        tampered_env = dict(valid_env)
        tampered_env["payload"] = {"msg": "evil_tampered", "seq": 1}
        res_tamper = bob.ingest_envelope(tampered_env)
        assert res_tamper["accepted"] is False

        # 5c. Adulteración de firma rechazada
        bad_sig_env = dict(valid_env)
        bad_sig_env["sig"] = "AAAA" + bad_sig_env["sig"][4:]
        res_bad_sig = bob.ingest_envelope(bad_sig_env)
        assert res_bad_sig["accepted"] is False

        # 5d. Timestamp expirado fuera de replay_window_s rechazado incluso con firma válida
        stale_env = alice.build_signed_envelope({"msg": "old"}, target_node_uuid="bob")
        stale_env["timestamp"] = time.time() - 300.0
        stale_env["sig"] = c_alice.sign_payload(alice._envelope_core(stale_env))
        res_stale = bob.ingest_envelope(stale_env)
        assert res_stale["accepted"] is False

        # 5e. Replay del mismo message_uuid rechazado en el segundo intento
        first = bob.ingest_envelope(valid_env)
        assert first["accepted"] is True
        replay = bob.ingest_envelope(valid_env)
        assert replay["accepted"] is False
        assert "replay" in replay.get("reason", "").lower() or "duplicate" in replay.get("reason", "").lower()

        # 5f. Flood (> rate_limit_max=3 en ventana) rechazado
        # Ya consumió 1 hueco con valid_env; quedan 2 más permitidos antes del bloqueo por flood
        acc_count = 0
        rej_reasons = []
        for idx in range(6):
            env = alice.build_signed_envelope({"msg": "flood", "i": idx}, target_node_uuid="bob")
            r = bob.ingest_envelope(env)
            if r["accepted"]:
                acc_count += 1
            else:
                rej_reasons.append(r.get("reason", ""))
        assert acc_count == 2
        assert len(rej_reasons) == 4
        assert any("rate" in reason.lower() or "flood" in reason.lower() or "limit" in reason.lower() for reason in rej_reasons)
    finally:
        alice.close()
        bob.close()
        rogue.close()
