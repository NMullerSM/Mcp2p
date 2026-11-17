"""El humano escribe a un agente desde la GUI POR LA BANDEJA (nunca por el teclado del PTY): `relay_send_as_human`.

Sobre firmado con `from = human:local`; confianza `human_confirmed` solo si la clave humana esta desbloqueada y, si no,
`pane_authenticated` (no hay mas niveles activos). Dialogo nativo si el destino es `elevated` (se lanzo con permisos
saltados). Un agente no puede responder a este remitente ni existe herramienta MCP equivalente.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from src.core.kernel import CoreKernel
from src.mcp.tools import MCPToolsHandler


class _Terminal:
    def __init__(self) -> None:
        import os

        self.pid = os.getpid()
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self.writes: List[Any] = []

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

    def send_input(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        self.writes.append((args, kwargs))
        return {"success": True}

    def close_session(self, pane_id: Any) -> Dict[str, Any]:
        self.sessions.pop(str(pane_id), None)
        return {"success": True}

    def close(self) -> None:
        self.sessions.clear()


@pytest.fixture
def gui(tmp_path: Path, monkeypatch):
    from src.app.api_bridge import ApiBridge
    import src.app.ui_adapters.extensions_lab_ui as lab_mod
    from src.storage.db import DatabaseManager

    monkeypatch.setattr(lab_mod, "is_unsupported_terminal_platform", lambda: False)
    monkeypatch.delenv("MCP2P_TEST_AUTO_CONFIRM", raising=False)
    bridge = ApiBridge(db_manager=DatabaseManager(db_path=str(tmp_path / "gui.db")))
    term = _Terminal()
    bridge.core_kernel.register_connector(CoreKernel.PORT_AGENT_TERMINAL, term)
    proj = bridge.db.create_project("GUI", str(tmp_path / "gui_repo"))["project_uuid"]
    other = bridge.db.create_project("Otro", str(tmp_path / "other_repo"))["project_uuid"]
    for pane in ("pane_1", "pane_2"):
        assert bridge.pty_spawn_pane(pane, "custom_shell", start_hub=False, project_uuid=proj)["success"] is True
    assert bridge.pty_spawn_pane("pane_3", "custom_shell", start_hub=False, project_uuid=other)["success"] is True
    reg = bridge.extensions_ui._get_pane_registry()
    handler = MCPToolsHandler(kernel=bridge.core_kernel, owns_kernel=False, pane_registry=reg)
    try:
        yield bridge, term, reg, handler, proj, other
    finally:
        handler.close()
        bridge._close_terminal_matrix()
        bridge.core_kernel.close()


def _inbox(handler: MCPToolsHandler, reg: Any, pane: str) -> List[Dict[str, Any]]:
    return json.loads(handler.inbox_next(session_token=reg.get_pane(pane).session_token))["messages"]


def test_human_message_reaches_the_inbox_with_verified_human_provenance(gui) -> None:
    bridge, term, reg, handler, proj, _other = gui
    asked: List[Any] = []
    bridge.confirmations.dialog = lambda t, m: asked.append((t, m)) or True
    out = bridge.relay_send_as_human(proj, "pane_2", "Revisa la rama develop, por favor.")
    assert out["success"] is True and out["state"] == "queued" and out["trust"] == "human_confirmed"
    assert out["target_pane_id"] == "pane_2" and out["chain"]["hop"] == 0
    assert asked == [], "un destino normal no pregunta: el humano ya esta escribiendo el texto"

    (msg,) = _inbox(handler, reg, "pane_2")
    env = msg["mcp2p_envelope"]
    assert env["verified"] is True and env["from"]["principal"] == "human:local" and env["from"]["kind"] == "human"
    assert env["trust"] == "human_confirmed" and env["kind"] == "note" and env["chain"]["hop"] == 0
    assert msg["payload_untrusted_text"] == "Revisa la rama develop, por favor."
    assert term.writes == [], "el sistema no escribe nada en ninguna terminal"
    assert _inbox(handler, reg, "pane_1") == []


def test_trust_falls_back_to_pane_authenticated_when_the_human_key_is_locked(gui, monkeypatch) -> None:
    bridge, _term, reg, handler, proj, _other = gui
    service = bridge.relay_ui._service()
    monkeypatch.setattr(type(service.identity), "device_certificate", lambda self: None)
    out = bridge.relay_send_as_human(proj, "pane_2", "hola", "task.request")
    assert out["success"] is True and out["trust"] == "pane_authenticated"
    (msg,) = _inbox(handler, reg, "pane_2")
    assert msg["mcp2p_envelope"]["trust"] == "pane_authenticated" and msg["mcp2p_envelope"]["kind"] == "task.request"
    assert msg["mcp2p_envelope"]["verified"] is True and msg["mcp2p_envelope"]["device_cert"] is False


def test_an_agent_cannot_reply_to_the_human_and_the_reply_is_not_accepted_as_a_chain(gui) -> None:
    bridge, _term, reg, handler, proj, _other = gui
    bridge.relay_send_as_human(proj, "pane_2", "hola")
    (msg,) = _inbox(handler, reg, "pane_2")
    reply = json.loads(
        handler.inbox_reply(msg_id=msg["mcp2p_envelope"]["msg_id"], text="listo", session_token=reg.get_pane("pane_2").session_token)
    )
    assert reply["success"] is False and reply["code"] == "NOT_REPLYABLE"


def test_validation_errors_say_what_failed_and_what_to_do(gui) -> None:
    bridge, term, reg, handler, proj, other = gui
    assert bridge.relay_send_as_human(proj, "pane_2", "   ")["code"] == "EMPTY_MESSAGE"
    assert bridge.relay_send_as_human(proj, "pane_2", "x" * 9000)["code"] == "MESSAGE_TOO_LARGE"
    assert bridge.relay_send_as_human(proj, "pane_2", "hola", "keyboard")["code"] == "INVALID_KIND"
    assert bridge.relay_send_as_human(proj, "pane_2", 5)["code"] == "EMPTY_MESSAGE"
    missing = bridge.relay_send_as_human(proj, "pane_9", "hola")
    assert missing["code"] == "TARGET_NOT_FOUND" and "terminal" in missing["message"].lower()
    cross = bridge.relay_send_as_human(proj, "pane_3", "hola")  # pane_3 es de otro proyecto
    assert cross["code"] == "PROJECT_SCOPE_FORBIDDEN" and cross["success"] is False
    assert bridge.relay_send_as_human(other, "pane_3", "hola")["success"] is True
    assert bridge.relay_send_as_human(proj, None, "hola")["code"] == "INVALID_ARGUMENT"
    assert _inbox(handler, reg, "pane_2") == []
    assert term.writes == []


def test_elevated_target_requires_the_native_dialog_with_the_exact_text(gui, monkeypatch) -> None:
    bridge, term, reg, handler, proj, _other = gui
    from src.app.ui_adapters.relay_ui import RelayUIAdapter

    monkeypatch.setattr(
        RelayUIAdapter, "_launch_flags", lambda self, rec: ("--dangerously-skip-permissions",) if rec.pane_id == "pane_2" else ()
    )
    answers: List[Any] = []
    decision = {"ok": False}
    bridge.confirmations.dialog = lambda t, m: answers.append((t, m)) or decision["ok"]

    declined = bridge.relay_send_as_human(proj, "pane_2", "ejecuta el despliegue")
    assert declined["success"] is False and declined["code"] == "CONFIRMATION_DECLINED"
    assert "ejecuta el despliegue" in answers[-1][1] and "pane_2" in answers[-1][1]
    assert "permisos" in answers[-1][1].lower()
    assert _inbox(handler, reg, "pane_2") == []

    decision["ok"] = True
    assert bridge.relay_send_as_human(proj, "pane_2", "ejecuta el despliegue")["success"] is True
    assert len(_inbox(handler, reg, "pane_2")) == 1

    n = len(answers)
    assert bridge.relay_send_as_human(proj, "pane_1", "algo normal")["success"] is True  # no elevated: sin dialogo
    assert len(answers) == n
    assert term.writes == []


def test_global_pause_holds_the_human_message_and_everything_is_audited(gui) -> None:
    bridge, _term, reg, handler, proj, _other = gui
    bridge.confirmations.dialog = lambda _t, _m: True
    bridge.relay_global_pause(True)
    held = bridge.relay_send_as_human(proj, "pane_2", "en pausa")
    assert held["success"] is True and held["held"] == "RELAY_PAUSED" and held["delivered"] is False
    assert _inbox(handler, reg, "pane_2") == []
    bridge.relay_global_pause(False)
    assert len(_inbox(handler, reg, "pane_2")) == 1
    status = bridge.relay_audit_status()
    assert status["chain"]["ok"] is True and status["events"]["submitted"] == 1


def test_there_is_no_mcp_tool_that_sends_as_the_human() -> None:
    import inspect

    names = {n for n, _ in inspect.getmembers(MCPToolsHandler, predicate=inspect.isfunction) if not n.startswith("_")}
    assert not {n for n in names if "human" in n or n == "relay_send_as_human"}
