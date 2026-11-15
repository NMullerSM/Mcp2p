"""Regresiones de la revision independiente 32 (EGM-65 en adelante): cada test reproduce el abuso medido."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from src.adapters.terminal.pty_host import PosixPtyHostConnector
from src.core.factory import ApplicationFactory
from src.mcp.server import create_mcp_server

posix_only = pytest.mark.skipif(not PosixPtyHostConnector.is_available(), reason="Solo POSIX")


# ---------------------------------------------------------------------------
# EGM-65: cerrar el handler del Hub embebido no debe desmontar el kernel compartido de la GUI
# ---------------------------------------------------------------------------


@posix_only
def test_egm65_shared_kernel_survives_embedded_handler_close(tmp_path: Path) -> None:
    kernel = ApplicationFactory.create_desktop_kernel(db_path=str(tmp_path / "gui.db"))
    terminal = kernel.terminal_connector
    try:
        spawn = terminal.spawn_session(1, cli_profile="custom_shell", command=["sleep", "30"], use_trampoline=False)
        assert spawn["success"] is True
        server = create_mcp_server(kernel=kernel, transport="streamable-http", pane_registry=None)
        # Lo que hacen EmbeddedMcpHub.stop() y EmbeddedMcpHub._cleanup_failed_start()
        server.mcp2p_handler.close()

        assert getattr(kernel, "_closed", False) is False
        assert kernel.terminal_connector is terminal
        alive = [s["pane_id"] for s in terminal.list_sessions() if s["alive"]]
        assert alive == ["pane_1"], "el cierre del Hub mato los paneles de la GUI"
        assert isinstance(kernel.db.list_projects(), list)
    finally:
        kernel.close()
    assert [s for s in terminal.list_sessions() if s["alive"]] == []


# ---------------------------------------------------------------------------
# EGM-66: el Hub embebido no acepta peticiones sin token (antes: identidad autodeclarada)
# ---------------------------------------------------------------------------


def test_egm66_embedded_hub_rejects_requests_without_pane_token(tmp_path: Path) -> None:
    import asyncio

    import httpx2

    from src.mcp.pane_registry import EmbeddedMcpHub, PaneRegistry

    kernel = ApplicationFactory.create_desktop_kernel(db_path=str(tmp_path / "hub.db"))
    reg = PaneRegistry(collab_repo=kernel.collab_repo)
    p1 = reg.register_pane(1)
    p2 = reg.register_pane(2)
    hub = EmbeddedMcpHub(kernel=kernel, pane_registry=reg, host="127.0.0.1", port=0)
    try:
        url = hub.start(timeout_s=10.0)
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": {"name": "probe", "version": "0"}}}
        accept = {"Accept": "application/json, text/event-stream"}

        async def _probe() -> None:
            async with httpx2.AsyncClient() as http:
                # Sin token: antes pasaba y podia declarar node_uuid de pane_2
                r = await http.post(url, headers=accept, json=init)
                assert r.status_code == 401
                assert r.json()["code"] == "MISSING_PANE_SESSION_TOKEN"
                # Token valido: sigue funcionando
                r_ok = await http.post(url, headers={**accept, "Authorization": f"Bearer {p1['session_token']}"},
                                       json=init)
                assert r_ok.status_code == 200

        asyncio.run(_probe())
        assert p1["node_uuid"] != p2["node_uuid"]
    finally:
        hub.stop()
        kernel.close()
