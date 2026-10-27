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
    def decode_and_verify_invite_token(cls, token_b64: str) -> Tuple[bool, Dict[str, Any], str]:
        """
        Decodifica y verifica criptográficamente un token de invitación.
        Retorna: (is_valid, data_dict, error_message).
        """
        try:
            padded = token_b64 + "=" * (-len(token_b64) % 4)
            json_bytes = base64.urlsafe_b64decode(padded)
            package = json.loads(json_bytes.decode("utf-8"))

            data = package.get("data", {})
            sender_pubkey = package.get("sender_pubkey", "")
            sig = package.get("sig", "")

            if not sender_pubkey or not sig:
                # Token heredado sin firma
                return True, data or package, ""

            is_valid = cls.verify_signature(data, sig, sender_pubkey)
            if not is_valid:
                return False, {}, "Firma digital del token P2P no válida o adulterada"

            return True, data, ""
        except Exception as e:
            return False, {}, f"Error al procesar token criptográfico: {str(e)}"
