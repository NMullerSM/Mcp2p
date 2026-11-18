"""Tests E2E para OSC 7501, KanbanSwarmAdapter, list_swarm_panes, send_pane_message y Auto-Relay."""

from __future__ import annotations

import inspect
import json
import os
import stat
import sys
import textwrap
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Set, Tuple

import pytest

import src.adapters.terminal.pty_host as pty_host_mod
from src.a2a.bus import A2ABus
from src.adapters.terminal.cli_catalog import CliCatalogService
from src.adapters.terminal.pty_common import (
    OSC_7501_PROBE_REPLY,
    Osc7501Parser,
    compute_session_activity,
)
from src.adapters.terminal.pty_host import PosixPtyHostConnector, _Osc7501Parser
from src.core.domain.context import DomainEvent
from src.core.events import DomainEventBus
from src.core.factory import ApplicationFactory
from src.core.kernel import CoreKernel
from src.core.services import SwarmRelayOrchestrator
from src.core.services.kanban_swarm_adapter import KanbanSwarmAdapter
from src.mcp.pane_registry import PaneRegistry
from src.mcp.tools import MCPToolsHandler
from src.storage.db import DatabaseManager


class _ControllableTerminalConnector:
    """Conector PTY simulado con estado idle/working controlable por panel."""

    def __init__(self) -> None:
        self.calls: List[Tuple[str, str]] = []
        self._idle: Dict[str, bool] = {}

    def set_idle(self, pane_id: str, idle: bool) -> None:
        self._idle[str(pane_id)] = bool(idle)

    def is_pane_idle(self, pane_id: Any, *, min_silence_s: float = 1.5) -> bool:
        return self._idle.get(str(pane_id), True)

    def get_pane_activity(self, pane_id: Any, *, min_silence_s: float = 1.5) -> Dict[str, Any]:
        idle = self.is_pane_idle(pane_id, min_silence_s=min_silence_s)
        return {
            "pane_id": str(pane_id),
            "state": "idle" if idle else "working",
            "source": "osc7501",
            "idle": idle,
        }

    def send_input(self, pane_id: Any, data: str) -> Dict[str, Any]:
        self.calls.append((str(pane_id), data))
        return {"success": True, "bytes_written": len(data)}


def _wait_until(predicate: Any, *, timeout_s: float = 4.0, step_s: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step_s)
    return bool(predicate())


# ----------------------------------------------------------------------
# 1. _Osc7501Parser + sesión PTY real
# ----------------------------------------------------------------------


def test_osc7501_parser_incremental_and_terminators() -> None:
    parser = _Osc7501Parser()
    assert parser.feed(b"hello \x1b]7501;work") == []
    assert parser.feed(b"ing\x07world\x1b]7501;idle\x1b\\") == ["working", "idle"]
    assert parser.feed(b"\x1b]7501;done;extra\x07") == ["done"]


def test_osc7501_parser_kv_formats_kind_and_probe() -> None:
    parser = Osc7501Parser()
    assert parser.last_kind is None
    assert parser.take_probes() == 0

    assert parser.feed(b"\x1b]7501;state=working\x07") == ["working"]
    assert parser.last_kind is None

    assert parser.feed(b"\x1b]7501;STATE=BLOCKED:KIND=permission\x07") == ["blocked"]
    assert parser.last_kind == "permission"

    assert parser.feed(b"\x1b]7501;state=blocked;kind=permission\x1b\\") == ["blocked"]
    assert parser.last_kind == "permission"

    assert parser.feed(b"\x1b]7501;state=idle\x1b\\") == ["idle"]
    assert parser.last_kind is None

    assert parser.feed(b"\x1b]7501;permission\x07") == ["blocked"]
    assert parser.last_kind == "permission"

    # Sonda OSC 7501 ("?"): no genera estado, incrementa contador y se resetea con take_probes().
    assert parser.feed(b"\x1b]7501;?\x07") == []
    assert parser.take_probes() == 1
    assert parser.take_probes() == 0

    assert parser.feed(b"\x1b]7501;?\x1b\\\x1b]7501;?\x07") == []
    assert parser.take_probes() == 2
    assert parser.take_probes() == 0


def test_osc7501_parser_trailing_output_framing() -> None:
    parser = Osc7501Parser()

    # Sonda, espacios, CRLF o CSI tras idle NO marcan trailing_output.
    assert parser.feed(b"prev output\x1b]7501;idle\x07") == ["idle"]
    assert parser.trailing_output is False

    assert parser.feed(b"\x1b]7501;?\x07  \r\n\x1b[0m") == []
    assert parser.trailing_output is False
    assert parser.take_probes() == 1

    # Fragmento parcial de prefijo OSC no cuenta como salida visible consumida.
    assert parser.feed(b"\x1b]75") == []
    assert parser.trailing_output is False
    assert parser.feed(b"01;?\x07") == []
    assert parser.trailing_output is False
    assert parser.take_probes() == 1

    # Salida visible en el MISMO chunk tras idle SÍ marca trailing_output.
    assert parser.feed(b"\x1b]7501;idle\x07forged text\n") == ["idle"]
    assert parser.trailing_output is True

    # Salida visible en un chunk posterior sin nuevo estado también marca trailing_output.
    assert parser.feed(b"\x1b]7501;idle\x07") == ["idle"]
    assert parser.trailing_output is False
    assert parser.feed(b"more visible text\n") == []
    assert parser.trailing_output is True


def test_compute_session_activity_expires_osc_idle_even_after_input() -> None:
    now = time.monotonic()
    session = SimpleNamespace(
        pane_id="pane_1",
        is_alive=lambda: True,
        paused=False,
        last_output_at=now - 0.1,
        last_input_at=now - 45.0,
        osc_status="idle",
        osc_status_at=now - 31.0,
        osc_kind=None,
    )
    activity = compute_session_activity(session, min_silence_s=1.5)
    assert activity["source"] == "silence_heuristic"
    assert activity["state"] == "working"
    assert activity["idle"] is False
    assert activity["osc_kind"] is None


def _close_connector(connector: PosixPtyHostConnector) -> None:
    shutdown = getattr(connector, "shutdown_all", None) or getattr(connector, "close_all", None)
    if callable(shutdown):
        shutdown()
    else:
        for s in list(getattr(connector, "_sessions", {}).values()):
            connector._shutdown_session(s)


@pytest.mark.skipif(not PosixPtyHostConnector.is_available(), reason="PTY POSIX no disponible")
def test_pty_real_osc7501_working_then_idle(tmp_path: Path) -> None:
    connector = PosixPtyHostConnector(max_panes=2)
    # Script interactivo que emite working, espera una línea por stdin y luego emite idle.
    script = (
        "import sys, time; "
        "sys.stdout.write('\\x1b]7501;working\\x07'); sys.stdout.flush(); "
        "sys.stdin.readline(); "
        "sys.stdout.write('\\x1b]7501;idle\\x07'); sys.stdout.flush(); "
        "time.sleep(1.0)"
    )
    try:
        res = connector.spawn_session(
            "pane_1",
            command=[sys.executable, "-c", script],
            cwd=tmp_path,
            use_trampoline=False,
        )
        assert res["success"] is True

        assert _wait_until(
            lambda: connector.get_pane_activity("pane_1").get("osc_status") == "working"
        )
        assert connector.is_pane_idle("pane_1") is False

        send_res = connector.send_input("pane_1", "\n")
        assert send_res["success"] is True

        assert _wait_until(
            lambda: connector.get_pane_activity("pane_1").get("osc_status") == "idle"
        )
        assert connector.is_pane_idle("pane_1") is True
    finally:
        _close_connector(connector)


@pytest.mark.skipif(not PosixPtyHostConnector.is_available(), reason="PTY POSIX no disponible")
def test_pty_real_osc7501_kv_blocked_kind_permission(tmp_path: Path) -> None:
    connector = PosixPtyHostConnector(max_panes=2)
    script = (
        "import sys, time; "
        "sys.stdout.write('\\x1b]7501;state=blocked:kind=permission\\x07'); sys.stdout.flush(); "
        "time.sleep(2.0)"
    )
    try:
        res = connector.spawn_session(
            "pane_1",
            command=[sys.executable, "-c", script],
            cwd=tmp_path,
            use_trampoline=False,
        )
        assert res["success"] is True

        assert _wait_until(
            lambda: connector.get_pane_activity("pane_1").get("osc_status") == "blocked"
        )
        activity = connector.get_pane_activity("pane_1")
        assert activity["state"] == "blocked"
        assert activity["osc_kind"] == "permission"
        assert activity["idle"] is False
    finally:
        _close_connector(connector)


class _MemorySettings:
    def __init__(self) -> None:
        self.data: Dict[Tuple[str, str], str] = {}

    def get_extension_setting(self, namespace: str, key: str, default: Optional[str] = None) -> Optional[str]:
        return self.data.get((namespace, key), default)

    def set_extension_setting(self, namespace: str, key: str, value: str) -> None:
        self.data[(namespace, key)] = value

    def delete_extension_setting(self, namespace: str, key: str) -> bool:
        return self.data.pop((namespace, key), None) is not None


def _make_python_wrapper(tmp_path: Path, name: str, script_py: Path) -> Path:
    wrapper = tmp_path / name
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script_py}" "$@"\n', encoding="utf-8")
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    return wrapper


def _install_double_spy(
    connector: PosixPtyHostConnector, monkeypatch: pytest.MonkeyPatch
) -> Tuple[List[Tuple[Any, Any]], List[Tuple[int, bytes, str]]]:
    send_input_calls: List[Tuple[Any, Any]] = []
    master_writes: List[Tuple[int, bytes, str]] = []
    master_fds: Set[int] = set()

    orig_send_input = connector.send_input
    orig_openpty = pty_host_mod.pty.openpty
    orig_os_write = os.write

    def spy_send_input(pane_id: Any, data: Any) -> Dict[str, Any]:
        send_input_calls.append((pane_id, data))
        return orig_send_input(pane_id, data)

    def spy_openpty() -> Tuple[int, int]:
        m_fd, s_fd = orig_openpty()
        master_fds.add(m_fd)
        return m_fd, s_fd

    def spy_os_write(fd: int, data: Any) -> int:
        if fd in master_fds:
            caller = inspect.stack()[1].function
            master_writes.append((fd, bytes(data), caller))
        return orig_os_write(fd, data)

    monkeypatch.setattr(connector, "send_input", spy_send_input)
    monkeypatch.setattr(pty_host_mod.pty, "openpty", spy_openpty)
    monkeypatch.setattr(os, "write", spy_os_write)
    return send_input_calls, master_writes


@pytest.mark.skipif(not PosixPtyHostConnector.is_available(), reason="PTY POSIX no disponible")
def test_pty_real_osc7501_probe_replies_without_calling_send_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert OSC_7501_PROBE_REPLY == b"\x1b]7501;?1\x07"
    script_path = tmp_path / "probe_child.py"
    script_path.write_text(
        textwrap.dedent(
            """\
            import os, select, sys, termios, time, tty
            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                os.write(sys.stdout.fileno(), b"\\x1b]7501;?\\x07")
                buf = b""
                deadline = time.monotonic() + 3.0
                while len(buf) < 10 and time.monotonic() < deadline:
                    r, _, _ = select.select([fd], [], [], 0.2)
                    if r:
                        chunk = os.read(fd, 64)
                        if not chunk:
                            break
                        buf += chunk
                os.write(sys.stdout.fileno(), b"PROBE_REPLY=" + buf.hex().encode() + b"\\n")
                time.sleep(0.5)
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
            """
        ),
        encoding="utf-8",
    )
    fake_claude = _make_python_wrapper(tmp_path, "fake_claude", script_path)
    settings = _MemorySettings()
    catalog_svc = CliCatalogService(
        settings,
        user_catalog_path=tmp_path / "no_user.json",
        which=lambda _n: None,
        home=str(tmp_path / "home"),
        environ={"SHELL": "/bin/sh"},
    )
    assert catalog_svc.set_binary_path("claude", str(fake_claude))["success"] is True

    connector = PosixPtyHostConnector(max_panes=2, catalog_service=catalog_svc, min_available_mb=0)
    send_input_calls, master_writes = _install_double_spy(connector, monkeypatch)

    expected_hex = b"\x1b]7501;?1\x07".hex()
    try:
        res = connector.spawn_session(
            "pane_1",
            cli_profile="claude",
            command=None,
            cwd=tmp_path,
            use_trampoline=False,
        )
        assert res["success"] is True

        assert _wait_until(
            lambda: f"PROBE_REPLY={expected_hex}" in connector.poll_output("pane_1").get("text", "")
        )
        assert send_input_calls == []
        assert len(master_writes) == 1
        assert master_writes[0][1] == b"\x1b]7501;?1\x07"
        assert master_writes[0][2] == "_write_osc_probe_reply"
        session = connector._sessions["pane_1"]
        assert session.osc_probes_replied == 1
        assert session.osc_probes_dropped == 0
    finally:
        _close_connector(connector)


@pytest.mark.skipif(not PosixPtyHostConnector.is_available(), reason="PTY POSIX no disponible")
@pytest.mark.parametrize(
    "cli_profile,use_manual_cmd",
    [
        ("custom_shell", False),
        ("claude", True),
        ("gemini", False),
    ],
)
def test_pty_real_osc7501_probe_ignored_for_custom_shell_manual_and_unverified_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cli_profile: str,
    use_manual_cmd: bool,
) -> None:
    script_path = tmp_path / "probe_silent_child.py"
    script_path.write_text(
        textwrap.dedent(
            """\
            import os, select, sys, termios, time, tty
            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                os.write(sys.stdout.fileno(), b"\\x1b]7501;?\\x07")
                buf = b""
                deadline = time.monotonic() + 0.3
                while time.monotonic() < deadline:
                    r, _, _ = select.select([fd], [], [], 0.05)
                    if r:
                        chunk = os.read(fd, 64)
                        if not chunk:
                            break
                        buf += chunk
                os.write(sys.stdout.fileno(), b"PROBE_REPLY=" + buf.hex().encode() + b"\\n")
                time.sleep(0.3)
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
            """
        ),
        encoding="utf-8",
    )
    fake_bin = _make_python_wrapper(tmp_path, f"fake_{cli_profile}", script_path)
    settings = _MemorySettings()
    catalog_svc = CliCatalogService(
        settings,
        user_catalog_path=tmp_path / "no_user.json",
        which=lambda _n: None,
        home=str(tmp_path / "home"),
        environ={"SHELL": str(fake_bin)},
    )
    if not use_manual_cmd:
        assert catalog_svc.set_binary_path(cli_profile, str(fake_bin))["success"] is True

    connector = PosixPtyHostConnector(max_panes=2, catalog_service=catalog_svc, min_available_mb=0)
    send_input_calls, master_writes = _install_double_spy(connector, monkeypatch)
    try:
        res = connector.spawn_session(
            "pane_1",
            cli_profile=cli_profile,
            command=[sys.executable, str(script_path)] if use_manual_cmd else None,
            cwd=tmp_path,
            use_trampoline=False,
        )
        assert res["success"] is True
        assert _wait_until(
            lambda: "PROBE_REPLY=" in connector.poll_output("pane_1").get("text", "")
        )
        text = connector.poll_output("pane_1").get("text", "")
        assert "PROBE_REPLY=\r\n" in text or "PROBE_REPLY=\n" in text
        assert send_input_calls == []
        assert master_writes == []
        session = connector._sessions["pane_1"]
        assert session.osc_probes_replied == 0
        assert session.osc_probes_dropped >= 1
    finally:
        _close_connector(connector)


@pytest.mark.skipif(not PosixPtyHostConnector.is_available(), reason="PTY POSIX no disponible")
def test_pty_real_osc7501_probe_avalanche_rate_limited_and_nonblocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script_path = tmp_path / "avalanche_child.py"
    script_path.write_text(
        textwrap.dedent(
            """\
            import os, sys, termios, time, tty
            fd_in = sys.stdin.fileno()
            old = termios.tcgetattr(fd_in)
            try:
                tty.setraw(fd_in)
                payload = b"\\x1b]7501;?\\x07" * 10000
                view = memoryview(payload)
                off = 0
                while off < len(view):
                    off += os.write(sys.stdout.fileno(), view[off:])
                os.write(sys.stdout.fileno(), b"\\nAFTER_AVALANCHE\\n")
                time.sleep(0.5)
            finally:
                termios.tcsetattr(fd_in, termios.TCSADRAIN, old)
            """
        ),
        encoding="utf-8",
    )
    fake_claude = _make_python_wrapper(tmp_path, "fake_claude_avalanche", script_path)
    settings = _MemorySettings()
    catalog_svc = CliCatalogService(
        settings,
        user_catalog_path=tmp_path / "no_user.json",
        which=lambda _n: None,
        home=str(tmp_path / "home"),
        environ={"SHELL": "/bin/sh"},
    )
    assert catalog_svc.set_binary_path("claude", str(fake_claude))["success"] is True

    connector = PosixPtyHostConnector(max_panes=2, catalog_service=catalog_svc, min_available_mb=0)
    send_input_calls, master_writes = _install_double_spy(connector, monkeypatch)
    try:
        res = connector.spawn_session(
            "pane_1",
            cli_profile="claude",
            command=None,
            cwd=tmp_path,
            use_trampoline=False,
        )
        assert res["success"] is True
        assert _wait_until(
            lambda: "AFTER_AVALANCHE" in connector.poll_output("pane_1").get("text", ""),
            timeout_s=5.0,
        )
        assert send_input_calls == []
        assert 1 <= len(master_writes) <= 2
        assert all(
            data == b"\x1b]7501;?1\x07" and caller == "_write_osc_probe_reply"
            for _, data, caller in master_writes
        )
        session = connector._sessions["pane_1"]
        assert session.osc_probes_replied <= 2
        assert session.osc_probes_dropped >= 9998
    finally:
        _close_connector(connector)


@pytest.mark.skipif(not PosixPtyHostConnector.is_available(), reason="PTY POSIX no disponible")
def test_pty_real_osc7501_state_clear_resets_status_and_kind(tmp_path: Path) -> None:
    connector = PosixPtyHostConnector(max_panes=2)
    script = (
        "import sys, time; "
        "sys.stdout.write('\\x1b]7501;state=blocked:kind=permission\\x07'); sys.stdout.flush(); "
        "sys.stdin.readline(); "
        "sys.stdout.write('\\x1b]7501;state=clear\\x07CLEARED_OK\\n'); sys.stdout.flush(); "
        "time.sleep(0.5)"
    )
    try:
        res = connector.spawn_session(
            "pane_1",
            command=[sys.executable, "-c", script],
            cwd=tmp_path,
            use_trampoline=False,
        )
        assert res["success"] is True
        assert _wait_until(
            lambda: connector.get_pane_activity("pane_1").get("osc_status") == "blocked"
        )
        assert connector.get_pane_activity("pane_1").get("osc_kind") == "permission"

        assert connector.send_input("pane_1", "\n")["success"] is True
        assert _wait_until(
            lambda: (
                "CLEARED_OK" in connector.poll_output("pane_1").get("text", "")
                and connector.get_pane_activity("pane_1").get("osc_status") is None
            )
        )
        activity = connector.get_pane_activity("pane_1")
        assert activity["osc_status"] is None
        assert activity["osc_kind"] is None
    finally:
        _close_connector(connector)


@pytest.mark.skipif(not PosixPtyHostConnector.is_available(), reason="PTY POSIX no disponible")
def test_pty_real_osc7501_idle_invalidated_by_subsequent_output(tmp_path: Path) -> None:
    connector = PosixPtyHostConnector(max_panes=2)
    script_path = tmp_path / "idle_invalidation_child.py"
    script_path.write_text(
        textwrap.dedent(
            """\
            import sys, time
            sys.stdout.write("\\x1b]7501;idle\\x07")
            sys.stdout.flush()
            sys.stdin.readline()
            for i in range(6):
                sys.stdout.write(f"normal output {i}\\n")
                sys.stdout.flush()
                time.sleep(0.03)
            time.sleep(1.0)
            """
        ),
        encoding="utf-8",
    )
    try:
        res = connector.spawn_session(
            "pane_1",
            command=[sys.executable, str(script_path)],
            cwd=tmp_path,
            use_trampoline=False,
        )
        assert res["success"] is True

        assert _wait_until(
            lambda: connector.get_pane_activity("pane_1").get("osc_status") == "idle"
        )
        assert connector.send_input("pane_1", "\n")["success"] is True

        assert _wait_until(
            lambda: (
                "normal output 5" in connector.poll_output("pane_1").get("text", "")
                and connector.get_pane_activity("pane_1").get("osc_status") is None
            )
        )
        activity = connector.get_pane_activity("pane_1")
        assert activity["osc_status"] is None
        assert activity["osc_kind"] is None
    finally:
        _close_connector(connector)


@pytest.mark.skipif(not PosixPtyHostConnector.is_available(), reason="PTY POSIX no disponible")
def test_pty_real_osc7501_idle_preserved_on_probe_and_whitespace_but_invalidated_same_chunk(
    tmp_path: Path,
) -> None:
    connector = PosixPtyHostConnector(max_panes=2)
    script_path = tmp_path / "idle_framing_child.py"
    script_path.write_text(
        textwrap.dedent(
            """\
            import os, select, sys, termios, time, tty
            fd_in = sys.stdin.fileno()
            fd_out = sys.stdout.fileno()
            old = termios.tcgetattr(fd_in)
            try:
                tty.setraw(fd_in)
                os.write(fd_out, b"\\x1b]7501;idle\\x07")
                time.sleep(0.15)
                # Sonda + CRLF + CSI tras idle no debe invalidar idle.
                os.write(fd_out, b"\\x1b]7501;?\\x07\\r\\n\\x1b[0m")
                # Consumir la respuesta de sonda del host y esperar señal de fase 2.
                deadline = time.monotonic() + 3.0
                buf = b""
                while time.monotonic() < deadline:
                    r, _, _ = select.select([fd_in], [], [], 0.1)
                    if r:
                        chunk = os.read(fd_in, 64)
                        if not chunk:
                            break
                        buf += chunk
                        if b"GO" in buf:
                            break
                # Mismo write con idle + texto visible posterior debe invalidar idle.
                os.write(fd_out, b"\\x1b]7501;idle\\x07forged trailing output\\n")
                time.sleep(1.0)
            finally:
                termios.tcsetattr(fd_in, termios.TCSADRAIN, old)
            """
        ),
        encoding="utf-8",
    )
    try:
        res = connector.spawn_session(
            "pane_1",
            command=[sys.executable, str(script_path)],
            cwd=tmp_path,
            use_trampoline=False,
        )
        assert res["success"] is True

        assert _wait_until(
            lambda: connector.get_pane_activity("pane_1").get("osc_status") == "idle"
        )
        # Dar margen a que la sonda + CRLF + CSI pasen por _reader_loop.
        time.sleep(0.3)
        assert connector.get_pane_activity("pane_1").get("osc_status") == "idle"

        assert connector.send_input("pane_1", "GO\n")["success"] is True

        assert _wait_until(
            lambda: (
                "forged trailing output" in connector.poll_output("pane_1").get("text", "")
                and connector.get_pane_activity("pane_1").get("osc_status") is None
            )
        )
        activity = connector.get_pane_activity("pane_1")
        assert activity["osc_status"] is None
        assert activity["osc_kind"] is None
    finally:
        _close_connector(connector)


# ----------------------------------------------------------------------
# 2. KanbanSwarmAdapter: list_swarm_panes y resolve_card_target
# ----------------------------------------------------------------------


def test_kanban_swarm_adapter_projects_assigned_cards_and_activity(tmp_path: Path) -> None:
    db = DatabaseManager(db_path=str(tmp_path / "adapter.db"))
    proj_uuid = db.create_project("Proyecto Adapter", str(tmp_path / "repo"))["project_uuid"]
    db.register_or_update_node("node-pane-2", "Pane 2", "agent-2")
    db.add_or_request_collaborator(proj_uuid, "node-pane-2", status="approved")
    db.create_card(
        project_uuid=proj_uuid,
        card_id="CARD-P2",
        title="Implementar endpoint",
        assignee_node_uuid="node-pane-2",
    )

    registry = PaneRegistry()
    registry.register_pane(
        "pane_1", node_uuid="node-pane-1", session_token="tok-1", role="coordinator", project_uuid=proj_uuid
    )
    registry.register_pane(
        "pane_2", node_uuid="node-pane-2", session_token="tok-2", role="implementer", project_uuid=proj_uuid
    )

    term = _ControllableTerminalConnector()
    term.set_idle("pane_1", False)
    term.set_idle("pane_2", True)

    adapter = KanbanSwarmAdapter(
        kanban_repo=db,
        pane_registry=registry,
        terminal_connector=term,
    )

    listing = adapter.list_swarm_panes(project_uuid=proj_uuid)
    assert listing["success"] is True
    assert listing["count"] == 2

    by_pane = {p["pane_id"]: p for p in listing["panes"]}
    assert by_pane["pane_1"]["activity_state"] == "working"
    assert by_pane["pane_1"]["idle"] is False
    assert by_pane["pane_1"]["assigned_cards"] == []

    assert by_pane["pane_2"]["activity_state"] == "idle"
    assert by_pane["pane_2"]["idle"] is True
    assert [c["card_id"] for c in by_pane["pane_2"]["assigned_cards"]] == ["CARD-P2"]

    target = adapter.resolve_card_target(proj_uuid, "CARD-P2")
    assert target is not None
    assert target["pane_id"] == "pane_2"
    assert target["node_uuid"] == "node-pane-2"
    assert target["matched_by"] == "assignee_node_uuid"


# ----------------------------------------------------------------------
# 3. MCPToolsHandler: list_swarm_panes, send_pane_message y EGM-22
# ----------------------------------------------------------------------


def test_mcp_tools_list_swarm_panes_and_send_pane_message_egm22(tmp_path: Path) -> None:
    db = DatabaseManager(db_path=str(tmp_path / "mcp_panes.db"))
    proj_uuid = db.create_project("Proyecto MCP Panes", str(tmp_path / "repo"))["project_uuid"]
    event_bus = DomainEventBus()
    bus = A2ABus(db)

    term = _ControllableTerminalConnector()
    term.set_idle("pane_1", True)
    term.set_idle("pane_2", True)

    kernel = ApplicationFactory.create_mcp_kernel(
        db_manager=db,
        bus=bus,
        event_bus=event_bus,
    )
    kernel.register_connector(CoreKernel.PORT_AGENT_TERMINAL, term)

    registry = PaneRegistry()
    registry.register_pane("pane_1", node_uuid="node-pane-1", session_token="tok-pane-1", project_uuid=proj_uuid)
    registry.register_pane("pane_2", node_uuid="node-pane-2", session_token="tok-pane-2", project_uuid=proj_uuid)

    handler = MCPToolsHandler(
        kernel=kernel,
        owns_kernel=False,
        pane_registry=registry,
    )
    try:
        # 1. list_swarm_panes expone paneles y auto_relay_enabled
        panes_out = json.loads(handler.list_swarm_panes(project_uuid=proj_uuid, session_token="tok-pane-1"))
        assert panes_out["success"] is True
        assert panes_out["count"] == 2
        assert "auto_relay_enabled" in panes_out
        assert panes_out["auto_relay_enabled"] is False

        # 2. EGM-22: Spoofing de node_uuid con token de pane_1 debe bloquearse
        spoof_out = json.loads(
            handler.send_pane_message(
                project_uuid=proj_uuid,
                target_node_uuid="node-pane-2",
                message="hola",
                node_uuid="node-pane-2",
                session_token="tok-pane-1",
            )
        )
        assert spoof_out["success"] is False
        assert spoof_out["code"] == "NODE_UUID_SPOOFING_FORBIDDEN"

        # 3. Envío exitoso de pane_1 -> pane_2
        ok_out = json.loads(
            handler.send_pane_message(
                project_uuid=proj_uuid,
                target_pane_id="pane_2",
                message="Revisa CARD-99 por favor",
                card_id="CARD-99",
                node_uuid="node-pane-1",
                session_token="tok-pane-1",
            )
        )
        assert ok_out["success"] is True
        assert ok_out["target_node_uuid"] == "node-pane-2"
        assert ok_out.get("queued") or ok_out.get("deduplicated")
        assert ok_out.get("message_uuid")

        # Pane 2 recibe el mensaje vía await_next_assignment
        pull_out = json.loads(
            handler.await_next_assignment(
                project_uuid=proj_uuid,
                node_uuid="node-pane-2",
                session_token="tok-pane-2",
                timeout_ms=0,
            )
        )
        assert pull_out["success"] is True
        # F0: el mensaje directo llega por la bandeja, con la procedencia aparte del texto.
        assert pull_out["has_messages"] is True
        (msg,) = pull_out["messages"]
        assert msg["text"] == "Revisa CARD-99 por favor"
        assert msg["from"]["node_uuid"] == "node-pane-1"
        assert msg["from"]["pane_id"] == "pane_1"
        assert msg["to"]["node_uuid"] == "node-pane-2"
        assert msg["id"] == ok_out["message_uuid"]
        assert term.calls == []  # F0: nada se escribe en ningun PTY
    finally:
        handler.close()
        kernel.close()


# ----------------------------------------------------------------------
# 4. Auto-Relay (F0): deja avisos en la bandeja del destino y NUNCA escribe en su PTY
# ----------------------------------------------------------------------


def test_auto_relay_leaves_notices_in_the_inbox_and_never_writes_to_the_pty(tmp_path: Path) -> None:
    db = DatabaseManager(db_path=str(tmp_path / "auto_relay.db"))
    proj_uuid = db.create_project("Proyecto AutoRelay", str(tmp_path / "repo"))["project_uuid"]
    event_bus = DomainEventBus()
    bus = A2ABus(db)

    term = _ControllableTerminalConnector()
    term.set_idle("pane_1", True)
    term.set_idle("pane_2", True)

    registry = PaneRegistry()
    registry.register_pane("pane_1", node_uuid="node-pane-1", session_token="tok-1", project_uuid=proj_uuid)
    # D2: el Auto-Relay es POR PANEL; aqui solo el destino (pane_2) lo tiene activo.
    registry.register_pane(
        "pane_2", node_uuid="node-pane-2", session_token="tok-2", project_uuid=proj_uuid, auto_relay_enabled=True
    )

    orch = SwarmRelayOrchestrator(
        event_bus=event_bus,
        bus=bus,
        pane_registry=registry,
    )
    kernel = ApplicationFactory.create_mcp_kernel(
        db_manager=db,
        bus=bus,
        event_bus=event_bus,
    )
    kernel.register_connector(CoreKernel.PORT_AGENT_TERMINAL, term)

    handler = MCPToolsHandler(
        kernel=kernel,
        owns_kernel=False,
        pane_registry=registry,
        relay_orchestrator=orch,
    )
    try:
        status = orch.get_auto_relay_status()
        assert status["auto_relay_enabled"] is True and status["auto_relay_panes"] == ["pane_2"]

        # Caso A: asignar una tarjeta a pane_2 deja un AVISO en su bandeja (con la asignacion en la cola pull).
        event_bus.publish(
            DomainEvent(
                event_type="kanban.card_assigned",
                project_uuid=proj_uuid,
                payload={
                    "card_id": "CARD-ORCA-1",
                    "target_node_uuid": "node-pane-2",
                    "sender_node_uuid": "node-pane-1",
                },
            )
        )
        assert term.calls == []

        # Caso B: pane_2 esta "working" y el humano teclea: da igual, no hay escritura que diferir.
        term.set_idle("pane_2", False)
        registry.record_human_keystroke("pane_2")
        res_msg = json.loads(
            handler.send_pane_message(
                project_uuid=proj_uuid,
                target_node_uuid="node-pane-2",
                message="Segundo aviso mientras trabajas",
                card_id="CARD-ORCA-2",
                node_uuid="node-pane-1",
                session_token="tok-1",
            )
        )
        assert res_msg["success"] is True
        assert term.calls == []

        pull = json.loads(
            handler.await_next_assignment(project_uuid=proj_uuid, node_uuid="node-pane-2", session_token="tok-2")
        )
        by_kind = {m["kind"]: m for m in pull["messages"]}
        assert set(by_kind) == {"relay_notice", "direct_message"}
        assert "CARD-ORCA-1" in by_kind["relay_notice"]["text"]
        assert by_kind["direct_message"]["text"] == "Segundo aviso mientras trabajas"
        assert by_kind["direct_message"]["from"]["node_uuid"] == "node-pane-1"
        assert pull["has_assignment"] is True
        assert pull["assignment"]["card_id"] == "CARD-ORCA-1"
        assert term.calls == []
    finally:
        handler.close()
        orch.close()
        kernel.close()
