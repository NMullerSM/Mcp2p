"""
Confirmacion de instalaciones de CLIs (dos pasos) con token de un solo uso.

`prepare` entrega el comando EXACTO de la lista cerrada del catalogo del repo mas un token ligado a
`(cli_id, comando, SO)`; `consume` solo acepta ese token una vez, antes de su vencimiento y con el
mismo comando. El JS nunca aporta el comando: solo `cli_id` y el token.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

DEFAULT_TOKEN_TTL_S = 120.0
MAX_PENDING_TOKENS = 16
_MAX_TOKEN_LEN = 256


@dataclass(frozen=True)
class _PendingInstall:
    digest: bytes
    cli_id: str
    command: str
    platform: str
    expires_at: float


def _digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


class InstallConfirmationBroker:
    """Tokens de confirmacion de corta vida; en memoria, nunca persistidos."""

    def __init__(
        self,
        *,
        ttl_s: float = DEFAULT_TOKEN_TTL_S,
        clock: Callable[[], float] = time.monotonic,
        max_pending: int = MAX_PENDING_TOKENS,
    ) -> None:
        self.ttl_s = float(ttl_s)
        self._clock = clock
        self._max_pending = max(1, int(max_pending))
        self._lock = threading.Lock()
        self._pending: Dict[bytes, _PendingInstall] = {}

    def issue(self, cli_id: str, command: str, platform: str) -> Tuple[str, float]:
        token = secrets.token_urlsafe(32)
        digest = _digest(token)
        now = self._clock()
        record = _PendingInstall(digest, cli_id, command, platform, now + self.ttl_s)
        with self._lock:
            self._prune_locked(now)
            # Un solo token vigente por CLI: preparar de nuevo invalida el anterior.
            for key in [k for k, v in self._pending.items() if v.cli_id == cli_id]:
                del self._pending[key]
            while len(self._pending) >= self._max_pending:
                oldest = min(self._pending.values(), key=lambda r: r.expires_at)
                del self._pending[oldest.digest]
            self._pending[digest] = record
        return token, self.ttl_s

    def pending_count(self) -> int:
        with self._lock:
            self._prune_locked(self._clock())
            return len(self._pending)

    def _prune_locked(self, now: float) -> None:
        # Los vencidos se conservan un margen para poder responder INSTALL_TOKEN_EXPIRED.
        grace = max(self.ttl_s, 60.0)
        for key in [k for k, v in self._pending.items() if now > v.expires_at + grace]:
            del self._pending[key]

    def consume(self, cli_id: object, token: object, command: str, platform: str) -> str:
        """Devuelve `OK`, `INSTALL_TOKEN_INVALID` o `INSTALL_TOKEN_EXPIRED`. Consume el token si lo reconoce."""
        if not isinstance(token, str) or not token or len(token) > _MAX_TOKEN_LEN:
            return "INSTALL_TOKEN_INVALID"
        candidate = _digest(token)
        now = self._clock()
        with self._lock:
            matched: Optional[_PendingInstall] = None
            # Recorrido completo y comparacion en tiempo constante (sin cortar al primer acierto).
            for record in self._pending.values():
                if hmac.compare_digest(record.digest, candidate):
                    matched = record
            if matched is None:
                return "INSTALL_TOKEN_INVALID"
            del self._pending[matched.digest]  # un solo uso: se quema aunque no cuadre el contexto
            self._prune_locked(now)
        if now > matched.expires_at:
            return "INSTALL_TOKEN_EXPIRED"
        if (
            not isinstance(cli_id, str)
            or not hmac.compare_digest(matched.cli_id.encode(), cli_id.encode())
            or not hmac.compare_digest(matched.command.encode(), command.encode())
            or not hmac.compare_digest(matched.platform.encode(), platform.encode())
        ):
            return "INSTALL_TOKEN_INVALID"
        return "OK"
