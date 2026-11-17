"""Ola 4 (F2.1, backend de la GUI): puente del canal del operador en `ApiBridge` (sesiones, revocar, estado del
emparejamiento) y apertura del canal por `OperatorUIAdapter.start_channel` (lo llama `main.py` al abrir la ventana).
Las funciones de arrancar o parar el canal NO se exponen al JS."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from helpers.relay_lab import install_pty_write_spy
from test_operator_channel_f2 import POSIX, Harness


@pytest.fixture(autouse=True)
def _no_system_pty_writes(monkeypatch):
    calls = install_pty_write_spy(monkeypatch)
    yield calls
    assert calls == [], f"el canal del operador escribio en un PTY: {calls}"


def test_gui_bridge_operator_sessions_status_and_revoke(tmp_path: Path) -> None:
    from src.app.api_bridge import ApiBridge
    from src.storage.db import DatabaseManager

    bridge = ApiBridge(db_manager=DatabaseManager(db_path=str(tmp_path / "gui.db")))
    short = Path(tempfile.mkdtemp(prefix="m2p"))
    try:
        kwargs = ({"transport": "unix", "socket_path": short / "op.sock"} if POSIX
                  else {"transport": "tcp", "endpoint_file": short / "op.endpoint"})
        info = bridge.operator_ui.start_channel(**kwargs)
        assert info["listening"] is True
        status = bridge.operator_pairing_status()
        assert status["success"] is True and status["channel"]["listening"] is True and status["active_sessions"] == 0
        assert bridge.operator_list_sessions() == {"success": True, "count": 0, "items": []}
        assert bridge.operator_revoke_session("no-existe")["code"] == "SESSION_NOT_FOUND"
        assert "operator_list_sessions" in {f.__name__ for f in bridge.public_js_functions()}
        assert not {"start_channel", "stop_channel"} & {f.__name__ for f in bridge.public_js_functions()}
        proxy = (["--transport", "unix", "--socket", str(short / "op.sock")] if POSIX
                 else ["--transport", "tcp", "--endpoint-file", str(short / "op.endpoint")])
        h = Harness(proxy)
        try:
            assert h.call("operator_list_targets")["result"]["success"] is True
            (item,) = bridge.operator_list_sessions()["items"]
            assert item["harness"]["pid"] == h.proc.pid
            assert bridge.operator_revoke_session(item["session_id"])["state"] == "revoked"
            assert bridge.operator_list_sessions()["count"] == 0
        finally:
            h.close()
    finally:
        bridge.operator_ui.stop_channel()
        bridge._close_terminal_matrix()
        bridge.core_kernel.close()
        shutil.rmtree(short, ignore_errors=True)
