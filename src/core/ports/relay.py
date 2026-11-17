"""Puertos del relevo persistente (Ola 3, F1.4 del dossier 37 §6.1).

- `MessageStorePort`: bandeja persistente de sobres con estados ``queued -> delivered -> acked`` (y ``expired``,
  ``parked``, ``discarded``). Columnas de reserva del puntero (`pointer_state`, `pointer_pty_incarnation`) existen pero
  NO se usan hasta la Ola 4 (dossier 44 §3.1-3.3).
- `RelayAuditPort`: registro de auditoria con cadena de hashes (`prev_hash`, `row_hash`) y `verify_chain()`.
- `RelayPausePort`: interruptor de parada global y por principal (persistido).
- `AgentWakePort` (previsto, Ola 4): despertar a un agente sin escribir en su terminal. Aqui solo se declara.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Protocol, runtime_checkable

MESSAGE_STATES = ("queued", "delivered", "acked", "expired", "parked", "discarded")
AUDIT_EVENTS = (
    "submitted",
    "delivered",
    "acked",
    "rejected",
    "parked",
    "released",
    "discarded",
    "expired",
    "purged",
    "paused",
    "resumed",
)


@runtime_checkable
class MessageStorePort(Protocol):
    def insert_message(self, row: Dict[str, Any], audit: Iterable[Dict[str, Any]]) -> bool: ...

    def get_message(self, msg_id: str) -> Optional[Dict[str, Any]]: ...

    def count_pending(self, to_principal: str) -> int: ...

    def has_deliverable(self, to_principal: str, project_uuid: str, now_ms: int) -> bool: ...

    def take_for_delivery(
        self, to_principal: str, project_uuid: str, *, limit: int, include_unacked: bool, now_ms: int, actor: str
    ) -> List[Dict[str, Any]]: ...

    def mark_acked(self, msg_ids: Iterable[str], to_principal: str, *, now_ms: int, actor: str) -> List[str]: ...

    def list_parked(self, project_uuid: Optional[str]) -> List[Dict[str, Any]]: ...

    def resolve_parked(self, msg_id: str, decision: str, *, now_ms: int, actor: str) -> Optional[Dict[str, Any]]: ...

    def expire_due(self, now_ms: int) -> int: ...

    def purge_content(self, older_than_ms: int, *, now_ms: int) -> int: ...

    def message_counts(self) -> Dict[str, int]: ...


@runtime_checkable
class RelayAuditPort(Protocol):
    def append_audit(self, entry: Dict[str, Any]) -> Dict[str, Any]: ...

    def verify_chain(self) -> Dict[str, Any]: ...

    def audit_counts(self) -> Dict[str, int]: ...


@runtime_checkable
class RelayPausePort(Protocol):
    def set_pause(self, scope: str, paused: bool, *, actor: str, reason: str, now_ms: int) -> None: ...

    def is_paused(self, scope: str) -> bool: ...

    def list_pauses(self) -> List[Dict[str, Any]]: ...


@runtime_checkable
class AgentWakePort(Protocol):  # pragma: no cover - previsto para la Ola 4 (hooks/puntero); sin implementacion
    def capabilities(self, principal: str) -> Dict[str, Any]: ...

    def request_wake(self, principal: str) -> Dict[str, Any]: ...
