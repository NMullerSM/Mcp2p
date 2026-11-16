"""Tests para SwarmRelayOrchestrator, await_next_assignment, guardas PTY y guard AST async en src/mcp/."""

from __future__ import annotations

import ast
import json
import threading
import time
from pathlib import Path
from typing import Any, List, Tuple

import pytest

from src.a2a.bus import A2ABus
from src.core.domain.context import DomainEvent
from src.core.events import DomainEventBus
from src.core.services import SwarmRelayOrchestrator
from src.mcp.pane_registry import PaneRegistry
from src.mcp.server import create_mcp_server
from src.mcp.tools import MCPToolsHandler
from src.p2p.crypto import NodeCryptoManager
from src.storage.db import DatabaseManager


class _FakeTerminalConnector:
    def __init__(self) -> None:
        self.calls: List[Tuple[int | str, str]] = []

    def send_input(self, pane_id: int | str, data: str) -> dict[str, Any]:
        self.calls.append((pane_id, data))
        return {"success": True, "bytes_written": len(data)}


# ----------------------------------------------------------------------
# 1. Suscripciones reales DomainEventBus + A2ABus y espera push->pull
# ----------------------------------------------------------------------


def test_relay_subscribes_domain_event_bus_and_a2a_bus_with_pull_delivery(tmp_path: Path) -> None:
    db = DatabaseManager(db_path=str(tmp_path / "relay_bus.db"))
    proj_id = db.create_project("Proyecto Relay", str(tmp_path / "repo"))["project_uuid"]
    event_bus = DomainEventBus()
    bus = A2ABus(db)

    orch = SwarmRelayOrchestrator(event_bus=event_bus, bus=bus)
    try:
        # Evento Kanban dirigido a node-a y otro evento de otro proyecto
        event_bus.publish(
            DomainEvent(
                event_type="kanban.lease_released",
                project_uuid=proj_id,
                payload={"card_id": "CARD-1", "target_node_uuid": "node-a"},
            )
        )
        event_bus.publish(
            DomainEvent(
                event_type="tribunal.dispute_opened",
                project_uuid="otro-proyecto",
                payload={"session_id": "DISP-9", "target_node_uuid": "node-a"},
            )
        )
        # RFC_TURN real mediante A2ABus.send_proposal (intent=COORDINATION)
        proposal = bus.send_proposal(
            project_uuid=proj_id,
            sender_node_uuid="node-b",
            session_id="RFC-42",
            component="relay",
            intent="Sincronizar relevo",
            rationale="Evitar polling",
            diff_ref="src/core/services/swarm_relay_service.py",
            impact="Bajo",
            card_id="CARD-1",
        )
        # Mensaje A2A directo con target explícito
        bus.send_informative(
            project_uuid=proj_id,
            sender_node_uuid="node-b",
            signal_type="HANDOFF",
            data={"target_node_uuid": "node-a", "next_step": "verify"},
            card_id="CARD-2",
        )

        first = orch.await_next_assignment("node-a", project_uuid=proj_id, timeout_ms=0)
        assert first["success"] is True
        assert first["has_assignment"] is True
        assert first["assignment"]["event_type"] == "kanban.lease_released"
        assert first["assignment"]["card_id"] == "CARD-1"

        second = orch.await_next_assignment("node-a", project_uuid=proj_id, timeout_ms=0)
        assert second["has_assignment"] is True
        assert second["assignment"]["signal_type"] == "HANDOFF"
        assert second["assignment"]["card_id"] == "CARD-2"

        # El proposal emitido a wildcard "*" también llega sin perderse al filtrar por proyecto
        third = orch.await_next_assignment("node-a", project_uuid=proj_id, timeout_ms=0)
        assert third["has_assignment"] is True
        assert third["assignment"]["kind"] == "RFC_TURN"
        assert third["assignment"]["session_id"] == "RFC-42"
        assert third["assignment"]["message_uuid"] == proposal["message_uuid"]

        # El evento de otro-proyecto sigue intacto hasta pedirlo sin filtro o con ese proyecto
        other = orch.await_next_assignment("node-a", project_uuid="otro-proyecto", timeout_ms=0)
        assert other["has_assignment"] is True
        assert other["assignment"]["event_type"] == "tribunal.dispute_opened"
    finally:
        orch.close()
        db.close()


def test_await_next_assignment_condition_wait_wakeup_and_timeout() -> None:
    orch = SwarmRelayOrchestrator()
    try:
        timed_out = orch.await_next_assignment("node-wait", timeout_ms=40)
        assert timed_out["success"] is True
        assert timed_out["has_assignment"] is False
        assert timed_out["assignment"] is None
        assert timed_out["timed_out"] is True

        result_holder: dict[str, Any] = {}

        def _waiter() -> None:
            result_holder["res"] = orch.await_next_assignment(
                "node-wait",
                project_uuid="proj-w",
                timeout_ms=2500,
            )

        t = threading.Thread(target=_waiter, daemon=True)
        t0 = time.monotonic()
        t.start()
        time.sleep(0.03)
        orch.enqueue_assignment(
            "node-wait",
            {"project_uuid": "proj-w", "kind": "HANDOFF", "card_id": "C-WAKE"},
        )
        t.join(timeout=2.0)
        elapsed = time.monotonic() - t0

        assert elapsed < 1.0
        assert result_holder["res"]["has_assignment"] is True
        assert result_holder["res"]["timed_out"] is False
        assert result_holder["res"]["assignment"]["card_id"] == "C-WAKE"
    finally:
        orch.close()


def test_relay_close_unsubscribes_and_wakes_blocked_waiters(tmp_path: Path) -> None:
    db = DatabaseManager(db_path=str(tmp_path / "close_relay.db"))
    proj_id = db.create_project("Proyecto Close", str(tmp_path / "repo"))["project_uuid"]
    event_bus = DomainEventBus()
    bus = A2ABus(db)

    orch = SwarmRelayOrchestrator(event_bus=event_bus, bus=bus)
    waiter_out: dict[str, Any] = {}

    def _blocked_waiter() -> None:
        waiter_out["res"] = orch.await_next_assignment("node-close", timeout_ms=5000)

    t = threading.Thread(target=_blocked_waiter, daemon=True)
    t.start()
    time.sleep(0.03)
    t0 = time.monotonic()
    orch.close()
    t.join(timeout=2.0)
    elapsed = time.monotonic() - t0

    assert elapsed < 1.0
    assert waiter_out["res"]["has_assignment"] is False
    assert waiter_out["res"]["closed"] is True
    assert orch._on_domain_event not in event_bus._subscribers.get("*", [])
    assert orch._on_a2a_message not in bus._subscribers.get("*", [])

    # Publicar tras close no encola nada
    event_bus.publish(DomainEvent("kanban.card_created", proj_id, {"card_id": "AFTER-CLOSE"}))
    assert orch.await_next_assignment("node-close", timeout_ms=0)["has_assignment"] is False
    db.close()


# ----------------------------------------------------------------------
# 2. Guarda Gate 0c (clave en texto plano / crypto=None bloqueada)
# ----------------------------------------------------------------------


def test_guard_gate_0c_blocks_plaintext_and_missing_crypto_allows_encrypted(tmp_path: Path) -> None:
    connector = _FakeTerminalConnector()

    # Caso 1: crypto=None fail-closed
    orch_no_crypto = SwarmRelayOrchestrator(terminal_connector=connector, crypto=None)
    res_none = orch_no_crypto.dispatch_to_pane("pane_1", "run checks")
    assert res_none["success"] is False
    assert res_none["blocked"] is True
    assert res_none["code"] == "GATE_0C_PLAIN_KEY_RELAY_BLOCKED"
    assert connector.calls == []
    orch_no_crypto.close()

    # Caso 2: NodeCryptoManager en texto plano (is_encrypted == False)
    crypto = NodeCryptoManager(key_path=tmp_path / "node_identity.key")
    assert crypto.is_encrypted is False
    orch = SwarmRelayOrchestrator(terminal_connector=connector, crypto=crypto)
    try:
        res_plain = orch.dispatch_to_pane("pane_1", "run checks")
        assert res_plain["success"] is False
        assert res_plain["blocked"] is True
        assert res_plain["code"] == "GATE_0C_PLAIN_KEY_RELAY_BLOCKED"
        assert connector.calls == []

        # Caso 3: tras cifrar la identidad con passphrase (is_encrypted == True), permite despacho
        crypto.set_passphrase("clave-segura-gate-0c")
        assert crypto.is_encrypted is True

        res_ok = orch.dispatch_to_pane("pane_1", "run checks", project_uuid="p1", card_id="c1")
        assert res_ok["success"] is True
        assert res_ok["dispatched"] is True
        assert connector.calls == [("pane_1", "run checks")]
    finally:
        orch.close()


# ----------------------------------------------------------------------
# 3. Guarda Anti-colisión humana (cooldown 5s)
# ----------------------------------------------------------------------


def test_guard_human_typing_cooldown_defers_for_5s_then_dispatches(tmp_path: Path) -> None:
    now = [1000.0]
    registry = PaneRegistry(clock=lambda: now[0])
    registry.register_pane("pane_1", node_uuid="agent-a", session_token="tok-a")
    crypto = NodeCryptoManager(key_path=tmp_path / "node_identity.key")
    crypto.set_passphrase("clave-segura-cooldown")
    connector = _FakeTerminalConnector()

    orch = SwarmRelayOrchestrator(
        pane_registry=registry,
        terminal_connector=connector,
        crypto=crypto,
        human_cooldown_seconds=5.0,
        clock=lambda: now[0],
    )
    try:
        registry.record_human_keystroke("pane_1")
        res_0 = orch.dispatch_to_pane("pane_1", "auto prompt", project_uuid="p1", card_id="c1")
        assert res_0["success"] is False
        assert res_0["deferred"] is True
        assert res_0["code"] == "HUMAN_TYPING_COOLDOWN_ACTIVE"
        assert connector.calls == []

        now[0] = 1004.9
        res_49 = orch.dispatch_to_pane("pane_1", "auto prompt", project_uuid="p1", card_id="c1")
        assert res_49["deferred"] is True
        assert res_49["code"] == "HUMAN_TYPING_COOLDOWN_ACTIVE"
        assert connector.calls == []
        assert len(orch.get_deferred_dispatches("pane_1")) == 2

        now[0] = 1005.1
        res_after = orch.dispatch_to_pane("pane_1", "direct prompt", project_uuid="p1", card_id="c1")
        assert res_after["success"] is True
        assert res_after["dispatched"] is True
        assert connector.calls == [("pane_1", "direct prompt")]

        flushed = orch.flush_deferred_dispatches("pane_1")
        assert flushed["dispatched"] == 2
        assert len(connector.calls) == 3
    finally:
        orch.close()


# ----------------------------------------------------------------------
# 4. Guarda Loop Breaker DoD (max_relay_rounds=6 y HITL)
# ----------------------------------------------------------------------


def test_guard_loop_breaker_dod_blocks_after_max_relay_rounds(tmp_path: Path) -> None:
    crypto = NodeCryptoManager(key_path=tmp_path / "node_identity.key")
    crypto.set_passphrase("clave-segura-loop")
    connector = _FakeTerminalConnector()

    orch = SwarmRelayOrchestrator(
        terminal_connector=connector,
        crypto=crypto,
        max_relay_rounds=6,
    )
    try:
        for round_idx in range(1, 7):
            res = orch.dispatch_to_pane(
                "pane_1",
                f"turn-{round_idx}",
                project_uuid="proj-lb",
                card_id="CARD-LB",
            )
            assert res["success"] is True
            assert res["rounds"] == round_idx

        assert len(connector.calls) == 6

        tripped = orch.dispatch_to_pane(
            "pane_1",
            "turn-7",
            project_uuid="proj-lb",
            card_id="CARD-LB",
        )
        assert tripped["success"] is False
        assert tripped["blocked"] is True
        assert tripped["status"] == "LOOP_BREAKER_TRIPPED"
        assert tripped["code"] == "RELAY_MAX_ROUNDS_EXCEEDED"
        assert tripped["requires_hitl"] is True
        assert len(connector.calls) == 6  # El 7mo no toca el PTY

        state = orch.get_loop_breaker_state(project_uuid="proj-lb", card_id="CARD-LB")
        assert state["status"] == "LOOP_BREAKER_TRIPPED"
        assert state["requires_hitl"] is True

        # Relevo en cola también aplica Loop Breaker DoD: una ronda es un CAMBIO DE SENTIDO entre dos nodos
        # (EGM-72), no cada evento; aquí agent-x y agent-y se devuelven la sesión sin converger.
        for idx in range(6):
            sender, target = ("agent-y", "agent-x") if idx % 2 == 0 else ("agent-x", "agent-y")
            enq = orch.enqueue_assignment(
                target,
                {"project_uuid": "proj-lb", "session_id": "RFC-LB", "round": idx + 1, "sender_node_uuid": sender},
            )
            assert enq["success"] is True

        enq_blocked = orch.enqueue_assignment(
            "agent-x",
            {"project_uuid": "proj-lb", "session_id": "RFC-LB", "round": 7, "sender_node_uuid": "agent-y"},
        )
        assert enq_blocked["success"] is False
        assert enq_blocked["status"] == "LOOP_BREAKER_TRIPPED"
        assert enq_blocked["code"] == "RELAY_MAX_ROUNDS_EXCEEDED"
        assert enq_blocked["requires_hitl"] is True

        # Intervención humana explícita restablece el circuito
        assert orch.reset_loop_breaker(project_uuid="proj-lb", card_id="CARD-LB") is True
        resumed = orch.dispatch_to_pane(
            "pane_1",
            "turn-after-hitl",
            project_uuid="proj-lb",
            card_id="CARD-LB",
        )
        assert resumed["success"] is True
        assert len(connector.calls) == 7
    finally:
        orch.close()


# ----------------------------------------------------------------------
# 5. Integración MCPToolsHandler + EGM-22 + registro en create_mcp_server
# ----------------------------------------------------------------------


def test_mcp_handler_and_server_await_next_assignment_with_egm22_protection(tmp_path: Path) -> None:
    db_path = str(tmp_path / "mcp_relay.db")
    db = DatabaseManager(db_path=db_path)
    proj_id = db.create_project("Proyecto MCP Relay", str(tmp_path / "repo"))["project_uuid"]
    registry = PaneRegistry()
    registry.register_pane("pane_1", node_uuid="node-alpha", session_token="tok-alpha")
    registry.register_pane("pane_2", node_uuid="node-beta", session_token="tok-beta")

    handler = MCPToolsHandler(db_manager=db, pane_registry=registry)
    try:
        # Encolar trabajo específico para node-beta y node-alpha
        relay = handler._get_relay_orchestrator()
        relay.enqueue_assignment(
            "node-beta",
            {"project_uuid": proj_id, "card_id": "CARD-BETA", "kind": "HANDOFF"},
        )
        relay.enqueue_assignment(
            "node-alpha",
            {"project_uuid": proj_id, "card_id": "CARD-ALPHA", "kind": "HANDOFF"},
        )

        # EGM-22: node-alpha NO puede leer la cola de node-beta
        spoofed = json.loads(
            handler.await_next_assignment(
                project_uuid=proj_id,
                node_uuid="node-beta",
                session_token="tok-alpha",
                timeout_ms=0,
            )
        )
        assert spoofed["success"] is False
        assert spoofed["code"] == "NODE_UUID_SPOOFING_FORBIDDEN"

        # Token inválido rechazado
        bad_tok = json.loads(
            handler.await_next_assignment(
                project_uuid=proj_id,
                node_uuid="node-alpha",
                session_token="tok-invalido",
                timeout_ms=0,
            )
        )
        assert bad_tok["success"] is False
        assert bad_tok["code"] == "INVALID_PANE_SESSION_TOKEN"

        # Lectura legítima de node-alpha y de node-beta (sigue intacta tras el intento spoof)
        ok_alpha = json.loads(
            handler.await_next_assignment(
                project_uuid=proj_id,
                node_uuid="node-alpha",
                session_token="tok-alpha",
                timeout_ms=0,
            )
        )
        assert ok_alpha["success"] is True
        assert ok_alpha["has_assignment"] is True
        assert ok_alpha["assignment"]["card_id"] == "CARD-ALPHA"
        assert "unread_notifications" in ok_alpha

        ok_beta = json.loads(
            handler.await_next_assignment(
                project_uuid=proj_id,
                node_uuid="node-beta",
                session_token="tok-beta",
                timeout_ms=0,
            )
        )
        assert ok_beta["success"] is True
        assert ok_beta["has_assignment"] is True
        assert ok_beta["assignment"]["card_id"] == "CARD-BETA"
    finally:
        handler.close()
        db.close()

    # Verificar registro en create_mcp_server
    srv = create_mcp_server(db_path=str(tmp_path / "srv_relay.db"))
    try:
        tools_map = getattr(getattr(srv, "_tool_manager", None), "_tools", {})
        assert "await_next_assignment" in tools_map
        tool_fn = tools_map["await_next_assignment"].fn
        raw_res = tool_fn(nodeUuid="node-srv", timeoutMs=0)
        parsed = json.loads(raw_res)
        assert parsed["success"] is True
        assert parsed["has_assignment"] is False
    finally:
        srv_handler = getattr(srv, "mcp2p_handler", None)
        if srv_handler is not None:
            srv_handler.close()


# ----------------------------------------------------------------------
# 6. Guard AST: ninguna función async def en src/mcp/**/*.py llama síncronamente
#    a subprocess.run, subprocess.Popen, sqlite3.connect o time.sleep
# ----------------------------------------------------------------------


_FORBIDDEN_CALLS = {
    ("subprocess", "run"),
    ("subprocess", "Popen"),
    ("sqlite3", "connect"),
    ("time", "sleep"),
}
_FORBIDDEN_ALIAS_NAMES = {"run", "Popen", "connect", "sleep"}


def _collect_imported_aliases(tree: ast.AST) -> dict[str, tuple[str, str]]:
    aliases: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {"subprocess", "sqlite3", "time"}:
            for alias in node.names:
                pair = (node.module, alias.name)
                if pair in _FORBIDDEN_CALLS:
                    aliases[alias.asname or alias.name] = pair
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in {"subprocess", "sqlite3", "time"}:
                    aliases[alias.asname or alias.name] = (alias.name, "")
    return aliases


def test_async_def_in_src_mcp_has_no_blocking_sync_calls() -> None:
    mcp_dir = Path(__file__).resolve().parents[1] / "src" / "mcp"
    py_files = sorted(mcp_dir.rglob("*.py"))
    assert py_files, "No se encontraron archivos Python en src/mcp/"

    violations: List[str] = []
    for path in py_files:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        aliases = _collect_imported_aliases(tree)

        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            for inner in ast.walk(node):
                if not isinstance(inner, ast.Call):
                    continue
                func = inner.func
                if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                    mod_name = aliases.get(func.value.id, (func.value.id, ""))[0]
                    if (mod_name, func.attr) in _FORBIDDEN_CALLS:
                        violations.append(
                            f"{path.name}:{inner.lineno} en async def {node.name}: "
                            f"{func.value.id}.{func.attr}()"
                        )
                elif isinstance(func, ast.Name) and func.id in aliases:
                    mod, attr = aliases[func.id]
                    if (mod, attr) in _FORBIDDEN_CALLS and func.id in _FORBIDDEN_ALIAS_NAMES:
                        violations.append(
                            f"{path.name}:{inner.lineno} en async def {node.name}: "
                            f"{func.id}() (de {mod}.{attr})"
                        )

    assert not violations, "Llamadas síncronas bloqueantes dentro de async def en src/mcp:\n" + "\n".join(violations)
