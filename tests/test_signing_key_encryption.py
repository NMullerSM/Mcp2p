"""
EGM-53 / Gate 0c: la clave Ed25519 humana puede vivir cifrada en disco (scrypt + AES-256-GCM)
y, una vez cifrada, ningún camino de firma funciona sin que el humano la desbloquee en el
proceso GUI con una frase que nunca se persiste.
"""

from __future__ import annotations

import base64
import stat
from pathlib import Path

import pytest

import src.p2p.crypto as crypto_mod
from src.p2p.crypto import (
    IdentityKeyCorruptError,
    NodeCryptoManager,
    SigningKeyLockedError,
    SigningKeyPassphraseError,
)

PASS = "correct horse battery staple"


@pytest.fixture(autouse=True)
def _fast_scrypt(monkeypatch):
    monkeypatch.setattr(NodeCryptoManager, "SCRYPT_N", 2 ** 10)


@pytest.fixture
def key_path(tmp_path) -> Path:
    return tmp_path / "id" / "node_identity.key"


def _encrypted(key_path: Path) -> NodeCryptoManager:
    mgr = NodeCryptoManager(key_path=key_path)
    mgr.set_passphrase(PASS)
    return mgr


def test_plain_key_behaviour_is_unchanged(key_path):
    mgr = NodeCryptoManager(key_path=key_path)
    assert key_path.exists() and not mgr.is_encrypted and not mgr.is_locked
    payload = {"a": 1}
    assert NodeCryptoManager.verify_signature(payload, mgr.sign_payload(payload), mgr.public_key_base64)


def test_set_passphrase_migrates_and_removes_plain_pem(key_path):
    mgr = NodeCryptoManager(key_path=key_path)
    pub_before = mgr.public_key_base64
    seed = mgr._private_key.private_bytes(
        crypto_mod.serialization.Encoding.Raw,
        crypto_mod.serialization.PrivateFormat.Raw,
        crypto_mod.serialization.NoEncryption(),
    )
    mgr.set_passphrase(PASS)

    assert not key_path.exists(), "el PEM en claro debe borrarse tras migrar"
    enc = mgr.encrypted_key_path
    assert enc.exists()
    assert stat.S_IMODE(enc.stat().st_mode) == 0o600
    blob = enc.read_bytes()
    assert base64.urlsafe_b64encode(seed) not in blob and seed not in blob
    assert b"PRIVATE KEY" not in blob

    reloaded = NodeCryptoManager(key_path=key_path)
    assert reloaded.public_key_base64 == pub_before, "la identidad pública no cambia al cifrar"


def test_new_process_starts_locked_and_every_signing_path_refuses(key_path):
    _encrypted(key_path)
    mgr = NodeCryptoManager(key_path=key_path)
    assert mgr.is_encrypted and mgr.is_locked
    assert mgr.public_key_base64  # la identidad sigue siendo legible

    for attempt in (
        lambda: mgr.sign_payload({"artifact_id": "X"}),
        lambda: mgr.sign_human_approval("ADR-1", "texto", "node", "dev", "APPROVED"),
        lambda: mgr.create_signed_invite_token({"p": 1}),
        lambda: mgr.sign_connector_manifest({"name": "c"}),
    ):
        with pytest.raises(SigningKeyLockedError) as exc:
            attempt()
        assert exc.value.code == "KEY_LOCKED"


def test_wrong_passphrase_keeps_key_locked(key_path):
    _encrypted(key_path)
    mgr = NodeCryptoManager(key_path=key_path)
    with pytest.raises(SigningKeyPassphraseError) as exc:
        mgr.unlock("not the passphrase at all")
    assert exc.value.code == "BAD_PASSPHRASE"
    assert mgr.is_locked
    with pytest.raises(SigningKeyLockedError):
        mgr.sign_payload({"x": 1})


def test_tampered_ciphertext_is_rejected(key_path):
    import json

    _encrypted(key_path)
    enc = key_path.with_name(key_path.name + ".enc")
    meta = json.loads(enc.read_text())
    # Atacante sustituye la clave pública por la suya: el AAD de GCM lo detecta.
    meta["public_key"] = NodeCryptoManager(key_path=key_path.parent / "other.key").public_key_base64
    enc.write_text(json.dumps(meta))
    mgr = NodeCryptoManager(key_path=key_path)
    with pytest.raises(SigningKeyPassphraseError):
        mgr.unlock(PASS)


def test_unlock_signs_with_same_identity_and_lock_forgets(key_path):
    _encrypted(key_path)
    mgr = NodeCryptoManager(key_path=key_path)
    mgr.unlock(PASS)
    block = mgr.sign_human_approval("ADR-1", "contenido", "node", "dev", "APPROVED")
    assert NodeCryptoManager.verify_artifact_signature("contenido", block)
    assert block["public_key"] == mgr.public_key_base64
    mgr.lock()
    assert mgr.is_locked
    with pytest.raises(SigningKeyLockedError):
        mgr.sign_payload({"x": 1})


def test_unlock_expires_after_ttl(key_path, monkeypatch):
    _encrypted(key_path)
    mgr = NodeCryptoManager(key_path=key_path)
    now = [1000.0]
    monkeypatch.setattr(crypto_mod.time, "monotonic", lambda: now[0])
    mgr.unlock(PASS, ttl_seconds=60)
    mgr.sign_payload({"x": 1})
    now[0] += 61
    assert mgr.is_locked
    with pytest.raises(SigningKeyLockedError):
        mgr.sign_payload({"x": 1})


def test_short_passphrase_rejected_and_nothing_written(key_path):
    mgr = NodeCryptoManager(key_path=key_path)
    with pytest.raises(SigningKeyPassphraseError):
        mgr.set_passphrase("short")
    assert key_path.exists() and not mgr.is_encrypted


def test_interrupted_migration_cleans_matching_plain_pem(key_path):
    mgr = NodeCryptoManager(key_path=key_path)
    pem = key_path.read_bytes()
    mgr.set_passphrase(PASS)
    key_path.write_bytes(pem)  # simula caída entre escribir .enc y borrar el PEM
    reloaded = NodeCryptoManager(key_path=key_path)
    assert not key_path.exists() and reloaded.is_locked


def test_interrupted_migration_with_foreign_plain_pem_fails_loudly(key_path):
    _encrypted(key_path)
    other = NodeCryptoManager(key_path=key_path.parent / "other.key")
    key_path.write_bytes((key_path.parent / "other.key").read_bytes())
    assert other.public_key_base64
    with pytest.raises(IdentityKeyCorruptError):
        NodeCryptoManager(key_path=key_path)


# ----------------------------------------------------------------------------
# ApiBridge: métodos que consumirá la UI y la ruta JS anidada de EGM-53
# ----------------------------------------------------------------------------


@pytest.fixture
def bridge(tmp_path, key_path):
    from src.app.api_bridge import ApiBridge
    from src.storage.db import DatabaseManager

    b = ApiBridge(db_manager=DatabaseManager(str(tmp_path / "bridge.db")))
    b.crypto = NodeCryptoManager(key_path=key_path)
    return b


def test_bridge_unlock_flow_and_js_reachable_path_is_locked(bridge, key_path):
    st = bridge.get_signing_key_status()
    assert st["success"] and st["encrypted"] is False and st["locked"] is False

    assert bridge.unlock_signing_key(PASS)["code"] == "KEY_NOT_ENCRYPTED"
    assert bridge.set_signing_key_passphrase("short")["code"] == "BAD_PASSPHRASE"

    res = bridge.set_signing_key_passphrase(PASS)
    assert res["success"] and res["encrypted"] and not res["locked"]
    assert not key_path.exists()

    assert bridge.lock_signing_key()["locked"] is True
    # La ruta que un JS inyectado en el webview alcanzaría por get_nested_attribute:
    for crypto in (bridge.crypto, bridge.tribunal_ui.crypto, bridge.kanban_ui.crypto):
        with pytest.raises(SigningKeyLockedError):
            crypto.sign_human_approval("ADR-9", "x", "n", "d", "APPROVED")

    bad = bridge.unlock_signing_key("wrong passphrase!!")
    assert bad["success"] is False and bad["code"] == "BAD_PASSPHRASE"
    ok = bridge.unlock_signing_key(PASS)
    assert ok["success"] and ok["locked"] is False
    bridge.crypto.sign_payload({"x": 1})


def test_hitl_sign_and_approve_reports_key_locked_code(bridge, tmp_path):
    p_uuid = "proj-egm53"
    bridge.db.create_project(name="P", base_path=str(tmp_path / "projs"), init_git=False, project_uuid=p_uuid)
    gov = Path(bridge.db.get_project_by_uuid(p_uuid)["gov_repo_path"])
    docs_base = gov / "docs" / "okf" if (gov / "docs" / "okf").exists() else gov
    dec_dir = docs_base / "architecture" / "decisions"
    dec_dir.mkdir(parents=True, exist_ok=True)
    (dec_dir / "ADR-777-x.md").write_text("# ADR-777\n", encoding="utf-8")
    bridge.set_signing_key_passphrase(PASS)
    bridge.lock_signing_key()
    res = bridge.tribunal_ui.sign_and_approve_item(p_uuid, "decision", "ADR-777")
    assert res["success"] is False and res["code"] == "KEY_LOCKED"


def test_mcp_kernel_never_loads_crypto(tmp_path):
    from src.core.factory import ApplicationFactory

    kernel = ApplicationFactory.create_mcp_kernel(db_path=str(tmp_path / "mcp.db"))
    assert getattr(kernel, "crypto", None) is None
