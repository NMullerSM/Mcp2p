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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

logger = logging.getLogger("mcp2p.crypto")


class NodeCryptoManager:
    """Gestiona la identidad criptográfica Ed25519 de un nodo local."""

    def __init__(self, key_path: Optional[Path | str] = None) -> None:
        self.key_path = Path(key_path) if key_path else Path.home() / ".mcp2p" / "node_identity.key"
        self._private_key: Optional[ed25519.Ed25519PrivateKey] = None
        self._public_key: Optional[ed25519.Ed25519PublicKey] = None
        self._load_or_generate_keypair()

    def _load_or_generate_keypair(self) -> None:
        """Carga la clave privada existente o genera un nuevo par Ed25519."""
        if self.key_path.exists():
            try:
                pem_data = self.key_path.read_bytes()
                self._private_key = serialization.load_pem_private_key(pem_data, password=None)
                self._public_key = self._private_key.public_key()
                return
            except Exception as e:
                logger.warning(f"No se pudo cargar la clave existente desde {self.key_path}: {e}. Generando nueva clave.")

        self._private_key = ed25519.Ed25519PrivateKey.generate()
        self._public_key = self._private_key.public_key()

        # Guardar de forma segura
        try:
            self.key_path.parent.mkdir(parents=True, exist_ok=True)
            pem_bytes = self._private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            )
            self.key_path.write_bytes(pem_bytes)
            self.key_path.chmod(0o600)
        except Exception as e:
            logger.warning(f"No se pudo guardar la clave privada en disco: {e}")

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
