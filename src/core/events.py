"""
Bus de eventos de dominio thread-safe en memoria para desacoplar servicios y adaptadores.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Dict, List

from src.core.domain.context import DomainEvent

logger = logging.getLogger("mcp2p.core.events")


class DomainEventBus:
    """Bus thread-safe (RLock) de publicación/suscripción de DomainEvent."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subscribers: Dict[str, List[Callable[[DomainEvent], None]]] = {}

    def subscribe(self, event_type: str, callback: Callable[[DomainEvent], None]) -> None:
        """Registra un callback para un tipo de evento (o '*' para todos)."""
        with self._lock:
            listeners = self._subscribers.setdefault(event_type, [])
            if callback not in listeners:
                listeners.append(callback)

    def unsubscribe(self, event_type: str, callback: Callable[[DomainEvent], None]) -> None:
        """Elimina un callback previamente registrado."""
        with self._lock:
            listeners = self._subscribers.get(event_type)
            if listeners and callback in listeners:
                listeners.remove(callback)

    def publish(self, event: DomainEvent) -> None:
        """Publica un evento de forma segura iterando sobre una copia de suscriptores."""
        with self._lock:
            callbacks = list(self._subscribers.get("*", [])) + list(
                self._subscribers.get(event.event_type, [])
            )
        for cb in callbacks:
            try:
                cb(event)
            except Exception as exc:
                logger.warning("Error en suscriptor de DomainEventBus (%s): %s", event.event_type, exc)
