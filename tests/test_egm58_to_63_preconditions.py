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
from src.app.api_bridge import ApiBridge, PublicJsApiFacade
from src.core.events import DomainEventBus
from src.core.factory import ApplicationFactory
from src.core.ports.connectors import AgentTerminalPort
from src.mcp.server import build_streamable_http_server, create_mcp_server, run_mcp_server
from src.mcp.tools import MCPToolsHandler
from src.storage.db import DatabaseManager


def _pywebview_get_functions(obj: Any) -> Dict[str, Any]:
    """Replica exacta del algoritmo de descubrimiento de `webview.util.inject_pywebview` (6.2.1)."""
    import inspect

    exposed_objects: list[int] = []

    def get_functions(target: object, base_name: str = "", functions: dict[str, Any] | None = None):
        obj_id = id(target)
        if obj_id in exposed_objects:
            return functions
        exposed_objects.append(obj_id)

        if functions is None:
            functions = {}

        for name in dir(target):
            full_name = f"{base_name}.{name}" if base_name else name
            if name.startswith("_"):
                continue
            attr = getattr(target, name)
            if not getattr(attr, "_serializable", True):
                continue
            if inspect.ismethod(attr) or inspect.isfunction(attr):
                functions[full_name] = list(inspect.getfullargspec(attr).args)[1:]
            elif inspect.isclass(attr) or (
                isinstance(attr, object) and not callable(attr) and hasattr(attr, "__module__")
            ):
                get_functions(attr, full_name, functions)
        return functions

    return get_functions(obj)


def _pywebview_get_nested_attribute(obj: object, attr_str: str) -> Any:
    """Replica exacta de `get_nested_attribute` en `webview.util.js_bridge_call` (6.2.1)."""
    for attr in attr_str.split("."):
        obj = getattr(obj, attr, None)
        if obj is None:
            return None
    return obj


# =========================================================================
# EGM-58: PublicJsApiFacade y cableado en app/main.py
# =========================================================================


def test_egm58_public_js_api_facade_blocks_internal_traversal(tmp_path: Path) -> None:
    db = DatabaseManager(str(tmp_path / "egm58.db"))
    try:
        api = ApiBridge(db_manager=db)
        facade = api.as_public_js_api()

        assert isinstance(facade, PublicJsApiFacade)
        assert facade.get_system_info()["active_node"] == api.get_system_info()["active_node"]
        assert facade.list_projects()["success"] is True
        assert facade.get_kanban_board()["success"] is True

        # Llamadas posicionales (como PyWebView `Array.prototype.slice.call(arguments)`) y kwargs
        created_pos = facade.create_project("ProjPos", "", "", False, 24)
        assert created_pos["success"] is True
        created_kw = facade.create_project(name="ProjKw")
        assert created_kw["success"] is True

        # Metadatos y __self__ seguros (apunta a facade, nunca a ApiBridge)
        assert facade.list_projects.__name__ == "list_projects"
        assert facade.list_projects.__doc__ == api.list_projects.__doc__
        assert facade.list_projects.__self__ is facade
        assert facade.list_projects.__self__ is not api

        # Descubrimiento tipo inject_pywebview: solo métodos planos de primer nivel, sin nested
        discovered = _pywebview_get_functions(facade)
        for expected in ("get_system_info", "list_projects", "get_kanban_board", "create_project"):
            assert expected in discovered
        for internal in ("as_public_js_api", "set_window", "set_transport", "ensure_local_node_registered"):
            assert internal not in discovered
        assert all("." not in fn_name for fn_name in discovered), (
            f"Se descubrieron rutas anidadas inesperadas: {[n for n in discovered if '.' in n]}"
        )

        # Recorrido tipo js_bridge_call/get_nested_attribute (no filtra '_')
        forbidden_paths = (
            "_bridge",
            "_bridge.core_kernel",
            "db",
            "core_kernel",
            "crypto",
            "extension_gate",
            "system_ui",
            "kanban_ui",
            "tribunal_ui",
            "list_projects.__self__.core_kernel",
            "list_projects.__self__.db",
            "list_projects.__self__.crypto",
            "list_projects.__self__.extension_gate",
            "list_projects.__self__._bridge",
            "list_projects.__wrapped__",
            "list_projects.__wrapped__.__self__",
            "as_public_js_api",
            "set_window",
            "set_transport",
            "ensure_local_node_registered",
        )
        for path in forbidden_paths:
            assert _pywebview_get_nested_attribute(facade, path) is None, (
                f"Ruta interna alcanzable desde PublicJsApiFacade: {path}"
            )

        # La referencia en WeakKeyDictionary se libera cuando desaparece la fachada
        import gc
        from src.app.api_bridge import _PUBLIC_FACADE_TARGETS

        assert facade in _PUBLIC_FACADE_TARGETS
        del facade
        gc.collect()
        assert len(_PUBLIC_FACADE_TARGETS) == 0
    finally:
        db.close()


def test_egm58_main_passes_public_js_api_facade_to_webview_create_window(
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

        def as_public_js_api(self) -> PublicJsApiFacade:
            return PublicJsApiFacade(self)  # type: ignore[arg-type]

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

    def _fake_create_window(**kwargs: Any) -> object:
        captured["create_window_kwargs"] = kwargs
        return object()

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
        js_api = captured["create_window_kwargs"]["js_api"]
        assert isinstance(js_api, PublicJsApiFacade)
        assert js_api is not dummy_bridge
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
    winpty_deps = [d for d in deps if d.startswith("pywinpty")]
    assert winpty_deps and "sys_platform == 'win32'" in winpty_deps[0], deps

    import_res = subprocess.run(
        [sys.executable, "-c", "import src.app.main, src.mcp.server; print('ok')"],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    assert import_res.returncode == 0, import_res.stderr
    assert "ok" in import_res.stdout

    mcp2p_bin = Path(sys.executable).parent / "mcp2p"
    assert mcp2p_bin.exists(), f"No se encontró ejecutable editable en {mcp2p_bin}"
    cli_res = subprocess.run(
        [str(mcp2p_bin), "--help"],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    assert cli_res.returncode == 0, cli_res.stderr
    assert "--mcp-server" in cli_res.stdout
