"""
Pruebas REALES de ConPTY (`pywinpty`) para `WinPtyHostConnector`. Solo corren en Windows (CI `windows-latest` y la
maquina del dueno): `pytest -m conpty -v -rA`.

Todo fallo imprime un diagnostico (version de Windows y pywinpty, `launch_command`, `tree_kill`, `reader_error`, texto
recibido) para poder entender un fallo del CI sin reproducirlo en local.
"""

from __future__ import annotations

import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import psutil
import pytest

pytestmark = [
    pytest.mark.conpty,
    pytest.mark.skipif(sys.platform != "win32", reason="ConPTY solo existe en Windows"),
]

if sys.platform == "win32":
    from src.adapters.terminal.mcp_injection import PaneMcpConfigStore
    from src.adapters.terminal.winpty_host import WinPtyHostConnector
    from src.runtime.terminal_platform import import_winpty, windows_build_number, windows_conpty_status

PY = sys.executable


def _env_report() -> str:
    mod = import_winpty()
    return (
        f"Windows build={windows_build_number()} python={sys.version.split()[0]} "
        f"pywinpty={getattr(mod, '__version__', None)} conpty_status={windows_conpty_status()}"
    )


class Pane:
    """Ayudante que acumula la salida y explica los fallos."""

    def __init__(self, host: Any, pane_id: int, spawn: Dict[str, Any]) -> None:
        self.host, self.pane_id, self.spawn = host, pane_id, spawn
        self.text = ""
        self.cursor = 0

    def diag(self) -> str:
        sessions = {s["pane_id"]: s for s in self.host.list_sessions()}
        info = sessions.get(f"pane_{self.pane_id}")
        return (
            f"\n--- diagnostico ConPTY ---\n{_env_report()}\nspawn={self.spawn}\nsesion={info}\n"
            f"salida={self.text!r}\n"
        )

    def pump(self) -> Dict[str, Any]:
        res = self.host.poll_output(self.pane_id, since_seq=self.cursor)
        self.cursor = res.get("next_seq", self.cursor)
        self.text += res.get("text", "")
        return res

    def wait_for(self, pattern: str, timeout: float = 20.0) -> re.Match:
        rx = re.compile(pattern)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.pump()
            found = rx.search(self.text)
            if found:
                return found
            time.sleep(0.05)
        raise AssertionError(f"no aparecio /{pattern}/ en {timeout}s{self.diag()}")

    def wait_exit(self, timeout: float = 20.0) -> Dict[str, Any]:
        deadline = time.monotonic() + timeout
        res: Dict[str, Any] = {}
        while time.monotonic() < deadline:
            res = self.pump()
            if res.get("alive") is False:
                return res
            time.sleep(0.05)
        raise AssertionError(f"el proceso no termino en {timeout}s{self.diag()}")


@pytest.fixture
def host(tmp_path: Path) -> Iterator[Any]:
    h = WinPtyHostConnector(
        max_panes=4, memory_probe=lambda: 10 ** 6, mcp_config_store=PaneMcpConfigStore(tmp_path / "mcp")
    )
    assert h.is_available(), f"ConPTY no disponible: {_env_report()}"
    try:
        yield h
    finally:
        h.close()


def _open(host: Any, pane_id: int, command: List[str], **kwargs: Any) -> Pane:
    res = host.spawn_session(pane_id, command=command, **kwargs)
    assert res.get("success") is True, f"spawn fallo: {res}\n{_env_report()}"
    return Pane(host, pane_id, res)


def _gone(pids: List[int], timeout: float = 10.0) -> List[int]:
    deadline = time.monotonic() + timeout
    alive = list(pids)
    while alive and time.monotonic() < deadline:
        alive = [p for p in alive if psutil.pid_exists(p) and psutil.Process(p).status() != psutil.STATUS_ZOMBIE]
        if alive:
            time.sleep(0.1)
    return alive


def _write_tree(tmp_path: Path) -> Path:
    script = tmp_path / "arbol.py"
    script.write_text(
        "import os,subprocess,sys,time\n"
        "depth=int(sys.argv[1])\n"
        "if depth<3:\n"
        "    c=subprocess.Popen([sys.executable,__file__,str(depth+1)],stdout=subprocess.PIPE,text=True)\n"
        "    sub=c.stdout.readline().strip()\n"
        "    print(str(os.getpid())+' '+sub if sub else os.getpid(),flush=True)\n"
        "else:\n"
        "    print(os.getpid(),flush=True)\n"
        "time.sleep(300)\n",
        encoding="utf-8",
    )
    return script


# -- disponibilidad ------------------------------------------------------------------------------------------


def test_conpty_is_available_on_this_runner() -> None:
    ok, reason = windows_conpty_status()
    assert ok, f"{reason}: {_env_report()}"
    print(_env_report())


def test_factory_builds_the_conpty_connector_and_never_answers_unsupported(tmp_path: Path) -> None:
    from src.core.factory import ApplicationFactory

    terminal = ApplicationFactory.create_terminal_host()
    try:
        assert isinstance(terminal.primary, WinPtyHostConnector), _env_report()
        res = terminal.spawn_session(1, command=["cmd.exe", "/c", "echo fabrica"])
        assert res.get("code") != "TERMINAL_PLATFORM_UNSUPPORTED" and res["success"] is True, res
        pane = Pane(terminal, 1, res)
        pane.wait_for("fabrica")
    finally:
        terminal.close()


# -- E/S -----------------------------------------------------------------------------------------------------


def test_cmd_echo_hola_and_exit_code(host: Any) -> None:
    pane = _open(host, 1, ["cmd.exe", "/c", "echo hola"])
    pane.wait_for("hola")
    res = pane.wait_exit()
    assert res["exit_code"] == 0, pane.diag()


def test_interactive_shell_echoes_typed_commands(host: Any) -> None:
    pane = _open(host, 1, ["cmd.exe"])
    pane.wait_for(r">")
    assert host.send_input(1, "echo mcp2p-eco-123\r")["success"] is True
    pane.wait_for(r"mcp2p-eco-123[\s\S]*mcp2p-eco-123")  # lo escrito (eco) y el resultado
    host.send_input(1, "exit\r")
    pane.wait_exit()


def test_utf8_output_survives_the_pty(host: Any) -> None:
    code = "import sys;sys.stdout.reconfigure(encoding='utf-8');print('ñandú ✓ 漢字')"
    pane = _open(host, 1, [PY, "-c", code])
    pane.wait_for("ñandú ✓ 漢字")


def test_cwd_env_and_hub_token_reach_the_child(host: Any, tmp_path: Path) -> None:
    work = tmp_path / "carpeta con espacios"
    work.mkdir()
    code = (
        "import os;print('CWD='+os.getcwd());print('PANE='+os.environ['MCP2P_PANE_ID']);"
        "print('TOK='+os.environ.get('MCP2P_SESSION_TOKEN',''));print('URL='+os.environ.get('MCP2P_HUB_URL',''));"
        "print('X='+os.environ.get('MI_VAR',''))"
    )
    pane = _open(host, 2, [PY, "-c", code], cwd=work, hub_url="http://127.0.0.1:8766/mcp",
                 session_token="tok-de-prueba", env_overrides={"MI_VAR": "valor"})
    pane.wait_for("X=valor")
    assert re.search(r"CWD=.*carpeta con espacios", pane.text), pane.diag()
    assert "PANE=pane_2" in pane.text and "TOK=tok-de-prueba" in pane.text
    assert "URL=http://127.0.0.1:8766/mcp" in pane.text


def test_cmd_shim_with_spaces_and_quotes_in_arguments(host: Any, tmp_path: Path) -> None:
    folder = tmp_path / "npm global"
    folder.mkdir()
    shim = folder / "mi-cli.cmd"
    shim.write_text("@echo off\r\necho shim-ok [%~1] [%~2]\r\n", encoding="ascii")
    pane = _open(host, 1, [str(shim), "uno", "dos palabras"])
    pane.wait_for(r"shim-ok \[uno\] \[dos palabras\]")


def test_cmd_shim_rejects_injection_attempts_without_starting_anything(host: Any, tmp_path: Path) -> None:
    shim = tmp_path / "x.cmd"
    shim.write_text("@echo off\r\necho no\r\n", encoding="ascii")
    res = host.spawn_session(1, command=[str(shim), "a & calc.exe"])
    assert res["success"] is False and res["code"] == "CLI_ARG_UNSAFE", res
    assert host.list_sessions() == []


def test_catalog_resolves_the_system_shell_and_runs_it(host: Any) -> None:
    res = host.spawn_session(1, cli_profile="custom_shell")
    assert res.get("success") is True, f"{res}\n{_env_report()}"
    pane = Pane(host, 1, res)
    pane.wait_for(r">")


# -- tamano, interrupcion, pausa -------------------------------------------------------------------------------


def test_resize_reaches_the_application(host: Any) -> None:
    code = (
        "import os,time\n"
        "while True:\n"
        "    s=os.get_terminal_size();print('SIZE',s.columns,s.lines,flush=True);time.sleep(0.1)\n"
    )
    pane = _open(host, 1, [PY, "-c", code], cols=100, rows=30)
    pane.wait_for(r"SIZE 100 30")
    assert host.resize_session(1, 61, 17)["success"] is True
    pane.wait_for(r"SIZE 61 17")


def test_ctrl_c_interrupts_a_running_command(host: Any) -> None:
    pane = _open(host, 1, ["ping.exe", "-t", "127.0.0.1"])
    pane.wait_for(r"127\.0\.0\.1")
    assert host.send_input(1, "\x03")["success"] is True
    pane.wait_exit(timeout=15.0)


def test_pause_freezes_the_child_while_output_keeps_being_drained(host: Any) -> None:
    code = "import time\ni=0\nwhile True:\n    print('TICK',i,flush=True);i+=1;time.sleep(0.05)\n"
    pane = _open(host, 1, [PY, "-c", code])
    pane.wait_for(r"TICK \d+")
    assert host.pause_session(1, True)["paused"] is True
    time.sleep(0.5)
    pane.pump()
    frozen = pane.cursor
    time.sleep(0.8)
    pane.pump()
    assert pane.cursor == frozen, "el hijo siguio escribiendo en pausa" + pane.diag()
    assert host.poll_output(1)["activity_state"] == "paused"
    assert host.pause_session(1, False)["paused"] is False
    before = pane.cursor
    deadline = time.monotonic() + 10
    while pane.cursor == before and time.monotonic() < deadline:
        pane.pump()
        time.sleep(0.05)
    assert pane.cursor > before, "no reanudo" + pane.diag()


# -- cierre sin huerfanos -------------------------------------------------------------------------------------------


def test_close_leaves_no_orphans_across_three_levels_of_grandchildren(host: Any, tmp_path: Path) -> None:
    script = _write_tree(tmp_path)
    pane = _open(host, 1, [PY, str(script), "1"])
    print("tree_kill =", pane.spawn.get("tree_kill"))
    found = pane.wait_for(r"(\d+) (\d+) (\d+)")
    pids = [int(x) for x in found.groups()]
    assert all(psutil.pid_exists(p) for p in pids), pane.diag()
    res = host.close_session(1)
    assert res["closed"] is True
    survivors = _gone(pids)
    assert survivors == [], f"procesos huerfanos tras close_session: {survivors}{pane.diag()}"


def test_the_job_object_is_in_use_for_the_root_process(host: Any) -> None:
    pane = _open(host, 1, ["cmd.exe", "/c", "ping -n 30 127.0.0.1 >nul"])
    assert pane.spawn["tree_kill"] == "job_object", f"el Job Object no se pudo asignar: {pane.spawn}\n{_env_report()}"


def test_close_does_not_hang_with_undrained_flood_of_output(host: Any) -> None:
    pane = _open(host, 1, [PY, "-c", "while True: print('x'*2000)"])
    time.sleep(1.0)  # nadie llama a poll_output: el lector debe drenar solo
    done: Dict[str, Any] = {}
    t = threading.Thread(target=lambda: done.update(host.close_session(1)), daemon=True)
    started = time.monotonic()
    t.start()
    t.join(timeout=20)
    assert not t.is_alive(), "close_session se colgo con salida sin drenar" + pane.diag()
    assert done.get("closed") is True
    print(f"close con flood: {time.monotonic() - started:.2f}s")


def test_four_panes_at_once_are_independent_and_all_close_clean(host: Any) -> None:
    panes = [
        _open(host, i, [PY, "-c", f"import os,time;print('PID',os.getpid(),'P{i}',flush=True);time.sleep(300)"])
        for i in (1, 2, 3, 4)
    ]
    pids = []
    for i, pane in enumerate(panes, start=1):
        m = pane.wait_for(rf"PID (\d+) P{i}")
        pids.append(int(m.group(1)))
        for j in (1, 2, 3, 4):
            if j != i:
                assert f"P{j}" not in pane.text
    assert len(set(pids)) == 4
    assert host.spawn_session(5, command=["cmd.exe"])["code"] == "PANE_LIMIT_REACHED"
    for i in (1, 2, 3, 4):
        assert host.close_session(i)["closed"] is True
    assert _gone(pids) == [], f"huerfanos: {pids}"


def test_closing_when_the_process_already_exited_is_clean(host: Any) -> None:
    pane = _open(host, 1, ["cmd.exe", "/c", "exit 7"])
    res = pane.wait_exit()
    assert res["exit_code"] == 7, pane.diag()
    closed = host.close_session(1)
    assert closed["closed"] is True and closed["exit_code"] == 7
