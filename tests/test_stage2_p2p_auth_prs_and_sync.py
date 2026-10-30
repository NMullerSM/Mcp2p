"""
Pruebas exhaustivas para Etapa 2:
1. Autenticación y GitHub OAuth Device Flow (RFC 8628).
2. Seguridad Criptográfica Ed25519 estricta en Tokens P2P.
3. Gestión y Auto-Merge de Pull Requests con Grace Period (SLA).
4. Red P2P Híbrida: LAN Webhook Listener + WAN git ls-remote watcher.
"""

import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
import pytest

from src.storage.db import DatabaseManager
from src.storage.github_sync import GitHubSyncManager
from src.p2p.crypto import NodeCryptoManager
from src.p2p.network import P2PNotificationService


@pytest.fixture
def stage2_env(tmp_path):
    db_path = tmp_path / "stage2.db"
    db_mgr = DatabaseManager(str(db_path))
    gh_sync = GitHubSyncManager(db_mgr)
    return {
        "db": db_mgr,
        "gh": gh_sync,
        "root": tmp_path
    }


def test_ed25519_strict_token_verification(tmp_path):
    """Verifica que tokens no firmados o adulterados sean estrictamente rechazados."""
    # 1. Token válido firmado
    crypto_mgr = NodeCryptoManager(key_path=tmp_path / "test_identity.key")
    data = {"project_uuid": "proj_xyz", "role": "developer"}
    token = crypto_mgr.create_signed_invite_token(data)

    is_valid, payload, err = NodeCryptoManager.decode_and_verify_invite_token(token)
    assert is_valid is True
    assert payload["project_uuid"] == "proj_xyz"
    assert err == ""

    # 2. Token adulterado -> Debe ser rechazado
    import base64
    padded = token + "=" * (-len(token) % 4)
    raw_dict = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    raw_dict["data"]["role"] = "owner_infiltrator"  # alteración de contenido
    tampered_bytes = json.dumps(raw_dict).encode("utf-8")
    tampered_b64 = base64.urlsafe_b64encode(tampered_bytes).decode("utf-8").rstrip("=")

    is_valid_tamp, _, err_tamp = NodeCryptoManager.decode_and_verify_invite_token(tampered_b64)
    assert is_valid_tamp is False
    assert "Firma digital del token P2P no válida" in err_tamp

    # 3. Token plano sin firma -> Rechazado por omisión de allow_unsigned
    unsigned_dict = {"data": {"project_uuid": "proj_unsigned"}}
    unsigned_b64 = base64.urlsafe_b64encode(json.dumps(unsigned_dict).encode("utf-8")).decode("utf-8").rstrip("=")

    is_valid_uns, _, err_uns = NodeCryptoManager.decode_and_verify_invite_token(unsigned_b64)
    assert is_valid_uns is False
    assert "Carece de firma" in err_uns


def test_github_oauth_device_flow_structure(stage2_env):
    """Verifica la estructura de inicio del Device Flow RFC 8628."""
    gh = stage2_env["gh"]
    
    # Comprobar inicio del flujo (simulado / respuesta de contrato)
    flow = gh.start_oauth_device_flow(client_id="dummy_client_id")
    assert "success" in flow
    if flow["success"]:
        assert "user_code" in flow
        assert "verification_uri" in flow
    else:
        # Si no hay conexión externa en el runner, debe proveer mensaje claro y fallback
        assert "fallback_suggestion" in flow


def test_pull_request_creation_and_grace_period_evaluation(stage2_env):
    """
    Verifica la creación de PRs con metadatos de gobernanza OKF y auto-merge tras ventana de gracia.
    """
    gh = stage2_env["gh"]
    db = stage2_env["db"]
    root = stage2_env["root"]

    # Crear repo git local para pruebas de merge
    local_repo = root / "app_code"
    local_repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=str(local_repo), capture_output=True)
    (local_repo / "main.py").write_text("print('version 1')\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(local_repo), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init main"], cwd=str(local_repo), capture_output=True)

    # Crear rama feature
    subprocess.run(["git", "checkout", "-b", "feature/task-pr"], cwd=str(local_repo), capture_output=True)
    (local_repo / "main.py").write_text("print('version 2 - pr')\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(local_repo), capture_output=True)
    subprocess.run(["git", "commit", "-m", "add feature"], cwd=str(local_repo), capture_output=True)
    subprocess.run(["git", "checkout", "main"], cwd=str(local_repo), capture_output=True)

    # 1. Crear PR con 72h de período de gracia (SLA)
    pr_res = gh.create_pull_request(
        repo_owner="test_org",
        repo_name="test_repo",
        title="feat: nueva funcionalidad P2P",
        head_branch="feature/task-pr",
        base_branch="main",
        body="Implementación de nodos P2P",
        card_id="TASK-88",
        sla_hours=72
    )
    assert pr_res["success"] is True
    assert pr_res["card_id"] == "TASK-88"
    assert pr_res["grace_period_expires_at"] is not None

    # 2. Evaluar PR antes de que expire la ventana -> NO debe permitir auto-merge sin force
    eval_pre = gh.evaluate_and_merge_pull_request(
        repo_owner="test_org",
        repo_name="test_repo",
        pr_number=pr_res["pr_number"],
        local_repo_path=local_repo,
        force_auto_merge=False
    )
    # Sin force_auto_merge y sin token remoto, previene merge antes de expiración
    assert eval_pre["success"] is False or eval_pre.get("merged") is not True

    # 3. Forzar auto-merge (simulando expiración del SLA o aprobación unánime de pares)
    eval_post = gh.evaluate_and_merge_pull_request(
        repo_owner="test_org",
        repo_name="test_repo",
        pr_number=pr_res["pr_number"],
        local_repo_path=local_repo,
        force_auto_merge=True
    )
    assert eval_post["success"] is True
    assert eval_post["merged"] is True

    # Verificar que main tiene los cambios de la feature branch
    content = (local_repo / "main.py").read_text(encoding="utf-8")
    assert "version 2 - pr" in content


def test_p2p_hybrid_lan_server_and_broadcast(stage2_env):
    """
    Verifica el servidor de notificaciones LAN en tiempo real y el broadcasting de eventos.
    """
    events_received = []

    def on_event(evt):
        events_received.append(evt)

    # Iniciar servicio P2P
    p2p_svc = P2PNotificationService(
        node_uuid="node_alpha",
        lan_port=42430,
        on_event_callback=on_event
    )

    started = p2p_svc.start_lan_server()
    assert started is True
    time.sleep(0.1)

    try:
        # Enviar aviso local simulando un par en la misma red
        local_target = f"127.0.0.1:{p2p_svc.lan_port}"
        res = p2p_svc.broadcast_lan_notification(
            peer_endpoints=[local_target],
            event_payload={"action": "card_updated", "card_id": "TASK-99", "status": "in_review"}
        )
        assert res["delivered"] == 1
        time.sleep(0.1)

        # Verificar que el callback fue invocado
        assert len(events_received) >= 1
        assert events_received[0]["action"] == "card_updated"
        assert events_received[0]["card_id"] == "TASK-99"
    finally:
        p2p_svc.stop_lan_server()


def test_p2p_wan_remote_watcher(stage2_env):
    """
    Verifica que el sondeo WAN detecte cambios en repositorios remotos sin tocar el working tree.
    """
    root = stage2_env["root"]
    
    # Crear un repo "remoto" local
    bare_remote = root / "remote_origin.git"
    bare_remote.mkdir()
    subprocess.run(["git", "init", "--bare"], cwd=str(bare_remote), capture_output=True)

    # Crear repo local y empujar a ese remoto
    local_repo = root / "local_repo"
    local_repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=str(local_repo), capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", str(bare_remote)], cwd=str(local_repo), capture_output=True)
    (local_repo / "file.txt").write_text("commit 1", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(local_repo), capture_output=True)
    subprocess.run(["git", "commit", "-m", "c1"], cwd=str(local_repo), capture_output=True)
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=str(local_repo), capture_output=True)

    p2p_svc = P2PNotificationService(node_uuid="node_test")

    # Primer chequeo: carga la referencia inicial
    check1 = p2p_svc.check_wan_remote_changes(local_repo)
    assert check1["success"] is True
    assert check1["has_changes"] is False

    # Simular que otro par empuja un nuevo commit al remoto
    other_peer = root / "other_peer"
    subprocess.run(["git", "clone", "-b", "main", str(bare_remote), str(other_peer)], capture_output=True)
    (other_peer / "file.txt").write_text("commit 2 por otro par", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(other_peer), capture_output=True)
    subprocess.run(["git", "commit", "-m", "c2"], cwd=str(other_peer), capture_output=True)
    subprocess.run(["git", "push", "origin", "main"], cwd=str(other_peer), capture_output=True)

    # Segundo chequeo: debe detectar has_changes == True
    check2 = p2p_svc.check_wan_remote_changes(local_repo)
    assert check2["success"] is True
    assert check2["has_changes"] is True
    assert "refs/heads/main" in check2["changed_refs"]
