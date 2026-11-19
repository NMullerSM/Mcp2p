"""Puertos de la entrega directa (WP-6b): escritor del PTY, actividad, almacén de entregas y política remota."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, Sequence, runtime_checkable

DELIVERY_STATES = ("reserved", "written", "submitted", "injected", "unverifiable", "reverted", "pointer_sent")
OPEN_DELIVERY_STATES = ("reserved", "written", "submitted", "pointer_sent")


@dataclass(frozen=True)
class WriteResult:
    ok: bool
    bytes_written: int
    code: str  # OK | PANE_GONE | INCARNATION_CHANGED | IO_ERROR


@runtime_checkable
class PaneInputPort(Protocol):
    def current_incarnation(self, pane_id: str) -> Optional[str]: ...

    def last_output_age_s(self, pane_id: str) -> Optional[float]: ...

    def write(self, pane_id: str, data: str, *, incarnation: str) -> WriteResult: ...


@runtime_checkable
class AgentActivityPort(Protocol):
    def snapshot(self, node_uuid: str) -> Any: ...

    def mark(self, node_uuid: str, event: str, at_ms: int, source: str) -> None: ...

    def subscribe(self, fn: Callable[[str, Any], None]) -> None: ...


@runtime_checkable
class DeliveryStorePort(Protocol):  # lo implementa SqliteRelayStore
    def peek_queued(self, to_principal: str, project_uuid: str, *, limit: int, now_ms: int) -> List[Dict[str, Any]]: ...

    def claim_for_injection(
        self,
        msg_ids: Sequence[str],
        to_principal: str,
        *,
        incarnation: str,
        batch_id: Optional[str],
        now_ms: int,
        actor: str,
    ) -> List[Dict[str, Any]]: ...

    def set_delivery_state(
        self,
        msg_ids: Sequence[str],
        state: str,
        *,
        reason: Optional[str],
        now_ms: int,
        bytes_written: int = 0,
        sanitized_controls: int = 0,
    ) -> None: ...

    def revert_injection(
        self, msg_ids: Sequence[str], *, reason: str, now_ms: int, allow_written: bool = False
    ) -> int: ...

    def mark_pointer_sent(
        self, msg_id: str, to_principal: str, project_uuid: str, *, incarnation: str, now_ms: int
    ) -> bool: ...

    def clear_pointer_sent(self, msg_id: str, *, now_ms: int) -> None: ...

    def open_deliveries(self) -> List[Dict[str, Any]]: ...

    def thread_rows(self, project_uuid: str, *, since_ms: int, limit: int) -> List[Dict[str, Any]]: ...


@runtime_checkable
class RemoteDeliveryPolicyPort(Protocol):  # implementación por defecto: permite todo lo local; WP-7 la sustituye
    def allows_injection(self, msg_row: Mapping[str, Any]) -> bool: ...


class LocalOnlyDeliveryPolicy:
    """Política por defecto: lo local se entrega; lo que venga de otro nodo no se escribe nunca."""

    def __init__(self, is_remote: Optional[Callable[[str], bool]] = None) -> None:
        self._is_remote = is_remote

    def allows_injection(self, msg_row: Mapping[str, Any]) -> bool:
        if callable(self._is_remote):
            return not self._is_remote(str(msg_row.get("msg_id") or ""))
        return True
