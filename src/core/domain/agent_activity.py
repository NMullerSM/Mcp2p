"""Dominio puro del estado de actividad de agentes por hooks (v2.0.2).

WP-0a define `AgentState` y `NORMALIZED_EVENTS` para validar el catálogo.
WP-6a añade `ActivitySnapshot` y la función pura `apply_event`.
"""

from __future__ import annotations

from enum import Enum
from typing import Tuple


class AgentState(str, Enum):
    """Estado de actividad del agente en un panel según los hooks del CLI."""

    UNKNOWN = "unknown"
    IDLE = "idle"
    WORKING = "working"
    PERMISSION = "permission"
    EXITED = "exited"


NORMALIZED_EVENTS: Tuple[str, ...] = (
    "session_started",
    "turn_started",
    "turn_ended",
    "permission_requested",
    "tool_used",
    "idle_notice",
    "subagent_ended",
    "injection_submitted",
    "human_submitted",
    "pane_exited",
)
