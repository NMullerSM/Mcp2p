"""
Host POSIX PTY (`PosixPtyHostConnector`) que implementa `AgentTerminalPort` para la malla de terminales
interactivas (EGM-24/59/63). La logica independiente del SO (buffer circular `PtyOutputRingBuffer`, parser OSC,
limites de paneles, validacion, admision por memoria, estado de actividad) vive en `pty_common.py` y se comparte
con el conector de Windows (`winpty_host.py`); aqui se re-exportan los nombres historicos.
"""

from __future__ import annotations

import errno
import logging
import os
import select
import shutil
import signal
import struct
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import psutil

from src.adapters.terminal.cli_catalog import CliSpec
from src.adapters.terminal.mcp_injection import McpInjectionError
from src.adapters.terminal.pty_common import (  # noqa: F401  (re-exportaciones historicas)
    DEFAULT_MANUAL_COMMAND,
    DEFAULT_MAX_PANES,
    DEFAULT_MIN_AVAILABLE_MB,
    DEFAULT_RING_BYTES,
    HARD_MAX_PANES,
    MAX_PANES_SETTING_KEY,
    MAX_PANES_SETTING_NAMESPACE,
    OSC_7501_MAX_PENDING as _OSC_7501_MAX_PENDING,
    OSC_7501_PREFIX as _OSC_7501_PREFIX,
    OSC_7501_STATUS_MAP as _OSC_7501_STATUS_MAP,
    SHELL_DEFAULT as _SHELL_DEFAULT,
    PaneLimitError,
    PtyHostBase,
    PtyOutputRingBuffer,
    REPO_ROOT as _REPO_ROOT,
    build_child_env,
    build_cli_profiles,
    clamp_max_panes,
    compute_session_activity,
    default_mcp_config as _default_mcp_config,
    load_configured_max_panes,
    normalize_pane_id,
    read_process_rss_mb as _read_process_rss_mb,
    Osc7501Parser as _Osc7501Parser,
)
from src.proc_utils import no_window_kwargs
from src.runtime.terminal_platform import is_windows_platform, unsupported_platform_response

if os.name == "posix":
    try:
        import fcntl
        import pty
        import termios
    except ImportError:  # pragma: no cover - plataformas POSIX exóticas sin módulos tty
        fcntl = None  # type: ignore[assignment]
        pty = None  # type: ignore[assignment]
        termios = None  # type: ignore[assignment]
else:  # pragma: no cover - win32 / no POSIX
    fcntl = None  # type: ignore[assignment]
    pty = None  # type: ignore[assignment]
    termios = None  # type: ignore[assignment]

logger = logging.getLogger("mcp2p.adapters.terminal.pty_host")

CLI_PROFILES: Dict[str, List[str]] = build_cli_profiles(_SHELL_DEFAULT)


@dataclass
class _PtySession:
    pane_id: str
    pane_index: int
    session_id: str
    cli_profile: str
    role: str
    node_uuid: Optional[str]
    command: List[str]
    launch_command: List[str]
    process: subprocess.Popen[Any]
    pid: int
    pgid: int
    master_fd: Optional[int]
    cols: int
    rows: int
    started_at: str
    buffer: PtyOutputRingBuffer
    paused: bool = False
    stop_event: threading.Event = field(default_factory=threading.Event)
    fd_lock: threading.Lock = field(default_factory=threading.Lock)
    reader_thread: Optional[threading.Thread] = None
    cwd: Optional[str] = None
    mcp_injection: Dict[str, Any] = field(default_factory=dict)
    last_output_at: float = field(default_factory=time.monotonic)
    last_input_at: float = 0.0
    osc_status: Optional[str] = None
    osc_status_at: float = 0.0
    osc_parser: _Osc7501Parser = field(default_factory=_Osc7501Parser, repr=False)

    def is_alive(self) -> bool:
        return self.process.poll() is None

    def exit_code(self) -> Optional[int]:
        return self.process.poll()

    def close_master_fd(self) -> None:
        with self.fd_lock:
            fd = self.master_fd
            if fd is not None:
                self.master_fd = None
                try:
                    os.close(fd)
                except OSError:
                    pass



class PosixPtyHostConnector(PtyHostBase):
    """
    Adaptador POSIX real de `AgentTerminalPort` respaldado por `pty.openpty()`,
    trampolín `--pty-exec` (`TIOCSCTTY`), control de ventana (`TIOCSWINSZ` + `SIGWINCH`),
    pausa (`SIGSTOP`/`SIGCONT`) y métricas `psutil`.
    """

    MODE = "posix_pty"

    @staticmethod
    def is_available() -> bool:
        return (
            os.name == "posix"
            and sys.platform != "win32"
            and pty is not None
            and fcntl is not None
            and termios is not None
        )

    def _platform_block(self, norm_pane: str) -> Optional[Dict[str, Any]]:
        if is_windows_platform():
            return {**unsupported_platform_response(), "pane_id": norm_pane}
        if not self.is_available():
            return {
                "success": False,
                "degraded": True,
                "code": "PTY_UNAVAILABLE",
                "pane_id": norm_pane,
                "message": "Backend POSIX PTY no disponible en esta plataforma.",
                "manual_command": DEFAULT_MANUAL_COMMAND,
                "mcp_config": _default_mcp_config(),
            }
        return None

    @staticmethod
    def _which_executable(binary: str, cwd: Optional[str]) -> Optional[str]:
        found = shutil.which(binary)
        if found:
            return found
        if cwd and os.path.sep in binary and not os.path.isabs(binary):
            candidate = Path(cwd) / binary
            return shutil.which(str(candidate))
        return None


    @staticmethod
    def _build_child_env(
        *,
        norm_pane: str,
        hub_url: Optional[str],
        session_token: Optional[str],
        node_uuid: Optional[str],
        env_overrides: Optional[Dict[str, Optional[str]]],
        use_trampoline: bool,
    ) -> Dict[str, str]:
        return build_child_env(
            norm_pane=norm_pane,
            hub_url=hub_url,
            session_token=session_token,
            node_uuid=node_uuid,
            env_overrides=env_overrides,
            pythonpath_prepend=(
                str(_REPO_ROOT) if use_trampoline and not getattr(sys, "frozen", False) else None
            ),
        )

    @staticmethod
    def _build_launch_command(resolved_cmd: List[str], *, use_trampoline: bool) -> List[str]:
        if not use_trampoline:
            return list(resolved_cmd)
        if getattr(sys, "frozen", False):
            return [sys.executable, "--pty-exec", "--", *resolved_cmd]
        return [sys.executable, "-m", "src.app.main", "--pty-exec", "--", *resolved_cmd]

    @staticmethod
    def _set_winsize(fd: int, cols: int, rows: int) -> None:
        if fcntl is None or termios is None:
            return
        winsize = struct.pack("HHHH", int(rows), int(cols), 0, 0)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)

    @staticmethod
    def _set_nonblocking(fd: int) -> None:
        if fcntl is None:
            os.set_blocking(fd, False)
            return
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    def _reader_loop(self, session: _PtySession) -> None:
        try:
            while not session.stop_event.is_set():
                fd = session.master_fd
                if fd is None:
                    break
                try:
                    rlist, _, _ = select.select([fd], [], [], 0.1)
                except (OSError, ValueError):
                    break
                if not rlist:
                    continue

                eof = False
                while True:
                    fd = session.master_fd
                    if fd is None:
                        eof = True
                        break
                    try:
                        chunk = os.read(fd, 4096)
                    except BlockingIOError:
                        break
                    except OSError as exc:
                        if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                            break
                        eof = True
                        break
                    if not chunk:
                        eof = True
                        break
                    now = time.monotonic()
                    session.last_output_at = now
                    for status in session.osc_parser.feed(chunk):
                        session.osc_status = status
                        session.osc_status_at = now
                    session.buffer.append(chunk)

                if eof:
                    break
        finally:
            session.buffer.flush()
            session.close_master_fd()
            # Si el proceso ya termino por su cuenta, su configuracion MCP (con el token) no debe quedar en disco.
            try:
                session.process.wait(timeout=2.0)
            except Exception:
                pass
            if not session.is_alive():
                self._mcp_store.cleanup(session.session_id)

    @staticmethod
    def _signal_group(session: _PtySession, sig: int) -> bool:
        """Envia `sig` al grupo de procesos del panel (o solo al lider si no hay grupo propio)."""
        if session.pgid > 1:
            try:
                os.killpg(session.pgid, sig)
                return True
            except (ProcessLookupError, OSError):
                pass
        try:
            session.process.send_signal(sig)
            return True
        except (ProcessLookupError, OSError):
            return False

    def _shutdown_session(self, session: _PtySession) -> Optional[int]:
        """Cierre ordenado como el de una terminal real: SIGHUP, luego SIGTERM, luego SIGKILL (EGM-77)."""
        proc = session.process

        if session.paused and proc.poll() is None and session.pgid > 1:
            try:
                os.killpg(session.pgid, signal.SIGCONT)
            except (ProcessLookupError, OSError):
                pass
            session.paused = False

        # Descendientes antes de senalar: los que se desligan del grupo (setsid) o quedan huerfanos tras
        # morir el lider ya no se encontrarian despues.
        descendants: List[psutil.Process] = []
        try:
            descendants = psutil.Process(session.pid).children(recursive=True)
        except (psutil.Error, OSError):
            descendants = []

        if proc.poll() is None:
            for sig, grace in ((signal.SIGHUP, 0.4), (signal.SIGTERM, 0.5)):
                if not self._signal_group(session, sig):
                    break
                try:
                    proc.wait(timeout=grace)
                    break
                except subprocess.TimeoutExpired:
                    continue
            if proc.poll() is None:
                self._signal_group(session, signal.SIGKILL)
                try:
                    proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    pass

        # Nadie debe sobrevivir al panel: lo que siga vivo del grupo o de los descendientes capturados.
        if session.pgid > 1:
            try:
                os.killpg(session.pgid, 0)
            except (ProcessLookupError, OSError):
                pass
            else:
                try:
                    os.killpg(session.pgid, signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass
        for child in descendants:
            try:
                if child.is_running():
                    child.kill()
            except (psutil.Error, OSError):
                pass

        # Dejar al lector drenar EOF/EIO tras morir el esclavo antes de cerrar el master_fd.
        thread = session.reader_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=0.3)

        session.stop_event.set()
        session.close_master_fd()

        if thread is not None and thread is not threading.current_thread() and thread.is_alive():
            thread.join(timeout=0.3)

        session.buffer.flush()
        self._mcp_store.cleanup(session.session_id)
        return proc.poll()


    def _spawn_reserved(
        self,
        *,
        norm_pane: str,
        pane_index: int,
        stale_session: Optional[_PtySession],
        alive_others: int,
        cli_profile: str,
        role: str,
        resolved_cmd: List[str],
        resolved_cwd: Optional[str],
        cols_int: int,
        rows_int: int,
        env_overrides: Optional[Dict[str, Optional[str]]],
        hub_url: Optional[str],
        session_token: Optional[str],
        node_uuid: Optional[str],
        use_trampoline: bool,
        launch_spec: Optional[CliSpec] = None,
    ) -> Dict[str, Any]:
        """Resto del spawn con el panel ya reservado en `_spawning` (lo libera `spawn_session`)."""
        if alive_others >= self.max_panes:
            return self._pane_limit_response(norm_pane, "max_panes", f"Máximo de {self.max_panes} paneles activos alcanzado.")
        if alive_others >= 1:
            available_mb = self._memory_probe()
            if available_mb < self.min_available_mb:
                return self._pane_limit_response(
                    norm_pane,
                    "low_memory",
                    f"Memoria disponible insuficiente ({available_mb:.0f} MB < {self.min_available_mb} MB) para abrir otro panel.",
                )

        if stale_session is not None:
            self._shutdown_session(stale_session)

        session_id = f"{norm_pane}-{uuid.uuid4().hex[:12]}"
        try:
            injection = self._mcp_store.prepare(
                launch_spec, session_id=session_id, hub_url=hub_url, token=session_token
            )
        except McpInjectionError as exc:
            return {
                "success": False,
                "degraded": False,
                "code": "MCP_CONFIG_FAILED",
                "pane_id": norm_pane,
                "message": f"No se pudo preparar la conexion MCP del panel: {exc}",
            }
        resolved_cmd = [*resolved_cmd, *injection.argv]
        launch_cmd = self._build_launch_command(resolved_cmd, use_trampoline=use_trampoline)
        child_env = self._build_child_env(
            norm_pane=norm_pane,
            hub_url=hub_url,
            session_token=session_token,
            node_uuid=node_uuid,
            env_overrides={**(env_overrides or {}), **injection.env},
            use_trampoline=use_trampoline,
        )

        master_fd: Optional[int] = None
        slave_fd: Optional[int] = None
        proc: Optional[subprocess.Popen[Any]] = None

        try:
            assert pty is not None
            master_fd, slave_fd = pty.openpty()
            self._set_winsize(slave_fd, cols_int, rows_int)
            self._set_nonblocking(master_fd)

            proc = subprocess.Popen(
                launch_cmd,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                preexec_fn=os.setsid,
                close_fds=True,
                cwd=resolved_cwd,
                env=child_env,
                **no_window_kwargs(),
            )
            os.close(slave_fd)
            slave_fd = None
        except OSError as exc:
            if slave_fd is not None:
                try:
                    os.close(slave_fd)
                except OSError:
                    pass
            if master_fd is not None:
                try:
                    os.close(master_fd)
                except OSError:
                    pass
            if proc is not None and proc.poll() is None:
                try:
                    proc.kill()
                    proc.wait(timeout=0.5)
                except OSError:
                    pass
            self._mcp_store.cleanup(session_id)
            return {
                "success": False,
                "degraded": True,
                "code": "PTY_OS_ERROR",
                "host_failure": True,
                "pane_id": norm_pane,
                "error": str(exc),
                "message": f"Fallo del subsistema PTY al iniciar sesión en {norm_pane}: {exc}",
                "manual_command": DEFAULT_MANUAL_COMMAND,
                "mcp_config": _default_mcp_config(),
            }

        try:
            pgid = os.getpgid(proc.pid)
        except OSError:
            pgid = proc.pid

        session = _PtySession(
            pane_id=norm_pane,
            pane_index=pane_index,
            session_id=session_id,
            cli_profile=cli_profile,
            role=role,
            node_uuid=node_uuid,
            command=resolved_cmd,
            launch_command=launch_cmd,
            process=proc,
            pid=proc.pid,
            pgid=pgid,
            master_fd=master_fd,
            cols=cols_int,
            rows=rows_int,
            started_at=datetime.now(timezone.utc).isoformat(),
            buffer=PtyOutputRingBuffer(max_bytes=self.ring_max_bytes),
            cwd=resolved_cwd,
            mcp_injection=injection.public(),
        )
        reader = threading.Thread(
            target=self._reader_loop,
            args=(session,),
            name=f"pty-reader-{session_id}",
            daemon=True,
        )
        session.reader_thread = reader

        with self._lock:
            self._sessions[norm_pane] = session

        reader.start()
        return {
            "success": True,
            "degraded": False,
            "pane_id": norm_pane,
            "session_id": session_id,
            "pid": proc.pid,
            "cli_profile": cli_profile,
            "role": role,
            "node_uuid": node_uuid,
            "cols": cols_int,
            "rows": rows_int,
            "mcp_injection": injection.public(),
        }

    def send_input(self, pane_id: Any, data: Union[str, bytes]) -> Dict[str, Any]:
        norm_pane, _ = self._normalize_pane(pane_id)
        if isinstance(data, str):
            raw = data.encode("utf-8")
        elif isinstance(data, (bytes, bytearray, memoryview)):
            raw = bytes(data)
        else:
            raise ValueError("data debe ser str o bytes.")

        with self._lock:
            session = self._sessions.get(norm_pane)

        if session is None:
            return {
                "success": False,
                "code": "SESSION_NOT_FOUND",
                "pane_id": norm_pane,
                "message": f"No hay sesión registrada en {norm_pane}.",
            }
        if not session.is_alive() or session.master_fd is None:
            return {
                "success": False,
                "code": "SESSION_NOT_ALIVE",
                "pane_id": norm_pane,
                "session_id": session.session_id,
                "exit_code": session.exit_code(),
                "message": f"La sesión en {norm_pane} ya finalizó.",
            }

        total_written = 0
        view = memoryview(raw)
        with session.fd_lock:
            fd = session.master_fd
            if fd is None:
                return {
                    "success": False,
                    "code": "SESSION_NOT_ALIVE",
                    "pane_id": norm_pane,
                    "session_id": session.session_id,
                    "message": f"Descriptor PTY cerrado en {norm_pane}.",
                }
            while total_written < len(view):
                try:
                    n = os.write(fd, view[total_written:])
                    if n <= 0:
                        raise OSError("os.write en PTY devolvió 0 bytes")
                    total_written += n
                except BlockingIOError:
                    _, wlist, _ = select.select([], [fd], [], 0.5)
                    if not wlist:
                        raise TimeoutError(f"Timeout escribiendo en PTY {norm_pane}")

        if total_written > 0:
            session.last_input_at = time.monotonic()

        return {
            "success": True,
            "pane_id": norm_pane,
            "session_id": session.session_id,
            "bytes_written": total_written,
        }

    def resize_session(self, pane_id: Any, cols: int, rows: int) -> Dict[str, Any]:
        norm_pane, _ = self._normalize_pane(pane_id)
        cols_int = int(cols)
        rows_int = int(rows)
        if cols_int <= 0 or rows_int <= 0:
            raise ValueError("cols y rows deben ser > 0.")

        with self._lock:
            session = self._sessions.get(norm_pane)

        if session is None:
            return {
                "success": False,
                "code": "SESSION_NOT_FOUND",
                "pane_id": norm_pane,
                "message": f"No hay sesión registrada en {norm_pane}.",
            }
        if not session.is_alive() or session.master_fd is None:
            return {
                "success": False,
                "code": "SESSION_NOT_ALIVE",
                "pane_id": norm_pane,
                "session_id": session.session_id,
                "exit_code": session.exit_code(),
                "message": f"La sesión en {norm_pane} no está activa.",
            }

        with session.fd_lock:
            if session.master_fd is None:
                return {
                    "success": False,
                    "code": "SESSION_NOT_ALIVE",
                    "pane_id": norm_pane,
                    "session_id": session.session_id,
                }
            self._set_winsize(session.master_fd, cols_int, rows_int)
            session.cols = cols_int
            session.rows = rows_int

        if session.pgid > 1:
            try:
                os.killpg(session.pgid, signal.SIGWINCH)
            except (ProcessLookupError, OSError):
                pass

        return {
            "success": True,
            "pane_id": norm_pane,
            "session_id": session.session_id,
            "cols": cols_int,
            "rows": rows_int,
        }

    def pause_session(self, pane_id: Any, paused: bool = True) -> Dict[str, Any]:
        norm_pane, _ = self._normalize_pane(pane_id)
        with self._lock:
            session = self._sessions.get(norm_pane)

        if session is None:
            return {
                "success": False,
                "code": "SESSION_NOT_FOUND",
                "pane_id": norm_pane,
                "message": f"No hay sesión registrada en {norm_pane}.",
            }
        if not session.is_alive():
            return {
                "success": False,
                "code": "SESSION_NOT_ALIVE",
                "pane_id": norm_pane,
                "session_id": session.session_id,
                "exit_code": session.exit_code(),
                "message": f"La sesión en {norm_pane} no está activa.",
            }

        sig = signal.SIGSTOP if paused else signal.SIGCONT
        try:
            if session.pgid > 1:
                os.killpg(session.pgid, sig)
            else:
                os.kill(session.pid, sig)
        except ProcessLookupError:
            return {
                "success": False,
                "code": "SESSION_NOT_ALIVE",
                "pane_id": norm_pane,
                "session_id": session.session_id,
                "exit_code": session.exit_code(),
            }

        session.paused = bool(paused)
        return {
            "success": True,
            "pane_id": norm_pane,
            "session_id": session.session_id,
            "paused": session.paused,
        }
