"""Precondiciones Fase 1 (EGM-58 a EGM-63): saneamiento previo al hito 4.1."""

from __future__ import annotations

import os
import subprocess
import sys
import types
from pathlib import Path
from typing import Any, Dict

import pytest

from src.a2a.bus import A2ABus
from src.adapters.outbound.extension_gate import (
    ExtensionSecurityGate,
    NullAgentTerminalConnector,
)
from src.app import main as app_main
from src.app.api_bridge import ApiBridge, InertJsApi
from src.core.events import DomainEventBus
from src.core.factory import ApplicationFactory
from src.core.ports.connectors import AgentTerminalPort
from src.mcp.server import build_streamable_http_server, create_mcp_server, run_mcp_server
from src.mcp.tools import MCPToolsHandler
from src.storage.db import DatabaseManager


# =========================================================================
# EGM-58 / EGM-73: funciones planas con `window.expose` y `js_api=None`
# =========================================================================


def _expose_on_mock_window(functions: Any):
    """Registra `functions` con el `Window.expose` REAL del SDK sobre una ventana simulada."""
    import threading
    from unittest.mock import MagicMock

    from webview.window import Window

    window = MagicMock()
    window._js_api = InertJsApi()
    window._functions = {}
    window._expose_lock = threading.Lock()
    Window.expose(window, *functions)
    return window


def test_egm58_public_js_functions_block_internal_traversal(tmp_path: Path) -> None:
    import threading
    import time

    import webview.util as wutil

    db = DatabaseManager(str(tmp_path / "egm58.db"))
    try:
        api = ApiBridge(db_manager=db)
        functions = api.public_js_functions()
        by_name = {fn.__name__: fn for fn in functions}

        assert by_name["get_system_info"]()["active_node"] == api.get_system_info()["active_node"]
        assert by_name["list_projects"]()["success"] is True
        # Llamadas posicionales (como PyWebView) y por nombre
        assert by_name["create_project"]("ProjPos", "", "", False, 24)["success"] is True
        assert by_name["create_project"](name="ProjKw")["success"] is True
        assert by_name["list_projects"].__doc__ == api.list_projects.__doc__

        for internal in ("public_js_functions", "set_window", "set_transport", "ensure_local_node_registered"):
            assert internal not in by_name
        assert not [n for n in by_name if n.startswith("_") or "." in n]

        window = _expose_on_mock_window(functions)
        # Los nombres de parametros llegan al JS (el SDK usa getfullargspec)
        import inspect

        assert inspect.getfullargspec(by_name["create_project"]).args[:3] == ["name", "code_repo_url", "gov_repo_url"]
        assert set(window._functions) == set(by_name)

        # El recorrido real de `js_bridge_call` solo resuelve por nombre EXACTO: ninguna ruta con puntos,
        # dunder ni atributo interno (EGM-73: `pty_*.__func__.__globals__.__setitem__` ya no es alcanzable).
        from src.app.ui_adapters import extensions_lab_ui

        marker = object()
        sentinel_before = getattr(extensions_lab_ui, "DEFAULT_SIGNALING_URL", marker)
        evaluated: list = []
        window.evaluate_js.side_effect = lambda code: evaluated.append(code)
        hostile = (
            "pty_spawn_pane.__func__.__globals__.__setitem__",
            "pty_spawn_pane.__globals__.__setitem__",
            "pty_spawn_pane.__closure__",
            "list_projects.__self__.core_kernel",
            "__builtins__.__setitem__",
            "__class__.__init__",
            "_rpc",
            "core_kernel",
            "db.get_connection",
            "public_js_functions",
            "set_window",
        )
        for idx, path in enumerate(hostile):
            wutil.js_bridge_call(window, path, ["DEFAULT_SIGNALING_URL", "http://evil"], f"id{idx}")
        time.sleep(0.3)
        assert evaluated == [], f"se ejecuto una ruta hostil: {evaluated[:1]}"
        assert getattr(extensions_lab_ui, "DEFAULT_SIGNALING_URL", marker) == sentinel_before
        import src.app.api_bridge as api_bridge_mod

        assert api_bridge_mod.DEFAULT_SIGNALING_URL == ""

        # Una llamada legitima por nombre exacto SI se ejecuta y devuelve el resultado
        done = threading.Event()
        window.evaluate_js.side_effect = lambda code: (evaluated.append(code), done.set())
        wutil.js_bridge_call(window, "list_projects", [], "ok1")
        assert done.wait(10) and '"success": true' in evaluated[-1].replace("\\", "")
    finally:
        db.close()


def test_egm58_main_creates_window_without_js_api_and_exposes_plain_functions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: Dict[str, Any] = {}

    class _DummyUpdater:
        def set_window(self, window: Any) -> None:
            captured["updater_window"] = window

    class _DummyBridge:
        def __init__(self) -> None:
            self.db = DatabaseManager(str(tmp_path / "egm58_main.db"))
            self.crypto = object()
            self._updater = _DummyUpdater()

        def get_signaling_server_url(self) -> str:
            return ""

        def _handle_peer_envelope(self, envelope: dict) -> None:
            pass

        def _handle_transport_message(self, message: dict) -> None:
            pass

        def set_transport(self, transport: Any) -> None:
            pass

        def set_window(self, window: Any) -> None:
            captured["bridge_window"] = window

        def get_system_info(self) -> Dict[str, Any]:
            return {"active_node": "n1", "wal_mode": True}

        def public_js_functions(self) -> list:
            def list_projects() -> Dict[str, Any]:
                return {"success": True}

            return [list_projects]

    class _DummyNetwork:
        def start_listening(self, *args: Any, **kwargs: Any) -> bool:
            return True

        def get_connector(self, name: str) -> Any:
            return types.SimpleNamespace(transport=object(), lan_port=42424)

        def close(self) -> None:
            captured["network_closed"] = True

    class _DummyKernel:
        def register_connector(self, *args: Any, **kwargs: Any) -> None:
            pass

        def close(self) -> None:
            captured["kernel_closed"] = True

    dummy_bridge = _DummyBridge()
    dummy_kernel = _DummyKernel()

    class _FakeWindow:
        def expose(self, *functions: Any) -> None:
            captured["exposed"] = [fn.__name__ for fn in functions]

    def _fake_create_window(**kwargs: Any) -> object:
        captured["create_window_kwargs"] = kwargs
        return _FakeWindow()

    fake_webview = types.ModuleType("webview")
    fake_webview.create_window = _fake_create_window  # type: ignore[attr-defined]
    fake_webview.start = lambda **kwargs: captured.setdefault("start_kwargs", kwargs)  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "webview", fake_webview)
    monkeypatch.setattr(
        app_main,
        "_build_kernel_and_bridge",
        lambda db_path, headless=False: (dummy_bridge.db, dummy_kernel, dummy_bridge),
    )
    monkeypatch.setattr(app_main, "_build_peer_network", lambda api, db, local_node: _DummyNetwork())
    monkeypatch.setattr(app_main, "get_ui_target", lambda: "file:///tmp/index.html")

    try:
        rc = app_main.main(["--db-path", str(tmp_path / "egm58_main.db")])
        assert rc == 0
        # EGM-73: la ventana recibe un js_api inerte (nada alcanzable); las funciones se registran por nombre
        assert isinstance(captured["create_window_kwargs"]["js_api"], InertJsApi)
        assert captured["exposed"] == ["list_projects"]
    finally:
        dummy_bridge.db.close()


# =========================================================================
# EGM-59: Endurecimiento de _dispatch_pty_exec
# =========================================================================


def test_egm59_dispatch_pty_exec_rejects_win32(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    exec_called = False

    def _fail_execvp(*_args: Any, **_kwargs: Any) -> None:
        nonlocal exec_called
        exec_called = True

    monkeypatch.setattr(os, "execvp", _fail_execvp)
    rc = app_main._dispatch_pty_exec(["python", "--version"])
    assert rc == 2
    assert exec_called is False


def test_egm59_dispatch_pty_exec_fails_closed_on_tiocsctty_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if sys.platform == "win32":
        pytest.skip("Prueba POSIX específica")

    import fcntl

    monkeypatch.setattr(os, "isatty", lambda fd: fd == 0)

    def _boom_ioctl(_fd: int, _req: int, _arg: int = 0) -> None:
        raise OSError("Operation not permitted")

    monkeypatch.setattr(fcntl, "ioctl", _boom_ioctl)
    exec_called = False

    def _fail_execvp(*_args: Any, **_kwargs: Any) -> None:
        nonlocal exec_called
        exec_called = True

    monkeypatch.setattr(os, "execvp", _fail_execvp)
    rc = app_main._dispatch_pty_exec(["echo", "hola"])
    assert rc == 1
    assert exec_called is False


def test_egm59_dispatch_pty_exec_sanitizes_pyinstaller_env_before_execvp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if sys.platform == "win32":
        pytest.skip("Prueba POSIX específica")

    monkeypatch.setattr(os, "isatty", lambda _fd: False)
    # En proceso: no tocar las senales de pytest (EGM-81 tiene su propia prueba en subproceso).
    monkeypatch.setattr(app_main, "_restore_default_signals", lambda: None)

    # Caso 1: _PYI_* + LD_LIBRARY_PATH_ORIG no vacía
    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", "/tmp/_MEI111")
    monkeypatch.setenv("_PYI_ARCHIVE_FILE", "/tmp/mcp2p")
    monkeypatch.setenv("_MEIPASS", "/tmp/_MEI111")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/lib:/opt/lib")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEI111:/usr/lib:/opt/lib")

    snapshots: list[dict[str, str]] = []

    def _fake_execvp(file: str, args: list[str]) -> None:
        snapshots.append(dict(os.environ))
        assert file == "echo"
        assert args == ["echo", "ok"]

    monkeypatch.setattr(os, "execvp", _fake_execvp)
    rc = app_main._dispatch_pty_exec(["--", "echo", "ok"])
    assert rc == 0
    env1 = snapshots[-1]
    assert not any(k.startswith("_PYI_") for k in env1)
    assert env1.get("LD_LIBRARY_PATH") == "/usr/lib:/opt/lib"
    assert "_MEIPASS" not in env1

    # Caso 2: LD_LIBRARY_PATH_ORIG vacía debe eliminar LD_LIBRARY_PATH
    monkeypatch.setenv("_PYI_FOO", "1")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEI222")
    assert app_main._dispatch_pty_exec(["echo", "ok"]) == 0
    env2 = snapshots[-1]
    assert not any(k.startswith("_PYI_") for k in env2)
    assert "LD_LIBRARY_PATH" not in env2

    # Caso 3: sin LD_LIBRARY_PATH_ORIG, filtra sys._MEIPASS de LD_LIBRARY_PATH
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", "/tmp/_MEI333", raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEI333:/custom/lib")
    assert app_main._dispatch_pty_exec(["echo", "ok"]) == 0
    env3 = snapshots[-1]
    assert env3.get("LD_LIBRARY_PATH") == "/custom/lib"

    # Caso 4: sin LD_LIBRARY_PATH_ORIG, si solo contenía _MEIPASS elimina LD_LIBRARY_PATH
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEI333")
    assert app_main._dispatch_pty_exec(["echo", "ok"]) == 0
    env4 = snapshots[-1]
    assert "LD_LIBRARY_PATH" not in env4


# =========================================================================
# EGM-60: Compartición de kernel/buses en create_mcp_server y close seguro
# =========================================================================


def test_egm60_mcp_server_shares_kernel_and_buses_and_respects_owns_kernel(tmp_path: Path) -> None:
    desktop_db_path = tmp_path / "desktop_shared.db"
    desktop_kernel = ApplicationFactory.create_desktop_kernel(db_path=str(desktop_db_path))
    try:
        server = create_mcp_server(kernel=desktop_kernel, pane_registry={"panes": 0})
        handler = getattr(server, "mcp2p_handler")
        assert isinstance(handler, MCPToolsHandler)
        assert handler.kernel is desktop_kernel
        assert handler.bus is desktop_kernel.bus
        assert handler.kernel.event_bus is desktop_kernel.event_bus
        assert handler._owns_kernel is False
        assert getattr(server, "mcp2p_pane_registry") == {"panes": 0}

        # handler.close() respeta close_db=False y deja la DB utilizable
        handler.close()
        projects = desktop_kernel.db.list_projects()
        assert isinstance(projects, list) and len(projects) >= 1
    finally:
        desktop_kernel.db.close()

    shared_db_path = tmp_path / "bus_shared.db"
    shared_db = DatabaseManager(str(shared_db_path))
    shared_bus = A2ABus(db=shared_db)
    shared_eb = DomainEventBus()
    server2 = None
    try:
        server2 = create_mcp_server(
            db_path=str(shared_db_path),
            bus=shared_bus,
            event_bus=shared_eb,
        )
        handler2 = getattr(server2, "mcp2p_handler")
        assert handler2.bus is shared_bus
        assert handler2.kernel.bus is shared_bus
        assert handler2.kernel.event_bus is shared_eb

        # Un handler construido con owns_kernel=False tampoco cierra la DB al cerrarse
        non_owner_handler = MCPToolsHandler(
            db_manager=shared_db, bus=shared_bus, event_bus=shared_eb, owns_kernel=False
        )
        non_owner_handler.close()
        assert isinstance(shared_db.list_projects(), list)
    finally:
        if server2 is not None:
            server2.mcp2p_handler.close()
        shared_db.close()

    # Un handler dueño por defecto (owns_kernel=True) sí cierra la DB al cerrarse
    owned_db = DatabaseManager(str(tmp_path / "owned.db"))
    owner_handler = MCPToolsHandler(db_manager=owned_db)
    assert owner_handler._owns_kernel is True
    owner_handler.close()
    with pytest.raises(RuntimeError, match="cerrado"):
        owned_db.list_projects()


# =========================================================================
# EGM-61: build_streamable_http_server y run_mcp_server sin stdout/stderr
# =========================================================================


def test_egm61_streamable_http_works_when_stdout_and_stderr_are_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = str(tmp_path / "egm61.db")
    server = create_mcp_server(db_path=db_path, transport="streamable-http")

    try:
        monkeypatch.setattr(sys, "stdout", None)
        monkeypatch.setattr(sys, "stderr", None)

        uv_server, sock, url = build_streamable_http_server(server, "127.0.0.1", 0)
        try:
            assert uv_server.config.log_config is None
            assert url.startswith("http://127.0.0.1:")
        finally:
            sock.close()

        # run_mcp_server con stderr=None no intenta escribir a stdout ni falla
        import anyio

        monkeypatch.setattr(anyio, "run", lambda _fn: None)
        run_mcp_server(server, transport="streamable-http", host="127.0.0.1", port=0)
    finally:
        server.mcp2p_handler.close()


# =========================================================================
# EGM-62: AgentTerminalPort y NullAgentTerminalConnector degradación segura
# =========================================================================


@pytest.mark.skipif(sys.platform == "win32", reason="en Windows el Null responde TERMINAL_PLATFORM_UNSUPPORTED (test_terminal_windows_degradation)")
def test_egm62_null_agent_terminal_connector_contract_and_safe_degradation() -> None:
    gate = ExtensionSecurityGate()
    terminal = NullAgentTerminalConnector(gate)

    assert isinstance(terminal, AgentTerminalPort)
    assert terminal.is_available() is False
    assert terminal.get_status()["available"] is False
    assert terminal.list_sessions() == []

    for method_name in (
        "spawn_session",
        "send_input",
        "resize_session",
        "pause_session",
        "close_session",
        "terminate_session",
    ):
        resp = getattr(terminal, method_name)("sess-1")
        assert resp["success"] is False
        assert resp["degraded"] is True
        assert resp["code"] == "TERMINAL_ADAPTER_DEGRADED"
        assert resp["reason"] == "TERMINAL_ADAPTER_NOT_INSTALLED"
        assert resp["manual_command"] == (
            "mcp2p --mcp-server --transport streamable-http --host 127.0.0.1 --port 8765"
        )
        assert resp["mcp_config"] == {
            "mcpServers": {
                "mcp2p": {
                    "url": "http://127.0.0.1:8765/mcp",
                }
            }
        }


# =========================================================================
# EGM-63: Dependencias declaradas y CLI editable .venv/bin/mcp2p
# =========================================================================


def test_egm63_pyproject_declares_pty_deps_and_editable_cli_works() -> None:
    import tomllib

    root = Path(__file__).resolve().parent.parent
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    deps = data["project"]["dependencies"]

    assert any(d.startswith("psutil") for d in deps), deps
    # EGM-79: Windows no tiene conector ConPTY (degrada), asi que `pywinpty` no se declara hasta que algo lo importe.
    assert not [d for d in deps if d.startswith("pywinpty")], deps

    import_res = subprocess.run(
        [sys.executable, "-c", "import src.app.main, src.mcp.server; print('ok')"],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    assert import_res.returncode == 0, import_res.stderr
    assert "ok" in import_res.stdout

    exe_dir = Path(sys.executable).parent
    candidates = [exe_dir / "mcp2p", exe_dir / "mcp2p.exe", exe_dir / "Scripts" / "mcp2p.exe"]  # Windows: Scripts\\mcp2p.exe
    mcp2p_bin = next((c for c in candidates if c.exists()), candidates[0])
    assert mcp2p_bin.exists(), f"No se encontró ejecutable editable en {candidates}"
    cli_res = subprocess.run(
        [str(mcp2p_bin), "--help"],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    assert cli_res.returncode == 0, cli_res.stderr
    assert "--mcp-server" in cli_res.stdout


def test_egm73_js_api_none_would_still_expose_nonetype_attributes() -> None:
    """Por que `main.py` usa `InertJsApi` y no `js_api=None`: con None el SDK aun resuelve `__class__.__init__`."""
    import time
    from unittest.mock import MagicMock

    import webview.util as wutil

    def _reaches(js_api: Any) -> bool:
        window = MagicMock()
        window._js_api = js_api
        window._functions = {}
        evaluated: list = []
        window.evaluate_js.side_effect = lambda code: evaluated.append(code)
        wutil.js_bridge_call(window, "__class__.__init__", [], "x1")
        time.sleep(0.2)
        return bool(evaluated)

    assert _reaches(None) is True  # el hueco medido
    assert _reaches(InertJsApi()) is False
