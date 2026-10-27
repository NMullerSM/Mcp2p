import tempfile
from pathlib import Path
import pytest

from src.p2p.crypto import NodeCryptoManager
from src.storage.db import DatabaseManager
from src.storage.github_sync import GitHubSyncManager
from src.app.api_bridge import ApiBridge


def test_crypto_signing_and_verification(tmp_path: Path):
    key_file = tmp_path / "test_identity.key"
    crypto = NodeCryptoManager(key_path=key_file)

    payload = {"project_uuid": "proj_123", "action": "CLAIM_TASK", "card_id": "c-9"}
    sig = crypto.sign_payload(payload)
    assert sig is not None

    pub_key = crypto.public_key_base64
    assert NodeCryptoManager.verify_signature(payload, sig, pub_key) is True

    # Verificar que carga la misma llave en segunda inicialización
    crypto2 = NodeCryptoManager(key_path=key_file)
    assert crypto2.public_key_base64 == pub_key


def test_signed_invite_token_lifecycle(tmp_path: Path):
    key_file = tmp_path / "test_invite.key"
    crypto = NodeCryptoManager(key_path=key_file)

    invite_data = {
        "project_uuid": "proj_crypto_456",
        "name": "Proyecto Criptográfico",
        "code_repo_url": "https://github.com/user/code.git",
        "gov_repo_url": "https://github.com/user/gov.git"
    }

    token = crypto.create_signed_invite_token(invite_data)
    assert token

    is_valid, decoded, err = NodeCryptoManager.decode_and_verify_invite_token(token)
    assert is_valid is True
    assert decoded["project_uuid"] == "proj_crypto_456"
    assert decoded["name"] == "Proyecto Criptográfico"
    assert err == ""


def test_project_collaborator_approval_hitl(tmp_path: Path):
    db_file = tmp_path / "test_collab.db"
    db = DatabaseManager(db_path=str(db_file))
    proj = db.create_project(name="Proyecto HITL", init_git=False)
    p_uuid = proj["project_uuid"]

    # Añadir colaborador en estado pendiente
    db.add_or_request_collaborator(
        project_uuid=p_uuid,
        node_uuid="node_bob_123",
        github_username="bob_dev",
        public_key="pubkey_bob",
        status="pending_approval"
    )

    collabs = db.get_project_collaborators(p_uuid)
    # Debe estar el owner y bob
    assert len(collabs) == 2
    bob = next(c for c in collabs if c["node_uuid"] == "node_bob_123")
    assert bob["status"] == "pending_approval"
    assert bob["github_username"] == "bob_dev"

    # Dueño aprueba explícitamente a Bob (HITL)
    assert db.approve_collaborator(p_uuid, "node_bob_123") is True

    collabs_after = db.get_project_collaborators(p_uuid)
    bob_approved = next(c for c in collabs_after if c["node_uuid"] == "node_bob_123")
    assert bob_approved["status"] == "approved"
    assert bob_approved["approved_at"] is not None


def test_api_bridge_mcp_snippet_and_okf_overview(tmp_path: Path):
    db_file = tmp_path / "test_bridge_ext.db"
    db = DatabaseManager(db_path=str(db_file))
    bridge = ApiBridge(db_manager=db)

    # 1. MCP Snippet
    snippet_res = bridge.get_mcp_config_snippet()
    assert snippet_res["success"] is True
    assert "mcpServers" in snippet_res["mcp_json"]
    assert "Actúa como un agente colaborador" in snippet_res["agent_seed_prompt"]

    # 2. GitHub Status
    gh_res = bridge.get_github_status()
    assert gh_res["success"] is True
    assert "authenticated" in gh_res["github"]

    # 3. OKF Overview
    okf_res = bridge.get_okf_overview()
    assert okf_res["success"] is True
    assert "adrs" in okf_res
    assert "skills" in okf_res
