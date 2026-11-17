"""Clave Ed25519 del DISPOSITIVO (F1.3 del dossier 37, decision D15): firma los sobres que sella el Hub.

Almacenamiento (por orden):

1. **Llavero del sistema** via `keyring` (Secret Service/KWallet en Linux, Credential Manager en Windows, Keychain en
   macOS). Servicio ``mcp2p-device-identity``, usuario = `node_uuid` del dispositivo, valor = semilla de 32 bytes en
   base64url. Se comprueba leyendo lo escrito (un backend «null» que acepta y olvida no cuenta).
2. **Archivo cifrado** de respaldo (`~/.mcp2p/device_identity.enc`) si no hay llavero utilizable (p. ej. CI Linux sin
   Secret Service): Fernet con una clave HKDF-SHA256 derivada de un secreto local (`device_identity.secret`, 32 bytes
   aleatorios, 0600) + el identificador de la maquina si existe (`/etc/machine-id`, `MachineGuid`). No pide la frase
   del humano: el Hub debe poder firmar sin desbloqueo. Protege frente a la copia suelta del archivo o a llevarselo a
   otra maquina; NO frente a un proceso del mismo usuario (riesgo residual del 37 §10). Se deja un aviso.

El **certificado del dispositivo** (`{node_uuid, device_ed25519_pub, not_after, human_pub}` firmado por la clave humana)
solo se emite si la clave humana esta desbloqueada; si no, los sobres llevan ``device_cert: null``.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import secrets
import sys
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from src.adapters.identity.envelope_crypto import (
    EnvelopeVerdict,
    b64url,
    b64url_decode,
    key_fingerprint,
    raw_public_key,
    sign_header,
    verify_envelope,
)

logger = logging.getLogger("mcp2p.adapters.identity.device")

KEYRING_SERVICE = "mcp2p-device-identity"
STORAGE_KEYRING = "keyring"
STORAGE_ENCRYPTED_FILE = "encrypted_file"
_FILE_VERSION = 1
_HKDF_INFO = b"mcp2p-device-key/v1"
CERT_VALIDITY_MS = 365 * 24 * 60 * 60 * 1000


def _default_base_dir() -> Path:
    return Path.home() / ".mcp2p"


def _machine_id() -> bytes:
    """Identificador estable de la maquina si el sistema lo ofrece ('' si no)."""
    if sys.platform.startswith("linux"):
        for candidate in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
            try:
                value = Path(candidate).read_text(encoding="ascii").strip()
                if value:
                    return value.encode("ascii")
            except OSError:
                continue
    elif sys.platform == "win32":  # pragma: no cover - solo Windows
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
                value, _ = winreg.QueryValueEx(key, "MachineGuid")
                return str(value).encode("utf-8")
        except OSError:
            pass
    return b""


def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    tmp = path.with_name(path.name + f".tmp-{secrets.token_hex(4)}")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    os.replace(tmp, path)


class DeviceKeyStore:
    """Carga o crea la semilla Ed25519 del dispositivo: llavero del SO o, si no hay, archivo cifrado."""

    def __init__(
        self,
        device_id: str,
        *,
        base_dir: Optional[Path] = None,
        keyring_backend: Any = None,
        use_keyring: bool = True,
    ) -> None:
        if not isinstance(device_id, str) or not device_id.strip():
            raise ValueError("device_id requerido")
        self.device_id = device_id.strip()
        self.base_dir = Path(base_dir) if base_dir is not None else _default_base_dir()
        self._keyring_backend = keyring_backend
        self._use_keyring = use_keyring
        self.storage: Optional[str] = None
        self.warning: Optional[str] = None

    @property
    def file_path(self) -> Path:
        return self.base_dir / "device_identity.enc"

    @property
    def secret_path(self) -> Path:
        return self.base_dir / "device_identity.secret"

    # -- llavero -------------------------------------------------------------------------------------------------

    def _keyring(self) -> Any:
        if self._keyring_backend is not None:
            return self._keyring_backend
        import keyring

        return keyring.get_keyring()

    def _usable_keyring(self) -> Optional[Any]:
        if not self._use_keyring:
            return None
        try:
            backend = self._keyring()
        except Exception as exc:
            self.warning = f"Llavero del sistema no disponible ({type(exc).__name__})."
            return None
        try:
            priority = float(getattr(type(backend), "priority", getattr(backend, "priority", 1)))
        except Exception:
            priority = 1.0
        if priority <= 0:
            self.warning = "Llavero del sistema no disponible (sin backend utilizable)."
            return None
        return backend

    def _load_from_keyring(self, backend: Any) -> Optional[bytes]:
        try:
            raw = backend.get_password(KEYRING_SERVICE, self.device_id)
        except Exception as exc:
            self.warning = f"Llavero del sistema no disponible ({type(exc).__name__})."
            return None
        if raw is None:
            return b""
        try:
            seed = b64url_decode(raw)
        except ValueError:
            seed = b""
        if len(seed) != 32:
            self.warning = "La clave del dispositivo en el llavero estaba dañada; se genera otra."
            return b""
        return seed

    def _store_in_keyring(self, backend: Any, seed: bytes) -> bool:
        try:
            backend.set_password(KEYRING_SERVICE, self.device_id, b64url(seed))
            return backend.get_password(KEYRING_SERVICE, self.device_id) == b64url(seed)
        except Exception as exc:
            self.warning = f"No se pudo guardar en el llavero del sistema ({type(exc).__name__})."
            return False

    # -- archivo cifrado ------------------------------------------------------------------------------------------

    def _local_secret(self, *, create: bool) -> Optional[bytes]:
        try:
            secret = self.secret_path.read_bytes()
            if len(secret) == 32:
                return secret
        except OSError:
            pass
        if not create:
            return None
        secret = secrets.token_bytes(32)
        _write_private(self.secret_path, secret)
        return secret

    def _fernet(self, salt: bytes, secret: bytes) -> Fernet:
        ikm = secret + b"|" + _machine_id() + b"|" + self.device_id.encode("utf-8")
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=_HKDF_INFO).derive(ikm)
        return Fernet(base64.urlsafe_b64encode(key))

    def _load_from_file(self) -> Optional[bytes]:
        if not self.file_path.exists():
            return None
        try:
            doc = json.loads(self.file_path.read_text(encoding="utf-8"))
            secret = self._local_secret(create=False)
            if doc.get("v") != _FILE_VERSION or secret is None or doc.get("device_id") != self.device_id:
                raise ValueError("formato")
            seed = self._fernet(b64url_decode(doc["salt"]), secret).decrypt(doc["token"].encode("ascii"))
            if len(seed) != 32:
                raise ValueError("semilla")
            return seed
        except (OSError, ValueError, KeyError, TypeError, InvalidToken) as exc:
            backup = self.file_path.with_name(self.file_path.name + f".broken-{int(time.time())}")
            try:
                os.replace(self.file_path, backup)
            except OSError:
                pass
            self.warning = f"El archivo de la clave del dispositivo no se pudo descifrar ({type(exc).__name__}); se genera otra."
            logger.warning(self.warning)
            return None

    def _store_in_file(self, seed: bytes) -> None:
        secret = self._local_secret(create=True)
        assert secret is not None
        salt = secrets.token_bytes(16)
        token = self._fernet(salt, secret).encrypt(seed).decode("ascii")
        doc = {"v": _FILE_VERSION, "alg": "fernet+hkdf-sha256", "device_id": self.device_id, "salt": b64url(salt),
               "token": token}
        _write_private(self.file_path, json.dumps(doc, sort_keys=True).encode("utf-8"))

    # -- API -------------------------------------------------------------------------------------------------------

    def load_or_create(self) -> ed25519.Ed25519PrivateKey:
        backend = self._usable_keyring()
        if backend is not None:
            seed = self._load_from_keyring(backend)
            if seed:
                self.storage = STORAGE_KEYRING
                return ed25519.Ed25519PrivateKey.from_private_bytes(seed)
            if seed == b"":  # llavero operativo pero sin clave: se adopta la del archivo o se crea una
                seed = self._load_from_file() or secrets.token_bytes(32)
                if self._store_in_keyring(backend, seed):
                    self.storage = STORAGE_KEYRING
                    return ed25519.Ed25519PrivateKey.from_private_bytes(seed)
                return self._fallback(seed)
        seed = self._load_from_file()
        return self._fallback(seed or secrets.token_bytes(32), existing=seed is not None)

    def _fallback(self, seed: bytes, *, existing: bool = False) -> ed25519.Ed25519PrivateKey:
        if not existing:
            self._store_in_file(seed)
        self.storage = STORAGE_ENCRYPTED_FILE
        note = "La clave del dispositivo se guarda en un archivo cifrado (sin llavero del sistema utilizable)."
        self.warning = f"{self.warning} {note}" if self.warning and note not in self.warning else (self.warning or note)
        logger.warning(self.warning)
        return ed25519.Ed25519PrivateKey.from_private_bytes(seed)


class DeviceIdentity:
    """`DeviceIdentityPort`: firma y verifica sobres con la clave del dispositivo; certificado humano opcional."""

    def __init__(
        self,
        device_id: str,
        *,
        key_store: Optional[DeviceKeyStore] = None,
        human_signer: Any = None,
        clock_ms: Any = None,
    ) -> None:
        self.device_id = device_id
        self.key_store = key_store or DeviceKeyStore(device_id)
        self._private_key = self.key_store.load_or_create()
        self._public_raw = raw_public_key(self._private_key.public_key())
        self._human_signer = human_signer
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._certificate: Optional[Dict[str, Any]] = self._load_certificate()

    def device_uri(self) -> str:
        return f"device:{self.device_id}"

    def public_key_b64(self) -> str:
        return b64url(self._public_raw)

    def fingerprint(self) -> str:
        return key_fingerprint(self._public_raw)

    def sign_header(self, header: Mapping[str, Any]) -> Dict[str, Any]:
        return sign_header(self._private_key, header)

    def verify(self, envelope: Mapping[str, Any], content: Optional[str], *, now_ms: Optional[int] = None) -> EnvelopeVerdict:
        return verify_envelope(envelope, content, self._public_raw, now_ms=now_ms)

    def status(self) -> Dict[str, Any]:
        return {
            "device_uri": self.device_uri(),
            "fingerprint": self.fingerprint(),
            "storage": self.key_store.storage,
            "warning": self.key_store.warning,
            "has_certificate": self._certificate is not None,
        }

    # -- certificado del dispositivo firmado por el humano ------------------------------------------------------------

    @property
    def certificate_path(self) -> Path:
        return self.key_store.base_dir / "device_cert.json"

    def device_certificate(self) -> Optional[Dict[str, Any]]:
        cert = self._certificate
        if cert is not None and int(cert.get("not_after", 0)) <= int(self._clock_ms()):
            self._certificate = cert = None
        if cert is None:
            cert = self.issue_certificate()
        return dict(cert) if cert is not None else None

    def _unsigned_cert(self, human_pub: str) -> Dict[str, Any]:
        return {
            "v": 1,
            "node_uuid": self.device_id,
            "device_ed25519_pub": self.public_key_b64(),
            "not_after": int(self._clock_ms()) + CERT_VALIDITY_MS,
            "human_pub": human_pub,
        }

    def issue_certificate(self) -> Optional[Dict[str, Any]]:
        """Emite el certificado SOLO si la clave humana esta disponible y desbloqueada; si no, None."""
        signer = self._human_signer
        if signer is None or bool(getattr(signer, "is_locked", True)):
            return None
        try:
            human_pub = str(signer.public_key_base64)
            cert = self._unsigned_cert(human_pub)
            cert["sig"] = signer.sign_payload(cert)
        except Exception:  # bloqueada a mitad o sin clave: sin certificado
            logger.debug("No se pudo emitir el certificado del dispositivo", exc_info=True)
            return None
        try:
            _write_private(self.certificate_path, json.dumps(cert, sort_keys=True).encode("utf-8"))
        except OSError:
            pass
        self._certificate = cert
        return cert

    def _load_certificate(self) -> Optional[Dict[str, Any]]:
        try:
            cert = json.loads(self.certificate_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not self.certificate_is_valid(cert):
            return None
        return cert

    def certificate_is_valid(self, cert: Mapping[str, Any]) -> bool:
        from src.p2p.crypto import NodeCryptoManager

        try:
            if cert.get("node_uuid") != self.device_id or cert.get("device_ed25519_pub") != self.public_key_b64():
                return False
            if int(cert.get("not_after", 0)) <= int(self._clock_ms()):
                return False
            body = {k: v for k, v in cert.items() if k != "sig"}
            return NodeCryptoManager.verify_signature(body, str(cert.get("sig") or ""), str(cert.get("human_pub")))
        except (TypeError, ValueError, AttributeError):
            return False


def build_device_identity(kernel: Any, *, base_dir: Optional[Path] = None) -> DeviceIdentity:
    """Identidad del dispositivo del Hub: `node_uuid` del kernel y la clave humana de la GUI si la hay."""
    human = None
    try:
        human = getattr(kernel, "crypto", None)
    except Exception:
        human = None
    device_id = str(getattr(kernel, "node_uuid", "") or "local-device")
    return DeviceIdentity(device_id, key_store=DeviceKeyStore(device_id, base_dir=base_dir), human_signer=human)
