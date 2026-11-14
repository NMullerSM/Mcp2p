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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

logger = logging.getLogger("mcp2p.crypto")


class IdentityKeyCorruptError(Exception):
    """Error lanzado cuando el archivo node_identity.key existe pero está corrupto o es inválido."""


class NodeCryptoManager:
    """Gestiona la identidad criptográfica Ed25519 de un nodo local."""

    def __init__(self, key_path: Optional[Path | str] = None) -> None:
        self.key_path = Path(key_path) if key_path else Path.home() / ".mcp2p" / "node_identity.key"
        self._private_key: Optional[ed25519.Ed25519PrivateKey] = None
        self._public_key: Optional[ed25519.Ed25519PublicKey] = None
        self._load_or_generate_keypair()

    def _load_or_generate_keypair(self) -> None:
        """Carga la clave privada existente o genera un nuevo par Ed25519 de forma atómica (0700/0600)."""
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
        canonical_bytes = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        signature = self._private_key.sign(canonical_bytes)
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
