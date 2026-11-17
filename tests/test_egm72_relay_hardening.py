"""EGM-72: el relevo queda seguro (sin activarse). El Loop Breaker cuenta CAMBIOS DE SENTIDO entre dos nodos
-no cada evento-, no depende de un `assignment_key` rotable, se reinicia desde la UI con dialogo nativo y las
colas tienen tope. `dispatch_to_pane` / `flush_*` ya no existen (F0 del dossier 37)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, List

import pytest

from conftest import make_test_db
from src.app.api_bridge import ApiBridge
from src.core.services.swarm_relay_service import MAX_QUEUE_DEPTH, MAX_TARGET_QUEUES, SwarmRelayOrchestrator

ROOT = Path(__file__).resolve().parent.parent


def _pingpong(orch: SwarmRelayOrchestrator, n: int, *, rotate_key: bool = False, card: str = "C1") -> List[dict]:
    out = []
    for i in range(n):
        sender, target = ("agent-a", "agent-b") if i % 2 == 0 else ("agent-b", "agent-a")
        payload = {"project_uuid": "p", "card_id": card, "sender_node_uuid": sender}
        if rotate_key:
            payload["assignment_key"] = f"rotada-{i}"
        out.append(orch.enqueue_assignment(target, payload))
    return out


def test_legitimate_repeated_events_do_not_trip_the_breaker() -> None:
    """Antes: 8 ediciones legitimas de una tarjeta dejaban LOOP_BREAKER_TRIPPED para siempre."""
    orch = SwarmRelayOrchestrator(max_relay_rounds=6)
    try:
        results = [
            orch.enqueue_assignment("*", {"project_uuid": "p", "card_id": "C1", "sender_node_uuid": "agent-a", "n": i})
            for i in range(40)
        ]
        assert all(r["success"] for r in results), [r.get("code") for r in results if not r["success"]]
        assert orch.get_loop_breaker_state(project_uuid="p", card_id="C1")["status"] == "ACTIVE"
        # una conversacion corta entre dos agentes tampoco lo dispara
        assert all(r["success"] for r in _pingpong(orch, 5, card="C2"))
    finally:
        orch.close()


def test_pingpong_trips_and_rotating_assignment_key_cannot_evade() -> None:
    """Antes: rotar `assignment_key` (lo controla el agente) daba 50/50 mensajes encolados."""
    orch = SwarmRelayOrchestrator(max_relay_rounds=6)
    try:
        results = _pingpong(orch, 12, rotate_key=True)
        assert [r["success"] for r in results] == [True] * 6 + [False] * 6
        assert results[6]["code"] == "RELAY_MAX_ROUNDS_EXCEEDED" and results[6]["requires_hitl"] is True
        state = orch.get_loop_breaker_state(project_uuid="p", card_id="C1")
        assert state["status"] == "LOOP_BREAKER_TRIPPED" and state["requires_hitl"] is True
        assert orch.list_tripped_loop_breakers() and orch.list_tripped_loop_breakers()[0].startswith("p:C1|")
        # una vez disparado tambien se bloquea al mismo remitente (HITL)
        assert orch.enqueue_assignment("agent-b", {"project_uuid": "p", "card_id": "C1", "sender_node_uuid": "agent-a"})["blocked"]
        # otra tarjeta u otro par de nodos no se ven afectados
        assert orch.enqueue_assignment("agent-b", {"project_uuid": "p", "card_id": "C9", "sender_node_uuid": "agent-a"})["success"]
        assert orch.enqueue_assignment("agent-z", {"project_uuid": "p", "card_id": "C1", "sender_node_uuid": "agent-c"})["success"]
    finally:
        orch.close()


def test_reset_is_scoped_to_card_and_resumes_the_flow() -> None:
    orch = SwarmRelayOrchestrator(max_relay_rounds=6)
    try:
        _pingpong(orch, 8)
        _pingpong(orch, 8, card="C2")
        assert orch.reset_loop_breaker(project_uuid="p", card_id="C1") is True
        assert orch.get_loop_breaker_state(project_uuid="p", card_id="C1")["status"] == "ACTIVE"
        assert orch.get_loop_breaker_state(project_uuid="p", card_id="C2")["status"] == "LOOP_BREAKER_TRIPPED"
        assert all(r["success"] for r in _pingpong(orch, 4))
    finally:
        orch.close()


def test_star_queue_is_bounded_and_drops_the_oldest() -> None:
    """Antes: 20 056 elementos tras 20 000 tarjetas sin consumidor."""
    orch = SwarmRelayOrchestrator()
    try:
        total = MAX_QUEUE_DEPTH + 50
        results = [orch.enqueue_assignment("*", {"project_uuid": "p", "card_id": f"K{i}", "seq": i}) for i in range(total)]
        assert all(r["success"] for r in results)
        assert results[MAX_QUEUE_DEPTH - 1]["dropped_oldest"] is False and results[MAX_QUEUE_DEPTH]["dropped_oldest"] is True
        assert results[-1]["queue_depth"] == MAX_QUEUE_DEPTH
        first = orch.await_next_assignment("*")
        assert first["assignment"]["seq"] == 50  # los 50 mas antiguos se descartaron
        assert orch._dropped_oldest_total == 50
    finally:
        orch.close()


def test_number_of_distinct_target_queues_is_bounded() -> None:
    orch = SwarmRelayOrchestrator()
    try:
        results = [orch.enqueue_assignment(f"nodo-{i}", {"project_uuid": "p"}) for i in range(MAX_TARGET_QUEUES + 20)]
        ok = [r for r in results if r["success"]]
        assert len(ok) == MAX_TARGET_QUEUES
        assert results[-1]["code"] == "QUEUE_TARGET_LIMIT"
        # vaciar una cola libera sitio
        assert orch.await_next_assignment("nodo-0")["has_assignment"] is True
        assert orch.enqueue_assignment("nodo-nuevo", {"project_uuid": "p"})["success"] is True
    finally:
        orch.close()


def test_dispatch_and_flush_no_longer_exist_anywhere() -> None:
    """EGM-72 (d) / F0: la inyeccion automatica en stdin se retiro; si alguien la reintroduce, este test obliga a revisarlo."""
    callers = []
    for path in (ROOT / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"\b(dispatch_to_pane|flush_deferred_dispatches|flush_idle_dispatches)\b", text):
            callers.append(str(path.relative_to(ROOT)))
    assert callers == [], f"reaparece la inyeccion automatica en stdin: {callers}"


@pytest.fixture
def bridge():
    b = ApiBridge(db_manager=make_test_db())
    try:
        yield b
    finally:
        b._close_terminal_matrix()
        b.core_kernel.close()


def test_bridge_reset_loop_breaker_needs_a_running_hub_and_native_confirmation(bridge: ApiBridge, monkeypatch: pytest.MonkeyPatch) -> None:
    assert "reset_loop_breaker" in {f.__name__ for f in bridge.public_js_functions()}
    assert bridge.reset_loop_breaker("p", "C1")["code"] == "RELAY_UNAVAILABLE"
    assert bridge.reset_loop_breaker("p", "")["code"] == "INVALID_ARGUMENT"
    assert bridge.reset_loop_breaker("p", {"x": 1})["code"] == "INVALID_ARGUMENT"

    assert bridge.extensions_ui._ensure_hub_started()
    relay = bridge.extensions_ui._embedded_hub.relay_orchestrator()
    assert relay is not None
    _pingpong(relay, 8)
    assert relay.get_loop_breaker_state(project_uuid="p", card_id="C1")["status"] == "LOOP_BREAKER_TRIPPED"

    monkeypatch.delenv("MCP2P_TEST_AUTO_CONFIRM", raising=False)
    # sin ventana ni dialogo: se rechaza y NO se reinicia
    assert bridge.reset_loop_breaker("p", "C1")["code"] == "CONFIRMATION_UNAVAILABLE"
    shown: List[Any] = []
    bridge.confirmations.dialog = lambda t, m: shown.append(m) or False
    assert bridge.reset_loop_breaker("p", "C1")["code"] == "CONFIRMATION_DECLINED"
    assert relay.get_loop_breaker_state(project_uuid="p", card_id="C1")["status"] == "LOOP_BREAKER_TRIPPED"
    bridge.confirmations.dialog = lambda t, m: shown.append(m) or True
    res = bridge.reset_loop_breaker("p", "C1")
    assert res["success"] is True
    assert "C1" in shown[0]
    assert relay.get_loop_breaker_state(project_uuid="p", card_id="C1")["status"] == "ACTIVE"
