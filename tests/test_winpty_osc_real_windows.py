"""
Medicion REAL de OSC 7501 en Windows ConPTY (`pywinpty`) y garantia de 0 escrituras hoy (decision H-D3).
Solo corre en Windows (CI `windows-latest` y la maquina del dueno): `pytest -m conpty -v -rA`.

No modifica `WinPtyHostConnector`: mide que entrega exactamente `pty.read()` / `poll_output` cuando el hijo
emite secuencias OSC 7501 (`idle`, `state=working:kind=turn` y sonda `?`) entre marcadores visibles, y
verifica que hoy `WinPtyHostConnector` mantiene `source == "silence_heuristic"`, `osc_status is None`,
no responde escribiendo nada en `pty.write()` ni `send_input()` y cierra sin procesos huerfanos.
"""

from __future__ import annotations

import re
import sys
import threading
import time
import types
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import psutil
import pytest

pytestmark = [
    pytest.mark.conpty,
    pytest.mark.skipif(sys.platform != "win32", reason="ConPTY solo existe en Windows"),
]

if sys.platform == "win32":
    from src.adapters.terminal.cli_catalog import CliCatalogService
    from src.adapters.terminal.mcp_injection import PaneMcpConfigStore
    from src.adapters.terminal.pty_common import Osc7501Parser
    from src.adapters.terminal.winpty_host import WinPtyHostConnector
    from src.runtime.terminal_platform import import_winpty, windows_build_number, windows_conpty_status

PY = sys.executable


def _env_report() -> str:
    mod = import_winpty()
    return (
        f"Windows build={windows_build_number()} python={sys.version.split()[0]} "
        f"pywinpty={getattr(mod, '__version__', None)} conpty_status={windows_conpty_status()}"
    )


class _SpyPTY:
    """Envoltorio minimo sobre `winpty.PTY` real para registrar `read()` crudo y `write()` sin tocar winpty_host."""

    def __init__(self, inner: Any, records: Dict[str, Any]) -> None:
        self._inner = inner
        self._records = records

    def spawn(
        self,
        appname: str,
        cmdline: Optional[str] = None,
        cwd: Optional[str] = None,
        env: Optional[str] = None,
    ) -> bool:
        return bool(self._inner.spawn(appname, cmdline=cmdline, cwd=cwd, env=env))

    @property
    def pid(self) -> Optional[int]:
        return self._inner.pid

    def read(self, blocking: bool = False) -> str:
        data = self._inner.read(blocking=blocking)
        if data:
            with self._records["lock"]:
                self._records["raw_reads"].append(data)
        return data

    def write(self, text: str) -> int:
        with self._records["lock"]:
            self._records["pty_writes"].append(text)
        return int(self._inner.write(text))

    def set_size(self, cols: int, rows: int) -> None:
        self._inner.set_size(cols, rows)

    def isalive(self) -> bool:
        return bool(self._inner.isalive())

    def get_exitstatus(self) -> Optional[int]:
        return self._inner.get_exitstatus()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _make_spy_winpty_module(records: Dict[str, Any]) -> Any:
    real_mod = import_winpty()
    assert real_mod is not None, f"pywinpty no disponible: {_env_report()}"

    def _pty_factory(cols: int, rows: int, backend: Optional[int] = None, **kwargs: Any) -> _SpyPTY:
        inner = real_mod.PTY(cols, rows, backend=backend, **kwargs)
        return _SpyPTY(inner, records)

    return types.SimpleNamespace(
        PTY=_pty_factory,
        Backend=real_mod.Backend,
        __version__=getattr(real_mod, "__version__", None),
    )


def _classify_osc_delivery(raw_text: str, before: str, after: str, payload: str) -> Dict[str, Any]:
    b_idx = raw_text.find(before)
    a_idx = raw_text.find(after, b_idx + len(before)) if b_idx != -1 else -1
    segment = raw_text[b_idx + len(before):a_idx] if (b_idx != -1 and a_idx != -1) else ""

    bel_seq = f"\x1b]7501;{payload}\x07"
    st_seq = f"\x1b]7501;{payload}\x1b\\"
    if bel_seq in segment or bel_seq in raw_text:
        status = "intact_bel"
    elif st_seq in segment or st_seq in raw_text:
        status = "transformed_st"
    elif f"7501;{payload}" in segment or f"7501;{payload}" in raw_text:
        status = "transformed_partial"
    elif "\x1b]7501;" in segment:
        status = "transformed_other"
    else:
        status = "filtered_or_stripped"

    return {
        "payload": payload,
        "delivery": status,
        "segment_repr": repr(segment),
    }


class _OscPane:
    """Ayudante de sondeo y diagnostico para ConPTY OSC 7501."""

    def __init__(self, host: Any, pane_id: int, spawn: Dict[str, Any], records: Dict[str, Any]) -> None:
        self.host = host
        self.pane_id = pane_id
        self.spawn = spawn
        self.records = records
        self.text = ""
        self.cursor = 0

    def raw_text(self) -> str:
        with self.records["lock"]:
            return "".join(self.records["raw_reads"])

    def raw_chunks(self) -> List[str]:
        with self.records["lock"]:
            return list(self.records["raw_reads"])

    def diag(self) -> str:
        sessions = {s["pane_id"]: s for s in self.host.list_sessions()}
        info = sessions.get(f"pane_{self.pane_id}")
        return (
            f"\n--- diagnostico ConPTY OSC 7501 ---\n"
            f"{_env_report()}\n"
            f"spawn={self.spawn}\n"
            f"sesion={info}\n"
            f"raw_reads={self.raw_chunks()!r}\n"
            f"poll_text={self.text!r}\n"
            f"pty_writes={self.records['pty_writes']!r}\n"
            f"send_input_calls={self.records['send_input_calls']!r}\n"
        )

    def pump(self) -> Dict[str, Any]:
        res = self.host.poll_output(self.pane_id, since_seq=self.cursor)
        self.cursor = res.get("next_seq", self.cursor)
        self.text += res.get("text", "")
        return res

    def wait_for(self, pattern: str, timeout: float = 20.0) -> re.Match[str]:
        rx = re.compile(pattern)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.pump()
            found = rx.search(self.text)
            if found:
                return found
            time.sleep(0.05)
        raise AssertionError(f"no aparecio /{pattern}/ en {timeout}s{self.diag()}")


def _gone(pids: List[int], timeout: float = 10.0) -> List[int]:
    deadline = time.monotonic() + timeout
    alive = list(pids)
    while alive and time.monotonic() < deadline:
        alive = [p for p in alive if psutil.pid_exists(p) and psutil.Process(p).status() != psutil.STATUS_ZOMBIE]
        if alive:
            time.sleep(0.1)
    return alive


@pytest.fixture
def osc_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Any, Dict[str, Any]]]:
    ok, reason = windows_conpty_status()
    assert ok and WinPtyHostConnector.is_available(), f"ConPTY no disponible ({reason}): {_env_report()}"

    records: Dict[str, Any] = {
        "lock": threading.Lock(),
        "raw_reads": [],
        "pty_writes": [],
        "send_input_calls": [],
    }
    spy_mod = _make_spy_winpty_module(records)
    clean_env = {"PATH": "", "PATHEXT": ".COM;.EXE;.BAT;.CMD"}
    host = WinPtyHostConnector(
        max_panes=4,
        memory_probe=lambda: 10 ** 6,
        mcp_config_store=PaneMcpConfigStore(tmp_path / "mcp"),
        catalog_service=CliCatalogService(
            include_user_catalog=False,
            platform="win32",
            environ=clean_env,
            home=str(tmp_path),
            which=lambda _n: None,
        ),
        winpty_module=spy_mod,
    )
    orig_send_input = host.send_input

    def _spy_send_input(pane_id: Any, data: Any) -> Dict[str, Any]:
        with records["lock"]:
            records["send_input_calls"].append((pane_id, data))
        return orig_send_input(pane_id, data)

    monkeypatch.setattr(host, "send_input", _spy_send_input)
    try:
        yield host, records
    finally:
        host.close()


# -- medicion OSC 7501 y garantia de 0 escrituras ------------------------------------------------------------


def test_conpty_osc_7501_measurement_and_zero_writes(osc_host: tuple[Any, Dict[str, Any]], tmp_path: Path) -> None:
    host, records = osc_host
    code = (
        "import os, sys, time\n"
        "out = sys.stdout.buffer\n"
        "def emit(b):\n"
        "    out.write(b)\n"
        "    out.flush()\n"
        "emit(f'PID={os.getpid()}\\r\\n'.encode('ascii'))\n"
        "emit(b'BEFORE_IDLE\\r\\n')\n"
        "emit(b'\\x1b]7501;idle\\x07')\n"
        "emit(b'AFTER_IDLE\\r\\n')\n"
        "emit(b'BEFORE_WORKING\\r\\n')\n"
        "emit(b'\\x1b]7501;state=working:kind=turn\\x07')\n"
        "emit(b'AFTER_WORKING\\r\\n')\n"
        "emit(b'BEFORE_PROBE\\r\\n')\n"
        "emit(b'\\x1b]7501;?\\x07')\n"
        "emit(b'AFTER_PROBE\\r\\n')\n"
        "emit(b'OSC_MEASURE_DONE\\r\\n')\n"
        "time.sleep(120)\n"
    )
    spawn = host.spawn_session(
        1,
        command=[PY, "-u", "-c", code],
        cwd=tmp_path,
        cols=120,
        rows=30,
        env_overrides={"PYTHONIOENCODING": "utf-8", "NO_COLOR": None},
    )
    assert spawn.get("success") is True, f"spawn fallo: {spawn}\n{_env_report()}"

    pane = _OscPane(host, 1, spawn, records)
    pid_match = pane.wait_for(r"PID=(\d+)")
    child_pid = int(pid_match.group(1))
    pane.wait_for(r"OSC_MEASURE_DONE")
    time.sleep(0.15)
    pane.pump()

    raw_text = pane.raw_text()
    for marker in (
        "BEFORE_IDLE",
        "AFTER_IDLE",
        "BEFORE_WORKING",
        "AFTER_WORKING",
        "BEFORE_PROBE",
        "AFTER_PROBE",
        "OSC_MEASURE_DONE",
    ):
        assert marker in pane.text, f"falta marcador {marker!r} en poll_output{pane.diag()}"
        assert marker in raw_text, f"falta marcador {marker!r} en raw pty.read(){pane.diag()}"

    # Medicion informativa (sin fallo artificial si ConPTY transforma o filtra OSC 7501).
    deliveries = {
        "idle": _classify_osc_delivery(raw_text, "BEFORE_IDLE", "AFTER_IDLE", "idle"),
        "working_turn": _classify_osc_delivery(
            raw_text, "BEFORE_WORKING", "AFTER_WORKING", "state=working:kind=turn"
        ),
        "probe": _classify_osc_delivery(raw_text, "BEFORE_PROBE", "AFTER_PROBE", "?"),
    }
    parser = Osc7501Parser()
    parsed_statuses = parser.feed(raw_text.encode("utf-8", errors="replace"))
    parsed_probes = parser.take_probes()

    busy = host.get_pane_activity(1, min_silence_s=60.0)
    quiet = host.get_pane_activity(1, min_silence_s=0.0)

    print(
        f"\n[H1.4 ConPTY OSC 7501] {_env_report()}\n"
        f"deliveries={deliveries}\n"
        f"parser_observation={{statuses={parsed_statuses!r}, kind={parser.last_kind!r}, probes={parsed_probes}}}\n"
        f"activity_busy={busy}\n"
        f"activity_quiet={quiet}\n"
        f"raw_chunks_count={len(pane.raw_chunks())}"
    )

    assert busy["source"] == "silence_heuristic", pane.diag()
    assert busy["osc_status"] is None and busy["osc_kind"] is None, pane.diag()
    assert quiet["source"] == "silence_heuristic" and quiet["osc_status"] is None, pane.diag()
    assert quiet["state"] == "idle" and quiet["idle"] is True, pane.diag()

    with records["lock"]:
        assert records["pty_writes"] == [], f"WinPtyHostConnector escribio en pty.write: {pane.diag()}"
        assert records["send_input_calls"] == [], f"WinPtyHostConnector llamo a send_input: {pane.diag()}"

    closed = host.close_session(1)
    assert closed["success"] is True and closed["closed"] is True, pane.diag()
    survivors = _gone([child_pid, int(spawn["pid"])])
    assert survivors == [], f"procesos huerfanos tras close_session: {survivors}{pane.diag()}"


def test_conpty_osc_7501_probe_burst_writes_nothing_and_closes_clean(
    osc_host: tuple[Any, Dict[str, Any]], tmp_path: Path
) -> None:
    host, records = osc_host
    code = (
        "import os, sys, time\n"
        "out = sys.stdout.buffer\n"
        "out.write(f'PID={os.getpid()}\\r\\nBURST_START\\r\\n'.encode('ascii'))\n"
        "for _ in range(10):\n"
        "    out.write(b'\\x1b]7501;?\\x07')\n"
        "out.write(b'\\x1b]7501;state=working:kind=turn\\x07')\n"
        "out.write(b'\\x1b]7501;idle\\x07')\n"
        "out.write(b'BURST_END\\r\\n')\n"
        "out.flush()\n"
        "time.sleep(120)\n"
    )
    spawn = host.spawn_session(
        1,
        command=[PY, "-u", "-c", code],
        cwd=tmp_path,
        cols=120,
        rows=30,
        env_overrides={"PYTHONIOENCODING": "utf-8", "NO_COLOR": None},
    )
    assert spawn.get("success") is True, f"spawn fallo: {spawn}\n{_env_report()}"

    pane = _OscPane(host, 1, spawn, records)
    child_pid = int(pane.wait_for(r"PID=(\d+)").group(1))
    pane.wait_for(r"BURST_END")
    time.sleep(0.15)
    pane.pump()

    raw_text = pane.raw_text()
    assert "BURST_START" in pane.text and "BURST_END" in pane.text, pane.diag()
    assert "BURST_START" in raw_text and "BURST_END" in raw_text, pane.diag()

    parser = Osc7501Parser()
    burst_statuses = parser.feed(raw_text.encode("utf-8", errors="replace"))
    burst_probes = parser.take_probes()
    activity = host.get_pane_activity(1, min_silence_s=60.0)

    print(
        f"\n[H1.4 ConPTY OSC 7501 burst] {_env_report()}\n"
        f"burst_observation={{statuses={burst_statuses!r}, probes={burst_probes}}}\n"
        f"activity={activity}"
    )

    assert activity["source"] == "silence_heuristic" and activity["osc_status"] is None, pane.diag()
    with records["lock"]:
        assert records["pty_writes"] == [], pane.diag()
        assert records["send_input_calls"] == [], pane.diag()

    closed = host.close_session(1)
    assert closed["success"] is True and closed["closed"] is True, pane.diag()
    survivors = _gone([child_pid, int(spawn["pid"])])
    assert survivors == [], f"procesos huerfanos tras close_session: {survivors}{pane.diag()}"
