"""EGM-76..79 y `cwd` en las sesiones serializadas de `pty_get_matrix_status`."""

from __future__ import annotations

import signal
import sys
import time
from pathlib import Path
from typing import List

import psutil
import pytest

from conftest import make_test_db
from src.adapters.terminal import PosixPtyHostConnector, WinPtyHostConnector
from src.adapters.terminal.mcp_injection import PaneMcpConfigStore
from src.app.api_bridge import ApiBridge
from src.core.services import swarm_relay_service as relay_mod

posix_only = pytest.mark.skipif(
    not PosixPtyHostConnector.is_available(),
    reason="Requiere señales POSIX SIGHUP/SIGTERM/SIGKILL y setsid",
)
pty_required = pytest.mark.skipif(
    not (PosixPtyHostConnector.is_available() or WinPtyHostConnector.is_available()),
    reason="Requiere backend PTY POSIX o ConPTY",
)


def _host(tmp_path: Path) -> PosixPtyHostConnector:
    return PosixPtyHostConnector(max_panes=2, min_available_mb=0, mcp_config_store=PaneMcpConfigStore(tmp_path / "mcp"))


def _alive(pids: List[int]) -> List[int]:
    return [p for p in pids if psutil.pid_exists(p) and psutil.Process(p).status() != psutil.STATUS_ZOMBIE]


# -- EGM-76 ---------------------------------------------------------------------------------------------


def test_egm76_await_timeout_is_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    assert relay_mod.MAX_AWAIT_TIMEOUT_MS == 300_000
    clamp = relay_mod.clamp_await_timeout_ms
    assert clamp(10**12) == 300_000 and clamp(-5) == 0 and clamp(None) == 0 and clamp("abc") == 0
    assert clamp(True) == 0 and clamp(1500) == 1500 and clamp("2500") == 2500 and clamp(float("1e30")) == 300_000

    monkeypatch.setattr(relay_mod, "MAX_AWAIT_TIMEOUT_MS", 150)
    orch = relay_mod.SwarmRelayOrchestrator()
    try:
        t0 = time.monotonic()
        res = orch.await_next_assignment("nodo", timeout_ms=10**12)  # sin tope esperaria ~31 000 anos
        assert res["timed_out"] is True and time.monotonic() - t0 < 5
    finally:
        orch.close()


def test_egm76_mcp_tool_reports_the_applied_timeout(tmp_path: Path) -> None:
    import json

    from src.mcp.tools import MCPToolsHandler

    from src.storage.db import DatabaseManager

    from src.mcp.pane_registry import PaneRegistry

    registry = PaneRegistry()
    registry.register_pane("pane_1", node_uuid="n1", session_token="tok-n1")
    handler = MCPToolsHandler(db_manager=DatabaseManager(db_path=str(tmp_path / "t.db")), pane_registry=registry)
    try:
        out = json.loads(handler.await_next_assignment(node_uuid="n1", session_token="tok-n1", timeout_ms="no-es-numero"))
        assert out["timeout_ms_applied"] == 0 and out["has_assignment"] is False
    finally:
        handler.close()


# -- EGM-77 ---------------------------------------------------------------------------------------------


def _spawn_sighup_ready_child(host: PosixPtyHostConnector) -> None:
    """Lanza un hijo Python que atrapa SIGHUP y avisa por stdout cuando el handler esta instalado."""
    script = (
        "import signal, sys, time; "
        "signal.signal(signal.SIGHUP, lambda *_: sys.exit(0)); "
        "print('READY', flush=True); "
        "time.sleep(100)"
    )
    res = host.spawn_session(1, cli_profile="custom_shell", command=[sys.executable, "-c", script], use_trampoline=False)
    assert res["success"] is True
    text, cursor, deadline = "", 0, time.monotonic() + 5
    while "READY" not in text and time.monotonic() < deadline:
        out = host.poll_output(1, cursor)
        cursor = out["next_seq"]
        text += out["text"]
        time.sleep(0.02)
    assert "READY" in text


@posix_only
def test_egm77_close_sends_sighup_first_so_clis_can_exit_cleanly(tmp_path: Path) -> None:
    host = _host(tmp_path)
    try:
        _spawn_sighup_ready_child(host)
        closed = host.close_session(1)
        # SIGHUP primero: el handler sale con 0; con SIGTERM/SIGKILL directos serian -15 / -9
        assert closed["exit_code"] == 0
    finally:
        host.close()


@posix_only
def test_egm77_close_sighup_is_hermetic_when_parent_ignores_sighup(tmp_path: Path) -> None:
    prev_hup = signal.signal(signal.SIGHUP, signal.SIG_IGN)
    host = _host(tmp_path)
    try:
        _spawn_sighup_ready_child(host)
        closed = host.close_session(1)
        assert closed["exit_code"] == 0
    finally:
        host.close()
        signal.signal(signal.SIGHUP, prev_hup)


@posix_only
def test_egm77_close_escalates_and_leaves_no_orphans_even_if_hup_and_term_are_ignored(tmp_path: Path) -> None:
    host = _host(tmp_path)
    pidfile = tmp_path / "pids"
    try:
        script = f"trap '' HUP TERM; (trap '' HUP TERM; setsid sleep 100 & echo $! >> {pidfile}; sleep 100) & sleep 100"
        res = host.spawn_session(1, cli_profile="custom_shell", command=["sh", "-c", script], use_trampoline=False)
        assert res["success"] is True
        deadline = time.monotonic() + 5
        while (not pidfile.exists() or not pidfile.read_text().strip()) and time.monotonic() < deadline:
            time.sleep(0.05)
        escaped = [int(x) for x in pidfile.read_text().split()]
        family = [res["pid"]] + [c.pid for c in psutil.Process(res["pid"]).children(recursive=True)] + escaped
        assert len(_alive(family)) >= 3
        closed = host.close_session(1)
        assert closed["exit_code"] == -9  # ignoraba HUP y TERM: acabo en SIGKILL
        time.sleep(0.3)
        assert _alive(family) == [], "quedaron procesos huerfanos del panel (incluido el que hizo setsid)"
    finally:
        host.close()


# -- EGM-78 ---------------------------------------------------------------------------------------------


def _prepare_bridge(tmp_path: Path) -> ApiBridge:
    b = ApiBridge(db_manager=make_test_db())
    primary = b.core_kernel.terminal_connector.primary
    if hasattr(primary, "min_available_mb"):
        primary.min_available_mb = 0
    if hasattr(primary, "_mcp_store"):
        primary._mcp_store = PaneMcpConfigStore(tmp_path / "mcp")
    return b


@pty_required
def test_egm78_poll_output_no_longer_duplicates_text_in_chunks(tmp_path: Path) -> None:
    b = _prepare_bridge(tmp_path)
    try:
        cmd = [
            sys.executable,
            "-u",
            "-c",
            "import sys, time; print('INPUT:' + sys.stdin.readline().strip(), flush=True); time.sleep(5)",
        ]
        assert b.pty_spawn_pane(1, "custom_shell", "implementer", cmd, 100, 28, False, str(tmp_path))["success"]
        b.pty_write_input(1, "hola-mundo\r")
        text, cursor, deadline = "", 0, time.monotonic() + 10
        while "INPUT:hola-mundo" not in text and time.monotonic() < deadline:
            res = b.pty_poll_output(1, cursor)
            assert "chunks" not in res
            cursor = res["next_seq"]
            text += res["text"]
            time.sleep(0.05)
        assert "INPUT:hola-mundo" in text
        assert set(res) >= {"success", "text", "next_seq", "since_seq", "truncated", "alive", "exit_code"}
        # contrato final tambien para una sesion inexistente
        missing = b.pty_poll_output(3, 0)
        assert "chunks" not in missing and missing["text"] == "" and missing["next_seq"] == 0
        # y con el puente degradado
        b.pty_close_pane(1)
        gone = b.pty_poll_output(1, 7)
        assert "chunks" not in gone and gone["since_seq"] == 7
    finally:
        b._close_terminal_matrix()
        b.core_kernel.close()


# -- cwd en las sesiones serializadas --------------------------------------------------------------------


@pty_required
def test_matrix_status_sessions_include_cwd(tmp_path: Path) -> None:
    b = _prepare_bridge(tmp_path)
    try:
        work = tmp_path / "trabajo"
        work.mkdir()
        res = b.pty_spawn_pane(1, "custom_shell", "implementer", None, 100, 28, False, str(work))
        assert res["success"] is True
        sessions = b.pty_get_matrix_status()["sessions"]
        assert [s["cwd"] for s in sessions] == [str(work.resolve())]
        assert "session_token" not in sessions[0]
    finally:
        b._close_terminal_matrix()
        b.core_kernel.close()


# -- EGM-79 ---------------------------------------------------------------------------------------------


def test_egm79_pywinpty_is_declared_only_for_windows_and_only_loaded_lazily() -> None:
    """EGM-79 fijaba que pywinpty NO estuviera declarado porque nada lo usaba. Ahora lo usa el conector ConPTY."""
    import tomllib

    root = Path(__file__).resolve().parent.parent
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    declared = [d for d in data["project"]["dependencies"] if d.lower().startswith("pywinpty")]
    assert len(declared) == 1 and "sys_platform == 'win32'" in declared[0]  # nunca en Linux/macOS
    # Import perezoso: ningun modulo de src/ importa `winpty` al cargarse (en Linux/macOS no existe).
    for path in (root / "src").rglob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            assert not (stripped.startswith("import winpty") or stripped.startswith("from winpty")), (path, line)
    import src.adapters.terminal.winpty_host  # noqa: F401  (importable sin pywinpty)
