"""
`WinPtyHostConnector` con un doble de `winpty` (corren en Linux): ciclo de vida, ring buffer, errores, limites,
cierre sin huerfanos (arbol de procesos REAL con psutil), pausa, resolucion PATHEXT simulada y entorno del panel.
Las pruebas con ConPTY real estan en `test_winpty_real_windows.py` (solo `win32`).
"""

from __future__ import annotations

import queue
import subprocess
import sys
import threading
import time
import types
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil
import pytest

from src.adapters.terminal.cli_catalog import CliCatalogService
from src.adapters.terminal.mcp_injection import PaneMcpConfigStore
from src.adapters.terminal.win_job import JobObjectError
from src.adapters.terminal.winpty_host import WinPtyHostConnector

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="dobles de winpty; en Windows corre test_winpty_real_windows")


class FakeWinptyError(Exception):
    pass


class FakePTY:
    """Imita la API de bajo nivel de `winpty.PTY` (read no bloqueante, spawn con appname/cmdline/cwd/env)."""

    created: List["FakePTY"] = []
    fail_ctor: Optional[Exception] = None
    spawn_returns = True
    next_pid = 91000
    spawn_hook: Any = None

    def __init__(self, cols: int, rows: int, backend: Optional[int] = None, mouse_mode: int = 0,
                 timeout: int = 30000, agent_config: int = 4) -> None:
        if FakePTY.fail_ctor is not None:
            raise FakePTY.fail_ctor
        self.cols, self.rows, self.backend = cols, rows, backend
        self.out: "queue.Queue[str]" = queue.Queue()
        self.writes: List[str] = []
        self.sizes: List[tuple] = []
        self.spawn_args: Dict[str, Any] = {}
        self.read_error: Optional[Exception] = None
        self.echo = False
        self._alive = False
        self._exit: Optional[int] = None
        self._eof = False
        self._pid: Optional[int] = None
        self.proc: Optional[subprocess.Popen] = None
        FakePTY.created.append(self)

    def spawn(self, appname: str, cmdline: Optional[str] = None, cwd: Optional[str] = None,
              env: Optional[str] = None) -> bool:
        self.spawn_args = {"appname": appname, "cmdline": cmdline, "cwd": cwd, "env": env}
        if not FakePTY.spawn_returns:
            return False
        self._alive = True
        FakePTY.next_pid += 1
        self._pid = FakePTY.next_pid
        if FakePTY.spawn_hook is not None:
            self.proc = FakePTY.spawn_hook()
            self._pid = self.proc.pid
        return True

    @property
    def pid(self) -> Optional[int]:
        return self._pid

    def read(self, blocking: bool = False) -> str:
        if self.read_error is not None:
            raise self.read_error
        try:
            return self.out.get_nowait()
        except queue.Empty:
            if self._eof:
                raise FakeWinptyError("Standard out reached EOF")
            return ""

    def write(self, text: str) -> int:
        if not self.isalive():
            raise FakeWinptyError("write on dead pty")
        self.writes.append(text)
        if self.echo:
            self.out.put(text)
        return len(text)

    def set_size(self, cols: int, rows: int) -> None:
        self.sizes.append((cols, rows))
        self.cols, self.rows = cols, rows

    def isalive(self) -> bool:
        if self.proc is not None:
            return self.proc.poll() is None
        return self._alive

    def get_exitstatus(self) -> Optional[int]:
        if self.proc is not None:
            return self.proc.poll()
        return self._exit

    def iseof(self) -> bool:
        return self._eof

    def cancel_io(self) -> bool:
        return True

    def feed(self, text: str) -> None:
        self.out.put(text)

    def die(self, code: int = 0) -> None:
        self._alive = False
        self._exit = code
        self._eof = True


class FakeJob:
    instances: List["FakeJob"] = []
    fail_assign = False
    effective = True

    def __init__(self) -> None:
        self.assigned: List[int] = []
        self.terminated = 0
        self.closed = False
        self.pty = FakePTY.created[-1]  # el job se crea justo tras el spawn del PTY al que pertenece
        FakeJob.instances.append(self)

    def assign_pid(self, pid: int) -> None:
        if FakeJob.fail_assign:
            raise JobObjectError("acceso denegado")
        self.assigned.append(pid)

    def terminate(self, exit_code: int = 1) -> bool:
        self.terminated += 1
        if not FakeJob.effective:
            return False
        pty = self.pty
        if pty.proc is not None:
            for child in psutil.Process(pty.proc.pid).children(recursive=True):
                child.kill()
            pty.proc.kill()
            pty.proc.wait()
        else:
            pty.die(exit_code)
        return True

    def close(self) -> None:
        self.closed = True


FAKE_FILES = {
    r"c:\windows\system32\cmd.exe",
    r"c:\windows\system32\ping.exe",
    r"c:\npm\claude.cmd",
    r"c:\npm\claude.ps1",
    r"c:\tools\mi herramienta\tool.exe",
}
ENV = {
    "PATH": r"C:\Windows\System32;C:\npm",
    "PATHEXT": ".COM;.EXE;.BAT;.CMD",
    "COMSPEC": r"C:\Windows\System32\cmd.exe",
}


@pytest.fixture(autouse=True)
def _reset_fakes() -> Any:
    FakePTY.created = []
    FakePTY.fail_ctor = None
    FakePTY.spawn_returns = True
    FakePTY.spawn_hook = None
    FakeJob.instances = []
    FakeJob.fail_assign = False
    FakeJob.effective = True
    yield


def _module() -> Any:
    return types.SimpleNamespace(PTY=FakePTY, Backend=types.SimpleNamespace(ConPTY=0, WinPTY=1))


def _host(tmp_path: Path, *, job_factory: Any = FakeJob, **kwargs: Any) -> WinPtyHostConnector:
    kwargs.setdefault("max_panes", 4)
    kwargs.setdefault("memory_probe", lambda: 10 ** 6)
    return WinPtyHostConnector(
        winpty_module=_module(),
        job_factory=job_factory,
        is_file=lambda p: p.lower() in FAKE_FILES,
        environ=ENV,
        mcp_config_store=PaneMcpConfigStore(tmp_path / "mcp"),
        catalog_service=CliCatalogService(
            include_user_catalog=False, platform="win32", environ=ENV, home=str(tmp_path), which=lambda n: None
        ),
        **kwargs,
    )


def _wait(pred: Any, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(0.01)
    raise AssertionError("condicion no cumplida a tiempo")


def _poll_text(host: Any, pane: Any, needle: str, timeout: float = 5.0) -> str:
    seen = ""
    cursor = 0
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        res = host.poll_output(pane, since_seq=cursor)
        cursor = res["next_seq"]
        seen += res["text"]
        if needle in seen:
            return seen
        time.sleep(0.01)
    raise AssertionError(f"no aparecio {needle!r}; visto: {seen!r}")


def _spawn_real_tree() -> subprocess.Popen:
    """Proceso raiz que lanza un nieto de larga vida (el arbol que un panel no debe dejar huerfano)."""
    code = (
        "import subprocess,sys,time;"
        "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)']);"
        "print(p.pid,flush=True);time.sleep(120)"
    )
    return subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)


# -- disponibilidad -------------------------------------------------------------------------------------


def test_is_available_is_false_off_windows_and_instance_override_with_double() -> None:
    assert WinPtyHostConnector.is_available() is False
    host = WinPtyHostConnector(winpty_module=_module())
    assert host.is_available() is True
    assert host.get_status()["mode"] == "windows_conpty"


def test_without_winpty_the_connector_degrades_with_a_clear_message(tmp_path: Path) -> None:
    host = WinPtyHostConnector(mcp_config_store=PaneMcpConfigStore(tmp_path / "m"))
    res = host.spawn_session(1, command=["cmd"])
    assert res["success"] is False and res["degraded"] is True
    assert res["code"] == "PTY_UNAVAILABLE" and "Windows" in res["message"]


# -- ciclo de vida --------------------------------------------------------------------------------------


def test_spawn_forces_conpty_and_passes_launch_parameters(tmp_path: Path) -> None:
    host = _host(tmp_path)
    work = tmp_path / "trabajo"
    work.mkdir()
    try:
        res = host.spawn_session(
            1, command=["cmd.exe", "/c", "echo hola"], cwd=work, cols=120, rows=30,
            env_overrides={"MI_VAR": "x"}, hub_url="http://127.0.0.1:8766/mcp", session_token="tok-secreto",
            node_uuid="node-1",
        )
        assert res["success"] is True and res["backend"] == "conpty" and res["tree_kill"] == "job_object"
        assert "tok-secreto" not in repr(res)
        pty = FakePTY.created[-1]
        assert pty.backend == 0 and (pty.cols, pty.rows) == (120, 30)
        args = pty.spawn_args
        assert args["appname"] == r"C:\Windows\System32\cmd.exe"
        assert args["cmdline"] == '/c "echo hola"'
        assert args["cwd"] == str(work)
        env = dict(item.split("=", 1) for item in args["env"].split("\x00") if item)
        assert env["MCP2P_PANE_ID"] == "pane_1" and env["MI_VAR"] == "x" and env["TERM"] == "xterm-256color"
        assert env["MCP2P_HUB_URL"] == "http://127.0.0.1:8766/mcp" and env["MCP2P_SESSION_TOKEN"] == "tok-secreto"
        assert env["MCP2P_NODE_UUID"] == "node-1"
        assert list(env) == sorted(env, key=str.upper)
        assert FakeJob.instances[-1].assigned == [res["pid"]]
    finally:
        host.close()


def test_spawn_session_string_command_and_quoted_path_with_spaces(tmp_path: Path) -> None:
    host = _host(tmp_path)
    try:
        res = host.spawn_session(1, command='"C:\\Tools\\mi herramienta\\tool.exe" --flag "dos palabras"')
        assert res["success"] is True, res
        args = FakePTY.created[-1].spawn_args
        assert args["appname"] == r"C:\Tools\mi herramienta\tool.exe"
        assert args["cmdline"] == '--flag "dos palabras"'
    finally:
        host.close()


def test_npm_cmd_shim_runs_through_cmd_exe_and_ps1_only_as_last_resort(tmp_path: Path) -> None:
    host = _host(tmp_path)
    try:
        res = host.spawn_session(
            1, command=["claude", "--mcp-config", r"C:\Users\Nombre Apellido\cfg.json", "-c", 'mcp.url="http://x"']
        )
        assert res["success"] is True, res
        args = FakePTY.created[-1].spawn_args
        assert args["appname"] == r"C:\Windows\System32\cmd.exe"
        assert args["cmdline"].startswith('/d /s /c "C:\\npm\\claude.cmd --mcp-config ')
        assert r'"C:\Users\Nombre Apellido\cfg.json"' in args["cmdline"]
        assert args["cmdline"].endswith('"')
    finally:
        host.close()


def test_cmd_script_rejects_shell_metacharacters_in_arguments(tmp_path: Path) -> None:
    host = _host(tmp_path)
    try:
        for bad in ("a & calc", "a|b", "x>y", "%PATH%", "a^b", "hola!"):
            res = host.spawn_session(1, command=["claude", bad])
            assert res["success"] is False and res["code"] == "CLI_ARG_UNSAFE", (bad, res)
        assert FakePTY.created == []
    finally:
        host.close()


def test_output_is_drained_continuously_into_the_bounded_ring_buffer(tmp_path: Path) -> None:
    host = _host(tmp_path, ring_max_bytes=64)
    try:
        assert host.spawn_session(1, command=["cmd"])["success"]
        pty = FakePTY.created[-1]
        pty.feed("hola ñandú ✓\r\n")
        text = _poll_text(host, 1, "ñandú ✓")
        assert "\ufffd" not in text
        for i in range(50):
            pty.feed(f"linea {i:03d} " + "x" * 20 + "\r\n")
        _wait(lambda: pty.out.empty())
        res = host.poll_output(1, since_seq=0)
        assert res["truncated"] is True and len(res["text"].encode("utf-8")) <= 64
        assert res["alive"] is True and res["paused"] is False and res["success"] is True
    finally:
        host.close()


def test_send_input_and_resize_reach_the_pty(tmp_path: Path) -> None:
    host = _host(tmp_path)
    try:
        assert host.spawn_session(1, command=["cmd"])["success"]
        pty = FakePTY.created[-1]
        pty.echo = True
        res = host.send_input(1, "dir\r")
        assert res["success"] and res["bytes_written"] == 4
        assert host.send_input(1, "ñ".encode("utf-8"))["bytes_written"] == 2
        assert pty.writes == ["dir\r", "ñ"]
        assert "dir" in _poll_text(host, 1, "dir")
        rz = host.resize_session(1, 90, 33)
        assert rz == {"success": True, "pane_id": "pane_1", "session_id": rz["session_id"], "cols": 90, "rows": 33}
        assert pty.sizes == [(90, 33)]
        assert host.list_sessions()[0]["cols"] == 90
        with pytest.raises(ValueError):
            host.resize_session(1, 0, 10)
        with pytest.raises(ValueError):
            host.send_input(1, 123)  # type: ignore[arg-type]
    finally:
        host.close()


def test_io_on_unknown_or_dead_sessions_uses_posix_error_codes(tmp_path: Path) -> None:
    host = _host(tmp_path)
    try:
        for fn in (lambda: host.send_input(2, "x"), lambda: host.resize_session(2, 80, 24),
                   lambda: host.pause_session(2), lambda: host.poll_output(2)):
            res = fn()
            assert res["success"] is False and res["code"] == "SESSION_NOT_FOUND"
        assert host.close_session(2) == {"success": True, "pane_id": "pane_2", "closed": False, "already_closed": True}

        assert host.spawn_session(2, command=["cmd"])["success"]
        FakePTY.created[-1].die(3)
        _wait(lambda: host.poll_output(2)["alive"] is False)
        for res in (host.send_input(2, "x"), host.resize_session(2, 80, 24), host.pause_session(2)):
            assert res["success"] is False and res["code"] == "SESSION_NOT_ALIVE" and res["exit_code"] == 3
        poll = host.poll_output(2)
        assert poll["alive"] is False and poll["exit_code"] == 3 and poll["activity_state"] == "exited"
    finally:
        host.close()


def test_output_in_flight_when_the_process_exits_is_not_lost(tmp_path: Path) -> None:
    host = _host(tmp_path)
    try:
        assert host.spawn_session(1, command=["cmd"])["success"]
        pty = FakePTY.created[-1]
        pty.feed("ultima linea\r\n")
        pty.die(0)
        assert "ultima linea" in _poll_text(host, 1, "ultima linea")
        _wait(lambda: host._sessions["pane_1"].reader_thread is not None and not host._sessions["pane_1"].reader_thread.is_alive())
        assert host._sessions["pane_1"].pty is None  # el lector libero el PTY (ClosePseudoConsole fuera de close_session)
    finally:
        host.close()


def test_reader_error_while_alive_is_reported_for_diagnosis(tmp_path: Path) -> None:
    host = _host(tmp_path)
    try:
        assert host.spawn_session(1, command=["cmd"])["success"]
        FakePTY.created[-1].read_error = FakeWinptyError("pipe rota")
        _wait(lambda: "reader_error" in host.poll_output(1))
        assert "pipe rota" in host.poll_output(1)["reader_error"]
        assert "pipe rota" in host.list_sessions()[0]["reader_error"]
    finally:
        host.close()


# -- errores y limites ----------------------------------------------------------------------------------------


def test_error_codes_match_the_posix_connector(tmp_path: Path) -> None:
    host = _host(tmp_path)
    try:
        r = host.spawn_session(1, command=["cmd"], cwd=tmp_path / "no-existe")
        assert r["code"] == "INVALID_CWD" and r["success"] is False
        r = host.spawn_session(1, command=["programa-que-no-existe"])
        assert r["code"] == "CLI_BINARY_NOT_FOUND" and r["missing_binary"] == "programa-que-no-existe"
        r = host.spawn_session(1, cli_profile="no_existe")
        assert r["code"] == "CLI_PROFILE_UNKNOWN" and "claude" in r["known_profiles"]
        r = host.spawn_session(1, command=["cmd"], resume=True)
        assert r["code"] == "RESUME_NOT_AVAILABLE"
        r = host.spawn_session(7, command=["cmd"])
        assert r["code"] == "PANE_LIMIT_REACHED" and r["reason"] == "pane_out_of_range"
        with pytest.raises(ValueError):
            host.spawn_session(1, command=["cmd"], cols=0)
        with pytest.raises(ValueError):
            host.spawn_session(0, command=["cmd"])
        assert FakePTY.created == []
    finally:
        host.close()


def test_pane_already_active_pane_limit_and_low_memory(tmp_path: Path) -> None:
    mem = {"mb": 10 ** 6}
    host = _host(tmp_path, max_panes=2, memory_probe=lambda: mem["mb"], min_available_mb=1024)
    try:
        first = host.spawn_session(1, command=["cmd"])
        assert first["success"]
        dup = host.spawn_session(1, command=["cmd"])
        assert dup["code"] == "PANE_ALREADY_ACTIVE" and dup["pid"] == first["pid"]
        assert host.spawn_session(2, command=["cmd"])["success"]
        assert host.spawn_session(3, command=["cmd"])["code"] == "PANE_LIMIT_REACHED"
        assert host.spawn_session(3, command=["cmd"])["reason"] == "pane_out_of_range"
        host.close_session(2)
        mem["mb"] = 10
        low = host.spawn_session(2, command=["cmd"])
        assert low["code"] == "PANE_LIMIT_REACHED" and low["reason"] == "low_memory"
        assert host.set_max_panes(4) == 4
        mem["mb"] = 10 ** 6
        assert host.spawn_session(3, command=["cmd"])["success"]
    finally:
        host.close()


def test_four_panes_at_once_and_close_all(tmp_path: Path) -> None:
    host = _host(tmp_path)
    try:
        for pane in (1, 2, 3, 4):
            assert host.spawn_session(pane, command=["cmd"], cols=80 + pane)["success"]
        status = host.get_status()
        assert status["active_sessions"] == 4 and len({s["session_id"] for s in status["sessions"]}) == 4
        assert host.spawn_session(5, command=["cmd"])["code"] == "PANE_LIMIT_REACHED"
    finally:
        host.close()
    assert host.list_sessions() == []
    assert all(job.closed for job in FakeJob.instances) and len(FakeJob.instances) == 4


def test_conpty_constructor_or_spawn_failure_is_a_host_failure_without_leaving_a_session(tmp_path: Path) -> None:
    host = _host(tmp_path)
    try:
        FakePTY.fail_ctor = FakeWinptyError("ConPTY no disponible")
        r = host.spawn_session(1, command=["cmd"])
        assert r["code"] == "PTY_OS_ERROR" and r["host_failure"] is True and "ConPTY" in r["message"]
        assert r["launch"]["appname"].lower().endswith("cmd.exe") and "FakeWinptyError" in r["error"]
        FakePTY.fail_ctor = None
        FakePTY.spawn_returns = False
        assert host.spawn_session(1, command=["cmd"])["code"] == "PTY_OS_ERROR"
        assert host.list_sessions() == []
    finally:
        host.close()


# -- cierre sin huerfanos -------------------------------------------------------------------------------------


def test_close_terminates_the_job_and_releases_everything(tmp_path: Path) -> None:
    host = _host(tmp_path)
    assert host.spawn_session(1, command=["cmd"])["success"]
    pty = FakePTY.created[-1]
    session = host._sessions["pane_1"]
    res = host.close_session(1)
    assert res["success"] and res["closed"] is True and res["exit_code"] == 1
    job = FakeJob.instances[-1]
    assert job.terminated == 1 and job.closed is True
    assert pty.isalive() is False
    assert session.pty is None and not session.reader_thread.is_alive()
    assert host.close_session(1)["already_closed"] is True


def test_close_kills_the_whole_process_tree_with_psutil_when_the_job_is_unavailable(tmp_path: Path) -> None:
    def broken_job() -> Any:
        raise JobObjectError("no hay Job Object")

    root = _spawn_real_tree()
    grandchild = int(root.stdout.readline())
    FakePTY.spawn_hook = lambda: root
    host = _host(tmp_path, job_factory=broken_job)
    try:
        res = host.spawn_session(1, command=["cmd"])
        assert res["success"] and res["tree_kill"] == "psutil"
        assert psutil.pid_exists(grandchild)
        closed = host.close_session(1)
        assert closed["closed"] is True
        _wait(lambda: not psutil.pid_exists(grandchild) or psutil.Process(grandchild).status() == psutil.STATUS_ZOMBIE)
        assert root.poll() is not None
    finally:
        for pid in (grandchild, root.pid):
            try:
                psutil.Process(pid).kill()
            except psutil.Error:
                pass
        root.wait()
        host.close()


def test_close_sweeps_descendants_even_if_the_job_terminate_fails(tmp_path: Path) -> None:
    FakeJob.effective = False
    root = _spawn_real_tree()
    grandchild = int(root.stdout.readline())
    FakePTY.spawn_hook = lambda: root
    host = _host(tmp_path)
    try:
        assert host.spawn_session(1, command=["cmd"])["success"]
        host.close_session(1)
        assert FakeJob.instances[-1].terminated == 1
        _wait(lambda: not psutil.pid_exists(grandchild) or psutil.Process(grandchild).status() == psutil.STATUS_ZOMBIE)
        assert root.poll() is not None
    finally:
        for pid in (grandchild, root.pid):
            try:
                psutil.Process(pid).kill()
            except psutil.Error:
                pass
        root.wait()
        host.close()


def test_job_assignment_failure_falls_back_to_psutil_but_still_spawns(tmp_path: Path) -> None:
    FakeJob.fail_assign = True
    host = _host(tmp_path)
    try:
        res = host.spawn_session(1, command=["cmd"])
        assert res["success"] and res["tree_kill"] == "psutil"
        assert FakeJob.instances[-1].closed is True  # el job a medio crear no se filtra
        assert host.list_sessions()[0]["tree_kill"] == "psutil"
        FakePTY.created[-1].die()  # el PID falso no existe para psutil: el doble no muere solo
    finally:
        host.close()


def test_respawning_over_a_dead_pane_shuts_the_stale_session_down(tmp_path: Path) -> None:
    host = _host(tmp_path)
    try:
        assert host.spawn_session(1, command=["cmd"])["success"]
        first_job = FakeJob.instances[-1]
        FakePTY.created[-1].die(0)
        _wait(lambda: host.poll_output(1)["alive"] is False)
        assert host.spawn_session(1, command=["cmd"])["success"]
        assert first_job.closed is True and len(FakePTY.created) == 2
    finally:
        host.close()


# -- pausa, actividad y RSS (proceso real) -----------------------------------------------------------------------


def test_pause_resume_suspends_the_real_process_tree_and_reading_never_stops(tmp_path: Path) -> None:
    root = _spawn_real_tree()
    grandchild = int(root.stdout.readline())
    FakePTY.spawn_hook = lambda: root
    host = _host(tmp_path)
    try:
        assert host.spawn_session(1, command=["cmd"])["success"]
        pty = FakePTY.created[-1]
        res = host.pause_session(1, True)
        assert res["success"] and res["paused"] is True
        assert psutil.Process(root.pid).status() == psutil.STATUS_STOPPED
        assert psutil.Process(grandchild).status() == psutil.STATUS_STOPPED
        assert host.pause_session(1, True)["paused"] is True  # idempotente
        assert host.poll_output(1)["activity_state"] == "paused"
        pty.feed("salida con el panel en pausa")  # el lector sigue drenando
        assert "en pausa" in _poll_text(host, 1, "en pausa")
        assert host.pause_session(1, False)["paused"] is False
        assert psutil.Process(root.pid).status() != psutil.STATUS_STOPPED
        assert psutil.Process(grandchild).status() != psutil.STATUS_STOPPED
        host.pause_session(1, True)
        host.close_session(1)  # cerrar en pausa reanuda y mata sin colgarse
        _wait(lambda: root.poll() is not None)
    finally:
        for pid in (grandchild, root.pid):
            try:
                psutil.Process(pid).kill()
            except psutil.Error:
                pass
        root.wait()
        host.close()


def test_activity_is_derived_from_output_silence_and_rss_from_psutil(tmp_path: Path) -> None:
    root = _spawn_real_tree()
    root.stdout.readline()
    FakePTY.spawn_hook = lambda: root
    host = _host(tmp_path)
    try:
        assert host.spawn_session(1, command=["cmd"])["success"]
        FakePTY.created[-1].feed("trabajando")
        _poll_text(host, 1, "trabajando")
        busy = host.get_pane_activity(1, min_silence_s=60.0)
        assert busy["state"] == "working" and busy["source"] == "silence_heuristic" and busy["osc_status"] is None
        quiet = host.get_pane_activity(1, min_silence_s=0.0)
        assert quiet["state"] == "idle" and host.is_pane_idle(1, min_silence_s=0.0) is True
        session = host.list_sessions()[0]
        assert session["rss_mb"] > 0 and session["activity_source"] in ("silence_heuristic", "process_state")
        assert session["backend"] == "conpty"
    finally:
        host.close()
        for p in psutil.Process(root.pid).children(recursive=True) if root.poll() is None else []:
            p.kill()
        root.kill()
        root.wait()


def test_osc_7501_is_not_interpreted_on_windows(tmp_path: Path) -> None:
    host = _host(tmp_path)
    try:
        assert host.spawn_session(1, command=["cmd"])["success"]
        FakePTY.created[-1].feed("\x1b]7501;working\x07")
        _poll_text(host, 1, "7501")
        assert host.get_pane_activity(1)["osc_status"] is None
    finally:
        host.close()


# -- fabrica ------------------------------------------------------------------------------------------------------


def test_factory_picks_the_conpty_connector_when_available_and_the_null_otherwise(monkeypatch: pytest.MonkeyPatch,
                                                                                tmp_path: Path) -> None:
    from src.core.factory import ApplicationFactory

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winpty", _module())
    monkeypatch.setattr(PaneMcpConfigStore, "_ensure_base", PaneMcpConfigStore._ensure_base)
    monkeypatch.setattr(
        "src.adapters.terminal.mcp_injection._default_base_dir", lambda: tmp_path / "mcp"
    )
    host = ApplicationFactory.create_terminal_host()
    try:
        assert isinstance(host.primary, WinPtyHostConnector)
        assert host.is_available() is True
        status = host.get_status()
        assert status["mode"] == "windows_conpty" and status["available"] is True
    finally:
        host.close()

    monkeypatch.setitem(sys.modules, "winpty", None)  # import falla -> ImportError
    degraded = ApplicationFactory.create_terminal_host()
    try:
        assert degraded.primary is degraded.fallback and degraded.is_available() is False
        assert degraded.spawn_session(1, command=["cmd"])["code"] == "TERMINAL_PLATFORM_UNSUPPORTED"
    finally:
        degraded.close()


def test_concurrent_spawns_of_the_same_pane_only_one_wins(tmp_path: Path) -> None:
    host = _host(tmp_path)
    results: List[Dict[str, Any]] = []
    try:
        threads = [threading.Thread(target=lambda: results.append(host.spawn_session(1, command=["cmd"]))) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sum(1 for r in results if r["success"]) == 1
        assert {r["code"] for r in results if not r["success"]} == {"PANE_ALREADY_ACTIVE"}
    finally:
        host.close()
