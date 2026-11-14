"""
Contexto de actor y eventos de dominio para gobernanza HITL y auditoría hexagonal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional


# Canales de actor reconocidos. Solo ACTOR_HUMAN_UI (GUI PyWebView) puede ejecutar
# transiciones reservadas al humano; MCP y fast CLI son siempre actores no humanos.
ACTOR_HUMAN_UI = "human_ui"
ACTOR_AGENT_MCP = "agent_mcp"
ACTOR_FAST_CLI = "fast_cli"


@dataclass(frozen=True)
class ActorContext:
    """Identidad y canal del actor que invoca una operación del núcleo."""

    kind: str
    node_uuid: str
    pane_id: Optional[str] = None
    session_id: Optional[str] = None
    human_presence_verified: bool = False

    def is_human_ui(self) -> bool:
        """True únicamente cuando el actor proviene de la interfaz humana autenticada."""
        return self.kind == ACTOR_HUMAN_UI


@dataclass(frozen=True)
class DomainEvent:
    """Evento inmutable emitido por los servicios de dominio del CoreKernel."""

    event_type: str
    project_uuid: str
    payload: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
