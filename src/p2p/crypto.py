"""
Módulo Criptográfico para MCP2P.

Proporciona:
1. Generación y persistencia de pares de claves Ed25519 por nodo (identidad criptográfica P2P).
2. Firma digital y verificación de mensajes y señales A2A a costo 0 de tokens.
3. Generación y validación de tokens de invitación P2P firmados.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

logger = logging.getLogger("mcp2p.crypto")


class IdentityKeyCorruptError(Exception):
    """Error lanzado cuando el archivo node_identity.key existe pero está corrupto o es inválido."""


class SigningKeyLockedError(Exception):
    """La clave Ed25519 humana está cifrada en disco y no se ha desbloqueado en esta sesión (EGM-53)."""

    code = "KEY_LOCKED"


class SigningKeyPassphraseError(Exception):
    """Frase de paso incorrecta o inválida al fijar/desbloquear la clave cifrada (EGM-53)."""

    code = "BAD_PASSPHRASE"


# Formato del archivo cifrado `<key_path>.enc` (EGM-53 / Gate 0c):
# JSON {"v":1,"kdf":"scrypt","n","r","p","salt","nonce","ct","public_key"} con la semilla
# Ed25519 (32 bytes) cifrada con AES-256-GCM bajo una clave derivada por scrypt de una frase
# que SOLO teclea el humano. La clave pública va en claro (no es secreta) para que la identidad
# del nodo siga disponible mientras la clave está bloqueada.
ENCRYPTED_KEY_VERSION = 1
MIN_PASSPHRASE_LENGTH = 10
DEFAULT_UNLOCK_TTL_SECONDS = 900


class NodeCryptoManager:
    """Gestiona la identidad criptográfica Ed25519 de un nodo local."""

    # Parámetros scrypt (≈ 32 MiB, ~0,1 s). Atributo de clase para que los tests puedan bajarlo.
    SCRYPT_N = 2 ** 15
    SCRYPT_R = 8
    SCRYPT_P = 1

    def __init__(self, key_path: Optional[Path | str] = None) -> None:
        self.key_path = Path(key_path) if key_path else Path.home() / ".mcp2p" / "node_identity.key"
        self._private_key: Optional[ed25519.Ed25519PrivateKey] = None
        self._public_key: Optional[ed25519.Ed25519PublicKey] = None
        self._unlocked_until: Optional[float] = None
        self._load_or_generate_keypair()

    # ------------------------------------------------------------------
    # Clave cifrada en disco (EGM-53 / Gate 0c)
    # ------------------------------------------------------------------

    @property
    def encrypted_key_path(self) -> Path:
        return self.key_path.with_name(self.key_path.name + ".enc")

    @property
    def is_encrypted(self) -> bool:
        """True si la identidad vive cifrada en disco (`<key_path>.enc`)."""
        return self.encrypted_key_path.exists()

    @property
    def is_locked(self) -> bool:
        """True si la clave privada no está disponible en memoria para firmar."""
        if self._private_key is None:
            return True
        if self._unlocked_until is not None and time.monotonic() >= self._unlocked_until:
            self.lock()
            return True
        return False

    def key_status(self) -> Dict[str, Any]:
        return {
            "encrypted": self.is_encrypted,
            "locked": self.is_locked,
            "public_key": self.public_key_base64,
        }

    @classmethod
    def _derive_kek(cls, passphrase: str, salt: bytes, n: int, r: int, p: int) -> bytes:
        return Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(passphrase.encode("utf-8"))

    @staticmethod
    def _secure_unlink(path: Path) -> None:
        """Sobrescribe con ceros y borra (best effort: en SSD/CoW no garantiza borrado físico)."""
        try:
            size = path.stat().st_size
            with open(path, "r+b") as f:
                f.write(b"\0" * size)
                f.flush()
                os.fsync(f.fileno())
        except Exception as exc:
            logger.warning(f"No se pudo sobrescribir {path} antes de borrarla: {exc}")
        path.unlink(missing_ok=True)

    def _load_encrypted_public_key(self) -> None:
        try:
            meta = json.loads(self.encrypted_key_path.read_text(encoding="utf-8"))
            if meta.get("v") != ENCRYPTED_KEY_VERSION or meta.get("kdf") != "scrypt":
                raise ValueError("Versión o KDF de clave cifrada no soportada")
            raw_pub = base64.urlsafe_b64decode(meta["public_key"])
            self._public_key = ed25519.Ed25519PublicKey.from_public_bytes(raw_pub)
        except Exception as exc:
            raise IdentityKeyCorruptError(
                f"Clave de identidad cifrada corrupta en '{self.encrypted_key_path}'."
            ) from exc
        self._private_key = None

    def set_passphrase(self, passphrase: str) -> None:
        """
        Cifra la identidad con una frase de paso humana y borra el PEM en claro.

        Migra la clave existente (no cambia la identidad pública). Si la clave ya está
        cifrada, exige que esté desbloqueada (permite así cambiar la frase de paso).
        """
        if not isinstance(passphrase, str) or len(passphrase) < MIN_PASSPHRASE_LENGTH:
            raise SigningKeyPassphraseError(
                f"La frase de paso debe tener al menos {MIN_PASSPHRASE_LENGTH} caracteres."
            )
        if self.is_locked:
            raise SigningKeyLockedError(
                "La clave está cifrada y bloqueada: desbloquéala antes de cambiar la frase de paso."
            )
        salt = os.urandom(16)
        nonce = os.urandom(12)
        n, r, p = self.SCRYPT_N, self.SCRYPT_R, self.SCRYPT_P
        kek = self._derive_kek(passphrase, salt, n, r, p)
        seed = self._private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        pub_b64 = self.public_key_base64
        ct = AESGCM(kek).encrypt(nonce, seed, pub_b64.encode("ascii"))
        b64 = lambda b: base64.urlsafe_b64encode(b).decode("ascii")  # noqa: E731
        blob = json.dumps({
            "v": ENCRYPTED_KEY_VERSION, "kdf": "scrypt", "n": n, "r": r, "p": p,
            "salt": b64(salt), "nonce": b64(nonce), "ct": b64(ct), "public_key": pub_b64,
        }).encode("utf-8")

        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.encrypted_key_path.with_name(self.encrypted_key_path.name + ".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(blob)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.encrypted_key_path)
        if self.key_path.exists():
            self._secure_unlink(self.key_path)

    def unlock(self, passphrase: str, ttl_seconds: Optional[float] = DEFAULT_UNLOCK_TTL_SECONDS) -> None:
        """Descifra la clave en memoria de ESTE proceso durante `ttl_seconds` (None = sin caducidad)."""
        if not self.is_encrypted:
            return
        try:
            meta = json.loads(self.encrypted_key_path.read_text(encoding="utf-8"))
            dec = lambda k: base64.urlsafe_b64decode(meta[k])  # noqa: E731
            kek = self._derive_kek(
                passphrase if isinstance(passphrase, str) else "",
                dec("salt"), int(meta["n"]), int(meta["r"]), int(meta["p"]),
            )
            seed = AESGCM(kek).decrypt(dec("nonce"), dec("ct"), meta["public_key"].encode("ascii"))
            key = ed25519.Ed25519PrivateKey.from_private_bytes(seed)
        except InvalidTag as exc:
            raise SigningKeyPassphraseError("Frase de paso incorrecta.") from exc
        except Exception as exc:
            raise SigningKeyPassphraseError(f"No se pudo descifrar la clave: {exc}") from exc
        if key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        ) != self._public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw):
            raise IdentityKeyCorruptError("La clave descifrada no coincide con la identidad pública.")
        self._private_key = key
        self._unlocked_until = (time.monotonic() + ttl_seconds) if ttl_seconds else None

    def lock(self) -> None:
        """Olvida la clave privada en memoria si la identidad está cifrada en disco."""
        if self.is_encrypted:
            self._private_key = None
            self._unlocked_until = None

    def _require_private_key(self) -> ed25519.Ed25519PrivateKey:
        if self.is_locked:
            raise SigningKeyLockedError(
                "KEY_LOCKED: la clave de firma humana está cifrada y bloqueada en esta sesión; "
                "desbloquéala con tu frase de paso."
            )
        return self._private_key

    def _load_or_generate_keypair(self) -> None:
        """Carga la clave privada existente o genera un nuevo par Ed25519 de forma atómica (0700/0600)."""
        if self.is_encrypted:
            self._load_encrypted_public_key()
            if self.key_path.exists():
                # Migración interrumpida: el PEM en claro sobrevivió. Borrarlo si es la misma
                # identidad; si no lo es, fallar ruidosamente en vez de elegir una.
                try:
                    plain = serialization.load_pem_private_key(self.key_path.read_bytes(), password=None)
                    same = plain.public_key().public_bytes(
                        serialization.Encoding.Raw, serialization.PublicFormat.Raw
                    ) == self._public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
                except Exception:
                    same = False
                if not same:
                    raise IdentityKeyCorruptError(
                        f"Coexisten '{self.key_path}' y '{self.encrypted_key_path}' con identidades distintas."
                    )
                self._secure_unlink(self.key_path)
            return
        if self.key_path.exists():
            try:
                pem_data = self.key_path.read_bytes()
                loaded_key = serialization.load_pem_private_key(pem_data, password=None)
                if not isinstance(loaded_key, ed25519.Ed25519PrivateKey):
                    raise ValueError("La clave cargada no es Ed25519PrivateKey")
                self._private_key = loaded_key
                self._public_key = self._private_key.public_key()
                return
            except Exception as exc:
                ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                backup_path = self.key_path.with_name(f"{self.key_path.name}.corrupt-{ts}")
                try:
                    shutil.copy2(self.key_path, backup_path)
                    os.chmod(backup_path, 0o600)
                except Exception as backup_exc:
                    logger.error(f"No se pudo respaldar la clave corrupta {self.key_path}: {backup_exc}")
                raise IdentityKeyCorruptError(
                    f"Clave de identidad corrupta en '{self.key_path}'. "
                    f"Respaldada en '{backup_path}'; no se regenerará silenciosamente."
                ) from exc

        self._private_key = ed25519.Ed25519PrivateKey.generate()
        self._public_key = self._private_key.public_key()

        # Asegurar directorio padre 0700 y creación atómica exclusiva 0600
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.key_path.parent, 0o700)
        pem_bytes = self._private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        try:
            fd = os.open(str(self.key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            # Carrera concurrente: intentar cargar el archivo ya creado
            pem_data = self.key_path.read_bytes()
            loaded_key = serialization.load_pem_private_key(pem_data, password=None)
            if not isinstance(loaded_key, ed25519.Ed25519PrivateKey):
                raise IdentityKeyCorruptError(f"Clave inválida tras carrera en '{self.key_path}'.")
            self._private_key = loaded_key
            self._public_key = self._private_key.public_key()
            return

        with os.fdopen(fd, "wb") as f:
            try:
                os.fchmod(f.fileno(), 0o600)
            except Exception:
                pass
            f.write(pem_bytes)
            f.flush()
            os.fsync(f.fileno())

    @property
    def public_key_base64(self) -> str:
        """Retorna la clave pública en formato base64 URL-safe."""
        raw_bytes = self._public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw
        )
        return base64.urlsafe_b64encode(raw_bytes).decode("ascii")

    def sign_payload(self, payload: Dict[str, Any]) -> str:
        """Firma un diccionario JSON y devuelve la firma en Base64 URL-safe."""
        private_key = self._require_private_key()
        canonical_bytes = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        signature = private_key.sign(canonical_bytes)
        return base64.urlsafe_b64encode(signature).decode("ascii")

    @staticmethod
    def verify_signature(payload: Dict[str, Any], signature_b64: str, public_key_b64: str) -> bool:
        """Verifica la firma Ed25519 de un payload dado la clave pública en base64."""
        try:
            raw_pub_bytes = base64.urlsafe_b64decode(public_key_b64 + "=" * (-len(public_key_b64) % 4))
            raw_sig_bytes = base64.urlsafe_b64decode(signature_b64 + "=" * (-len(signature_b64) % 4))
            pub_key = ed25519.Ed25519PublicKey.from_public_bytes(raw_pub_bytes)
            canonical_bytes = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
            pub_key.verify(raw_sig_bytes, canonical_bytes)
            return True
        except (InvalidSignature, ValueError, Exception) as exc:
            logger.debug(f"Firma criptográfica inválida: {exc}")
            return False

    def create_signed_invite_token(self, invite_data: Dict[str, Any]) -> str:
        """Empaqueta los datos de invitación y añade la firma criptográfica del nodo."""
        signature = self.sign_payload(invite_data)
        package = {
            "data": invite_data,
            "sender_pubkey": self.public_key_base64,
            "sig": signature
        }
        json_bytes = json.dumps(package, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return base64.urlsafe_b64encode(json_bytes).decode("ascii")

    @classmethod
    def decode_and_verify_invite_token(cls, token_b64: str, allow_unsigned: bool = False) -> Tuple[bool, Dict[str, Any], str]:
        """
        Decodifica y verifica criptográficamente un token de invitación Ed25519.
        Retorna: (is_valid, data_dict, error_message).
        Por defecto, exige firma criptográfica válida y rechaza tokens sin firmar.
        """
        try:
            padded = token_b64 + "=" * (-len(token_b64) % 4)
            json_bytes = base64.urlsafe_b64decode(padded)
            package = json.loads(json_bytes.decode("utf-8"))

            data = package.get("data", {})
            sender_pubkey = package.get("sender_pubkey", "")
            sig = package.get("sig", "")

            if not sender_pubkey or not sig:
                if allow_unsigned:
                    return True, data or package, ""
                return False, {}, "Token rechazado: Carece de firma criptográfica Ed25519 y clave pública del emisor."

            is_valid = cls.verify_signature(data, sig, sender_pubkey)
            if not is_valid:
                return False, {}, "Firma digital del token P2P no válida o adulterada"

            return True, data, ""
        except Exception as e:
            return False, {}, f"Error al procesar token criptográfico: {str(e)}"

    def sign_human_approval(
        self,
        artifact_id: str,
        content_text: str,
        node_uuid: str,
        developer_name: str,
        decision: str = "APPROVED"
    ) -> Dict[str, Any]:
        """
        Calcula el hash SHA-256 de content_text y lo firma con la clave privada Ed25519 del desarrollador local.
        Exclusivo para la bandeja HITL (Human-in-the-Loop); no accesible por herramientas de agentes MCP.
        """
        import hashlib
        sha256_hash = hashlib.sha256(content_text.encode("utf-8")).hexdigest()
        now_str = datetime.now(timezone.utc).isoformat()
        
        payload_to_sign = {
            "artifact_id": artifact_id,
            "content_sha256": sha256_hash,
            "node_uuid": node_uuid,
            "developer_name": developer_name,
            "decision": decision,
            "signed_at": now_str
        }
        sig_b64 = self.sign_payload(payload_to_sign)
        return {
            **payload_to_sign,
            "signature_ed25519": sig_b64,
            "public_key": self.public_key_base64
        }

    @staticmethod
    def verify_artifact_signature(
        content_text: str,
        signature_block: Dict[str, Any]
    ) -> bool:
        """Verifica que la firma de un artefacto corresponda a su contenido actual y clave pública."""
        import hashlib
        sha256_hash = hashlib.sha256(content_text.encode("utf-8")).hexdigest()
        if signature_block.get("content_sha256") != sha256_hash:
            return False
            
        pubkey = signature_block.get("public_key")
        sig = signature_block.get("signature_ed25519")
        if not pubkey or not sig:
            return False
            
        payload = {
            "artifact_id": signature_block.get("artifact_id"),
            "content_sha256": signature_block.get("content_sha256"),
            "node_uuid": signature_block.get("node_uuid"),
            "developer_name": signature_block.get("developer_name"),
            "decision": signature_block.get("decision"),
            "signed_at": signature_block.get("signed_at")
        }
        return NodeCryptoManager.verify_signature(payload, sig, pubkey)

    def sign_connector_manifest(self, manifest_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Firma un manifiesto de conector externo con la clave privada Ed25519 del propietario."""
        if not isinstance(manifest_dict, dict):
            raise ValueError("manifest_dict debe ser un diccionario.")
        payload = {k: v for k, v in manifest_dict.items() if k != "signature_ed25519"}
        if not payload.get("owner_public_key"):
            payload["owner_public_key"] = self.public_key_base64
        signature = self.sign_payload(payload)
        return {**payload, "signature_ed25519": signature}

    @staticmethod
    def verify_connector_manifest(manifest_dict: Dict[str, Any], public_key_b64: str) -> bool:
        """Verifica la firma Ed25519 de un manifiesto de conector contra public_key_b64 confiable."""
        if not isinstance(manifest_dict, dict) or not isinstance(public_key_b64, str) or not public_key_b64:
            return False
        signature = manifest_dict.get("signature_ed25519")
        if not isinstance(signature, str) or not signature:
            return False
        owner_pub = manifest_dict.get("owner_public_key")
        if owner_pub and owner_pub != public_key_b64:
            return False
        payload = {k: v for k, v in manifest_dict.items() if k != "signature_ed25519"}
        return NodeCryptoManager.verify_signature(payload, signature, public_key_b64)


def sign_connector_manifest(
    manifest_dict: Dict[str, Any],
    crypto_manager: Optional[NodeCryptoManager] = None,
) -> Dict[str, Any]:
    """Helper de módulo para firmar un manifiesto de conector Ed25519."""
    manager = crypto_manager or NodeCryptoManager()
    return manager.sign_connector_manifest(manifest_dict)


def verify_connector_manifest(manifest_dict: Dict[str, Any], public_key_b64: str) -> bool:
    """Helper de módulo para verificar un manifiesto de conector Ed25519."""
    return NodeCryptoManager.verify_connector_manifest(manifest_dict, public_key_b64)
