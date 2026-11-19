"""Puerto de verificación criptográfica de sobres emitidos por otros nodos (v2.0.2, WP-7)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class PeerVerdict:
    """Resultado de verificar un sobre remoto contra clave conocida o certificado del sobre."""

    ok: bool
    code: str
    fingerprint: Optional[str]
    public_key_b64: Optional[str]
    human_pub: Optional[str]


@runtime_checkable
class PeerEnvelopeVerifierPort(Protocol):
    def verify(self, envelope: Mapping[str, Any], content: Optional[str], *, now_ms: int) -> PeerVerdict: ...
