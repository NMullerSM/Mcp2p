"""Entrega directa de extremo a extremo con el puente real (WP-6b): relevo firmado + motor de entrega + PTY falso.

- Con el catálogo que se publica (todo `unverified`) la entrega directa está inerte: nada se escribe en ninguna terminal.
- Con las capacidades medidas (simuladas), orquestador → implementador y la respuesta vuelven como texto en la terminal,
  con cabecera del Hub, sin parar y sin que un agente pueda saltarse los roles.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List

import pytest

from src.core.kernel import CoreKernel
from src.mcp.tools import MCPToolsHandler
from tests.helpers.direct_delivery_lab import wait_until


class _Terminal:
    def __init__(self) -> None:
        self.pid = os.getpid()
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self.writes: List[Dict[str, Any]] = []

    def is_available(self) -> bool:
        return True

    def get_status(self) -> Dict[str, Any]:
        return {"available": True, "degraded": False, "mode": "fake"}

    def list_sessions(self) -> list:
        return list(self.sessions.values())

    def spawn_session(self, **kwargs: Any) -> Dict[str, Any]:
        sess = {"pane_id": kwargs["pane_id"], "alive": True, "session_id": f"s-{kwargs['pane_id']}", "pid": self.pid}
        self.sessions[kwargs["pane_id"]] = sess
        return {"success": True, **sess}

    def send_input(self, pane_id: Any, data: Any, *, expected_session_id: Any = None) -> Dict[str, Any]:
        self.writes.append({"pane_id": str(pane_id), "data": data, "expected": expected_session_id})
        return {"success": True, "bytes_written": len(str(data).encode("utf-8"))}

    def get_pane_activity(self, pane_id: Any, *, min_silence_s: float = 1.5) -> Dict[str, Any]:
        return {"pane_id": str(pane_id), "silence_s": 30.0}

    def close_session(self, pane_id: Any) -> Dict[str, Any]:
        self.sessions.pop(str(pane_id), None)
        return {"success": True}

    def close(self) -> None:
        self.sessions.clear()


def _build(tmp_path: Path, monkeypatch, *, verified: bool):
    from src.app.api_bridge import ApiBridge
    import src.app.ui_adapters.delivery_ui as delivery_mod
    import src.app.ui_adapters.extensions_lab_ui as lab_mod
    from src.storage.db import DatabaseManager

    monkeypatch.setattr(lab_mod, "is_unsupported_terminal_platform", lambda: False)
    monkeypatch.delenv("MCP2P_TEST_AUTO_CONFIRM", raising=False)
    if verified:
        monkeypatch.setattr(
            delivery_mod,
            "_build_capabilities",
            lambda getter: (lambda rec: {
                "delivery_verified": True, "activity_verified": True, "paste_mode": "bracketed",
                "submit_delay_ms": 100, "max_inject_bytes": 2048, "confirm_event": "turn_started"}),
        )
        monkeypatch.setattr(delivery_mod, "_build_privilege_of", lambda getter: (lambda rec: "standard"))
    bridge = ApiBridge(db_manager=DatabaseManager(db_path=str(tmp_path / "gui.db")))
    term = _Terminal()
    bridge.core_kernel.register_connector(CoreKernel.PORT_AGENT_TERMINAL, term)
    proj = bridge.db.create_project("GUI", str(tmp_path / "gui_repo"))["project_uuid"]
    for pane in ("pane_1", "pane_2"):
        assert bridge.pty_spawn_pane(pane, "custom_shell", start_hub=False, project_uuid=proj)["success"] is True
    reg = bridge.extensions_ui._get_pane_registry()
    handler = MCPToolsHandler(kernel=bridge.core_kernel, owns_kernel=False, pane_registry=reg)
    return bridge, term, reg, handler, proj


@pytest.fixture
def inert(tmp_path, monkeypatch):
    bridge, term, reg, handler, proj = _build(tmp_path, monkeypatch, verified=False)
    try:
        yield bridge, term, reg, handler, proj
    finally:
        handler.close()
        bridge._close_terminal_matrix()
        bridge.core_kernel.close()


@pytest.fixture
def live(tmp_path, monkeypatch):
    bridge, term, reg, handler, proj = _build(tmp_path, monkeypatch, verified=True)
    try:
        yield bridge, term, reg, handler, proj
    finally:
        handler.close()
        bridge._close_terminal_matrix()
        bridge.core_kernel.close()


def _idle(bridge, reg, pane: str) -> None:
    rec = reg.get_pane(pane)
    activity = bridge.core_kernel.agent_activity
    activity.ingest(rec.node_uuid, None, "turn_ended", 0, "hook")
    import time

    activity.ingest(rec.node_uuid, None, "turn_ended", int(time.time() * 1000), "hook")


def test_shipped_catalog_keeps_direct_delivery_inert(inert):
    bridge, term, reg, handler, proj = inert
    t1 = reg.get_pane("pane_1").session_token
    out = json.loads(handler.send_message(session_token=t1, target_pane_id="pane_2", text="hola"))
    assert out["success"] is True
    _idle(bridge, reg, "pane_2")
    service = bridge.core_kernel.direct_delivery_service
    service.pump(reg.get_pane("pane_2").node_uuid)
    assert term.writes == [], "con el catálogo sin medir, la entrega directa no escribe nada"
    status = bridge.relay_get_delivery_status(proj)
    pane2 = [p for p in status["panes"] if p["pane_id"] == "pane_2"][0]
    assert pane2["effective_mode"] == "inbox" and pane2["queued"] == 1
    assert pane2["mode_reason"] in ("CLI_NOT_VERIFIED", "TARGET_ELEVATED")


def test_orchestrator_assigns_and_reply_comes_back_injected(live):
    bridge, term, reg, handler, proj = live
    assert bridge.roles_ui.pty_set_pane_role(pane_id="pane_1", role="orchestrator")["success"] is True
    assert bridge.roles_ui.pty_set_pane_role(pane_id="pane_2", role="implementer")["success"] is True
    n1, n2 = reg.get_pane("pane_1").node_uuid, reg.get_pane("pane_2").node_uuid
    t1, t2 = reg.get_pane("pane_1").session_token, reg.get_pane("pane_2").session_token
    _idle(bridge, reg, "pane_1")
    _idle(bridge, reg, "pane_2")
    service = bridge.core_kernel.direct_delivery_service

    sent = json.loads(handler.send_message(session_token=t1, target_pane_id="pane_2", text="Implementa X",
                                           kind="task.request"))
    assert sent["success"] is True, sent
    assert wait_until(lambda: any(w["pane_id"] == "pane_2" for w in term.writes), timeout_s=10), term.writes
    paste = [w["data"] for w in term.writes if w["pane_id"] == "pane_2"][0]
    assert "de=orquestador:Terminal 1" in paste and "tipo=task.request" in paste and "Implementa X" in paste
    assert paste.startswith("\x1b[200~") and paste.endswith("\x1b[201~")
    assert wait_until(lambda: [w["data"] for w in term.writes if w["pane_id"] == "pane_2"][-1:] == ["\r"])
    assert all(w["expected"] == "s-pane_2" for w in term.writes), "siempre con la encarnación vigente"

    msg_id = paste.split("msg=")[1].split(" ")[0]
    reg_hook = bridge.core_kernel.agent_activity
    reg_hook.ingest(n2, None, "turn_started", 1, "hook")
    reply = json.loads(handler.inbox_reply(session_token=t2, msg_id=msg_id, text="Hecho"))
    assert reply["success"] is True, reply
    import time

    reg_hook.ingest(n2, None, "turn_ended", int(time.time() * 1000) + 5, "hook")
    # el orquestador (T1) está libre: la respuesta le llega como texto con hop + 1
    assert wait_until(lambda: any(w["pane_id"] == "pane_1" for w in term.writes), timeout_s=10), term.writes
    back = [w["data"] for w in term.writes if w["pane_id"] == "pane_1"][0]
    assert "de=implementador:Terminal 2" in back and "Hecho" in back and "tipo=task.reply" in back
    service.stop()


def test_peer_cannot_assign_tasks_and_nothing_is_written(live):
    bridge, term, reg, handler, proj = live
    t1 = reg.get_pane("pane_1").session_token
    _idle(bridge, reg, "pane_2")
    out = json.loads(handler.send_message(session_token=t1, target_pane_id="pane_2", text="hazlo", kind="task.request"))
    assert out["success"] is False and out["code"] == "ROLE_KIND_FORBIDDEN"
    assert term.writes == []


def test_enabling_direct_on_a_pane_asks_for_confirmation_first(inert):
    bridge, term, reg, handler, proj = inert
    res = bridge.pty_set_delivery_mode("pane_2", "direct")
    assert res["success"] is False and res["code"] == "CONFIRMATION_REQUIRED"
    assert reg.get_pane("pane_2").delivery_mode == "auto", "sin confirmar no cambia nada"
    assert bridge.pty_set_delivery_mode("pane_2", "inbox")["success"] is True
    assert reg.get_pane("pane_2").delivery_mode == "inbox"
    assert bridge.pty_set_delivery_mode("pane_2", "x")["code"] == "INVALID_ARGUMENT"


def test_human_types_to_two_panes_through_the_bridge_without_header(live):
    bridge, term, reg, handler, proj = live
    out = bridge.relay_type_to_panes(proj, ["pane_1", "pane_2"], "Pausa y haz commit")
    assert out["success"] is True and len(out["results"]) == 2
    assert {w["pane_id"] for w in term.writes} == {"pane_1", "pane_2"}
    assert all("[MCP2P" not in w["data"] for w in term.writes if w["data"] != "\r")
    other = bridge.relay_type_to_panes("00000000-0000-4000-8000-0000000000ff", ["pane_1"], "x")
    assert other["success"] is False and other["code"] == "PROJECT_SCOPE_FORBIDDEN"


def test_relay_list_threads_reports_state_labels(live):
    bridge, term, reg, handler, proj = live
    t1 = reg.get_pane("pane_1").session_token
    json.loads(handler.send_message(session_token=t1, target_pane_id="pane_2", text="una nota"))
    threads = bridge.relay_list_threads(proj)
    assert threads["success"] is True
    assert threads["threads"][0]["status_label"] in ("En cola", "Entregando en su terminal", "Entregado en su terminal")
    assert threads["threads"][0]["text"] == "una nota"
