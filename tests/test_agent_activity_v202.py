"""Pruebas WP-6a (v2.0.2): dominio puro `agent_activity`, `AgentActivityRegistry` y turno humano."""

from __future__ import annotations

import threading
from typing import List, Tuple

import pytest

from src.core.domain.agent_activity import (
    NORMALIZED_EVENTS,
    ActivitySnapshot,
    AgentState,
    apply_event,
    initial_snapshot,
)
from src.core.services.agent_activity_registry import AgentActivityRegistry
from src.mcp.pane_registry import PaneRegistry


def _snap(state: AgentState, *, turn_seq: int = 1, since_ms: int = 100, source: str = "hook") -> ActivitySnapshot:
    return ActivitySnapshot(
        state=state,
        since_ms=since_ms,
        source=source,
        last_event="seed",
        turn_seq=turn_seq,
        last_idle_at_ms=since_ms if state == AgentState.IDLE else None,
        incarnation="inc-1",
    )


# Tabla normativa completa de §4.8 (10 eventos x 5 estados = 50 casos)
_TRANSITION_CASES: List[Tuple[str, AgentState, AgentState, int]] = [
    # session_started
    ("session_started", AgentState.UNKNOWN, AgentState.IDLE, 0),
    ("session_started", AgentState.IDLE, AgentState.IDLE, 0),
    ("session_started", AgentState.WORKING, AgentState.WORKING, 0),
    ("session_started", AgentState.PERMISSION, AgentState.PERMISSION, 0),
    ("session_started", AgentState.EXITED, AgentState.IDLE, 0),
    # turn_started (+1 turno solo desde UNKNOWN/IDLE)
    ("turn_started", AgentState.UNKNOWN, AgentState.WORKING, 1),
    ("turn_started", AgentState.IDLE, AgentState.WORKING, 1),
    ("turn_started", AgentState.WORKING, AgentState.WORKING, 0),
    ("turn_started", AgentState.PERMISSION, AgentState.WORKING, 0),
    ("turn_started", AgentState.EXITED, AgentState.WORKING, 0),
    # injection_submitted (+1 turno solo desde UNKNOWN/IDLE)
    ("injection_submitted", AgentState.UNKNOWN, AgentState.WORKING, 1),
    ("injection_submitted", AgentState.IDLE, AgentState.WORKING, 1),
    ("injection_submitted", AgentState.WORKING, AgentState.WORKING, 0),
    ("injection_submitted", AgentState.PERMISSION, AgentState.WORKING, 0),
    ("injection_submitted", AgentState.EXITED, AgentState.WORKING, 0),
    # human_submitted (+1 turno solo desde UNKNOWN/IDLE)
    ("human_submitted", AgentState.UNKNOWN, AgentState.WORKING, 1),
    ("human_submitted", AgentState.IDLE, AgentState.WORKING, 1),
    ("human_submitted", AgentState.WORKING, AgentState.WORKING, 0),
    ("human_submitted", AgentState.PERMISSION, AgentState.WORKING, 0),
    ("human_submitted", AgentState.EXITED, AgentState.WORKING, 0),
    # turn_ended
    ("turn_ended", AgentState.UNKNOWN, AgentState.IDLE, 0),
    ("turn_ended", AgentState.IDLE, AgentState.IDLE, 0),
    ("turn_ended", AgentState.WORKING, AgentState.IDLE, 0),
    ("turn_ended", AgentState.PERMISSION, AgentState.IDLE, 0),
    ("turn_ended", AgentState.EXITED, AgentState.EXITED, 0),
    # permission_requested
    ("permission_requested", AgentState.UNKNOWN, AgentState.PERMISSION, 0),
    ("permission_requested", AgentState.IDLE, AgentState.PERMISSION, 0),
    ("permission_requested", AgentState.WORKING, AgentState.PERMISSION, 0),
    ("permission_requested", AgentState.PERMISSION, AgentState.PERMISSION, 0),
    ("permission_requested", AgentState.EXITED, AgentState.EXITED, 0),
    # tool_used
    ("tool_used", AgentState.UNKNOWN, AgentState.WORKING, 0),
    ("tool_used", AgentState.IDLE, AgentState.WORKING, 0),
    ("tool_used", AgentState.WORKING, AgentState.WORKING, 0),
    ("tool_used", AgentState.PERMISSION, AgentState.WORKING, 0),
    ("tool_used", AgentState.EXITED, AgentState.EXITED, 0),
    # idle_notice
    ("idle_notice", AgentState.UNKNOWN, AgentState.IDLE, 0),
    ("idle_notice", AgentState.IDLE, AgentState.IDLE, 0),
    ("idle_notice", AgentState.WORKING, AgentState.IDLE, 0),
    ("idle_notice", AgentState.PERMISSION, AgentState.PERMISSION, 0),
    ("idle_notice", AgentState.EXITED, AgentState.EXITED, 0),
    # subagent_ended (sin cambio)
    ("subagent_ended", AgentState.UNKNOWN, AgentState.UNKNOWN, 0),
    ("subagent_ended", AgentState.IDLE, AgentState.IDLE, 0),
    ("subagent_ended", AgentState.WORKING, AgentState.WORKING, 0),
    ("subagent_ended", AgentState.PERMISSION, AgentState.PERMISSION, 0),
    ("subagent_ended", AgentState.EXITED, AgentState.EXITED, 0),
    # pane_exited
    ("pane_exited", AgentState.UNKNOWN, AgentState.EXITED, 0),
    ("pane_exited", AgentState.IDLE, AgentState.EXITED, 0),
    ("pane_exited", AgentState.WORKING, AgentState.EXITED, 0),
    ("pane_exited", AgentState.PERMISSION, AgentState.EXITED, 0),
    ("pane_exited", AgentState.EXITED, AgentState.EXITED, 0),
]


def test_transition_table_covers_all_normalized_events_and_states() -> None:
    covered = {(ev, st) for ev, st, _, _ in _TRANSITION_CASES}
    expected = {(ev, st) for ev in NORMALIZED_EVENTS for st in AgentState}
    assert covered == expected


@pytest.mark.parametrize(("event", "prev_state", "expected_state", "turn_delta"), _TRANSITION_CASES)
def test_apply_event_transition_table(
    event: str, prev_state: AgentState, expected_state: AgentState, turn_delta: int
) -> None:
    prev = _snap(prev_state, turn_seq=3, since_ms=100, source="hook")
    nxt = apply_event(prev, event, at_ms=500, source="hook")
    assert nxt.state == expected_state
    assert nxt.turn_seq == 3 + turn_delta
    if expected_state != prev_state:
        assert nxt.since_ms == 500
    else:
        assert nxt.since_ms == prev.since_ms


def test_apply_event_tracks_reliable_hook_idle_and_rejects_unknown_event() -> None:
    s0 = initial_snapshot("inc-1", at_ms=10)
    s1 = apply_event(s0, "human_submitted", at_ms=20, source="human")
    assert s1.state == AgentState.WORKING and s1.source == "human" and s1.last_idle_at_ms is None

    s2 = apply_event(s1, "turn_ended", at_ms=40, source="hook")
    assert s2.state == AgentState.IDLE and s2.source == "hook" and s2.last_idle_at_ms == 40

    # Si un evento no es de fuente "hook", no actualiza last_idle_at_ms
    s3 = apply_event(s2, "idle_notice", at_ms=60, source="injection")
    assert s3.last_idle_at_ms == 40

    with pytest.raises(ValueError):
        apply_event(s2, "invented_event", at_ms=80, source="hook")


def test_registry_ignores_events_from_old_incarnation() -> None:
    reg = AgentActivityRegistry(clock_ms=lambda: 1000)
    assert reg.snapshot("node-1").state == AgentState.UNKNOWN

    reg.reset("node-1", "inc-new")
    assert reg.snapshot("node-1").state == AgentState.UNKNOWN
    assert reg.snapshot("node-1").incarnation == "inc-new"

    seen_sessions: List[Tuple[str, str]] = []
    reg.on_cli_session_id(lambda n, sid: seen_sessions.append((n, sid)))

    # Evento de una encarnación vieja se descarta por completo
    ignored = reg.ingest("node-1", "inc-old", "turn_ended", 1100, "hook", cli_session_id="cli-old")
    assert ignored.state == AgentState.UNKNOWN
    assert ignored.incarnation == "inc-new"
    assert seen_sessions == []

    # Evento de la encarnación vigente se aplica
    accepted = reg.ingest("node-1", "inc-new", "turn_ended", 1200, "hook", cli_session_id="cli-new")
    assert accepted.state == AgentState.IDLE
    assert accepted.source == "hook"
    assert accepted.last_idle_at_ms == 1200
    assert seen_sessions == [("node-1", "cli-new")]


def test_registry_isolates_subscriber_errors() -> None:
    reg = AgentActivityRegistry(clock_ms=lambda: 1000)
    reg.reset("node-1", "inc-1")
    observed: List[Tuple[str, AgentState]] = []

    def _bad_sub(node: str, snap: ActivitySnapshot) -> None:
        raise RuntimeError("boom activity")

    def _good_sub(node: str, snap: ActivitySnapshot) -> None:
        # Además verifica que el cerrojo no esté retenido durante la notificación
        assert reg.snapshot(node).state == snap.state
        observed.append((node, snap.state))

    reg.subscribe(_bad_sub)
    reg.subscribe(_good_sub)

    session_observed: List[Tuple[str, str]] = []
    reg.on_cli_session_id(lambda n, sid: (_ for _ in ()).throw(RuntimeError("boom session")))
    reg.on_cli_session_id(lambda n, sid: session_observed.append((n, sid)))

    reg.ingest("node-1", "inc-1", "turn_started", 1050, "hook", cli_session_id="sess-42")
    assert observed == [("node-1", AgentState.WORKING)]
    assert session_observed == [("node-1", "sess-42")]


def test_human_submit_marks_working() -> None:
    activity = AgentActivityRegistry(clock_ms=lambda: 2000)
    panes = PaneRegistry(clock=lambda: 2.0)
    pane = panes.register_pane("pane_1", cli_profile="claude", project_uuid="proj-1")
    node_uuid = pane["node_uuid"]

    activity.reset(node_uuid, "pane_1-s1")
    activity.ingest(node_uuid, "pane_1-s1", "turn_ended", 2000, "hook")
    assert activity.snapshot(node_uuid).state == AgentState.IDLE

    called_nodes: List[str] = []

    def _on_submit(n: str) -> None:
        # Verificar que el cerrojo de PaneRegistry está libre al invocar oyentes
        assert panes.get_pane("pane_1") is not None
        called_nodes.append(n)
        activity.mark(n, "human_submitted", 2500, "human")

    panes.add_human_submit_listener(_on_submit)

    # Pulsación sin Enter no dispara turno humano
    panes.record_human_keystroke("pane_1", submitted=False)
    assert called_nodes == []
    assert activity.snapshot(node_uuid).state == AgentState.IDLE

    # Pulsación con Enter sí marca WORKING con fuente "human" y conserva la encarnación
    panes.record_human_keystroke("pane_1", submitted=True)
    assert called_nodes == [node_uuid]
    snap = activity.snapshot(node_uuid)
    assert snap.state == AgentState.WORKING
    assert snap.source == "human"
    assert snap.incarnation == "pane_1-s1"

    # En hilo separado también funciona sin bloqueo
    t = threading.Thread(target=lambda: panes.record_human_keystroke("pane_1", submitted=True))
    t.start()
    t.join(timeout=5)
    assert len(called_nodes) == 2
