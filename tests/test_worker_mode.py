"""Modo trabajador (F2.4 del dossier 37): dos agentes dentro de paneles conversan por la bandeja sin escribir en
ninguna terminal, quedandose en `inbox_wait` y respondiendo con `inbox_reply`.

Cubre: bandera por panel persistida junto al Auto-Relay, interruptor de la GUI con dialogo nativo al activar, el
protocolo que se le comunica al agente (instrucciones del `initialize`, descripciones de las herramientas y `hint`
de la respuesta) y los tiempos de espera por CLI. La conversacion extremo a extremo con procesos reales esta en
`tests/test_worker_mode_e2e.py`.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List

import pytest

from src.core.kernel import CoreKernel
from src.mcp import worker_protocol
from src.mcp.pane_registry import PaneRegistry
from src.storage.db import DatabaseManager

# ----------------------------------------------------------------------------------------------------------------
# Persistencia y migracion
# ----------------------------------------------------------------------------------------------------------------


def test_worker_mode_column_is_added_idempotently_to_an_old_pane_identities_table(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE pane_identities (project_uuid TEXT NOT NULL, pane_slot TEXT NOT NULL, node_uuid TEXT NOT NULL "
        "UNIQUE, auto_relay_enabled INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, last_spawned_at TEXT, "
        "PRIMARY KEY (project_uuid, pane_slot));"
    )
    conn.execute("INSERT INTO pane_identities VALUES ('p', 'pane_1', 'n-1', 1, '2026-01-01', NULL);")
    conn.commit()
    conn.close()
    for _ in range(2):  # abrir dos veces: la migracion es idempotente y no toca filas
        db = DatabaseManager(db_path=str(path))
        row = db.get_pane_identity("p", "pane_1")
        assert row is not None and row["auto_relay_enabled"] is True and row["worker_mode"] is False
        db.close()


def test_worker_mode_is_persisted_per_slot_and_survives_reopening_the_pane(tmp_path: Path) -> None:
    db = DatabaseManager(db_path=str(tmp_path / "w.db"))
    try:
        _persisted_body(db)
    finally:
        db.close()


def _persisted_body(db: DatabaseManager) -> None:
    from src.adapters.persistence.sqlite_repositories import SqlitePaneIdentityRepository

    registry = PaneRegistry(identity_repo=SqlitePaneIdentityRepository(db))
    registry.register_pane("pane_1", project_uuid="p", cli_profile="claude")
    registry.register_pane("pane_2", project_uuid="p", cli_profile="claude")
    assert registry.get_pane("pane_1").worker_mode is False
    updated = registry.set_worker_mode("pane_2", True)
    assert updated is not None and updated.worker_mode is True
    assert registry.get_pane("pane_1").worker_mode is False  # por panel
    assert registry.get_pane("pane_2").to_dict()["worker_mode"] is True
    assert db.get_pane_identity("p", "pane_2")["worker_mode"] is True
    # Auto-Relay y modo trabajador son banderas independientes
    assert db.get_pane_identity("p", "pane_2")["auto_relay_enabled"] is False
    reopened = registry.register_pane("pane_2", project_uuid="p", cli_profile="claude")
    assert reopened["worker_mode"] is True
    assert registry.get_pane("pane_2").worker_mode is True
    assert registry.set_worker_mode("pane_9", True) is None  # panel que no existe


def test_worker_mode_without_a_repository_lives_in_memory() -> None:
    registry = PaneRegistry()
    registry.register_pane("pane_1", project_uuid="p", cli_profile="claude")
    registry.set_worker_mode("pane_1", True)
    again = registry.register_pane("pane_1", project_uuid="p", cli_profile="claude")
    assert again["worker_mode"] is True


# ----------------------------------------------------------------------------------------------------------------
# Puente de la GUI
# ----------------------------------------------------------------------------------------------------------------


class _FakeTerminal:
    max_panes = 6

    def __init__(self) -> None:
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self.writes: List[Any] = []

    def is_available(self) -> bool:
        return True

    def get_status(self) -> Dict[str, Any]:
        return {"available": True, "degraded": False, "mode": "fake"}

    def list_sessions(self) -> List[Dict[str, Any]]:
        return list(self.sessions.values())

    def spawn_session(self, **kwargs: Any) -> Dict[str, Any]:
        sess = {"pane_id": kwargs["pane_id"], "alive": True, "session_id": f"s{len(self.sessions) + 1}", "pid": 4000}
        self.sessions[kwargs["pane_id"]] = sess
        return {"success": True, **sess}

    def close_session(self, pane_id: Any) -> Dict[str, Any]:
        self.sessions.pop(str(pane_id), None)
        return {"success": True, "pane_id": str(pane_id)}

    def send_input(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        self.writes.append((args, kwargs))
        return {"success": True}

    def close(self) -> None:
        self.sessions.clear()


@pytest.fixture
def gui(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from src.app.api_bridge import ApiBridge
    import src.app.ui_adapters.extensions_lab_ui as lab

    monkeypatch.setattr(lab, "is_unsupported_terminal_platform", lambda: False)
    monkeypatch.delenv("MCP2P_TEST_AUTO_CONFIRM", raising=False)
    bridge = ApiBridge(db_manager=DatabaseManager(db_path=str(tmp_path / "gui.db")))
    term = _FakeTerminal()
    bridge.core_kernel.register_connector(CoreKernel.PORT_AGENT_TERMINAL, term)
    try:
        yield bridge, term
    finally:
        bridge._close_terminal_matrix()
        bridge.core_kernel.close()


def test_gui_worker_mode_toggle_asks_natively_to_enable_and_persists(gui, tmp_path: Path) -> None:
    bridge, term = gui
    proj = bridge.db.create_project("A", str(tmp_path / "wa"))["project_uuid"]
    bridge.pty_spawn_pane("pane_1", "custom_shell", start_hub=False, project_uuid=proj)
    bridge.pty_spawn_pane("pane_2", "custom_shell", start_hub=False, project_uuid=proj)

    asked: List[tuple] = []
    bridge.confirmations.dialog = lambda title, message: asked.append((title, message)) or False
    refused = bridge.pty_set_worker_mode("pane_2", True)
    assert refused["success"] is False and asked, "activar el modo trabajador exige el dialogo nativo"
    assert "pane_2" in asked[0][1] and "ninguna terminal" in asked[0][1]
    assert bridge.db.get_pane_identity(proj, "pane_2")["worker_mode"] is False

    bridge.confirmations.dialog = lambda _t, _m: True
    on = bridge.pty_set_worker_mode("pane_2", True)
    assert on["success"] is True and on["worker_mode"] is True and on["project_uuid"] == proj
    panes = {p["pane_id"]: p["worker_mode"] for p in bridge.pty_get_matrix_status()["panes"]}
    assert panes == {"pane_1": False, "pane_2": True}

    # Apagarlo es bajar autonomia: no pregunta
    bridge.confirmations.dialog = lambda _t, _m: (_ for _ in ()).throw(AssertionError("no debe preguntar"))
    off = bridge.pty_set_worker_mode("pane_2", False)
    assert off["success"] is True and off["worker_mode"] is False
    assert bridge.db.get_pane_identity(proj, "pane_2")["worker_mode"] is False
    assert term.writes == [], "el modo trabajador nunca escribe en una terminal"


def test_gui_worker_mode_rejects_bad_arguments_and_unknown_panes(gui) -> None:
    bridge, _term = gui
    assert bridge.pty_set_worker_mode("pane_1", "si")["code"] == "INVALID_ARGUMENT"
    assert bridge.pty_set_worker_mode("pane_1", None)["code"] == "INVALID_ARGUMENT"
    assert bridge.pty_set_worker_mode("pane_1", True)["code"] == "PANE_NOT_FOUND"


# ----------------------------------------------------------------------------------------------------------------
# Protocolo que se le comunica al agente (sin escribir en su terminal)
# ----------------------------------------------------------------------------------------------------------------


def test_protocol_text_is_short_constant_and_names_the_loop() -> None:
    text = worker_protocol.WORKER_INSTRUCTIONS
    assert 0 < len(text) < 1500
    for needle in ("inbox_wait", "inbox_reply", "inbox_ack", "worker_mode", "payload_untrusted_text"):
        assert needle in text, needle
    # constante: sin datos de ningun panel ni de ningun mensaje
    assert text == worker_protocol.WORKER_INSTRUCTIONS
    assert "{" not in text and "}" not in text


def test_wait_timeout_recommendation_is_below_every_known_cli_default() -> None:
    # Codex corta una llamada a un servidor MCP a los 60 s salvo `tool_timeout_sec`; la recomendacion generica debe
    # quedar por debajo para no depender de lo que cada CLI haga con la configuracion del Hub.
    assert worker_protocol.recommended_wait_ms("codex") < 60_000
    assert worker_protocol.recommended_wait_ms("desconocido") < 60_000
    assert worker_protocol.recommended_wait_ms("claude") <= worker_protocol.MAX_WAIT_MS
    assert worker_protocol.recommended_wait_ms("claude") >= worker_protocol.recommended_wait_ms("codex")


def test_server_initialize_carries_the_worker_protocol_and_tools_describe_it(tmp_path: Path) -> None:
    from src.mcp.server import create_mcp_server

    server = create_mcp_server(db_path=str(tmp_path / "srv.db"))
    assert server.instructions == worker_protocol.WORKER_INSTRUCTIONS

    import anyio

    async def _tools() -> Dict[str, str]:
        return {t.name: (t.description or "") for t in await server.list_tools()}

    described = anyio.run(_tools)
    assert "inbox_wait" in described["inbox_wait"] and "worker_mode" in described["inbox_wait"]
    assert "worker_mode" in described["inbox_next"]
    assert "inbox_wait" in described["inbox_reply"]


def test_inbox_responses_report_worker_mode_and_hint_only_for_worker_panes(tmp_path: Path) -> None:
    from src.core.factory import ApplicationFactory
    from src.mcp.pane_registry import pane_session_context
    from src.mcp.tools import MCPToolsHandler

    kernel = ApplicationFactory.create_mcp_kernel(db_path=str(tmp_path / "hint.db"))
    try:
        proj = kernel.db.create_project("P", str(tmp_path / "r"))["project_uuid"]
        registry = PaneRegistry(identity_repo=kernel.pane_identity_repo)
        pane = registry.register_pane("pane_1", project_uuid=proj, cli_profile="codex")
        handler = MCPToolsHandler(kernel=kernel, owns_kernel=False, pane_registry=registry)

        def call(tool: str, **kwargs: Any) -> Dict[str, Any]:
            with pane_session_context(registry.get_pane("pane_1")):
                return json.loads(getattr(handler, tool)(**kwargs))

        quiet = call("inbox_wait", timeout_ms=0)
        assert quiet["worker_mode"] is False and "hint" not in quiet
        assert call("inbox_next")["worker_mode"] is False

        registry.set_worker_mode("pane_1", True)
        busy = call("inbox_wait", timeout_ms=0)
        assert busy["worker_mode"] is True and busy["timed_out"] is True
        assert "inbox_wait" in busy["hint"] and f"timeout_ms={busy['timeout_ms_recommended']}" in busy["hint"]
        assert busy["timeout_ms_recommended"] == worker_protocol.recommended_wait_ms("codex")
        nxt = call("inbox_next")
        assert nxt["worker_mode"] is True and "inbox_wait" in nxt["hint"]
        assert pane["pane_id"] == "pane_1"
    finally:
        kernel.close()


# ----------------------------------------------------------------------------------------------------------------
# Tiempos de espera de cada CLI (se escriben en la configuracion MCP que el Hub genera, no en la terminal)
# ----------------------------------------------------------------------------------------------------------------


def test_claude_config_raises_the_per_server_tool_timeout() -> None:
    from src.adapters.terminal.mcp_injection import _render_config

    claude = _render_config("claude_mcp_json", "http://127.0.0.1:1/mcp", "tok")["mcpServers"]["mcp2p"]
    assert claude["timeout"] == worker_protocol.TOOL_TIMEOUT_MS
    assert worker_protocol.TOOL_TIMEOUT_MS > worker_protocol.MAX_WAIT_MS


def test_codex_catalog_raises_tool_timeout_sec() -> None:
    catalog = json.loads(
        (Path(__file__).resolve().parents[1] / "src" / "adapters" / "terminal" / "cli_catalog.json").read_text(
            encoding="utf-8"
        )
    )
    codex = next(c for c in catalog["clis"] if c["id"] == "codex")
    joined = " ".join(codex["mcp"]["argv"])
    assert f"mcp_servers.mcp2p.tool_timeout_sec={worker_protocol.TOOL_TIMEOUT_MS // 1000}" in joined
