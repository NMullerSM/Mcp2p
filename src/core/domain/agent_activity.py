"""Dominio puro del estado de actividad de agentes por hooks (v2.0.2).

WP-0a define `AgentState` y `NORMALIZED_EVENTS` para validar el catálogo.
WP-6a añade `ActivitySnapshot` y la función pura `apply_event`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple


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

_TURN_START_EVENTS = frozenset({"turn_started", "injection_submitted", "human_submitted"})


@dataclass(frozen=True)
class ActivitySnapshot:
    """Instantánea inmutable del estado de actividad de un agente en un panel."""

    state: AgentState
    since_ms: int
    source: str  # "hook" | "injection" | "human" | "initial" | "hub"
    last_event: str
    turn_seq: int
    last_idle_at_ms: Optional[int]
    incarnation: Optional[str]


def initial_snapshot(incarnation: Optional[str] = None, at_ms: int = 0) -> ActivitySnapshot:
    """Crea la instantánea inicial `UNKNOWN` para una encarnación nueva o desconocida."""
    return ActivitySnapshot(
        state=AgentState.UNKNOWN,
        since_ms=at_ms,
        source="initial",
        last_event="",
        turn_seq=0,
        last_idle_at_ms=None,
        incarnation=incarnation,
    )


def apply_event(prev: ActivitySnapshot, event: str, at_ms: int, source: str) -> ActivitySnapshot:
    """Función pura de transición de estado de actividad según la tabla normativa (4.8)."""
    if event not in NORMALIZED_EVENTS:
        raise ValueError(f"Evento de actividad desconocido: {event!r}")

    if event == "subagent_ended":
        return prev

    if event == "pane_exited":
        if prev.state == AgentState.EXITED:
            return prev
        return ActivitySnapshot(
            state=AgentState.EXITED,
            since_ms=at_ms,
            source=source,
            last_event=event,
            turn_seq=prev.turn_seq,
            last_idle_at_ms=prev.last_idle_at_ms,
            incarnation=prev.incarnation,
        )

    if event in _TURN_START_EVENTS:
        new_state = AgentState.WORKING
        new_turn_seq = prev.turn_seq + 1 if prev.state in (AgentState.UNKNOWN, AgentState.IDLE) else prev.turn_seq
        since_ms = at_ms if new_state != prev.state else prev.since_ms
        return ActivitySnapshot(
            state=new_state,
            since_ms=since_ms,
            source=source,
            last_event=event,
            turn_seq=new_turn_seq,
            last_idle_at_ms=prev.last_idle_at_ms,
            incarnation=prev.incarnation,
        )

    if prev.state == AgentState.EXITED:
        if event == "session_started":
            new_state = AgentState.IDLE
        else:
            return prev
    elif event == "session_started":
        if prev.state in (AgentState.WORKING, AgentState.PERMISSION):
            return prev
        new_state = AgentState.IDLE
    elif event == "turn_ended":
        new_state = AgentState.IDLE
    elif event == "permission_requested":
        new_state = AgentState.PERMISSION
    elif event == "tool_used":
        new_state = AgentState.WORKING
    elif event == "idle_notice":
        if prev.state == AgentState.PERMISSION:
            return prev
        new_state = AgentState.IDLE
    else:  # pragma: no cover - todos los NORMALIZED_EVENTS están cubiertos
        raise ValueError(f"Evento de actividad no manejado: {event!r}")

    since_ms = at_ms if new_state != prev.state else prev.since_ms
    last_idle_at_ms = at_ms if (new_state == AgentState.IDLE and source == "hook") else prev.last_idle_at_ms
    return ActivitySnapshot(
        state=new_state,
        since_ms=since_ms,
        source=source,
        last_event=event,
        turn_seq=prev.turn_seq,
        last_idle_at_ms=last_idle_at_ms,
        incarnation=prev.incarnation,
    )
