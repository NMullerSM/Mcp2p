"""Puertos del módulo de chat. Un adaptador (agentes locales hoy; nodos P2P o servidor después) implementa los que
necesite y se registra en `ChatService`; el módulo no sabe de dónde vienen los mensajes ni por dónde salen."""

from __future__ import annotations

from typing import List, Protocol, Sequence, runtime_checkable

from src.chat.domain import ChatEntry, Participant, SendResult


@runtime_checkable
class ChatSource(Protocol):
    """Fuente de entradas del feed. Por consulta (`list_since`); las fuentes de empuje (un *hook* entrante de P2P o del
    servidor) llaman a `ChatService.publish` y pueden dejar `list_since` vacío."""

    id: str

    def list_since(self, project_uuid: str, since_rev_ms: int, limit: int) -> List[ChatEntry]:
        """Entradas del proyecto cuyo `rev_ms` es >= `since_rev_ms`, de la más antigua a la más reciente."""
        ...


@runtime_checkable
class ChatTransport(Protocol):
    """Envío del humano a participantes de un tipo (`Participant.kind`). Debe devolver un resultado por destino."""

    def send(self, project_uuid: str, targets: Sequence[Participant], text: str, kind: str) -> List[SendResult]: ...


@runtime_checkable
class RosterProvider(Protocol):
    """A quién se puede nombrar con `@` en un proyecto."""

    id: str

    def participants(self, project_uuid: str) -> List[Participant]: ...
