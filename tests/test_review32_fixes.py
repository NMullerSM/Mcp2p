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
