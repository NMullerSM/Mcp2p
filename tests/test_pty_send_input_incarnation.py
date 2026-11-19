"""
WP-6b: `send_input(..., expected_session_id=...)` rechaza escribir si la encarnacion del PTY cambio.

- POSIX: PTY real. Un hijo Python vuelca stdin a un archivo; se cierra y se reabre el mismo pane
  para obtener otro session_id y comprobar que nada llega al hijo con el id viejo.
- winpty: doble `FakePTY` de `tests/test_winpty_host.py` (corre en Linux). No hay ConPTY real aqui.
"""

from __future__ import annotations

import inspect
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict

import pytest

from src.adapters.terminal.pty_host import PosixPtyHostConnector
from src.adapters.terminal.winpty_host import WinPtyHostConnector
from tests.test_winpty_host import FakeJob, FakePTY, _host as _winpty_host

posix_only = pytest.mark.skipif(
    os.name != "posix" or sys.platform == "win32", reason="PTY POSIX real (no aplica en Windows)"
)
winpty_only = pytest.mark.skipif(sys.platform == "win32", reason="dobles de winpty; en Windows corre test_winpty_real_windows")

_DUMP_SCRIPT = (
    "import sys\n"
    "path = sys.argv[1]\n"
    "print('READY', flush=True)\n"
    "with open(path, 'a', encoding='utf-8') as fh:\n"
    "    for line in sys.stdin:\n"
    "        fh.write(line)\n"
    "        fh.flush()\n"
)


def _poll_until(host: Any, pane: Any, needle: str, timeout: float = 5.0) -> str:
    seen = ""
    cursor = 0
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        res = host.poll_output(pane, since_seq=cursor)
        cursor = int(res.get("next_seq", cursor))
        seen += res.get("text", "")
        if needle in seen:
            return seen
        time.sleep(0.02)
    raise AssertionError(f"no aparecio {needle!r}; visto: {seen!r}")


def _wait_file_contains(path: Path, needle: str, timeout: float = 5.0) -> str:
    deadline = time.monotonic() + timeout
    text = ""
    while time.monotonic() < deadline:
        if path.exists():
            text = path.read_text(encoding="utf-8")
            if needle in text:
                return text
        time.sleep(0.02)
    raise AssertionError(f"el archivo no recibio {needle!r}; contenido: {text!r}")


def _spawn_dump(host: PosixPtyHostConnector, out: Path) -> Dict[str, Any]:
    res = host.spawn_session(pane_id=1, command=[sys.executable, "-u", "-c", _DUMP_SCRIPT, str(out)])
    if not res["success"] and res.get("code") == "PTY_OS_ERROR":
        pytest.skip(f"el entorno no permite abrir PTY: {res}")
    assert res["success"] is True, res
    _poll_until(host, 1, "READY")
    return res


# -- POSIX (PTY real) ---------------------------------------------------------------------------


@posix_only
def test_send_input_without_expected_session_id_writes(tmp_path: Path) -> None:
    out = tmp_path / "stdin.txt"
    host = PosixPtyHostConnector(max_panes=4)
    try:
        _spawn_dump(host, out)
        text = "SIN_ID_OK\n"
        res = host.send_input(1, text)
        assert res["success"] is True, res
        assert res["bytes_written"] == len(text.encode("utf-8"))
        # Forma de exito sin cambios: no aparece ningun campo nuevo.
        assert set(res) == {"success", "pane_id", "session_id", "bytes_written"}
        _wait_file_contains(out, "SIN_ID_OK")
    finally:
        host.close()


@posix_only
def test_send_input_with_matching_session_id_writes(tmp_path: Path) -> None:
    out = tmp_path / "stdin.txt"
    host = PosixPtyHostConnector(max_panes=4)
    try:
        spawn = _spawn_dump(host, out)
        res = host.send_input(1, "CON_ID_OK\n", expected_session_id=spawn["session_id"])
        assert res["success"] is True, res
        assert res["session_id"] == spawn["session_id"]
        assert res["bytes_written"] == len("CON_ID_OK\n")
        _wait_file_contains(out, "CON_ID_OK")
    finally:
        host.close()


@posix_only
def test_send_input_with_stale_session_id_returns_incarnation_changed_and_writes_nothing(tmp_path: Path) -> None:
    out = tmp_path / "stdin.txt"
    host = PosixPtyHostConnector(max_panes=4)
    try:
        old = _spawn_dump(host, out)
        assert host.close_session(1)["success"] is True
        new = _spawn_dump(host, out)
        assert new["session_id"] != old["session_id"]

        last_before = host._sessions["pane_1"].last_input_at
        res = host.send_input(1, "STALE_NO_DEBE_LLEGAR\n", expected_session_id=old["session_id"])
        assert res == {
            "success": False,
            "code": "INCARNATION_CHANGED",
            "pane_id": "pane_1",
            "session_id": new["session_id"],
            "message": "La sesión del panel cambió; no se escribió nada.",
        }
        assert host._sessions["pane_1"].last_input_at == last_before

        # Prueba de vida: la sesion nueva sigue recibiendo entrada con su id correcto.
        ok = host.send_input(1, "FRESCO_OK\n", expected_session_id=new["session_id"])
        assert ok["success"] is True, ok
        content = _wait_file_contains(out, "FRESCO_OK")
        assert "STALE_NO_DEBE_LLEGAR" not in content
    finally:
        host.close()


@posix_only
def test_send_input_with_expected_id_on_dead_session_keeps_not_alive_code(tmp_path: Path) -> None:
    out = tmp_path / "stdin.txt"
    host = PosixPtyHostConnector(max_panes=4)
    try:
        spawn = _spawn_dump(host, out)
        session = host._sessions["pane_1"]
        session.process.kill()
        session.process.wait(timeout=5)
        res = host.send_input(1, "NO_DEBE_LLEGAR\n", expected_session_id=spawn["session_id"])
        assert res["success"] is False
        assert res["code"] == "SESSION_NOT_ALIVE"
    finally:
        host.close()


def test_stale_id_check_happens_under_the_write_lock_posix() -> None:
    # Inspeccion de fuente: no necesita PTY, corre en cualquier plataforma.
    src = inspect.getsource(PosixPtyHostConnector.send_input)
    lock_line = next(line for line in src.splitlines() if "with session.fd_lock:" in line)
    guard_line = next(line for line in src.splitlines() if "expected_session_id is not None and" in line)
    lock_at = src.index("with session.fd_lock:")
    guard_at = src.index("expected_session_id is not None and")
    write_at = src.index("os.write(fd")
    assert lock_at < guard_at < write_at
    # La guarda va dentro del bloque `with` (mas indentada que la linea del cerrojo).
    indent = lambda s: len(s) - len(s.lstrip())  # noqa: E731
    assert indent(guard_line) > indent(lock_line)


# -- winpty (doble de FakePTY) -------------------------------------------------------------------


@pytest.fixture
def _winpty_fakes_reset() -> Any:
    FakePTY.created = []
    FakePTY.fail_ctor = None
    FakePTY.spawn_returns = True
    FakePTY.spawn_hook = None
    FakeJob.instances = []
    FakeJob.fail_assign = False
    FakeJob.effective = True
    yield


@winpty_only
def test_winpty_send_input_without_expected_session_id_writes(tmp_path: Path, _winpty_fakes_reset: Any) -> None:
    host = _winpty_host(tmp_path)
    try:
        assert host.spawn_session(1, command=["cmd"])["success"]
        pty = FakePTY.created[-1]
        res = host.send_input(1, "SIN_ID\r")
        assert res["success"] is True and res["bytes_written"] == 7
        assert set(res) == {"success", "pane_id", "session_id", "bytes_written"}
        assert pty.writes == ["SIN_ID\r"]
    finally:
        host.close()


@winpty_only
def test_winpty_send_input_with_matching_session_id_writes(tmp_path: Path, _winpty_fakes_reset: Any) -> None:
    host = _winpty_host(tmp_path)
    try:
        spawn = host.spawn_session(1, command=["cmd"])
        assert spawn["success"]
        pty = FakePTY.created[-1]
        res = host.send_input(1, "CON_ID\r", expected_session_id=spawn["session_id"])
        assert res["success"] is True, res
        assert pty.writes == ["CON_ID\r"]
    finally:
        host.close()


@winpty_only
def test_winpty_send_input_with_stale_session_id_returns_incarnation_changed_and_writes_nothing(
    tmp_path: Path, _winpty_fakes_reset: Any
) -> None:
    host = _winpty_host(tmp_path)
    try:
        old = host.spawn_session(1, command=["cmd"])
        assert old["success"]
        assert host.close_session(1)["success"] is True
        new = host.spawn_session(1, command=["cmd"])
        assert new["success"]
        assert new["session_id"] != old["session_id"]
        new_pty = FakePTY.created[-1]

        last_before = host._sessions["pane_1"].last_input_at
        res = host.send_input(1, "STALE\r", expected_session_id=old["session_id"])
        assert res == {
            "success": False,
            "code": "INCARNATION_CHANGED",
            "pane_id": "pane_1",
            "session_id": new["session_id"],
            "message": "La sesión del panel cambió; no se escribió nada.",
        }
        assert new_pty.writes == []
        assert host._sessions["pane_1"].last_input_at == last_before
    finally:
        host.close()


def test_stale_id_check_happens_under_the_write_lock_winpty() -> None:
    src = inspect.getsource(WinPtyHostConnector.send_input)
    lock_line = next(line for line in src.splitlines() if "with session.io_lock:" in line)
    guard_line = next(line for line in src.splitlines() if "expected_session_id is not None and" in line)
    lock_at = src.index("with session.io_lock:")
    guard_at = src.index("expected_session_id is not None and")
    write_at = src.index("pty.write(text)")
    assert lock_at < guard_at < write_at
    indent = lambda s: len(s) - len(s.lstrip())  # noqa: E731
    assert indent(guard_line) > indent(lock_line)
