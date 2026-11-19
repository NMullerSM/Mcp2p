"""
Host de terminales para Windows (`WinPtyHostConnector`): implementa `AgentTerminalPort` sobre ConPTY a traves de
la API de bajo nivel de `pywinpty` (`winpty.PTY`, backend ConPTY forzado: sin respaldo silencioso a winpty).

Decisiones (dossier `38_TERMINALES_WINDOWS_CONPTY.md`):
- Un hilo lector por sesion SIEMPRE drena el pseudo-terminal hacia el `PtyOutputRingBuffer` acotado; la lectura
  nunca se pausa (ni siquiera con el panel en pausa) porque `ClosePseudoConsole` puede colgarse con salida sin
  drenar en builds anteriores a 26100. El hilo es el dueno del objeto `PTY`: lo libera al terminar.
- Sin procesos huerfanos: el proceso raiz se asigna a un Job Object `KILL_ON_JOB_CLOSE` (`win_job.py`); si no se
  puede, o ademas, se mata el arbol con `psutil` (descendientes capturados ANTES de matar al raiz).
- Pausa = `NtSuspendProcess` (via `psutil.suspend`) sobre el raiz y sus descendientes.
- Estado de actividad por silencio de salida. El OSC 7501 no se interpreta en Windows (ConPTY no garantiza su
  paso): `osc_status` queda siempre vacio y `activity_source` es `silence_heuristic`.
- Los codigos de error son los del conector POSIX.
"""

from __future__ import annotations

import logging
import shlex
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Union

import psutil

from src.adapters.terminal.cli_catalog import CliSpec
from src.adapters.terminal.pty_common import (
    DEFAULT_MANUAL_COMMAND,
    Osc7501Parser,
    PtyHostBase,
    PtyOutputRingBuffer,
    build_child_env,
    default_mcp_config,
)
from src.adapters.terminal.win_job import JobObjectError, WindowsJobObject
from src.adapters.terminal.win_launch import (
    WindowsLaunchError,
    build_env_block,
    build_windows_launch,
    resolve_windows_executable,
)
from src.runtime.terminal_platform import (
    import_winpty,
    is_windows_platform,
    unsupported_platform_response,
    windows_conpty_status,
)

logger = logging.getLogger("mcp2p.adapters.terminal.winpty_host")

READER_IDLE_SLEEP_S = 0.01
READER_EXIT_GRACE_S = 0.25  # tras morir el proceso, tiempo para recoger la ultima salida en vuelo
KILL_WAIT_S = 2.0


@dataclass
class _WinPtySession:
    pane_id: str
    pane_index: int
    session_id: str
    cli_profile: str
    role: str
    node_uuid: Optional[str]
    command: List[str]
    launch_command: List[str]
    pty: Optional[Any]
    pid: int
    cols: int
    rows: int
    started_at: str
    buffer: PtyOutputRingBuffer
    job: Optional[WindowsJobObject] = None
    tree_kill: str = "psutil"  # "job_object" | "psutil"
    paused: bool = False
    stop_event: threading.Event = field(default_factory=threading.Event)
    io_lock: threading.Lock = field(default_factory=threading.Lock)
    reader_thread: Optional[threading.Thread] = None
    cwd: Optional[str] = None
    mcp_injection: Dict[str, Any] = field(default_factory=dict)
    last_output_at: float = field(default_factory=time.monotonic)
    last_input_at: float = 0.0
    osc_status: Optional[str] = None
    osc_status_at: float = 0.0
    osc_parser: Osc7501Parser = field(default_factory=Osc7501Parser, repr=False)
    suspended: List[psutil.Process] = field(default_factory=list)
    reader_error: Optional[str] = None
    bytes_read: int = 0
    _dead: bool = False
    _exit_code: Optional[int] = None

    def is_alive(self) -> bool:
        if self._dead:
            return False
        pty = self.pty
        if pty is None:
            self._dead = True
            return False
        try:
            alive = bool(pty.isalive())
        except Exception:
            alive = False
        if not alive:
            self._record_exit(pty)
        return alive

    def _record_exit(self, pty: Any) -> None:
        if self._dead:
            return
        try:
            code = pty.get_exitstatus()
            self._exit_code = int(code) if code is not None else None
        except Exception:
            self._exit_code = None
        self._dead = True

    def exit_code(self) -> Optional[int]:
        self.is_alive()
        return self._exit_code


def _strip_quotes(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        return token[1:-1]
    return token


class WinPtyHostConnector(PtyHostBase):
    """
    Adaptador de `AgentTerminalPort` para Windows 10 1809+ (ConPTY) respaldado por `pywinpty`.

    Parametros de inyeccion (solo tests): `winpty_module` (doble de `winpty`), `job_factory` (crea el Job Object),
    `is_file`/`environ` (resolucion `PATHEXT` simulada) y `assume_available` (omite la comprobacion de plataforma).
    """

    MODE = "windows_conpty"

    def __init__(
        self,
        *,
        winpty_module: Optional[Any] = None,
        job_factory: Optional[Callable[[], WindowsJobObject]] = None,
        is_file: Optional[Callable[[str], bool]] = None,
        environ: Optional[Mapping[str, str]] = None,
        assume_available: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._winpty_override = winpty_module
        self._job_factory = job_factory or WindowsJobObject
        self._is_file_override = is_file
        self._environ_override = environ
        if assume_available or winpty_module is not None:
            self.is_available = lambda: True  # type: ignore[method-assign]

    # -- disponibilidad -----------------------------------------------------

    @staticmethod
    def is_available() -> bool:
        return is_windows_platform() and windows_conpty_status()[0]

    def _winpty(self) -> Optional[Any]:
        return self._winpty_override if self._winpty_override is not None else import_winpty()

    def _platform_block(self, norm_pane: str) -> Optional[Dict[str, Any]]:
        if self.is_available() and self._winpty() is not None:
            return None
        if is_windows_platform():
            return {**unsupported_platform_response(), "pane_id": norm_pane}
        return {
            "success": False,
            "degraded": True,
            "code": "PTY_UNAVAILABLE",
            "pane_id": norm_pane,
            "message": "El backend ConPTY solo existe en Windows.",
            "manual_command": DEFAULT_MANUAL_COMMAND,
            "mcp_config": default_mcp_config(),
        }

    # -- resolucion del ejecutable -------------------------------------------

    @staticmethod
    def _split_command_string(command: str) -> List[str]:
        return [_strip_quotes(t) for t in shlex.split(command, posix=False)]

    def _which_executable(self, binary: str, cwd: Optional[str]) -> Optional[str]:  # type: ignore[override]
        kwargs: Dict[str, Any] = {"cwd": cwd, "environ": self._environ_override}
        if self._is_file_override is not None:
            kwargs["is_file"] = self._is_file_override
        return resolve_windows_executable(binary, **kwargs)

    # -- serializacion con datos de diagnostico ---------------------------------

    @classmethod
    def _serialize_session(cls, session: Any) -> Dict[str, Any]:
        data = super()._serialize_session(session)
        data["backend"] = "conpty"
        data["tree_kill"] = session.tree_kill
        if session.reader_error:
            data["reader_error"] = session.reader_error
        return data

    def poll_output(self, pane_id: Any, since_seq: int = 0) -> Dict[str, Any]:
        res = super().poll_output(pane_id, since_seq)
        if res.get("success"):
            with self._lock:
                session = self._sessions.get(res["pane_id"])
            if session is not None and session.reader_error:
                res["reader_error"] = session.reader_error
        return res

    # -- lector -----------------------------------------------------------------

    def _reader_loop(self, session: _WinPtySession) -> None:
        pty = session.pty
        try:
            exit_deadline: Optional[float] = None
            while pty is not None and not session.stop_event.is_set():
                try:
                    data = pty.read(blocking=False)
                except Exception as exc:  # EOF ("Standard out reached EOF") o error real del pseudo-terminal
                    if session.is_alive() and not session.stop_event.is_set():
                        session.reader_error = f"{type(exc).__name__}: {exc}"
                        logger.warning("Lectura ConPTY fallida en %s: %s", session.pane_id, session.reader_error)
                    break
                if data:
                    session.last_output_at = time.monotonic()
                    session.bytes_read += len(data)
                    session.buffer.append(data)
                    exit_deadline = None
                    continue
                if not session.is_alive():
                    now = time.monotonic()
                    if exit_deadline is None:
                        exit_deadline = now + READER_EXIT_GRACE_S
                    if now >= exit_deadline:
                        break
                session.stop_event.wait(READER_IDLE_SLEEP_S)
        finally:
            try:
                session.buffer.flush()
                session.is_alive()  # captura el codigo de salida mientras el PTY existe
            except Exception:  # pragma: no cover - defensivo
                pass
            session.pty = None
            del pty  # libera ClosePseudoConsole aqui (hilo demonio), nunca en close_session
            if not session.is_alive():
                self._mcp_store.cleanup(session.session_id)

    # -- arbol de procesos ----------------------------------------------------------

    @staticmethod
    def _snapshot_descendants(pid: int) -> List[psutil.Process]:
        try:
            return psutil.Process(pid).children(recursive=True)
        except (psutil.Error, OSError):
            return []

    @staticmethod
    def _kill_procs(procs: List[psutil.Process]) -> None:
        for proc in procs:
            try:
                if proc.is_running():
                    proc.kill()
            except (psutil.Error, OSError):
                pass
        try:
            psutil.wait_procs(procs, timeout=1.0)
        except Exception:  # pragma: no cover - defensivo
            pass

    @staticmethod
    def _resume_procs(session: _WinPtySession) -> None:
        procs, session.suspended = session.suspended, []
        for proc in procs:
            try:
                proc.resume()
            except (psutil.Error, OSError):
                pass
        session.paused = False

    def _shutdown_session(self, session: _WinPtySession) -> Optional[int]:
        """
        Cierre sin huerfanos: reanuda si estaba en pausa, captura descendientes, mata el job (o el arbol con
        psutil), espera al raiz, barre lo que quede y deja al lector drenar y liberar el PTY.
        """
        if session.paused:
            self._resume_procs(session)

        descendants = self._snapshot_descendants(session.pid)
        root_alive = session.is_alive()
        if root_alive or descendants:
            job_killed = session.job.terminate(1) if session.job is not None else False
            if not job_killed:
                try:
                    root = psutil.Process(session.pid)
                    self._kill_procs([root, *descendants])
                except (psutil.Error, OSError):
                    self._kill_procs(descendants)
            deadline = time.monotonic() + KILL_WAIT_S
            while session.is_alive() and time.monotonic() < deadline:
                time.sleep(0.02)
            if session.is_alive():
                try:
                    self._kill_procs([psutil.Process(session.pid)])
                except (psutil.Error, OSError):
                    pass

        # Barrido final: nadie sobrevive (escapes del job, ventana previa a la asignacion).
        self._kill_procs(descendants)

        thread = session.reader_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=READER_EXIT_GRACE_S + 1.0)
        session.stop_event.set()
        if thread is not None and thread is not threading.current_thread() and thread.is_alive():
            thread.join(timeout=1.0)

        if session.job is not None:
            session.job.close()
        session.buffer.flush()
        self._mcp_store.cleanup(session.session_id)
        return session.exit_code()

    # -- spawn ---------------------------------------------------------------------------

    def _spawn_reserved(
        self,
        *,
        norm_pane: str,
        pane_index: int,
        stale_session: Optional[_WinPtySession],
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
        use_trampoline: bool = False,
        launch_spec: Optional[CliSpec] = None,
        launch_request: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        refused = self._admission_error(norm_pane, alive_others)
        if refused is not None:
            return refused

        if stale_session is not None:
            self._shutdown_session(stale_session)

        winpty = self._winpty()
        if winpty is None:  # carrera: desaparecio entre la comprobacion y el spawn
            return {**unsupported_platform_response(), "pane_id": norm_pane}

        session_id = self._new_session_id(norm_pane)
        injection, mcp_error = self._prepare_mcp(launch_spec, session_id, hub_url, session_token, norm_pane)
        if mcp_error is not None or injection is None:
            return mcp_error or {"success": False, "code": "MCP_CONFIG_FAILED", "pane_id": norm_pane}
        aug, aug_error = self._prepare_launch_augmentation(
            launch_spec, session_id, norm_pane, hub_url, launch_request
        )
        if aug_error is not None:
            return aug_error
        full_cmd = [*resolved_cmd, *injection.argv, *aug.argv]

        exe = self._which_executable(full_cmd[0], resolved_cwd)
        if exe is None:
            self._mcp_store.cleanup(session_id)
            return {
                "success": False,
                "degraded": False,
                "code": "CLI_BINARY_NOT_FOUND",
                "pane_id": norm_pane,
                "cli_profile": cli_profile,
                "missing_binary": full_cmd[0],
                "message": f"No se encontró el ejecutable '{full_cmd[0]}'.",
                "manual_command": DEFAULT_MANUAL_COMMAND,
                "mcp_config": default_mcp_config(),
            }
        try:
            launch = build_windows_launch([exe, *full_cmd[1:]], comspec=(self._environ_override or {}).get("COMSPEC"))
        except WindowsLaunchError as exc:
            self._mcp_store.cleanup(session_id)
            return {
                "success": False,
                "degraded": False,
                "code": exc.code,
                "pane_id": norm_pane,
                "cli_profile": cli_profile,
                "message": str(exc),
            }

        child_env = build_child_env(
            norm_pane=norm_pane,
            hub_url=hub_url,
            session_token=session_token,
            node_uuid=node_uuid,
            env_overrides={**(env_overrides or {}), **injection.env, **aug.env},
        )

        backend_enum = getattr(winpty, "Backend", None)
        conpty_backend = getattr(backend_enum, "ConPTY", 0)
        pty: Any = None
        try:
            pty = winpty.PTY(cols_int, rows_int, backend=conpty_backend)
            spawned = pty.spawn(launch.appname, cmdline=launch.cmdline, cwd=resolved_cwd, env=build_env_block(child_env))
            if not spawned:
                raise RuntimeError("PTY.spawn devolvio False")
            pid = pty.pid
            if not pid:
                raise RuntimeError("PTY.spawn no devolvio PID")
        except Exception as exc:
            pty = None  # libera el pseudo-terminal a medio crear
            self._mcp_store.cleanup(session_id)
            logger.warning("ConPTY: fallo al lanzar %r %r: %s", launch.appname, launch.cmdline, exc)
            return {
                "success": False,
                "degraded": True,
                "code": "PTY_OS_ERROR",
                "host_failure": True,
                "pane_id": norm_pane,
                "error": f"{type(exc).__name__}: {exc}",
                "launch": {"appname": launch.appname, "cmdline": launch.cmdline, "kind": launch.kind},
                "message": f"Fallo del subsistema ConPTY al iniciar sesión en {norm_pane}: {exc}",
                "manual_command": DEFAULT_MANUAL_COMMAND,
                "mcp_config": default_mcp_config(),
            }

        job: Optional[WindowsJobObject] = None
        tree_kill = "psutil"
        try:
            job = self._job_factory()
            job.assign_pid(int(pid))
            tree_kill = "job_object"
        except (JobObjectError, OSError) as exc:
            logger.warning("Job Object no disponible para %s (PID %s); se usara psutil: %s", norm_pane, pid, exc)
            if job is not None:
                job.close()
            job = None

        session = _WinPtySession(
            pane_id=norm_pane,
            pane_index=pane_index,
            session_id=session_id,
            cli_profile=cli_profile,
            role=role,
            node_uuid=node_uuid,
            command=full_cmd,
            launch_command=launch.display,
            pty=pty,
            pid=int(pid),
            cols=cols_int,
            rows=rows_int,
            started_at=self._utc_now_iso(),
            buffer=PtyOutputRingBuffer(max_bytes=self.ring_max_bytes),
            job=job,
            tree_kill=tree_kill,
            cwd=resolved_cwd,
            mcp_injection=injection.public(),
        )
        del pty
        reader = threading.Thread(
            target=self._reader_loop, args=(session,), name=f"conpty-reader-{session_id}", daemon=True
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
            "pid": session.pid,
            "cli_profile": cli_profile,
            "role": role,
            "node_uuid": node_uuid,
            "cols": cols_int,
            "rows": rows_int,
            "mcp_injection": injection.public(),
            "launch_meta": dict(aug.meta),
            "launch_notes": list(aug.notes),
            "backend": "conpty",
            "tree_kill": tree_kill,
        }

    # -- entrada / tamano / pausa ---------------------------------------------------------

    def _live_session(self, pane_id: Any, not_alive_message: str):
        norm_pane, _ = self._normalize_pane(pane_id)
        with self._lock:
            session = self._sessions.get(norm_pane)
        if session is None:
            return norm_pane, None, self._session_not_found(norm_pane)
        if not session.is_alive() or session.pty is None:
            return norm_pane, None, self._session_not_alive(norm_pane, session, not_alive_message)
        return norm_pane, session, None

    @staticmethod
    def _io_failure(norm_pane: str, session: _WinPtySession, action: str, exc: Exception) -> Dict[str, Any]:
        if not session.is_alive():
            return WinPtyHostConnector._session_not_alive(norm_pane, session, f"La sesión en {norm_pane} ya finalizó.")
        return {
            "success": False,
            "degraded": True,
            "code": "PTY_OS_ERROR",
            "host_failure": True,
            "pane_id": norm_pane,
            "session_id": session.session_id,
            "error": f"{type(exc).__name__}: {exc}",
            "message": f"Fallo de ConPTY al {action} en {norm_pane}: {exc}",
        }

    def send_input(
        self,
        pane_id: Any,
        data: Union[str, bytes],
        *,
        expected_session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if isinstance(data, str):
            text = data
        elif isinstance(data, (bytes, bytearray, memoryview)):
            text = bytes(data).decode("utf-8", errors="replace")
        else:
            raise ValueError("data debe ser str o bytes.")
        norm_pane, session, error = self._live_session(pane_id, "La sesión ya finalizó.")
        if error is not None or session is None:
            return error or {}
        with session.io_lock:
            pty = session.pty
            if pty is None:
                return self._session_not_alive(norm_pane, session, f"Descriptor ConPTY cerrado en {norm_pane}.")
            if expected_session_id is not None and session.session_id != expected_session_id:
                return {
                    "success": False,
                    "code": "INCARNATION_CHANGED",
                    "pane_id": norm_pane,
                    "session_id": session.session_id,
                    "message": "La sesión del panel cambió; no se escribió nada.",
                }
            try:
                pty.write(text)
            except Exception as exc:
                return self._io_failure(norm_pane, session, "escribir", exc)
        written = len(text.encode("utf-8"))
        if written > 0:
            session.last_input_at = time.monotonic()
        return {
            "success": True,
            "pane_id": norm_pane,
            "session_id": session.session_id,
            "bytes_written": written,
        }

    def resize_session(self, pane_id: Any, cols: int, rows: int) -> Dict[str, Any]:
        cols_int = int(cols)
        rows_int = int(rows)
        if cols_int <= 0 or rows_int <= 0:
            raise ValueError("cols y rows deben ser > 0.")
        norm_pane, session, error = self._live_session(pane_id, "La sesión no está activa.")
        if error is not None or session is None:
            return error or {}
        with session.io_lock:
            pty = session.pty
            if pty is None:
                return self._session_not_alive(norm_pane, session, f"La sesión en {norm_pane} no está activa.")
            try:
                pty.set_size(cols_int, rows_int)
            except Exception as exc:
                return self._io_failure(norm_pane, session, "redimensionar", exc)
        session.cols = cols_int
        session.rows = rows_int
        return {
            "success": True,
            "pane_id": norm_pane,
            "session_id": session.session_id,
            "cols": cols_int,
            "rows": rows_int,
        }

    def pause_session(self, pane_id: Any, paused: bool = True) -> Dict[str, Any]:
        norm_pane, session, error = self._live_session(pane_id, "La sesión no está activa.")
        if error is not None or session is None:
            return error or {}
        if not hasattr(psutil.Process, "suspend"):
            return {
                "success": False,
                "code": "PAUSE_NOT_SUPPORTED",
                "pane_id": norm_pane,
                "session_id": session.session_id,
                "message": "Esta plataforma no permite suspender procesos.",
            }
        if paused:
            if session.paused:
                return {"success": True, "pane_id": norm_pane, "session_id": session.session_id, "paused": True}
            done: List[psutil.Process] = []
            try:
                procs = [psutil.Process(session.pid), *self._snapshot_descendants(session.pid)]
                for proc in procs:
                    try:
                        proc.suspend()
                        done.append(proc)
                    except (psutil.NoSuchProcess, psutil.ZombieProcess):
                        continue  # un descendiente murio entre la captura y la suspension
                if not done:
                    raise psutil.NoSuchProcess(session.pid)
            except psutil.NoSuchProcess:
                return self._session_not_alive(norm_pane, session, f"La sesión en {norm_pane} ya finalizó.")
            except (psutil.Error, OSError) as exc:
                for proc in done:
                    try:
                        proc.resume()
                    except (psutil.Error, OSError):
                        pass
                return {
                    "success": False,
                    "code": "PAUSE_FAILED",
                    "pane_id": norm_pane,
                    "session_id": session.session_id,
                    "message": f"No se pudo suspender el panel {norm_pane}: {exc}",
                }
            session.suspended = done
            session.paused = True
        else:
            self._resume_procs(session)
        return {
            "success": True,
            "pane_id": norm_pane,
            "session_id": session.session_id,
            "paused": session.paused,
        }


__all__ = ["WinPtyHostConnector"]
