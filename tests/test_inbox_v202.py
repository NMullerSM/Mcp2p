"""Bandeja de los agentes y retiro del modo trabajador (WP-11).

El modo trabajador (el agente en bucle de `inbox_wait`) se retiró: los mensajes llegan por la entrega directa o por la
bandeja, que el agente lee con `inbox_next`/`inbox_wait`. Cubre las instrucciones globales del servidor MCP, que las
respuestas de la bandeja ya no traen campos del modo, que no queda superficie para activarlo (puente, confirmaciones,
registro de paneles) y que una base de datos de la versión anterior, con su columna `worker_mode`, se abre sin error.
Los tiempos de espera por CLI (que `inbox_wait` sigue necesitando) también se comprueban aquí.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict

from src.core.domain.confirmations import CONFIRMATION_TABLE
from src.core.services.message_relay_service import MAX_WAIT_MS, TOOL_TIMEOUT_MS
from src.mcp import agent_instructions
from src.mcp.pane_registry import PaneRegistry
from src.storage.db import DatabaseManager

RETIRED_WORDS = ("worker_mode", "modo trabajador", "timeout_ms_recommended")


# ----------------------------------------------------------------------------------------------------------------
# Instrucciones y herramientas
# ----------------------------------------------------------------------------------------------------------------


def test_instructions_are_short_constant_and_describe_the_inbox_without_the_worker_loop() -> None:
    text = agent_instructions.AGENT_INSTRUCTIONS
    assert 0 < len(text) < 1500
    for needle in ("inbox_next", "inbox_wait", "inbox_reply", "send_message", "payload_untrusted_text", "whoami"):
        assert needle in text, needle
    assert not any(word in text for word in RETIRED_WORDS)
    assert "{" not in text and "}" not in text, "constante: sin datos de ningún panel ni mensaje"


def test_server_initialize_carries_the_instructions_and_tools_do_not_mention_the_retired_mode(tmp_path: Path) -> None:
    import anyio

    from src.mcp.server import create_mcp_server

    server = create_mcp_server(db_path=str(tmp_path / "srv.db"))
    assert server.instructions == agent_instructions.AGENT_INSTRUCTIONS

    async def _tools() -> Dict[str, str]:
        return {t.name: (t.description or "") for t in await server.list_tools()}

    described = anyio.run(_tools)
    assert "timed_out" in described["inbox_wait"]
    for name in ("inbox_wait", "inbox_next", "inbox_reply", "inbox_ack", "send_message"):
        assert not any(word in described[name] for word in RETIRED_WORDS), name
    assert "set_worker_mode" not in described and "pty_set_worker_mode" not in described


def test_inbox_responses_carry_no_worker_fields(tmp_path: Path) -> None:
    from src.core.factory import ApplicationFactory
    from src.mcp.pane_registry import pane_session_context
    from src.mcp.tools import MCPToolsHandler

    kernel = ApplicationFactory.create_mcp_kernel(db_path=str(tmp_path / "inbox.db"))
    try:
        proj = kernel.db.create_project("P", str(tmp_path / "r"))["project_uuid"]
        registry = PaneRegistry(identity_repo=kernel.pane_identity_repo)
        registry.register_pane("pane_1", project_uuid=proj, cli_profile="codex")
        handler = MCPToolsHandler(kernel=kernel, owns_kernel=False, pane_registry=registry)

        def call(tool: str, **kwargs: Any) -> Dict[str, Any]:
            with pane_session_context(registry.get_pane("pane_1")):
                return json.loads(getattr(handler, tool)(**kwargs))

        waited = call("inbox_wait", timeout_ms=0)
        assert waited["timed_out"] is True and waited["messages"] == []
        nxt = call("inbox_next")
        for out in (waited, nxt):
            assert not {"worker_mode", "hint", "timeout_ms_recommended"} & set(out)
        assert "worker_mode" not in call("whoami")
    finally:
        kernel.close()


# ----------------------------------------------------------------------------------------------------------------
# Sin superficie para activar el modo
# ----------------------------------------------------------------------------------------------------------------


def test_no_surface_left_to_enable_the_worker_mode() -> None:
    from src.app.api_bridge import ApiBridge

    assert not hasattr(ApiBridge, "pty_set_worker_mode")
    assert "worker_mode.enable" not in CONFIRMATION_TABLE
    registry = PaneRegistry()
    registry.register_pane("pane_1", project_uuid="p", cli_profile="claude")
    assert not hasattr(registry, "set_worker_mode")
    rec = registry.get_pane("pane_1")
    assert not hasattr(rec, "worker_mode") and "worker_mode" not in rec.to_dict()


# ----------------------------------------------------------------------------------------------------------------
# Bases de datos de la versión anterior
# ----------------------------------------------------------------------------------------------------------------


def _old_db_with_worker_flag(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE pane_identities (project_uuid TEXT NOT NULL, pane_slot TEXT NOT NULL, node_uuid TEXT NOT NULL "
        "UNIQUE, auto_relay_enabled INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, last_spawned_at TEXT, "
        "worker_mode INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (project_uuid, pane_slot));"
    )
    conn.execute("INSERT INTO pane_identities VALUES ('p', 'pane_1', 'n-1', 1, '2026-01-01', NULL, 1);")
    conn.commit()
    conn.close()


def test_old_database_with_the_worker_column_opens_and_the_flag_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    _old_db_with_worker_flag(path)
    for _ in range(2):  # abrir dos veces: la migración es idempotente y no toca filas
        db = DatabaseManager(db_path=str(path))
        try:
            row = db.get_pane_identity("p", "pane_1")
            assert row is not None and row["auto_relay_enabled"] is True and "worker_mode" not in row
            from src.adapters.persistence.sqlite_repositories import SqlitePaneIdentityRepository

            registry = PaneRegistry(identity_repo=SqlitePaneIdentityRepository(db))
            pane = registry.register_pane("pane_1", project_uuid="p", cli_profile="claude")
            assert pane["auto_relay_enabled"] is True and "worker_mode" not in pane
        finally:
            db.close()
    conn = sqlite3.connect(path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(pane_identities);")}
    conn.close()
    assert "worker_mode" in cols, "la columna antigua no se borra: se ignora"


def test_new_database_does_not_create_the_worker_column(tmp_path: Path) -> None:
    db = DatabaseManager(db_path=str(tmp_path / "new.db"))
    try:
        with db.get_connection() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(pane_identities);")}
        assert "worker_mode" not in cols and "auto_relay_enabled" in cols
    finally:
        db.close()


# ----------------------------------------------------------------------------------------------------------------
# Tiempos de espera de cada CLI (se escriben en la configuración MCP que el Hub genera, no en la terminal)
# ----------------------------------------------------------------------------------------------------------------


def test_claude_config_raises_the_per_server_tool_timeout() -> None:
    from src.adapters.terminal.mcp_injection import _render_config

    claude = _render_config("claude_mcp_json", "http://127.0.0.1:1/mcp", "tok")["mcpServers"]["mcp2p"]
    assert claude["timeout"] == TOOL_TIMEOUT_MS
    assert TOOL_TIMEOUT_MS > MAX_WAIT_MS


def test_codex_catalog_raises_tool_timeout_sec() -> None:
    catalog = json.loads(
        (Path(__file__).resolve().parents[1] / "src" / "adapters" / "terminal" / "cli_catalog.json").read_text(
            encoding="utf-8"
        )
    )
    codex = next(c for c in catalog["clis"] if c["id"] == "codex")
    joined = " ".join(codex["mcp"]["argv"])
    assert f"mcp_servers.mcp2p.tool_timeout_sec={TOOL_TIMEOUT_MS // 1000}" in joined
