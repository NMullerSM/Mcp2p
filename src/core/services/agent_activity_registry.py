"""Registro en memoria del estado de actividad de agentes por panel (v2.0.2, WP-6a)."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Dict, List, Optional

from src.core.domain.agent_activity import (
    ActivitySnapshot,
    apply_event,
    initial_snapshot,
)

logger = logging.getLogger("mcp2p.core.agent_activity_registry")


def _default_clock_ms() -> int:
    return int(time.time() * 1000)


class AgentActivityRegistry:
    """Implementa `AgentActivityPort` con aislamiento de encarnaciones y suscriptores fuera del cerrojo."""

    def __init__(self, *, clock_ms: Optional[Callable[[], int]] = None) -> None:
        self._lock = threading.Lock()
        self._clock_ms = clock_ms or _default_clock_ms
        self._states: Dict[str, ActivitySnapshot] = {}
        self._activity_listeners: List[Callable[[str, ActivitySnapshot], None]] = []
        self._session_id_listeners: List[Callable[[str, str], None]] = []

    def snapshot(self, node_uuid: str) -> ActivitySnapshot:
        """Devuelve la instantánea vigente de `node_uuid`, o `UNKNOWN` si aún no hay datos."""
        with self._lock:
            snap = self._states.get(node_uuid)
            if snap is not None:
                return snap
        return initial_snapshot()

    def subscribe(self, fn: Callable[[str, ActivitySnapshot], None]) -> None:
        """Registra un oyente notificado con `(node_uuid, snapshot)` fuera del cerrojo."""
        with self._lock:
            self._activity_listeners.append(fn)

    def on_cli_session_id(self, fn: Callable[[str, str], None]) -> None:
        """Registra un oyente notificado con `(node_uuid, cli_session_id)` cuando llega un id de sesión."""
        with self._lock:
            self._session_id_listeners.append(fn)

    def reset(self, node_uuid: str, incarnation: str) -> None:
        """Reinicia `node_uuid` a `UNKNOWN` para una nueva encarnación (spawn/relanzamiento)."""
        now_ms = int(self._clock_ms())
        snap = initial_snapshot(incarnation=incarnation, at_ms=now_ms)
        with self._lock:
            self._states[node_uuid] = snap
            listeners = list(self._activity_listeners)
        self._notify_activity(listeners, node_uuid, snap)

    def mark(self, node_uuid: str, event: str, at_ms: int, source: str) -> None:
        """Aplica `event` sobre la encarnación vigente de `node_uuid` (interfaz `AgentActivityPort`)."""
        self.ingest(node_uuid, None, event, at_ms, source)

    def ingest(
        self,
        node_uuid: str,
        incarnation: Optional[str],
        event: str,
        at_ms: int,
        source: str,
        cli_session_id: Optional[str] = None,
    ) -> ActivitySnapshot:
        """Ingiere un evento normalizado descartando encarnaciones obsoletas y notificando fuera del cerrojo."""
        activity_listeners: List[Callable[[str, ActivitySnapshot], None]] = []
        session_listeners: List[Callable[[str, str], None]] = []
        notify_activity = False

        with self._lock:
            prev = self._states.get(node_uuid)
            if prev is None:
                prev = initial_snapshot(incarnation=incarnation)
            elif incarnation is not None and prev.incarnation is not None and incarnation != prev.incarnation:
                return prev
            elif incarnation is not None and prev.incarnation is None:
                prev = ActivitySnapshot(
                    state=prev.state,
                    since_ms=prev.since_ms,
                    source=prev.source,
                    last_event=prev.last_event,
                    turn_seq=prev.turn_seq,
                    last_idle_at_ms=prev.last_idle_at_ms,
                    incarnation=incarnation,
                )

            next_snap = apply_event(prev, event, at_ms, source)
            if next_snap != prev:
                self._states[node_uuid] = next_snap
                activity_listeners = list(self._activity_listeners)
                notify_activity = True
            elif node_uuid not in self._states and prev.incarnation is not None:
                self._states[node_uuid] = next_snap

            if cli_session_id:
                session_listeners = list(self._session_id_listeners)

        if notify_activity:
            self._notify_activity(activity_listeners, node_uuid, next_snap)
        if cli_session_id and session_listeners:
            self._notify_session_id(session_listeners, node_uuid, cli_session_id)
        return next_snap

    @staticmethod
    def _notify_activity(
        listeners: List[Callable[[str, ActivitySnapshot], None]],
        node_uuid: str,
        snap: ActivitySnapshot,
    ) -> None:
        for fn in listeners:
            try:
                fn(node_uuid, snap)
            except Exception:
                logger.warning("Fallo en suscriptor de AgentActivityRegistry; se continua.", exc_info=True)

    @staticmethod
    def _notify_session_id(
        listeners: List[Callable[[str, str], None]],
        node_uuid: str,
        cli_session_id: str,
    ) -> None:
        for fn in listeners:
            try:
                fn(node_uuid, cli_session_id)
            except Exception:
                logger.warning("Fallo en oyente on_cli_session_id; se continua.", exc_info=True)
