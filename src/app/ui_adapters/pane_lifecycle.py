"""Bus ligero de eventos de ciclo de vida de paneles en la capa de aplicación (v2.0.2)."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, List, Mapping, Optional, Sequence

logger = logging.getLogger("mcp2p.app.pane_lifecycle")


@dataclass(frozen=True)
class PaneSpawnedEvent:
    """Evento emitido tras abrir con éxito un panel de terminal."""

    pane_id: str
    project_uuid: str
    node_uuid: str
    cli_profile: str
    cwd: Optional[str]
    session_id: Optional[str]
    launch_meta: Mapping[str, str] = field(default_factory=dict)
    request: Mapping[str, Any] = field(default_factory=dict)


class PaneLifecycle:
    """Suscriptores del ciclo de vida de paneles (spawn, cierre humano y cierre de app)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._spawned_listeners: List[Callable[[PaneSpawnedEvent], None]] = []
        self._closed_listeners: List[Callable[[str, str], None]] = []
        self._closing_listeners: List[Callable[[Mapping[str, Sequence[str]]], None]] = []

    def on_spawned(self, fn: Callable[[PaneSpawnedEvent], None]) -> None:
        """Registra un oyente llamado con `PaneSpawnedEvent` al abrir un panel."""
        with self._lock:
            self._spawned_listeners.append(fn)

    def on_closed_by_user(self, fn: Callable[[str, str], None]) -> None:
        """Registra un oyente llamado con `(project_uuid, pane_id)` al cerrar un panel."""
        with self._lock:
            self._closed_listeners.append(fn)

    def on_app_closing(self, fn: Callable[[Mapping[str, Sequence[str]]], None]) -> None:
        """Registra un oyente llamado con `{project_uuid: [pane_id vivos]}` antes de cerrar sesiones."""
        with self._lock:
            self._closing_listeners.append(fn)

    def _dispatch(self, listeners: Sequence[Callable[..., None]], *args: Any, event_name: str) -> None:
        for fn in listeners:
            try:
                fn(*args)
            except Exception:
                logger.warning("Fallo en oyente de ciclo de vida '%s'; se continua.", event_name, exc_info=True)

    def emit_spawned(self, ev: PaneSpawnedEvent) -> None:
        """Notifica a los oyentes de `spawned` aislando cualquier excepción."""
        with self._lock:
            snapshot = list(self._spawned_listeners)
        self._dispatch(snapshot, ev, event_name="spawned")

    def emit_closed_by_user(self, project_uuid: str, pane_id: str) -> None:
        """Notifica a los oyentes de `closed_by_user` aislando cualquier excepción."""
        with self._lock:
            snapshot = list(self._closed_listeners)
        self._dispatch(snapshot, project_uuid, pane_id, event_name="closed_by_user")

    def emit_app_closing(self, alive: Mapping[str, Sequence[str]]) -> None:
        """Notifica a los oyentes de `app_closing` aislando cualquier excepción."""
        with self._lock:
            snapshot = list(self._closing_listeners)
        self._dispatch(snapshot, alive, event_name="app_closing")
