"""
T4 (plan 2026-11-15, seccion 7): cuando en Windows NO hay conector ConPTY utilizable (pywinpty ausente o Windows
anterior a 1809) el puerto de terminales degrada a un mensaje claro (`TERMINAL_PLATFORM_UNSUPPORTED`) sin
excepciones y sin confundirse con el degradado generico. Se simula `sys.platform` y la ausencia de `pywinpty`
(`sys.modules["winpty"] = None`) por monkeypatch, de modo que el caso vale tambien en un Windows real con pywinpty.

Si el conector SI esta disponible (doble de `winpty` inyectado) la plataforma NO se declara no soportada.
"""

from __future__ import annotations

import sys
from typing import Any, Dict

import pytest

from conftest import make_test_db
from src.adapters.outbound.extension_gate import NullAgentTerminalConnector
from src.adapters.terminal import PosixPtyHostConnector
from src.app.api_bridge import ApiBridge
from src.core.factory import ApplicationFactory

UNSUPPORTED = "TERMINAL_PLATFORM_UNSUPPORTED"


@pytest.fixture(autouse=True)
def _pywinpty_missing(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Estos casos son el de "pywinpty no disponible"; los de "conector disponible" lo desactivan con un marcador."""
    if "connector_available" in request.keywords:
        return
    monkeypatch.setitem(sys.modules, "winpty", None)  # `import winpty` -> ImportError, tambien en Windows real


def _assert_unsupported(res: Dict[str, Any]) -> None:
    assert res["success"] is False
    assert res["code"] == UNSUPPORTED, res
    assert res["code"] != "TERMINAL_ADAPTER_DEGRADED"
    assert res["unsupported_platform"] is True and res["platform"] == "win32"
    assert "Windows" in res["message"]
    assert res["manual_command"].startswith("mcp2p --mcp-server")
    assert isinstance(res["mcp_config"], dict)


def test_every_port_method_answers_unsupported_on_win32_without_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    host = ApplicationFactory.create_terminal_host()  # en win32 el primario efectivo es el Null
    try:
        assert host.is_available() is False
        _assert_unsupported(host.spawn_session(pane_id=1, cli_profile="custom_shell"))
        _assert_unsupported(host.send_input(1, "ls\n"))
        _assert_unsupported(host.resize_session(1, 80, 24))
        _assert_unsupported(host.pause_session(1, True))
        _assert_unsupported(host.close_session(1))
        _assert_unsupported(host.terminate_session(1))
        poll = host.poll_output(1)
        _assert_unsupported(poll)
        assert "chunks" not in poll and poll["alive"] is False
        status = host.get_status()
        assert status["available"] is False and status["degraded"] is True
    finally:
        host.close()


def test_null_connector_and_real_host_agree_on_win32(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    _assert_unsupported(NullAgentTerminalConnector().spawn_session(pane_id=1))
    _assert_unsupported(PosixPtyHostConnector(max_panes=2).spawn_session(pane_id=1, cli_profile="claude"))


@pytest.mark.skipif(sys.platform == "win32", reason="en Windows el codigo correcto es TERMINAL_PLATFORM_UNSUPPORTED")
def test_off_windows_the_generic_degraded_code_is_unchanged() -> None:
    res = NullAgentTerminalConnector().spawn_session(pane_id=1)
    assert res["code"] == "TERMINAL_ADAPTER_DEGRADED"


def test_bridge_pty_methods_report_unsupported_platform_on_win32(monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = ApiBridge(db_manager=make_test_db())
    try:
        monkeypatch.setattr(sys, "platform", "win32")
        status = bridge.pty_get_matrix_status()
        assert status["platform_unsupported"] is True and status["code"] == UNSUPPORTED
        assert status["degraded"] is True and status["manual_command"]

        _assert_unsupported(bridge.pty_spawn_pane(1, "custom_shell", "implementer", None, 100, 28, False))
        _assert_unsupported(bridge.pty_write_input(1, "x"))
        _assert_unsupported(bridge.pty_poll_output(1))
        _assert_unsupported(bridge.pty_resize_pane(1, 80, 24))
        _assert_unsupported(bridge.pty_pause_pane(1))
        _assert_unsupported(bridge.pty_close_pane(1))
        _assert_unsupported(bridge.pty_prepare_install("claude"))
        _assert_unsupported(bridge.pty_install_cli("claude", "tok"))
        # shutil.which toma una rama win32 (ctypes/_winapi) que no existe aqui: se aisla del SO simulado.
        # En Windows real el primario es el Null (sin catalog_service): solo se aisla si existe.
        catalog_service = getattr(bridge.core_kernel.terminal_connector.primary, "catalog_service", None)
        if catalog_service is not None:
            catalog_service._which = lambda name: None
        listing = bridge.pty_list_cli_catalog()
        assert listing["success"] is True and listing["terminal_platform_supported"] is False
        assert listing["platform"] == "win32"
    finally:
        monkeypatch.undo()
        bridge.core_kernel.close()


# -- Windows con pywinpty: el conector SI esta disponible ------------------------------------------------------


class _FakeWinpty:
    class PTY:  # solo hace falta que exista; estos casos no lanzan procesos
        pass

    class Backend:
        ConPTY = 0
        WinPTY = 1


@pytest.mark.connector_available
def test_windows_with_pywinpty_is_not_reported_as_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.runtime.terminal_platform import is_unsupported_terminal_platform, unsupported_reason

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winpty", _FakeWinpty)
    assert is_unsupported_terminal_platform() is False and unsupported_reason() is None

    monkeypatch.setitem(sys.modules, "winpty", None)
    assert is_unsupported_terminal_platform() is True and unsupported_reason() == "PYWINPTY_MISSING"


@pytest.mark.connector_available
def test_windows_older_than_1809_is_unsupported_even_with_pywinpty(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.runtime.terminal_platform import unsupported_platform_response, unsupported_reason

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winpty", _FakeWinpty)
    monkeypatch.setattr(sys, "getwindowsversion", lambda: type("V", (), {"build": 17134})(), raising=False)
    assert unsupported_reason() == "WINDOWS_TOO_OLD"
    res = unsupported_platform_response()
    assert res["code"] == UNSUPPORTED and res["reason"] == "WINDOWS_TOO_OLD" and "1809" in res["message"]

    monkeypatch.setattr(sys, "getwindowsversion", lambda: type("V", (), {"build": 26100})(), raising=False)
    assert unsupported_reason() is None


def test_unsupported_message_names_the_missing_component(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    res = NullAgentTerminalConnector().spawn_session(pane_id=1)
    assert res["reason"] == "PYWINPTY_MISSING" and "pywinpty" in res["message"] and "Windows" in res["message"]


@pytest.mark.connector_available
def test_matrix_status_and_catalog_follow_the_real_connector_not_the_os(monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = ApiBridge(db_manager=make_test_db())
    try:
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setitem(sys.modules, "winpty", _FakeWinpty)
        status = bridge.pty_get_matrix_status()
        assert status["platform_unsupported"] is False and status["code"] == "OK" and status["message"] == ""
        catalog_service = getattr(bridge.core_kernel.terminal_connector.primary, "catalog_service", None)
        if catalog_service is not None:
            catalog_service._which = lambda name: None
        assert bridge.pty_list_cli_catalog()["terminal_platform_supported"] is True

        monkeypatch.setitem(sys.modules, "winpty", None)
        status = bridge.pty_get_matrix_status()
        assert status["platform_unsupported"] is True and status["code"] == UNSUPPORTED
    finally:
        monkeypatch.undo()
        bridge.core_kernel.close()
