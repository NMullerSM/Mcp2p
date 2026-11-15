"""
Host POSIX PTY (`PosixPtyHostConnector`) y buffer circular incremental (`PtyOutputRingBuffer`)
que implementan `AgentTerminalPort` para la malla de terminales interactivas (EGM-24/59/63).
"""

from __future__ import annotations

import codecs
import errno
import logging
import os
import select
import shlex
import shutil
import signal
import struct
import subprocess
import sys
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple, Union

import psutil

from src.adapters.terminal.cli_catalog import (
    CliCatalogService,
    SettingsStore,
    legacy_profiles,
    load_repo_catalog,
)
from src.proc_utils import no_window_kwargs
from src.runtime.terminal_platform import is_unsupported_terminal_platform, unsupported_platform_response

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

DEFAULT_MAX_PANES = 4
HARD_MAX_PANES = 6  # tope absoluto configurable (EGM-22 / plan 2026-11-15, seccion 5b)
DEFAULT_MIN_AVAILABLE_MB = 1024  # admision por memoria: MemAvailable minima para abrir otro panel
MAX_PANES_SETTING_NAMESPACE = "terminal_matrix"
MAX_PANES_SETTING_KEY = "max_panes"
DEFAULT_RING_BYTES = 262144  # 256 KB
DEFAULT_MANUAL_COMMAND = "mcp2p --mcp-server --transport streamable-http --host 127.0.0.1 --port 8765"

_SHELL_DEFAULT = os.environ.get("SHELL") or "/bin/sh"


def _build_cli_profiles() -> Dict[str, List[str]]:
    """`CLI_PROFILES` se deriva del catalogo versionado del repo (`cli_catalog.json`)."""
    try:
        return legacy_profiles(load_repo_catalog(), _SHELL_DEFAULT)
    except Exception as exc:  # pragma: no cover - solo si el artefacto del build esta corrupto
        logger.error("cli_catalog.json no se pudo cargar; usando perfiles minimos: %s", exc)
        return {"claude": ["claude"], "custom_shell": [_SHELL_DEFAULT]}


CLI_PROFILES: Dict[str, List[str]] = _build_cli_profiles()

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _default_mcp_config() -> Dict[str, Any]:
    return {
        "mcpServers": {
            "mcp2p": {
                "url": "http://127.0.0.1:8765/mcp",
            }
        }
    }


class PaneLimitError(ValueError):
    """`pane_id` numericamente valido pero por encima del maximo de paneles configurado."""


def clamp_max_panes(value: Any, default: int = DEFAULT_MAX_PANES) -> int:
    """Convierte un valor externo en un maximo de paneles dentro de `1..HARD_MAX_PANES`."""
    if isinstance(value, bool):
        return default
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(1, min(HARD_MAX_PANES, number))


def load_configured_max_panes(settings: Optional[SettingsStore], default: int = DEFAULT_MAX_PANES) -> int:
    """Lee el maximo de paneles persistido (si existe y es valido); si no, `default`."""
    if settings is None:
        return default
    try:
        raw = settings.get_extension_setting(MAX_PANES_SETTING_NAMESPACE, MAX_PANES_SETTING_KEY, None)
    except Exception:
        return default
    return clamp_max_panes(raw, default) if raw is not None else default


def normalize_pane_id(pane_id: Any, *, max_panes: int = DEFAULT_MAX_PANES) -> Tuple[str, int]:
    """
    Normaliza `pane_id` aceptando `1..max_panes`, `"1".."4"` o `"pane_1".."pane_4"`.
    Devuelve `(canonical_pane_id, pane_index)` o lanza `ValueError` si es inválido.
    """
    limit = max(1, int(max_panes))
    if isinstance(pane_id, bool):
        raise ValueError(f"pane_id inválido: {pane_id!r} (se esperaba 1..{limit} o 'pane_1'..'pane_{limit}').")

    index: Optional[int] = None
    if isinstance(pane_id, int):
        index = pane_id
    elif isinstance(pane_id, str):
        raw = pane_id.strip()
        if raw.isdigit():
            index = int(raw)
        elif raw.startswith("pane_") and raw[5:].isdigit():
            index = int(raw[5:])

    if index is not None and index > limit:
        raise PaneLimitError(
            f"pane_id fuera de rango: {pane_id!r} (rango válido: 1..{limit} o 'pane_1'..'pane_{limit}')."
        )
    if index is None or index < 1:
        raise ValueError(
            f"pane_id fuera de rango: {pane_id!r} (rango válido: 1..{limit} o 'pane_1'..'pane_{limit}')."
        )
    return f"pane_{index}", index


def _read_process_rss_mb(pid: Optional[int]) -> float:
    """Lee el RSS actual en MiB vía `psutil.Process(pid)` tolerando desaparición o permisos."""
    if not pid or pid <= 0:
        return 0.0
    try:
        proc = psutil.Process(int(pid))
        rss_bytes = proc.memory_info().rss
        return round(max(0, int(rss_bytes)) / (1024.0 * 1024.0), 3)
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return 0.0


class PtyOutputRingBuffer:
    """
    Buffer circular thread-safe para salida PTY con cursor monótono (`seq`) y
    decodificación UTF-8 incremental (`errors="replace"`).
    """

    def __init__(self, max_bytes: int = DEFAULT_RING_BYTES) -> None:
        if int(max_bytes) <= 0:
            raise ValueError("max_bytes debe ser > 0.")
        self.max_bytes = int(max_bytes)
        self._lock = threading.RLock()
        self._chunks: Deque[Tuple[int, int, str]] = deque()
        self._total_bytes = 0
        self._seq = 0
        # Cualquier `since_seq < _min_untruncated_since` perdió chunks por eviction/recorte.
        self._min_untruncated_since = 0
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")

    @property
    def next_seq(self) -> int:
        with self._lock:
            return self._seq

    @property
    def total_bytes(self) -> int:
        with self._lock:
            return self._total_bytes

    def _evict_to_fit(self, incoming_bytes: int) -> None:
        while self._chunks and (self._total_bytes + incoming_bytes) > self.max_bytes:
            evicted_seq, evicted_len, _ = self._chunks.popleft()
            self._total_bytes -= evicted_len
            if evicted_seq > self._min_untruncated_since:
                self._min_untruncated_since = evicted_seq

    def append(self, data: Union[bytes, bytearray, memoryview, str]) -> int:
        """Agrega un chunk al ring buffer y retorna su número de secuencia monótono."""
        with self._lock:
            if isinstance(data, str):
                if not data:
                    return self._seq
                pending = self._decoder.decode(b"", final=True)
                raw = data.encode("utf-8", errors="replace")
                text = pending + raw.decode("utf-8", errors="replace")
            elif isinstance(data, (bytes, bytearray, memoryview)):
                raw = bytes(data)
                if not raw:
                    return self._seq
                text = self._decoder.decode(raw, final=False)
            else:
                raise TypeError(f"Tipo de dato no soportado en PtyOutputRingBuffer: {type(data).__name__}")

            self._seq += 1
            seq = self._seq
            raw_len = len(raw)

            if raw_len > self.max_bytes:
                # Vaciar chunks previos y conservar la cola que cabe en `max_bytes`.
                self._evict_to_fit(self.max_bytes + 1)
                tail = raw[-self.max_bytes :]
                self._decoder.reset()
                text = tail.decode("utf-8", errors="replace")
                raw_len = len(tail)
                self._min_untruncated_since = max(self._min_untruncated_since, seq)
            else:
                self._evict_to_fit(raw_len)

            self._chunks.append((seq, raw_len, text))
            self._total_bytes += raw_len
            return seq

    def flush(self) -> int:
        """Finaliza secuencias UTF-8 incompletas al alcanzar EOF."""
        with self._lock:
            tail = self._decoder.decode(b"", final=True)
            if not tail:
                return self._seq
            self._seq += 1
            seq = self._seq
            raw_len = len(tail.encode("utf-8", errors="replace"))
            if raw_len > self.max_bytes:
                raw_tail = tail.encode("utf-8", errors="replace")[-self.max_bytes :]
                tail = raw_tail.decode("utf-8", errors="replace")
                raw_len = len(raw_tail)
                self._evict_to_fit(self.max_bytes + 1)
                self._min_untruncated_since = max(self._min_untruncated_since, seq)
            else:
                self._evict_to_fit(raw_len)
            self._chunks.append((seq, raw_len, tail))
            self._total_bytes += raw_len
            return seq

    def poll_since(self, since_seq: int = 0) -> Dict[str, Any]:
        """Devuelve los chunks con `seq > since_seq` y metadatos de truncado/cursor."""
        cursor = max(0, int(since_seq))
        with self._lock:
            truncated = cursor < self._min_untruncated_since
            chunks = [
                {"seq": c_seq, "text": c_text}
                for c_seq, _, c_text in self._chunks
                if c_seq > cursor
            ]
            combined = "".join(item["text"] for item in chunks)
            return {
                "since_seq": since_seq,
                "next_seq": self._seq,
                "truncated": truncated,
                "chunks": chunks,
                "text": combined,
            }


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


class PosixPtyHostConnector:
    """
    Adaptador POSIX real de `AgentTerminalPort` respaldado por `pty.openpty()`,
    trampolín `--pty-exec` (`TIOCSCTTY`), control de ventana (`TIOCSWINSZ` + `SIGWINCH`),
    pausa (`SIGSTOP`/`SIGCONT`) y métricas `psutil`.
    """

    def __init__(
        self,
        *,
        max_panes: int = DEFAULT_MAX_PANES,
        ring_max_bytes: int = DEFAULT_RING_BYTES,
        gate: Optional[Any] = None,
        connector_id: str = "agent_terminal_matrix",
        catalog_service: Optional[CliCatalogService] = None,
        min_available_mb: int = DEFAULT_MIN_AVAILABLE_MB,
        memory_probe: Optional[Callable[[], float]] = None,
    ) -> None:
        if int(max_panes) < 1 or int(max_panes) > HARD_MAX_PANES:
            raise ValueError(f"max_panes debe estar entre 1 y {HARD_MAX_PANES}.")
        self._max_panes = int(max_panes)
        self.ring_max_bytes = int(ring_max_bytes)
        self.gate = gate
        self.connector_id = connector_id
        self.catalog_service = catalog_service if catalog_service is not None else CliCatalogService()
        self.min_available_mb = max(0, int(min_available_mb))
        self._memory_probe = memory_probe or self._default_memory_probe
        self._lock = threading.RLock()
        self._sessions: Dict[str, _PtySession] = {}

    @property
    def max_panes(self) -> int:
        return self._max_panes

    def set_max_panes(self, value: int) -> int:
        """Cambia el maximo (1..HARD_MAX_PANES). No cierra sesiones vivas: solo frena nuevas."""
        number = int(value)
        if number < 1 or number > HARD_MAX_PANES:
            raise ValueError(f"max_panes debe estar entre 1 y {HARD_MAX_PANES}.")
        self._max_panes = number
        return number

    @staticmethod
    def _default_memory_probe() -> float:
        try:
            return float(psutil.virtual_memory().available) / (1024.0 * 1024.0)
        except Exception:
            return float("inf")

    @staticmethod
    def is_available() -> bool:
        return (
            os.name == "posix"
            and sys.platform != "win32"
            and pty is not None
            and fcntl is not None
            and termios is not None
        )

    def _normalize_pane(self, pane_id: Any) -> Tuple[str, int]:
        return normalize_pane_id(pane_id, max_panes=self.max_panes)

    @staticmethod
    def _parse_command(command: Union[List[str], str]) -> List[str]:
        if isinstance(command, str):
            parts = shlex.split(command)
        elif isinstance(command, (list, tuple)):
            parts = [str(part) for part in command]
        else:
            raise ValueError("command debe ser str o secuencia de str.")
        if not parts or not parts[0]:
            raise ValueError("command no puede estar vacío.")
        return parts

    def _pane_limit_response(self, norm_pane: str, reason: str, message: str) -> Dict[str, Any]:
        return {
            "success": False,
            "degraded": False,
            "code": "PANE_LIMIT_REACHED",
            "reason": reason,
            "pane_id": norm_pane,
            "max_panes": self.max_panes,
            "message": message,
        }

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
        child_env = dict(os.environ)
        is_frozen = bool(getattr(sys, "frozen", False))

        # Evitar filtrar passphrases de firma o tokens de sesión de otros paneles al árbol del agente.
        for key in list(child_env.keys()):
            upper = key.upper()
            if upper == "MCP2P_SESSION_TOKEN":
                child_env.pop(key, None)
            elif upper.startswith("MCP2P_") and ("PASSPHRASE" in upper or "PRIVATE_KEY" in upper):
                child_env.pop(key, None)
            elif upper in ("SIGNING_KEY_PASSPHRASE", "KEY_PASSPHRASE"):
                child_env.pop(key, None)
            elif not is_frozen and (key.startswith("_PYI_") or key == "_MEIPASS"):
                child_env.pop(key, None)

        child_env["TERM"] = "xterm-256color"
        child_env["MCP2P_PANE_ID"] = norm_pane
        if hub_url is not None and str(hub_url).strip():
            child_env["MCP2P_HUB_URL"] = str(hub_url).strip()
        if session_token is not None and str(session_token).strip():
            child_env["MCP2P_SESSION_TOKEN"] = str(session_token).strip()
        if node_uuid is not None and str(node_uuid).strip():
            child_env["MCP2P_NODE_UUID"] = str(node_uuid).strip()

        if use_trampoline and not is_frozen:
            repo_str = str(_REPO_ROOT)
            existing_pp = child_env.get("PYTHONPATH", "")
            pp_parts = [p for p in existing_pp.split(os.pathsep) if p] if existing_pp else []
            if repo_str not in pp_parts:
                child_env["PYTHONPATH"] = os.pathsep.join([repo_str, *pp_parts]) if pp_parts else repo_str

        if env_overrides:
            for k, v in env_overrides.items():
                key_str = str(k)
                if v is None:
                    child_env.pop(key_str, None)
                else:
                    child_env[key_str] = str(v)

        return child_env

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
                    session.buffer.append(chunk)

                if eof:
                    break
        finally:
            session.buffer.flush()
            session.close_master_fd()

    def _shutdown_session(self, session: _PtySession) -> Optional[int]:
        proc = session.process

        if session.paused and proc.poll() is None and session.pgid > 1:
            try:
                os.killpg(session.pgid, signal.SIGCONT)
            except (ProcessLookupError, OSError):
                pass
            session.paused = False

        if proc.poll() is None:
            sent_term = False
            if session.pgid > 1:
                try:
                    os.killpg(session.pgid, signal.SIGTERM)
                    sent_term = True
                except (ProcessLookupError, OSError):
                    sent_term = False
            if not sent_term:
                try:
                    proc.terminate()
                except OSError:
                    pass

            try:
                proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                if proc.poll() is None:
                    sent_kill = False
                    if session.pgid > 1:
                        try:
                            os.killpg(session.pgid, signal.SIGKILL)
                            sent_kill = True
                        except (ProcessLookupError, OSError):
                            sent_kill = False
                    if not sent_kill:
                        try:
                            proc.kill()
                        except OSError:
                            pass
                    try:
                        proc.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
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
        return proc.poll()

    @staticmethod
    def _serialize_session(session: _PtySession) -> Dict[str, Any]:
        alive = session.is_alive()
        return {
            "pane_id": session.pane_id,
            "session_id": session.session_id,
            "cli_profile": session.cli_profile,
            "role": session.role,
            "node_uuid": session.node_uuid,
            "pid": session.pid,
            "alive": alive,
            "paused": session.paused,
            "rss_mb": _read_process_rss_mb(session.pid) if alive else 0.0,
            "cols": session.cols,
            "rows": session.rows,
            "started_at": session.started_at,
            "next_seq": session.buffer.next_seq,
        }

    # ------------------------------------------------------------------
    # AgentTerminalPort
    # ------------------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        sessions = self.list_sessions()
        return {
            "connector_id": self.connector_id,
            "available": self.is_available(),
            "mode": "posix_pty",
            "active_sessions": sum(1 for s in sessions if s["alive"]),
            "max_panes": self.max_panes,
            "hard_max_panes": HARD_MAX_PANES,
            "sessions": sessions,
        }

    def list_sessions(self) -> List[Dict[str, Any]]:
        with self._lock:
            snapshot = list(self._sessions.values())
        return [self._serialize_session(s) for s in snapshot]

    def spawn_session(
        self,
        pane_id: Any = 1,
        *,
        cli_profile: str = "custom_shell",
        role: str = "implementer",
        command: Optional[Union[List[str], str]] = None,
        cwd: Optional[Union[str, Path]] = None,
        cols: int = 100,
        rows: int = 28,
        env_overrides: Optional[Dict[str, Optional[str]]] = None,
        hub_url: Optional[str] = None,
        session_token: Optional[str] = None,
        node_uuid: Optional[str] = None,
        use_trampoline: bool = True,
    ) -> Dict[str, Any]:
        try:
            norm_pane, pane_index = self._normalize_pane(pane_id)
        except PaneLimitError as exc:
            return {
                "success": False,
                "degraded": False,
                "code": "PANE_LIMIT_REACHED",
                "reason": "pane_out_of_range",
                "pane_id": str(pane_id),
                "max_panes": self.max_panes,
                "message": str(exc),
            }
        cols_int = int(cols)
        rows_int = int(rows)
        if cols_int <= 0 or rows_int <= 0:
            raise ValueError("cols y rows deben ser > 0.")

        if is_unsupported_terminal_platform():
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

        resolved_cwd: Optional[str] = None
        if cwd is not None:
            resolved_cwd = str(Path(cwd))
            if not os.path.isdir(resolved_cwd):
                return {
                    "success": False,
                    "degraded": False,
                    "code": "INVALID_CWD",
                    "pane_id": norm_pane,
                    "message": f"Directorio de trabajo inexistente: {resolved_cwd}",
                }

        profile_label = cli_profile if isinstance(cli_profile, str) else repr(cli_profile)
        if command is not None:
            resolved_cmd = self._parse_command(command)
            binary_ok = self._which_executable(resolved_cmd[0], resolved_cwd) is not None
            display_name = profile_label
        else:
            argv, resolution, spec = self.catalog_service.build_launch(cli_profile)
            if spec is None:
                known = self.catalog_service.catalog().ids()
                return {
                    "success": False,
                    "degraded": False,
                    "code": "CLI_PROFILE_UNKNOWN",
                    "pane_id": norm_pane,
                    "cli_profile": profile_label,
                    "known_profiles": known,
                    "message": f"Perfil de CLI desconocido: {profile_label}. Permitidos: {known}.",
                }
            display_name = spec.display_name
            binary_ok = argv is not None
            resolved_cmd = argv if argv is not None else [spec.binary]

        if not binary_ok:
            hint = self.catalog_service.install_hint(cli_profile) if isinstance(cli_profile, str) else {
                "install_available": False, "install_command": None,
            }
            suffix = (
                " Puede instalarlo desde el panel lateral de terminales."
                if hint.get("install_available")
                else " Configure la ruta del binario o instálelo manualmente."
            )
            return {
                "success": False,
                "degraded": False,
                "code": "CLI_BINARY_NOT_FOUND",
                "pane_id": norm_pane,
                "cli_profile": profile_label,
                "missing_binary": resolved_cmd[0],
                "message": f"No se encontró el ejecutable de {display_name} ('{resolved_cmd[0]}').{suffix}",
                "manual_command": DEFAULT_MANUAL_COMMAND,
                "mcp_config": _default_mcp_config(),
                **hint,
            }

        stale_session: Optional[_PtySession] = None
        with self._lock:
            existing = self._sessions.get(norm_pane)
            if existing is not None:
                if existing.is_alive():
                    return {
                        "success": False,
                        "degraded": False,
                        "code": "PANE_ALREADY_ACTIVE",
                        "pane_id": norm_pane,
                        "session_id": existing.session_id,
                        "pid": existing.pid,
                        "message": f"El panel {norm_pane} ya tiene una sesión viva (PID {existing.pid}).",
                    }
                stale_session = self._sessions.pop(norm_pane, None)
            alive_others = sum(
                1 for pane_key, other in self._sessions.items() if pane_key != norm_pane and other.is_alive()
            )

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

        launch_cmd = self._build_launch_command(resolved_cmd, use_trampoline=use_trampoline)
        child_env = self._build_child_env(
            norm_pane=norm_pane,
            hub_url=hub_url,
            session_token=session_token,
            node_uuid=node_uuid,
            env_overrides=env_overrides,
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

        session_id = f"{norm_pane}-{uuid.uuid4().hex[:12]}"
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

    def poll_output(self, pane_id: Any, since_seq: int = 0) -> Dict[str, Any]:
        norm_pane, _ = self._normalize_pane(pane_id)
        with self._lock:
            session = self._sessions.get(norm_pane)

        if session is None:
            return {
                "success": False,
                "code": "SESSION_NOT_FOUND",
                "pane_id": norm_pane,
                "alive": False,
                "exit_code": None,
                "paused": False,
                "rss_mb": 0.0,
                "since_seq": since_seq,
                "next_seq": since_seq,
                "truncated": False,
                "chunks": [],
                "text": "",
            }

        delta = session.buffer.poll_since(since_seq)
        alive = session.is_alive()
        exit_code = session.exit_code()
        rss_mb = _read_process_rss_mb(session.pid) if alive else 0.0
        return {
            "success": True,
            "pane_id": norm_pane,
            "session_id": session.session_id,
            "alive": alive,
            "exit_code": exit_code,
            "paused": session.paused,
            "rss_mb": rss_mb,
            **delta,
        }

    def close_session(self, pane_id: Any) -> Dict[str, Any]:
        norm_pane, _ = self._normalize_pane(pane_id)
        with self._lock:
            session = self._sessions.pop(norm_pane, None)

        if session is None:
            return {
                "success": True,
                "pane_id": norm_pane,
                "closed": False,
                "already_closed": True,
            }

        exit_code = self._shutdown_session(session)
        return {
            "success": True,
            "pane_id": norm_pane,
            "session_id": session.session_id,
            "pid": session.pid,
            "exit_code": exit_code,
            "closed": True,
        }

    def terminate_session(self, pane_id: Any) -> Dict[str, Any]:
        return self.close_session(pane_id)

    def close(self) -> None:
        with self._lock:
            pane_ids = list(self._sessions.keys())
        for pane_id in pane_ids:
            try:
                self.close_session(pane_id)
            except Exception as exc:
                logger.debug("Error cerrando sesión PTY '%s': %s", pane_id, exc)
