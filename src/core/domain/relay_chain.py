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
from src.core.domain.roles import ChainBudget

DEFAULT_MAX_CHAIN_DEPTH = 1
DEFAULT_CHAIN_TTL_MS = 2 * 60 * 60 * 1000
CHAIN_DEPTH_EXCEEDED = "CHAIN_DEPTH_EXCEEDED"
CHAIN_BUDGET_EXHAUSTED = "CHAIN_BUDGET_EXHAUSTED"
CHAIN_BUDGET_EXPIRED = "CHAIN_BUDGET_EXPIRED"


@dataclass(frozen=True)
class ChainContext:
    chain_id: str
    hop: int
    origin_trust: TrustLevel
    updated_at_ms: int


@dataclass
class _ChainBudgetState:
    budget: ChainBudget
    started_at_ms: int
    messages: int


@dataclass(frozen=True)
class ChainLink:
    """Eslabon que el Hub asigna a un sobre nuevo."""

    chain_id: str
    hop: int
    origin_trust: Optional[TrustLevel]
    max_depth: int
    inherited: bool
    exceeded_reason: Optional[str] = None

    @property
    def exceeded(self) -> bool:
        return self.exceeded_reason is not None or self.hop > self.max_depth


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
        self._chains: Dict[str, _ChainBudgetState] = {}

    def _default_budget(self) -> ChainBudget:
        return ChainBudget(
            max_hops=self.max_depth,
            max_messages=self.max_depth + 1,
            max_age_ms=self.ttl_ms,
        )

    def _prune_expired_chains_locked(self, now: int) -> None:
        active_chain_ids = {
            ctx.chain_id
            for ctx in self._contexts.values()
            if now - ctx.updated_at_ms <= self.ttl_ms
        }
        stale_ids = [
            cid
            for cid, state in self._chains.items()
            if cid not in active_chain_ids
            and now - state.started_at_ms > max(self.ttl_ms, state.budget.max_age_ms)
        ]
        for cid in stale_ids:
            self._chains.pop(cid, None)

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
        sender_budget: Optional[ChainBudget] = None,
        sender_can_extend: bool = False,
    ) -> ChainLink:
        """Eslabon del proximo sobre que emite `sender`. `parent_*` solo influyen si hay contexto vivo (respuesta
        dentro de la misma cadena): un turno humano posterior abre siempre una cadena nueva."""
        with self._lock:
            now = int(self._clock_ms())
            self._prune_expired_chains_locked(now)
            ctx = self._live_locked(sender, now, human_turn_at_ms=human_turn_at_ms)
            default_budget = self._default_budget()
            if ctx is None:
                budget = sender_budget if sender_budget is not None else default_budget
                chain_id = self._new_chain_id()
                self._chains[chain_id] = _ChainBudgetState(budget=budget, started_at_ms=now, messages=1)
                exceeded_reason: Optional[str] = None
                if 0 > budget.max_hops:
                    exceeded_reason = CHAIN_DEPTH_EXCEEDED
                elif 1 > budget.max_messages:
                    exceeded_reason = CHAIN_BUDGET_EXHAUSTED
                return ChainLink(
                    chain_id,
                    0,
                    None,
                    budget.max_hops,
                    inherited=False,
                    exceeded_reason=exceeded_reason,
                )

            base_hop = ctx.hop
            chain_id = ctx.chain_id
            if parent_chain_id and parent_hop is not None:
                chain_id = parent_chain_id
                base_hop = max(ctx.hop, int(parent_hop))
            new_hop = base_hop + 1

            state = self._chains.get(chain_id)
            if state is None:
                state = _ChainBudgetState(
                    budget=sender_budget if sender_budget is not None else default_budget,
                    started_at_ms=ctx.updated_at_ms,
                    messages=0,
                )
                self._chains[chain_id] = state
            if sender_can_extend and sender_budget is not None:
                state.budget = ChainBudget(
                    max_hops=max(state.budget.max_hops, sender_budget.max_hops),
                    max_messages=max(state.budget.max_messages, sender_budget.max_messages),
                    max_age_ms=max(state.budget.max_age_ms, sender_budget.max_age_ms),
                )
            state.messages += 1
            budget = state.budget
            exceeded_reason = None
            if new_hop > budget.max_hops:
                exceeded_reason = CHAIN_DEPTH_EXCEEDED
            elif now - state.started_at_ms > budget.max_age_ms:
                exceeded_reason = CHAIN_BUDGET_EXPIRED
            elif state.messages > budget.max_messages:
                exceeded_reason = CHAIN_BUDGET_EXHAUSTED
            return ChainLink(
                chain_id,
                new_hop,
                ctx.origin_trust,
                budget.max_hops,
                inherited=True,
                exceeded_reason=exceeded_reason,
            )
