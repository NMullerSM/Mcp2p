"""Aceptacion F1 (Ola 3, dossier 37 §8): identidad del dispositivo, sobre firmado, bandeja persistente, auditoria y
herramientas del relevo. EGM-105 con procesos reales esta en `test_peer_process.py`; los vectores del sobre en
`test_envelope_vectors.py`; la cadena causal en `test_relay_chain.py`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from src.adapters.identity.ed25519_identity import (
    KEYRING_SERVICE,
    STORAGE_ENCRYPTED_FILE,
    STORAGE_KEYRING,
    DeviceIdentity,
    DeviceKeyStore,
)
from src.core.domain.envelope import build_header

# ----------------------------------------------------------------------------------------------------------------
# F1.3 Clave del dispositivo: llavero del sistema con archivo cifrado de respaldo (D15)
# ----------------------------------------------------------------------------------------------------------------


class FakeKeyring:
    """Backend de `keyring` en memoria (no toca el llavero real)."""

    priority = 5

    def __init__(self) -> None:
        self.store: Dict[tuple, str] = {}
        self.sets = 0

    def get_password(self, service: str, username: str) -> Optional[str]:
        return self.store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.sets += 1
        self.store[(service, username)] = password


class ForgetfulKeyring(FakeKeyring):
    """Acepta y olvida (como `keyring.backends.null`): no debe contar como llavero."""

    def set_password(self, service: str, username: str, password: str) -> None:
        self.sets += 1


class BrokenKeyring(FakeKeyring):
    def get_password(self, service: str, username: str) -> Optional[str]:
        from keyring.errors import KeyringLocked

        raise KeyringLocked("bloqueado")


def _header(**over: Any) -> Dict[str, Any]:
    base = dict(
        msg_id="m-1", kind="note", from_uri="pane:pane-a", from_device="device:dev-1", to_uri="pane:pane-b",
        project_uuid="p", chain_id="c-1", hop=0, parent_msg_id=None, content="hola", created_at=1000,
        expires_at=2000, trust="pane_authenticated",
    )
    base.update(over)
    return build_header(**base)


def test_device_key_lives_in_the_os_keyring_and_is_stable(tmp_path: Path) -> None:
    kr = FakeKeyring()
    first = DeviceIdentity("dev-1", key_store=DeviceKeyStore("dev-1", base_dir=tmp_path, keyring_backend=kr))
    assert first.key_store.storage == STORAGE_KEYRING and first.key_store.warning is None
    assert (KEYRING_SERVICE, "dev-1") in kr.store
    assert not (tmp_path / "device_identity.enc").exists()  # nada en disco
    again = DeviceIdentity("dev-1", key_store=DeviceKeyStore("dev-1", base_dir=tmp_path, keyring_backend=kr))
    assert again.public_key_b64() == first.public_key_b64()
    env = first.sign_header(_header())
    assert again.verify(env, "hola").ok and env["sig"]["key"] == first.fingerprint()


def test_without_usable_keyring_falls_back_to_encrypted_file_with_warning(tmp_path: Path) -> None:
    from keyring.backends.fail import Keyring as FailKeyring

    for backend in (FailKeyring(), ForgetfulKeyring(), BrokenKeyring()):
        base = tmp_path / type(backend).__name__
        ident = DeviceIdentity("dev-1", key_store=DeviceKeyStore("dev-1", base_dir=base, keyring_backend=backend))
        assert ident.key_store.storage == STORAGE_ENCRYPTED_FILE
        assert ident.key_store.warning and "archivo cifrado" in ident.key_store.warning
        blob = (base / "device_identity.enc").read_bytes()
        seed = ident._private_key.private_bytes_raw()
        assert seed not in blob and json.loads(blob)["alg"] == "fernet+hkdf-sha256"
        reloaded = DeviceIdentity(
            "dev-1", key_store=DeviceKeyStore("dev-1", base_dir=base, keyring_backend=FailKeyring())
        )
        assert reloaded.public_key_b64() == ident.public_key_b64()


def test_keyring_adopts_an_existing_file_key_instead_of_rotating(tmp_path: Path) -> None:
    from keyring.backends.fail import Keyring as FailKeyring

    old = DeviceIdentity("dev-1", key_store=DeviceKeyStore("dev-1", base_dir=tmp_path, keyring_backend=FailKeyring()))
    kr = FakeKeyring()
    new = DeviceIdentity("dev-1", key_store=DeviceKeyStore("dev-1", base_dir=tmp_path, keyring_backend=kr))
    assert new.key_store.storage == STORAGE_KEYRING and new.public_key_b64() == old.public_key_b64()


def test_tampered_or_foreign_key_file_is_not_trusted(tmp_path: Path) -> None:
    from keyring.backends.fail import Keyring as FailKeyring

    store = DeviceKeyStore("dev-1", base_dir=tmp_path, keyring_backend=FailKeyring())
    original = DeviceIdentity("dev-1", key_store=store)
    doc = json.loads((tmp_path / "device_identity.enc").read_text())
    doc["token"] = doc["token"][:-6] + "AAAAAA"
    (tmp_path / "device_identity.enc").write_text(json.dumps(doc))
    regenerated = DeviceIdentity("dev-1", key_store=DeviceKeyStore("dev-1", base_dir=tmp_path, keyring_backend=FailKeyring()))
    assert regenerated.public_key_b64() != original.public_key_b64()
    assert "no se pudo descifrar" in (regenerated.key_store.warning or "")
    assert list(tmp_path.glob("device_identity.enc.broken-*"))
    # otro dispositivo no puede abrir el archivo de este
    other = DeviceKeyStore("dev-2", base_dir=tmp_path, keyring_backend=FailKeyring())
    assert DeviceIdentity("dev-2", key_store=other).public_key_b64() != regenerated.public_key_b64()


def test_device_certificate_requires_the_unlocked_human_key(tmp_path: Path) -> None:
    from keyring.backends.fail import Keyring as FailKeyring
    from src.p2p.crypto import NodeCryptoManager

    store = DeviceKeyStore("dev-1", base_dir=tmp_path, keyring_backend=FailKeyring())
    assert DeviceIdentity("dev-1", key_store=store).device_certificate() is None  # sin clave humana

    class _Locked:
        is_locked = True

        def sign_payload(self, payload):  # pragma: no cover - no debe llamarse
            raise AssertionError("no debe firmar bloqueada")

    locked = DeviceIdentity("dev-1", key_store=store, human_signer=_Locked())
    assert locked.device_certificate() is None

    human = NodeCryptoManager(key_path=tmp_path / "human" / "node_identity.key")
    ident = DeviceIdentity("dev-1", key_store=store, human_signer=human)
    cert = ident.device_certificate()
    assert cert is not None and cert["device_ed25519_pub"] == ident.public_key_b64()
    assert cert["human_pub"] == human.public_key_base64 and ident.certificate_is_valid(cert)
    forged = dict(cert, device_ed25519_pub="AAAA")
    assert ident.certificate_is_valid(forged) is False
    # persistido y reutilizado aunque despues no haya clave humana
    assert DeviceIdentity("dev-1", key_store=store).device_certificate() == cert
