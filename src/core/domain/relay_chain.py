"""Cadena causal sellada por el Hub (F1.1 del dossier 37 §3.5). Sustituye al Loop Breaker por `card_id` (EGM-93).

Dominio puro con reloj inyectable. Reglas:

- Cuando el Hub ENTREGA un sobre a un principal, ese principal queda «contaminado» por la cadena: su contexto activo
  es el `(chain_id, hop)` de MAYOR salto entregado desde su ultimo turno humano (no el ultimo: asi no puede rebajar
  su salto contestando a un mensaje mas antiguo).
- Todo lo que emita mientras tenga contexto activo hereda la cadena y `hop + 1`. Ni la cadena ni el salto son
  parametros de ninguna herramienta: los fija el Hub.
- El contexto termina SOLO cuando el humano envia un turno en ese panel (tecleo + Enter en la GUI) o al vencer el TTL
  (2 h). Lo siguiente que emita abre una cadena nueva con `hop = 0`.
- Si `hop > max_depth`, el sobre no se entrega: queda `parked` para el humano con `CHAIN_DEPTH_EXCEEDED`.

Con `max_depth = 1` (por defecto) se permite una respuesta automatica: A->B (hop 0), B->A (hop 1); el siguiente
A->B (hop 2) queda aparcado. `card_id` no participa: rotarlo no cambia nada.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from src.core.domain.identity import TrustLevel, min_trust

DEFAULT_MAX_CHAIN_DEPTH = 1
DEFAULT_CHAIN_TTL_MS = 2 * 60 * 60 * 1000
CHAIN_DEPTH_EXCEEDED = "CHAIN_DEPTH_EXCEEDED"


@dataclass(frozen=True)
class ChainContext:
    chain_id: str
    hop: int
    origin_trust: TrustLevel
    updated_at_ms: int


@dataclass(frozen=True)
class ChainLink:
    """Eslabon que el Hub asigna a un sobre nuevo."""

    chain_id: str
    hop: int
    origin_trust: Optional[TrustLevel]
    max_depth: int
    inherited: bool

    @property
    def exceeded(self) -> bool:
        return self.hop > self.max_depth


def _now_ms() -> int:
    return int(time.time() * 1000)


class ChainTracker:
    """Contexto de cadena por principal (clave = URI estable del principal), thread-safe y en memoria."""

    def __init__(
        self,
        *,
        max_depth: int = DEFAULT_MAX_CHAIN_DEPTH,
        ttl_ms: int = DEFAULT_CHAIN_TTL_MS,
        clock_ms: Optional[Callable[[], int]] = None,
        new_chain_id: Optional[Callable[[], str]] = None,
    ) -> None:
        if isinstance(max_depth, bool) or int(max_depth) < 0:
            raise ValueError("max_depth debe ser un entero >= 0")
        self.max_depth = int(max_depth)
        self.ttl_ms = max(1, int(ttl_ms))
        self._clock_ms = clock_ms or _now_ms
        self._new_chain_id = new_chain_id or (lambda: str(uuid.uuid4()))
        self._lock = threading.Lock()
        self._contexts: Dict[str, ChainContext] = {}

    def observe_delivery(self, principal: str, *, chain_id: str, hop: int, trust: "TrustLevel | str") -> None:
        """El Hub entrego a `principal` un sobre de `(chain_id, hop)`: sube su contexto si el salto es mayor."""
        now = int(self._clock_ms())
        level = TrustLevel(trust)
        with self._lock:
            current = self._live_locked(principal, now, human_turn_at_ms=None)
            if current is not None and current.hop > int(hop):
                origin = min_trust(current.origin_trust, level)
                self._contexts[principal] = ChainContext(current.chain_id, current.hop, origin, now)
                return
            origin = level if current is None else min_trust(current.origin_trust, level)
            self._contexts[principal] = ChainContext(chain_id, int(hop), origin, now)

    def reset(self, principal: str) -> None:
        """Turno humano explicito: el principal deja de arrastrar cualquier cadena."""
        with self._lock:
            self._contexts.pop(principal, None)

    def _live_locked(self, principal: str, now: int, *, human_turn_at_ms: Optional[int]) -> Optional[ChainContext]:
        ctx = self._contexts.get(principal)
        if ctx is None:
            return None
        if now - ctx.updated_at_ms > self.ttl_ms:
            self._contexts.pop(principal, None)
            return None
        if human_turn_at_ms is not None and human_turn_at_ms >= ctx.updated_at_ms:
            self._contexts.pop(principal, None)
            return None
        return ctx

    def context_for(self, principal: str, *, human_turn_at_ms: Optional[int] = None) -> Optional[ChainContext]:
        with self._lock:
            return self._live_locked(principal, int(self._clock_ms()), human_turn_at_ms=human_turn_at_ms)

    def next_link(
        self,
        sender: str,
        *,
        human_turn_at_ms: Optional[int] = None,
        parent_chain_id: Optional[str] = None,
        parent_hop: Optional[int] = None,
    ) -> ChainLink:
        """Eslabon del proximo sobre que emite `sender`. `parent_*` solo influyen si hay contexto vivo (respuesta
        dentro de la misma cadena): un turno humano posterior abre siempre una cadena nueva."""
        with self._lock:
            ctx = self._live_locked(sender, int(self._clock_ms()), human_turn_at_ms=human_turn_at_ms)
        if ctx is None:
            return ChainLink(self._new_chain_id(), 0, None, self.max_depth, inherited=False)
        base_hop = ctx.hop
        chain_id = ctx.chain_id
        if parent_chain_id and parent_hop is not None:
            chain_id = parent_chain_id
            base_hop = max(ctx.hop, int(parent_hop))
        return ChainLink(chain_id, base_hop + 1, ctx.origin_trust, self.max_depth, inherited=True)
