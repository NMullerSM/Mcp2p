"""Firma y verificacion Ed25519 del sobre v1 (F1.1). Funciones puras sobre `cryptography`.

`verify_envelope` devuelve un `EnvelopeVerdict` con un codigo estable; nunca lanza ante un sobre manipulado.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from src.core.domain.envelope import (
    SIGNATURE_ALG,
    EnvelopeError,
    content_hash,
    content_size,
    header_of,
    signing_bytes,
)


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def b64url_decode(text: str) -> bytes:
    if not isinstance(text, str):
        raise ValueError("base64 invalido")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def raw_public_key(public_key: ed25519.Ed25519PublicKey) -> bytes:
    return public_key.public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)


def key_fingerprint(public_raw: bytes) -> str:
    """Huella corta (16 hex) de una clave publica Ed25519 en bruto."""
    return hashlib.sha256(public_raw).hexdigest()[:16]


def sign_header(private_key: ed25519.Ed25519PrivateKey, header: Mapping[str, Any]) -> dict:
    """Sobre completo: la cabecera validada + `sig` = Ed25519(dominio + cabecera canonica)."""
    data = signing_bytes(header)
    public_raw = raw_public_key(private_key.public_key())
    envelope = dict(header)
    envelope["sig"] = {
        "alg": SIGNATURE_ALG,
        "key": key_fingerprint(public_raw),
        "value": b64url(private_key.sign(data)),
    }
    return envelope


@dataclass(frozen=True)
class EnvelopeVerdict:
    ok: bool
    code: str

    def __bool__(self) -> bool:  # pragma: no cover - azucar
        return self.ok


def verify_envelope(
    envelope: Mapping[str, Any],
    content: Optional[str],
    public_key: "ed25519.Ed25519PublicKey | bytes | str",
    *,
    now_ms: Optional[int] = None,
) -> EnvelopeVerdict:
    """Verifica esquema, firma, huella de la clave, hash del contenido y caducidad.

    `content=None` verifica solo la cabecera (contenido ya purgado por retencion). Codigos: OK, SCHEMA_INVALID,
    NON_CANONICAL, VERSION_UNSUPPORTED, UNSUPPORTED_ALG, KEY_MISMATCH, BAD_SIGNATURE, CONTENT_HASH_MISMATCH, EXPIRED.
    """
    try:
        if isinstance(public_key, str):
            public_key = b64url_decode(public_key)
        if isinstance(public_key, (bytes, bytearray)):
            public_key = ed25519.Ed25519PublicKey.from_public_bytes(bytes(public_key))
    except (ValueError, TypeError):
        return EnvelopeVerdict(False, "KEY_MISMATCH")
    if not isinstance(envelope, Mapping):
        return EnvelopeVerdict(False, "SCHEMA_INVALID")
    sig = envelope.get("sig")
    if not isinstance(sig, Mapping) or set(sig) != {"alg", "key", "value"}:
        return EnvelopeVerdict(False, "SCHEMA_INVALID")
    if sig.get("alg") != SIGNATURE_ALG:
        return EnvelopeVerdict(False, "UNSUPPORTED_ALG")
    header = header_of(envelope)
    try:
        data = signing_bytes(header)
    except EnvelopeError as exc:
        return EnvelopeVerdict(False, exc.code)
    if sig.get("key") != key_fingerprint(raw_public_key(public_key)):
        return EnvelopeVerdict(False, "KEY_MISMATCH")
    try:
        public_key.verify(b64url_decode(str(sig.get("value") or "")), data)
    except (InvalidSignature, ValueError, TypeError):
        return EnvelopeVerdict(False, "BAD_SIGNATURE")
    if content is not None:
        try:
            if content_hash(content) != header["content_hash"] or content_size(content) != header["content_size"]:
                return EnvelopeVerdict(False, "CONTENT_HASH_MISMATCH")
        except EnvelopeError:
            return EnvelopeVerdict(False, "CONTENT_HASH_MISMATCH")
    if now_ms is not None and int(now_ms) >= int(header["expires_at"]):
        return EnvelopeVerdict(False, "EXPIRED")
    return EnvelopeVerdict(True, "OK")
