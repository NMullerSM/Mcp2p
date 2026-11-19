"""Sobre de mensaje v1 del relevo (F1.1 del dossier 37 §3.3, Ola 3).

Dominio puro: esquema, validacion y codificacion canonica. La firma Ed25519 vive en el adaptador
(`src/adapters/identity/envelope_crypto.py`); aqui solo se define QUE bytes se firman.

Cabecera (todo lo firmado; `sig` va aparte):

================  =========  ============================================================================
Campo             Tipo       Lo pone
================  =========  ============================================================================
``v``             int = 1    Hub
``msg_id``        str        Hub (UUID). Clave de deduplicacion y anti-repeticion
``kind``          str        Remitente, validado contra `ENVELOPE_KINDS`
``from``          str        **Hub** (URI del principal autenticado). Nunca un parametro
``from_device``   str        Hub (``device:<node_uuid>``, el dispositivo que firma)
``to``            str        Remitente pide un destino; el Hub lo resuelve y escribe la URI estable
``project_uuid``  str        Hub (proyecto de la sesion del remitente)
``chain_id``      str        **Hub** (cadena causal sellada)
``hop``           int >= 0   **Hub**
``parent_msg_id`` str|null   Hub (sobre al que responde, si lo hay)
``content_hash``  str        Hub: ``sha256:<hex>`` de los bytes UTF-8 del contenido, tal cual
``content_size``  int        Hub: bytes UTF-8 del contenido
``created_at``    int (ms)   Hub. Nunca flotantes
``expires_at``    int (ms)   Hub
``trust``         str        **Hub** (`TrustLevel`)
``device_cert``   obj|null   Hub: certificado del dispositivo firmado por el humano, o null
================  =========  ============================================================================

Codificacion canonica: solo cadenas en NFC, enteros (|n| < 2**53), booleanos, null, listas y objetos con claves de
texto; nunca flotantes. ``json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)`` en UTF-8, que para
ese subconjunto coincide con JCS (RFC 8785). Los vectores fijos de ``tests/vectors/envelope_v1.json`` la congelan.

Bytes firmados: ``b"MCP2P-ENV/1\\n" + canonical(cabecera)``. El contenido entra por su hash (``content_hash``).
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Any, Dict, Mapping, Optional

from src.core.domain.identity import TrustLevel, parse_principal_uri

ENVELOPE_VERSION = 1
SIGNING_DOMAIN = b"MCP2P-ENV/1\n"
SIGNATURE_ALG = "Ed25519"
MAX_SAFE_INT = 2**53 - 1
# Tipos de sobre. Ninguno transporta ordenes de teclado: el contenido siempre es un DATO para el agente.
ENVELOPE_KINDS = frozenset(
    {"note", "task.request", "task.reply", "question", "finding", "status", "relay_notice"}
)

HEADER_FIELDS = (
    "v",
    "msg_id",
    "kind",
    "from",
    "from_device",
    "to",
    "project_uuid",
    "chain_id",
    "hop",
    "parent_msg_id",
    "content_hash",
    "content_size",
    "created_at",
    "expires_at",
    "trust",
    "device_cert",
)
_STR_FIELDS = ("msg_id", "kind", "from", "from_device", "to", "chain_id", "content_hash", "trust")
_INT_FIELDS = ("hop", "content_size", "created_at", "expires_at")


class EnvelopeError(ValueError):
    """Sobre no valido. `code` es estable y apto para respuestas y auditoria."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def _check_canonical_value(value: Any, path: str) -> None:
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INT:
            raise EnvelopeError("NON_CANONICAL", f"{path}: entero fuera de rango")
        return
    if isinstance(value, float):
        raise EnvelopeError("NON_CANONICAL", f"{path}: flotante no admitido")
    if isinstance(value, str):
        if unicodedata.normalize("NFC", value) != value:
            raise EnvelopeError("NON_CANONICAL", f"{path}: texto no esta en NFC")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise EnvelopeError("NON_CANONICAL", f"{path}: texto no codificable en UTF-8") from None
        return
    if isinstance(value, (list, tuple)):
        for idx, item in enumerate(value):
            _check_canonical_value(item, f"{path}[{idx}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise EnvelopeError("NON_CANONICAL", f"{path}: clave no textual")
            _check_canonical_value(key, f"{path}.<key>")
            _check_canonical_value(item, f"{path}.{key}")
        return
    raise EnvelopeError("NON_CANONICAL", f"{path}: tipo {type(value).__name__} no admitido")


def canonical_json(value: Any) -> bytes:
    """Codificacion canonica determinista (JSON ordenado, sin espacios, UTF-8). Rechaza lo no canonico."""
    _check_canonical_value(value, "$")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode(
        "utf-8"
    )


def content_hash(content: str) -> str:
    """``sha256:<hex>`` de los bytes UTF-8 del contenido EXACTO (sin normalizar: es un dato, no la cabecera)."""
    if not isinstance(content, str):
        raise EnvelopeError("SCHEMA_INVALID", "el contenido debe ser texto")
    try:
        raw = content.encode("utf-8")
    except UnicodeEncodeError:
        raise EnvelopeError("SCHEMA_INVALID", "contenido no codificable en UTF-8") from None
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def content_size(content: str) -> int:
    return len(content.encode("utf-8"))


def validate_header(header: Mapping[str, Any]) -> None:
    """Valida el esquema v1 de la cabecera; EnvelopeError con `code` estable si algo falla."""
    if not isinstance(header, Mapping):
        raise EnvelopeError("SCHEMA_INVALID", "la cabecera debe ser un objeto")
    keys = set(header)
    missing = [f for f in HEADER_FIELDS if f not in keys]
    unknown = sorted(keys - set(HEADER_FIELDS))
    if missing:
        raise EnvelopeError("SCHEMA_INVALID", f"faltan campos: {missing}")
    if unknown:
        raise EnvelopeError("SCHEMA_INVALID", f"campos desconocidos: {unknown}")
    if header["v"] != ENVELOPE_VERSION or isinstance(header["v"], bool):
        raise EnvelopeError("VERSION_UNSUPPORTED", str(header["v"]))
    for name in _STR_FIELDS:
        if not isinstance(header[name], str) or not header[name]:
            raise EnvelopeError("SCHEMA_INVALID", f"{name} debe ser texto no vacio")
    if not isinstance(header["project_uuid"], str):
        raise EnvelopeError("SCHEMA_INVALID", "project_uuid debe ser texto")
    for name in _INT_FIELDS:
        value = header[name]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise EnvelopeError("SCHEMA_INVALID", f"{name} debe ser un entero >= 0")
    if header["parent_msg_id"] is not None and (
        not isinstance(header["parent_msg_id"], str) or not header["parent_msg_id"]
    ):
        raise EnvelopeError("SCHEMA_INVALID", "parent_msg_id debe ser texto o null")
    if header["device_cert"] is not None and not isinstance(header["device_cert"], dict):
        raise EnvelopeError("SCHEMA_INVALID", "device_cert debe ser objeto o null")
    if header["kind"] not in ENVELOPE_KINDS:
        raise EnvelopeError("SCHEMA_INVALID", f"kind desconocido: {header['kind']}")
    try:
        TrustLevel(header["trust"])
    except ValueError:
        raise EnvelopeError("SCHEMA_INVALID", "trust desconocido") from None
    for name in ("from", "from_device", "to"):
        try:
            parse_principal_uri(header[name])
        except ValueError:
            raise EnvelopeError("SCHEMA_INVALID", f"{name} no es una URI de principal") from None
    if not header["content_hash"].startswith("sha256:") or len(header["content_hash"]) != 71:
        raise EnvelopeError("SCHEMA_INVALID", "content_hash debe ser sha256:<64 hex>")
    if header["expires_at"] <= header["created_at"]:
        raise EnvelopeError("SCHEMA_INVALID", "expires_at debe ser posterior a created_at")
    _check_canonical_value(dict(header), "$")


def header_of(envelope: Mapping[str, Any]) -> Dict[str, Any]:
    """La cabecera (todo salvo `sig`) de un sobre completo."""
    return {k: v for k, v in envelope.items() if k != "sig"}


def signing_bytes(header: Mapping[str, Any]) -> bytes:
    """Bytes exactos que firma el dispositivo: dominio + cabecera canonica (valida el esquema antes)."""
    validate_header(header)
    return SIGNING_DOMAIN + canonical_json(dict(header))


def envelope_digest(envelope: Mapping[str, Any]) -> str:
    """sha256 hex del sobre completo canonico (cabecera + firma). Lo usa la auditoria."""
    return hashlib.sha256(canonical_json(dict(envelope))).hexdigest()


def build_header(
    *,
    msg_id: str,
    kind: str,
    from_uri: str,
    from_device: str,
    to_uri: str,
    project_uuid: str,
    chain_id: str,
    hop: int,
    parent_msg_id: Optional[str],
    content: str,
    created_at: int,
    expires_at: int,
    trust: str,
    device_cert: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Construye y valida una cabecera v1 (solo la llama el Hub)."""
    header: Dict[str, Any] = {
        "v": ENVELOPE_VERSION,
        "msg_id": msg_id,
        "kind": kind,
        "from": from_uri,
        "from_device": from_device,
        "to": to_uri,
        "project_uuid": project_uuid,
        "chain_id": chain_id,
        "hop": int(hop),
        "parent_msg_id": parent_msg_id,
        "content_hash": content_hash(content),
        "content_size": content_size(content),
        "created_at": int(created_at),
        "expires_at": int(expires_at),
        "trust": str(trust.value if hasattr(trust, "value") else trust),
        "device_cert": device_cert,
    }
    validate_header(header)
    return header
