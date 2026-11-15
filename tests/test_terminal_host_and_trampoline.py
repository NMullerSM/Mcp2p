"""
Pruebas de Tarea 4.1: `PosixPtyHostConnector`, `PtyOutputRingBuffer`,
`SafeTerminalHostSupervisor`, trampolín `--pty-exec` y `ApplicationFactory.create_terminal_host`.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

import psutil
import pytest

from src.adapters.outbound.extension_gate import (
    ExtensionSecurityGate,
    NullAgentTerminalConnector,
)
from src.adapters.terminal import (
    CLI_PROFILES,
    PosixPtyHostConnector,
    PtyOutputRingBuffer,
    SafeTerminalHostSupervisor,
    normalize_pane_id,
)
from src.adapters.terminal import pty_host as pty_host_mod
from src.core.factory import ApplicationFactory
from src.core.kernel import CoreKernel
from src.core.ports.connectors import AgentTerminalPort


def _wait_for_output(
    host: Any,
    pane_id: Any,
    substring: str,
    *,
    since_seq: int = 0,
    timeout_s: float = 5.0,
) -> tuple[str, int, Dict[str, Any]]:
    """Acumula deltas de `poll_output` hasta ver `substring` o expirar `timeout_s`."""
    deadline = time.monotonic() + timeout_s
    cursor = since_seq
    chunks: List[str] = []
    last_resp: Dict[str, Any] = {}
    while time.monotonic() < deadline:
        resp = host.poll_output(pane_id, since_seq=cursor)
        last_resp = resp
        cursor = int(resp.get("next_seq", cursor))
        if resp.get("text"):
            chunks.append(resp["text"])
        combined = "".join(chunks)
        if substring in combined:
            return combined, cursor, last_resp
        time.sleep(0.03)
    combined = "".join(chunks)
    raise AssertionError(
        f"Timeout ({timeout_s}s) esperando {substring!r} en {pane_id}; acumulado={combined!r}; last={last_resp!r}"
    )


# =========================================================================
# 1. Fábrica hexagonal y contrato AgentTerminalPort
# =========================================================================


def test_factory_create_terminal_host_and_kernel_integration(tmp_path: Path) -> None:
    host = ApplicationFactory.create_terminal_host()
    try:
        assert isinstance(host, AgentTerminalPort)
        assert host.get_circuit_state() == {"primary": "closed"}
        status = host.get_status()
        assert "available" in status
        assert "mode" in status
        assert "circuit_state" in status
        assert status["max_panes"] == 4
        assert status["sessions"] == []
    finally:
        host.close()

    # Modo sin PTY explícito usa NullAgentTerminalConnector tras el supervisor
    gate = ExtensionSecurityGate()
    null_sup = ApplicationFactory.create_terminal_host(gate=gate, use_pty_host=False)
    try:
        assert isinstance(null_sup, AgentTerminalPort)
        assert null_sup.is_available() is False
        assert null_sup.get_circuit_state() == {"primary": "closed"}
        resp = null_sup.spawn_session(pane_id=1)
        assert resp["success"] is False
        assert resp["degraded"] is True
        assert resp["code"] == "TERMINAL_ADAPTER_DEGRADED"
        assert resp["supervisor_fallback"] is True
        assert resp.get("manual_command")
        assert isinstance(resp.get("mcp_config"), dict)
    finally:
        null_sup.close()

    # CoreKernel desktop enchufa SafeTerminalHostSupervisor sin romper introspección ni self-test
    kernel = ApplicationFactory.create_desktop_kernel(db_path=str(tmp_path / "kernel_term.db"))
    try:
        term = kernel.terminal_connector
        assert isinstance(term, SafeTerminalHostSupervisor)
        assert isinstance(term, AgentTerminalPort)
        self_test = kernel.run_kernel_self_test()
        assert self_test["ok"] is True, self_test
        by_name = {c["name"]: c for c in self_test["connectors"]}
        term_entry = by_name[CoreKernel.PORT_AGENT_TERMINAL]
        assert term_entry["plugged"] is True
        assert term_entry["status"] != "error"
        assert term_entry["details"]["circuit_state"] == {"primary": "closed"}
    finally:
        kernel.close()


# =========================================================================
# 2. PtyOutputRingBuffer
# =========================================================================


def test_ring_buffer_incremental_polling_utf8_and_eviction() -> None:
    buf = PtyOutputRingBuffer(max_bytes=20)
    assert buf.poll_since(0) == {
        "since_seq": 0,
        "next_seq": 0,
        "truncated": False,
        "chunks": [],
        "text": "",
    }

    # Caracter UTF-8 de 4 bytes partido byte a byte debe decodificarse íntegro
    emoji_bytes = "🚀".encode("utf-8")
    for b in emoji_bytes:
        seq = buf.append(bytes([b]))
    assert seq == 4
    res = buf.poll_since(0)
    assert res["text"] == "🚀"
    assert res["next_seq"] == 4
    assert res["truncated"] is False

    # Bytes inválidos UTF-8 se reemplazan con U+FFFD sin lanzar
    s_bad = buf.append(b"\xff\xfe")
    res_bad = buf.poll_since(4)
    assert res_bad["next_seq"] == s_bad
    assert "\ufffd" in res_bad["text"]

    # Desbordamiento de max_bytes expulsa chunks antiguos y reporta truncated=True solo si se perdieron
    s1 = buf.append("1234567890")  # 10 bytes
    s2 = buf.append("ABCDEFGHIJ")  # 10 bytes -> total 20, evicta anteriores
    stale_poll = buf.poll_since(0)
    assert stale_poll["truncated"] is True
    assert "1234567890ABCDEFGHIJ" in stale_poll["text"]

    fresh_poll = buf.poll_since(s1)
    assert fresh_poll["truncated"] is False
    assert fresh_poll["text"] == "ABCDEFGHIJ"
    assert buf.poll_since(s2)["chunks"] == []

    # Chunk individual > max_bytes conserva la cola acotada y marca truncated
    s_big = buf.append(b"x" * 35)
    assert buf.total_bytes <= 20
    big_poll = buf.poll_since(s2)
    assert big_poll["truncated"] is True
    assert len(big_poll["text"]) <= 20
    assert buf.poll_since(s_big)["truncated"] is False


def test_ring_buffer_thread_safety() -> None:
    buf = PtyOutputRingBuffer(max_bytes=4096)
    errors: List[Exception] = []

    def writer(tid: int) -> None:
        try:
            for i in range(50):
                buf.append(f"t{tid}-{i}\n")
        except Exception as exc:
            errors.append(exc)

    def reader() -> None:
        try:
            seq = 0
            for _ in range(100):
                res = buf.poll_since(seq)
                assert res["next_seq"] >= seq
                seq = res["next_seq"]
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
    threads.append(threading.Thread(target=reader))
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)
    assert not errors


# =========================================================================
# 3. Normalización de pane_id, CLI_PROFILES y degradación binarios ausentes
# =========================================================================


def test_pane_id_normalization_and_validation() -> None:
    assert set(CLI_PROFILES.keys()) == {"claude", "gemini", "codex", "opencode", "custom_shell"}

    for raw, expected in [
        (1, ("pane_1", 1)),
        ("1", ("pane_1", 1)),
        ("pane_1", ("pane_1", 1)),
        (4, ("pane_4", 4)),
        ("4", ("pane_4", 4)),
        ("pane_4", ("pane_4", 4)),
    ]:
        assert normalize_pane_id(raw) == expected

    for bad in (0, -1, 5, "0", "5", "pane_0", "pane_5", "pane_x", "", None, True, False):
        with pytest.raises(ValueError):
            normalize_pane_id(bad)

    host = PosixPtyHostConnector(max_panes=4)
    try:
        with pytest.raises(ValueError):
            host.spawn_session(pane_id=5, command=[sys.executable, "-V"])
        with pytest.raises(ValueError):
            host.spawn_session(pane_id=1, cli_profile="desconocido_xyz")

        missing = host.spawn_session(pane_id=1, command=["__binario_inexistente_mcp2p_41__"])
        assert missing["success"] is False
        assert missing["degraded"] is True
        assert missing["code"] == "CLI_BINARY_NOT_FOUND"
        assert missing.get("manual_command")
        assert host.list_sessions() == []
    finally:
        host.close()


# =========================================================================
# 4. POSIX PTY real + trampolín --pty-exec + psutil + resize/pause/close
# =========================================================================


def test_posix_pty_spawn_trampoline_interactive_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "posix" or sys.platform == "win32":
        pytest.skip("Prueba específica de backend POSIX PTY")

    # Simular variables heredables no deseadas en el proceso padre
    monkeypatch.setenv("_PYI_TEST_LEAK", "1")
    monkeypatch.setenv("MCP2P_KEY_PASSPHRASE", "frase_secreta_que_no_debe_filtrarse")

    script = tmp_path / "pty_echo_agent.py"
    script.write_text(
        "\n".join(
            [
                "import fcntl, os, struct, sys, termios",
                "def winsz():",
                "    try:",
                "        r, c, _, _ = struct.unpack('HHHH', fcntl.ioctl(0, termios.TIOCGWINSZ, b'\\x00'*8))",
                "        return f'{c}x{r}'",
                "    except Exception:",
                "        sz = os.get_terminal_size(0)",
                "        return f'{sz.columns}x{sz.lines}'",
                "has_tty = os.isatty(0)",
                "with open('/dev/tty', 'r'):",
                "    tty_open = True",
                "fg_ok = (os.tcgetpgrp(0) == os.getpgid(0))",
                "env_ok = (",
                "    os.environ.get('TERM') == 'xterm-256color'",
                "    and os.environ.get('MCP2P_HUB_URL') == 'http://127.0.0.1:8765/mcp'",
                "    and os.environ.get('MCP2P_PANE_ID') == 'pane_1'",
                "    and os.environ.get('MCP2P_SESSION_TOKEN') == 'tok-pane-1'",
                "    and os.environ.get('MCP2P_NODE_UUID') == 'agent_pane_1'",
                "    and os.environ.get('CUSTOM_PTY_VAR') == '42'",
                "    and '_PYI_TEST_LEAK' not in os.environ",
                "    and 'MCP2P_KEY_PASSPHRASE' not in os.environ",
                ")",
                "print(f'READY:{winsz()}:tty={has_tty and tty_open}:fg={fg_ok}:env={env_ok}', flush=True)",
                "while True:",
                "    line = sys.stdin.readline()",
                "    if not line:",
                "        break",
                "    cmd = line.strip()",
                "    if cmd == 'ping':",
                "        print('PONG_REPLY', flush=True)",
                "    elif cmd == 'size':",
                "        print(f'SIZE:{winsz()}', flush=True)",
                "    elif cmd == 'quit':",
                "        print('BYE_REPLY', flush=True)",
                "        break",
            ]
        ),
        encoding="utf-8",
    )

    host = PosixPtyHostConnector(max_panes=4)
    try:
        spawn = host.spawn_session(
            pane_id=1,
            cli_profile="claude",
            role="implementer",
            command=[sys.executable, "-u", str(script)],
            cwd=str(tmp_path),
            cols=100,
            rows=28,
            hub_url="http://127.0.0.1:8765/mcp",
            session_token="tok-pane-1",
            node_uuid="agent_pane_1",
            env_overrides={"CUSTOM_PTY_VAR": "42"},
            use_trampoline=True,
        )

        # Si el entorno sandbox no permite abrir /dev/ptmx, verificar degradación limpia.
        if not spawn["success"] and spawn.get("code") == "PTY_OS_ERROR":
            assert spawn["degraded"] is True
            assert host.list_sessions() == []
            return

        assert spawn["success"] is True, spawn
        assert spawn["degraded"] is False
        assert spawn["pane_id"] == "pane_1"
        assert spawn["cli_profile"] == "claude"
        assert spawn["role"] == "implementer"
        assert spawn["node_uuid"] == "agent_pane_1"
        pid = int(spawn["pid"])
        assert pid > 0

        # No permitir dos sesiones vivas en el mismo pane_id
        dup = host.spawn_session(pane_id="pane_1", command=[sys.executable, "-c", "import time; time.sleep(1)"])
        assert dup["success"] is False
        assert dup["code"] == "PANE_ALREADY_ACTIVE"

        # 1. Poll inicial incremental y verificación de trampolín + TIOCSCTTY + env
        out1, seq, poll1 = _wait_for_output(host, "1", "READY:100x28:tty=True:fg=True:env=True", since_seq=0)
        assert poll1["alive"] is True
        assert poll1["rss_mb"] >= 0.0
        assert seq > 0

        # 2. send_input (bytes y str) + poll incremental sin repetir salida previa
        inp = host.send_input("pane_1", b"ping\n")
        assert inp["success"] is True
        out2, seq, _ = _wait_for_output(host, 1, "PONG_REPLY", since_seq=seq)
        assert "READY:" not in out2

        # 3. resize_session (TIOCSWINSZ + SIGWINCH)
        rsz = host.resize_session(1, 132, 42)
        assert rsz["success"] is True
        assert rsz["cols"] == 132 and rsz["rows"] == 42
        host.send_input(1, "size\n")
        _, seq, _ = _wait_for_output(host, "pane_1", "SIZE:132x42", since_seq=seq)

        # 4. pause_session (SIGSTOP / SIGCONT) y métricas psutil
        p_on = host.pause_session(1, True)
        assert p_on["success"] is True and p_on["paused"] is True
        sessions = host.list_sessions()
        assert len(sessions) == 1
        s0 = sessions[0]
        assert s0["pane_id"] == "pane_1"
        assert s0["pid"] == pid
        assert s0["alive"] is True
        assert s0["paused"] is True
        assert s0["rss_mb"] >= 0.0
        assert s0["cols"] == 132 and s0["rows"] == 42

        p_off = host.pause_session("pane_1", False)
        assert p_off["success"] is True and p_off["paused"] is False

        status = host.get_status()
        assert status["available"] is True
        assert status["mode"] == "posix_pty"
        assert status["active_sessions"] == 1

        # 5. Salida natural del proceso + reutilización de pane_id + terminate_session
        host.send_input(1, "quit\n")
        _, seq, _ = _wait_for_output(host, 1, "BYE_REPLY", since_seq=seq)
        exit_deadline = time.monotonic() + 2.0
        poll_dead = host.poll_output(1, since_seq=seq)
        while poll_dead.get("alive") and time.monotonic() < exit_deadline:
            time.sleep(0.03)
            poll_dead = host.poll_output(1, since_seq=seq)
        assert poll_dead["alive"] is False
        assert poll_dead["exit_code"] == 0

        # Una sesión muerta permite reutilizar el mismo pane_id
        respawn = host.spawn_session(
            pane_id=1,
            command=[sys.executable, "-c", "import time; print('RESPAWNED', flush=True); time.sleep(10)"],
            use_trampoline=True,
        )
        assert respawn["success"] is True
        pid2 = int(respawn["pid"])
        assert pid2 != pid
        _wait_for_output(host, 1, "RESPAWNED", since_seq=0)

        # 6. close_session / terminate_session limpio (también cuando estaba pausado)
        host.pause_session(1, True)
        closed = host.terminate_session(1)
        assert closed["success"] is True
        assert closed["closed"] is True
        assert host.list_sessions() == []

        # Verificar que el proceso ya no existe vivo en psutil
        deadline = time.monotonic() + 2.0
        while psutil.pid_exists(pid2) and time.monotonic() < deadline:
            try:
                if psutil.Process(pid2).status() == psutil.STATUS_ZOMBIE:
                    break
            except psutil.NoSuchProcess:
                break
            time.sleep(0.02)
        if psutil.pid_exists(pid2):
            try:
                assert psutil.Process(pid2).status() == psutil.STATUS_ZOMBIE
            except psutil.NoSuchProcess:
                pass

        # Cierre idempotente
        closed_again = host.close_session(1)
        assert closed_again["success"] is True
        assert closed_again["already_closed"] is True
    finally:
        host.close()


# =========================================================================
# 5. Degradación no POSIX (win32) y fallo en pty.openpty()
# =========================================================================


def test_non_posix_and_openpty_failure_degrade_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    host = PosixPtyHostConnector(max_panes=4)

    # Simulación win32 (sys.platform y os.name)
    monkeypatch.setattr(pty_host_mod.sys, "platform", "win32")
    assert host.is_available() is False
    win_resp = host.spawn_session(pane_id=1, command=[sys.executable, "-V"])
    assert win_resp["success"] is False
    assert win_resp["degraded"] is True
    assert win_resp["code"] == "PTY_UNAVAILABLE"

    monkeypatch.undo()
    monkeypatch.setattr(pty_host_mod.os, "name", "nt")
    assert host.is_available() is False
    nt_resp = host.spawn_session(pane_id=1, command=[sys.executable, "-V"])
    assert nt_resp["success"] is False
    assert nt_resp["degraded"] is True
    assert nt_resp["code"] == "PTY_UNAVAILABLE"

    # Restaurar plataforma y simular fallo de pty.openpty()
    monkeypatch.undo()
    if not host.is_available() or pty_host_mod.pty is None:
        pytest.skip("Solo aplicable en POSIX con módulo pty")

    def _fail_openpty() -> tuple[int, int]:
        raise OSError("Sin dispositivos PTY disponibles")

    monkeypatch.setattr(pty_host_mod.pty, "openpty", _fail_openpty)
    err_resp = host.spawn_session(pane_id=1, command=[sys.executable, "-V"])
    assert err_resp["success"] is False
    assert err_resp["degraded"] is True
    assert err_resp["code"] == "PTY_OS_ERROR"
    assert err_resp["host_failure"] is True
    assert host.list_sessions() == []


# =========================================================================
# 6. SafeTerminalHostSupervisor: Circuit Breaker + Fallback + Recovery
# =========================================================================


class _StubTerminalPrimary:
    def __init__(self) -> None:
        self.available = True
        self.fail_with: Exception | None = None
        self.calls = 0
        self.closed = False

    def is_available(self) -> bool:
        return self.available

    def get_status(self) -> Dict[str, Any]:
        return {
            "available": self.available,
            "mode": "stub_pty",
            "active_sessions": 0,
            "max_panes": 4,
            "sessions": [],
        }

    def list_sessions(self) -> List[Dict[str, Any]]:
        self.calls += 1
        if self.fail_with is not None:
            raise self.fail_with
        return []

    def spawn_session(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        self.calls += 1
        if self.fail_with is not None:
            raise self.fail_with
        return {"success": True, "degraded": False, "pane_id": "pane_1", "pid": 9999}

    def send_input(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        self.calls += 1
        if self.fail_with is not None:
            raise self.fail_with
        return {"success": True}

    def resize_session(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return {"success": True}

    def pause_session(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return {"success": True}

    def poll_output(self, pane_id: Any = 1, since_seq: int = 0) -> Dict[str, Any]:
        self.calls += 1
        if self.fail_with is not None:
            raise self.fail_with
        return {"success": True, "since_seq": since_seq, "next_seq": since_seq, "truncated": False, "chunks": [], "text": ""}

    def close_session(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return {"success": True}

    def terminate_session(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return {"success": True}

    def close(self) -> None:
        self.closed = True


def test_supervisor_circuit_breaker_opens_falls_back_and_recovers() -> None:
    now = [100.0]
    primary = _StubTerminalPrimary()
    fallback = NullAgentTerminalConnector()
    sup = SafeTerminalHostSupervisor(
        primary,
        fallback,
        failure_threshold=3,
        cooldown_s=15.0,
        clock=lambda: now[0],
    )

    assert sup.is_available() is True
    assert sup.get_circuit_state() == {"primary": "closed"}

    # 3 fallos consecutivos abren el circuito sin propagar excepción
    primary.fail_with = OSError("PTY backend roto")
    for idx in range(3):
        resp = sup.spawn_session(pane_id=1)
        assert resp["success"] is False
        assert resp["degraded"] is True
        assert resp["code"] == "TERMINAL_ADAPTER_DEGRADED"
        assert resp["supervisor_fallback"] is True
        assert resp.get("manual_command")
        assert isinstance(resp.get("mcp_config"), dict)
        expected_state = "open" if idx == 2 else "closed"
        assert resp["circuit_state"] == expected_state

    assert sup.get_circuit_state() == {"primary": "open"}
    assert sup.is_available() is False
    st_open = sup.get_status()
    assert st_open["degraded"] is True
    assert st_open["available"] is False
    assert st_open["circuit"]["consecutive_failures"] == 3

    # Con el circuito abierto no se invoca al primario
    calls_at_open = primary.calls
    poll_fb = sup.poll_output(1, since_seq=0)
    assert primary.calls == calls_at_open
    assert poll_fb["code"] == "TERMINAL_ADAPTER_DEGRADED"
    assert poll_fb["supervisor_fallback"] is True
    assert poll_fb["chunks"] == []

    # Al vencer cooldown_s pasa a half_open; un fallo reabre inmediatamente
    now[0] += 16.0
    assert sup.get_circuit_state() == {"primary": "half_open"}
    reopen = sup.send_input(1, "ls\n")
    assert reopen["code"] == "TERMINAL_ADAPTER_DEGRADED"
    assert reopen["circuit_state"] == "open"
    assert sup.get_circuit_state() == {"primary": "open"}

    # Nuevo cooldown + éxito en half_open cierra el circuito
    now[0] += 16.0
    assert sup.get_circuit_state() == {"primary": "half_open"}
    primary.fail_with = None
    recovered = sup.spawn_session(pane_id=1)
    assert recovered["success"] is True
    assert recovered["degraded"] is False
    assert sup.get_circuit_state() == {"primary": "closed"}
    assert sup.is_available() is True

    sup.close()
    assert primary.closed is True


def test_supervisor_with_real_host_distinguishes_missing_binary_from_pty_os_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not PosixPtyHostConnector.is_available() or pty_host_mod.pty is None:
        pytest.skip("Solo aplicable en POSIX con módulo pty")

    host = PosixPtyHostConnector(max_panes=4)
    sup = SafeTerminalHostSupervisor(
        host,
        NullAgentTerminalConnector(),
        failure_threshold=2,
        cooldown_s=10.0,
    )
    try:
        # Binario inexistente: error controlado, NO abre el circuit breaker
        for _ in range(3):
            res = sup.spawn_session(pane_id=1, command=["__cli_ausente_mcp2p__"])
            assert res["code"] == "CLI_BINARY_NOT_FOUND"
            assert "supervisor_fallback" not in res
        assert sup.get_circuit_state() == {"primary": "closed"}

        # OSError real en pty.openpty(): SÍ cuenta como fallo de circuito y cae a NullAgentTerminalConnector
        def _boom_openpty() -> tuple[int, int]:
            raise OSError("EMFILE simulation")

        monkeypatch.setattr(pty_host_mod.pty, "openpty", _boom_openpty)
        r1 = sup.spawn_session(pane_id=1, command=[sys.executable, "-V"])
        assert r1["code"] == "TERMINAL_ADAPTER_DEGRADED"
        assert r1["supervisor_fallback"] is True
        assert sup.get_circuit_state() == {"primary": "closed"}

        r2 = sup.spawn_session(pane_id=1, command=[sys.executable, "-V"])
        assert r2["code"] == "TERMINAL_ADAPTER_DEGRADED"
        assert r2["supervisor_fallback"] is True
        assert sup.get_circuit_state() == {"primary": "open"}
    finally:
        sup.close()


def test_core_kernel_close_terminates_active_pty_sessions(tmp_path: Path) -> None:
    if not PosixPtyHostConnector.is_available():
        pytest.skip("Solo aplicable en POSIX")

    kernel = ApplicationFactory.create_desktop_kernel(db_path=str(tmp_path / "kernel_close.db"))
    term = kernel.terminal_connector
    spawn = term.spawn_session(
        pane_id=2,
        command=[sys.executable, "-c", "import time; print('KERNEL_PTY_UP', flush=True); time.sleep(30)"],
        use_trampoline=True,
    )
    if not spawn["success"] and spawn.get("code") == "TERMINAL_ADAPTER_DEGRADED":
        kernel.close()
        return

    assert spawn["success"] is True, spawn
    pid = int(spawn["pid"])
    try:
        _wait_for_output(term, 2, "KERNEL_PTY_UP", since_seq=0)
        assert len(term.list_sessions()) == 1
    finally:
        kernel.close()

    assert term.list_sessions() == []
    deadline = time.monotonic() + 2.0
    while psutil.pid_exists(pid) and time.monotonic() < deadline:
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                break
        except psutil.NoSuchProcess:
            break
        time.sleep(0.02)
    if psutil.pid_exists(pid):
        try:
            assert psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            pass
