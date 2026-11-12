"""
Puerto hexagonal de transporte P2P y su adaptador nulo.

El puerto (P2PTransport) define el contrato que cualquier implementación de
transporte debe cumplir. El adaptador nulo (NullTransportAdapter) es una
implementación segura que no realiza ninguna conexión de red.
"""

from abc import ABC, abstractmethod
from typing import Callable


class P2PTransport(ABC):
    """Contrato abstracto de transporte P2P."""

    @abstractmethod
    def try_connect(self, peer_rendezvous_id: str, timeout_s: float = 8.0) -> bool:
        """Intenta establecer conexión con el par identificado por su rendezvous id."""

    @abstractmethod
    def send(self, message: dict) -> bool:
        """Envía un mensaje al par conectado. Devuelve True si se envió."""

    @abstractmethod
    def on_message(self, callback: Callable[[dict], None]) -> None:
        """Registra un callback invocado con cada mensaje recibido."""

    @abstractmethod
    def close(self) -> None:
        """Cierra el transporte y libera sus recursos."""


class NullTransportAdapter(P2PTransport):
    """Implementación no operativa: nunca conecta ni envía nada."""

    def try_connect(self, peer_rendezvous_id: str, timeout_s: float = 8.0) -> bool:
        return False

    def send(self, message: dict) -> bool:
        return False

    def on_message(self, callback: Callable[[dict], None]) -> None:
        return None

    def close(self) -> None:
        return None
