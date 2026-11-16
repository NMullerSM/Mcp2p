"""Tests E2E para OSC 7501, KanbanSwarmAdapter, list_swarm_panes, send_pane_message y Auto-Relay."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

from src.a2a.bus import A2ABus
from src.adapters.terminal.pty_host import PosixPtyHostConnector, _Osc7501Parser
from src.core.domain.context import DomainEvent
from src.core.events import DomainEventBus
from src.core.factory import ApplicationFactory
from src.core.kernel import CoreKernel
from src.core.services import SwarmRelayOrchestrator
from src.core.services.kanban_swarm_adapter import KanbanSwarmAdapter
from src.mcp.pane_registry import PaneRegistry
from src.mcp.tools import MCPToolsHandler
from src.p2p.crypto import NodeCryptoManager
from src.storage.db import DatabaseManager


class _ControllableTerminalConnector:
    """Conector PTY simulado con estado idle/working controlable por panel."""

    def __init__(self) -> None:
        self.calls: List[Tuple[str, str]] = []
        self._idle: Dict[str, bool] = {}

    def set_idle(self, pane_id: str, idle: bool) -> None:
        self._idle[str(pane_id)] = bool(idle)

    def is_pane_idle(self, pane_id: Any, *, min_silence_s: float = 1.5) -> bool:
        return self._idle.get(str(pane_id), True)

    def get_pane_activity(self, pane_id: Any, *, min_silence_s: float = 1.5) -> Dict[str, Any]:
        idle = self.is_pane_idle(pane_id, min_silence_s=min_silence_s)
        return {
            "pane_id": str(pane_id),
            "state": "idle" if idle else "working",
            "source": "osc7501",
            "idle": idle,
        }

    def send_input(self, pane_id: Any, data: str) -> Dict[str, Any]:
        self.calls.append((str(pane_id), data))
        return {"success": True, "bytes_written": len(data)}


def _wait_until(predicate: Any, *, timeout_s: float = 4.0, step_s: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step_s)
    return bool(predicate())


# ----------------------------------------------------------------------
# 1. _Osc7501Parser + sesión PTY real
# ----------------------------------------------------------------------


def test_osc7501_parser_incremental_and_terminators() -> None:
    parser = _Osc7501Parser()
    assert parser.feed(b"hello \x1b]7501;work") == []
    assert parser.feed(b"ing\x07world\x1b]7501;idle\x1b\\") == ["working", "idle"]
    assert parser.feed(b"\x1b]7501;done;extra\x07") == ["done"]


@pytest.mark.skipif(not PosixPtyHostConnector.is_available(), reason="PTY POSIX no disponible")
def test_pty_real_osc7501_working_then_idle(tmp_path: Path) -> None:
    connector = PosixPtyHostConnector(max_panes=2)
    # Script interactivo que emite working, espera una línea por stdin y luego emite idle.
    script = (
        "import sys, time; "
        "sys.stdout.write('\\x1b]7501;working\\x07'); sys.stdout.flush(); "
        "sys.stdin.readline(); "
        "sys.stdout.write('\\x1b]7501;idle\\x07'); sys.stdout.flush(); "
        "time.sleep(1.0)"
    )
    try:
        res = connector.spawn_session(
            "pane_1",
            command=[sys.executable, "-c", script],
            cwd=tmp_path,
            use_trampoline=False,
        )
        assert res["success"] is True

        assert _wait_until(
            lambda: connector.get_pane_activity("pane_1").get("osc_status") == "working"
        )
        assert connector.is_pane_idle("pane_1") is False

        send_res = connector.send_input("pane_1", "\n")
        assert send_res["success"] is True

        assert _wait_until(
            lambda: connector.get_pane_activity("pane_1").get("osc_status") == "idle"
        )
        assert connector.is_pane_idle("pane_1") is True
    finally:
        shutdown = getattr(connector, "shutdown_all", None) or getattr(connector, "close_all", None)
        if callable(shutdown):
            shutdown()
        else:
            for s in list(getattr(connector, "_sessions", {}).values()):
                connector._shutdown_session(s)


# ----------------------------------------------------------------------
# 2. KanbanSwarmAdapter: list_swarm_panes y resolve_card_target
# ----------------------------------------------------------------------


def test_kanban_swarm_adapter_projects_assigned_cards_and_activity(tmp_path: Path) -> None:
    db = DatabaseManager(db_path=str(tmp_path / "adapter.db"))
    proj_uuid = db.create_project("Proyecto Adapter", str(tmp_path / "repo"))["project_uuid"]
    db.register_or_update_node("node-pane-2", "Pane 2", "agent-2")
    db.add_or_request_collaborator(proj_uuid, "node-pane-2", status="approved")
    db.create_card(
        project_uuid=proj_uuid,
        card_id="CARD-P2",
        title="Implementar endpoint",
        assignee_node_uuid="node-pane-2",
    )

    registry = PaneRegistry()
    registry.register_pane("pane_1", node_uuid="node-pane-1", session_token="tok-1", role="coordinator")
    registry.register_pane("pane_2", node_uuid="node-pane-2", session_token="tok-2", role="implementer")

    term = _ControllableTerminalConnector()
    term.set_idle("pane_1", False)
    term.set_idle("pane_2", True)

    adapter = KanbanSwarmAdapter(
        kanban_repo=db,
        pane_registry=registry,
        terminal_connector=term,
    )

    listing = adapter.list_swarm_panes(project_uuid=proj_uuid)
    assert listing["success"] is True
    assert listing["count"] == 2

    by_pane = {p["pane_id"]: p for p in listing["panes"]}
    assert by_pane["pane_1"]["activity_state"] == "working"
    assert by_pane["pane_1"]["idle"] is False
    assert by_pane["pane_1"]["assigned_cards"] == []

    assert by_pane["pane_2"]["activity_state"] == "idle"
    assert by_pane["pane_2"]["idle"] is True
    assert [c["card_id"] for c in by_pane["pane_2"]["assigned_cards"]] == ["CARD-P2"]

    target = adapter.resolve_card_target(proj_uuid, "CARD-P2")
    assert target is not None
    assert target["pane_id"] == "pane_2"
    assert target["node_uuid"] == "node-pane-2"
    assert target["matched_by"] == "assignee_node_uuid"


# ----------------------------------------------------------------------
# 3. MCPToolsHandler: list_swarm_panes, send_pane_message y EGM-22
# ----------------------------------------------------------------------


def test_mcp_tools_list_swarm_panes_and_send_pane_message_egm22(tmp_path: Path) -> None:
    db = DatabaseManager(db_path=str(tmp_path / "mcp_panes.db"))
    proj_uuid = db.create_project("Proyecto MCP Panes", str(tmp_path / "repo"))["project_uuid"]
    event_bus = DomainEventBus()
    bus = A2ABus(db)

    term = _ControllableTerminalConnector()
    term.set_idle("pane_1", True)
    term.set_idle("pane_2", True)

    crypto = NodeCryptoManager(key_path=tmp_path / "node_identity.key")
    crypto.set_passphrase("clave-segura-mcp")

    kernel = ApplicationFactory.create_mcp_kernel(
        db_manager=db,
        bus=bus,
        event_bus=event_bus,
    )
    kernel.register_connector(CoreKernel.PORT_AGENT_TERMINAL, term)
    kernel.register_connector(CoreKernel.PORT_CRYPTO, crypto)

    registry = PaneRegistry()
    registry.register_pane("pane_1", node_uuid="node-pane-1", session_token="tok-pane-1")
    registry.register_pane("pane_2", node_uuid="node-pane-2", session_token="tok-pane-2")

    handler = MCPToolsHandler(
        kernel=kernel,
        owns_kernel=False,
        pane_registry=registry,
    )
    try:
        # 1. list_swarm_panes expone paneles y auto_relay_enabled
        panes_out = json.loads(handler.list_swarm_panes(project_uuid=proj_uuid))
        assert panes_out["success"] is True
        assert panes_out["count"] == 2
        assert "auto_relay_enabled" in panes_out
        assert panes_out["auto_relay_enabled"] is False

        # 2. EGM-22: Spoofing de node_uuid con token de pane_1 debe bloquearse
        spoof_out = json.loads(
            handler.send_pane_message(
                project_uuid=proj_uuid,
                target_node_uuid="node-pane-2",
                message="hola",
                node_uuid="node-pane-2",
                session_token="tok-pane-1",
            )
        )
        assert spoof_out["success"] is False
        assert spoof_out["code"] == "NODE_UUID_SPOOFING_FORBIDDEN"

        # 3. Envío exitoso de pane_1 -> pane_2
        ok_out = json.loads(
            handler.send_pane_message(
                project_uuid=proj_uuid,
                target_pane_id="pane_2",
                message="Revisa CARD-99 por favor",
                card_id="CARD-99",
                node_uuid="node-pane-1",
                session_token="tok-pane-1",
            )
        )
        assert ok_out["success"] is True
        assert ok_out["target_node_uuid"] == "node-pane-2"
        assert ok_out.get("queued") or ok_out.get("deduplicated")
        assert ok_out.get("message_uuid")

        # Pane 2 recibe el mensaje vía await_next_assignment
        pull_out = json.loads(
            handler.await_next_assignment(
                project_uuid=proj_uuid,
                node_uuid="node-pane-2",
                session_token="tok-pane-2",
                timeout_ms=0,
            )
        )
        assert pull_out["success"] is True
        assert pull_out["has_assignment"] is True
        assert pull_out["assignment"]["sender_node_uuid"] == "node-pane-1"
        msg_body = (
            pull_out["assignment"].get("message")
            or pull_out["assignment"].get("payload", {}).get("message")
        )
        assert msg_body == "Revisa CARD-99 por favor"
    finally:
        handler.close()
        kernel.close()


# ----------------------------------------------------------------------
# 4. Flujo completo Auto-Relay estilo Orca/Hermes (idle vs working -> flush)
# ----------------------------------------------------------------------


def test_auto_relay_orca_hermes_flow_idle_and_working_flush(tmp_path: Path) -> None:
    db = DatabaseManager(db_path=str(tmp_path / "auto_relay.db"))
    proj_uuid = db.create_project("Proyecto AutoRelay", str(tmp_path / "repo"))["project_uuid"]
    event_bus = DomainEventBus()
    bus = A2ABus(db)

    term = _ControllableTerminalConnector()
    term.set_idle("pane_1", True)
    term.set_idle("pane_2", True)

    crypto = NodeCryptoManager(key_path=tmp_path / "node_identity.key")
    crypto.set_passphrase("clave-segura-orca")

    registry = PaneRegistry()
    registry.register_pane("pane_1", node_uuid="node-pane-1", session_token="tok-1")
    registry.register_pane("pane_2", node_uuid="node-pane-2", session_token="tok-2")

    orch = SwarmRelayOrchestrator(
        event_bus=event_bus,
        bus=bus,
        pane_registry=registry,
        terminal_connector=term,
        crypto=crypto,
        auto_relay_enabled=True,
    )
    kernel = ApplicationFactory.create_mcp_kernel(
        db_manager=db,
        bus=bus,
        event_bus=event_bus,
    )
    kernel.register_connector(CoreKernel.PORT_AGENT_TERMINAL, term)
    kernel.register_connector(CoreKernel.PORT_CRYPTO, crypto)

    handler = MCPToolsHandler(
        kernel=kernel,
        owns_kernel=False,
        pane_registry=registry,
        relay_orchestrator=orch,
    )
    try:
        assert orch.get_auto_relay_status()["key_encrypted"] is True
        assert orch.get_auto_relay_status()["auto_relay_enabled"] is True

        # Caso A: Pane 2 en idle -> asignar tarjeta Kanban despacha aviso inmediato al PTY
        event_bus.publish(
            DomainEvent(
                event_type="kanban.card_assigned",
                project_uuid=proj_uuid,
                payload={
                    "card_id": "CARD-ORCA-1",
                    "target_node_uuid": "node-pane-2",
                    "sender_node_uuid": "node-pane-1",
                },
            )
        )
        assert len(term.calls) == 1
        assert term.calls[0][0] == "pane_2"
        assert "CARD-ORCA-1" in term.calls[0][1]

        # Caso B: Pane 2 en working -> send_pane_message se retiene en _pending_idle_dispatches
        term.set_idle("pane_2", False)
        res_msg = json.loads(
            handler.send_pane_message(
                project_uuid=proj_uuid,
                target_node_uuid="node-pane-2",
                message="Segundo aviso mientras trabajas",
                card_id="CARD-ORCA-2",
                node_uuid="node-pane-1",
                session_token="tok-1",
            )
        )
        assert res_msg["success"] is True
        assert len(term.calls) == 1  # No despachado aún porque está working
        assert orch.get_auto_relay_status()["pending_idle_count"] == 1

        # Cuando Pane 2 pasa a idle y se llama flush_idle_dispatches(), se entrega
        term.set_idle("pane_2", True)
        flushed = orch.flush_idle_dispatches()
        assert len(flushed) == 1
        assert flushed[0]["success"] is True
        assert orch.get_auto_relay_status()["pending_idle_count"] == 0
        assert len(term.calls) == 2
        assert term.calls[1][0] == "pane_2"
        assert "Segundo aviso mientras trabajas" in term.calls[1][1]
    finally:
        handler.close()
        orch.close()
        kernel.close()
