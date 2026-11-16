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
import time
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
from src.adapters.terminal.cli_catalog import CliSpec
from src.adapters.terminal.mcp_injection import McpInjection, McpInjectionError, PaneMcpConfigStore
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
        """Texto con `seq > since_seq` y metadatos de truncado/cursor (sin duplicarlo en `chunks`, EGM-78)."""
        cursor = max(0, int(since_seq))
        with self._lock:
            truncated = cursor < self._min_untruncated_since
            combined = "".join(c_text for c_seq, _, c_text in self._chunks if c_seq > cursor)
            return {
                "since_seq": since_seq,
                "next_seq": self._seq,
                "truncated": truncated,
                "text": combined,
            }


_OSC_7501_PREFIX = b"\x1b]7501;"
_OSC_7501_MAX_PENDING = 512
_OSC_7501_STATUS_MAP: Dict[str, str] = {
    "idle": "idle",
    "working": "working",
    "blocked": "blocked",
    "permission": "blocked",
    "done": "done",
    "error": "error",
}


class _Osc7501Parser:
    """Parser incremental de secuencias OSC 7501 (\\x1b]7501;<payload>\\x07 o \\x1b\\\\)."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, chunk: bytes) -> List[str]:
        if not chunk:
            return []
        self._buf.extend(chunk)
        statuses: List[str] = []

        while self._buf:
            start = self._buf.find(_OSC_7501_PREFIX)
            if start == -1:
                # Conservar solo el sufijo más largo que pueda ser prefijo de "\x1b]7501;".
                max_prefix = len(_OSC_7501_PREFIX) - 1
                kept = b""
                for k in range(min(len(self._buf), max_prefix), 0, -1):
                    tail = bytes(self._buf[-k:])
                    if _OSC_7501_PREFIX.startswith(tail):
                        kept = tail
                        break
                self._buf.clear()
                if kept:
                    self._buf.extend(kept)
                break

            if start > 0:
                del self._buf[:start]

            body_start = len(_OSC_7501_PREFIX)
            bel_idx = self._buf.find(b"\x07", body_start)
            st_idx = self._buf.find(b"\x1b\\", body_start)

            if bel_idx != -1 and (st_idx == -1 or bel_idx < st_idx):
                term_idx, term_len = bel_idx, 1
            elif st_idx != -1:
                term_idx, term_len = st_idx, 2
            else:
                if len(self._buf) > _OSC_7501_MAX_PENDING:
                    del self._buf[:body_start]
                    continue
                break

            raw_payload = bytes(self._buf[body_start:term_idx])
            del self._buf[: term_idx + term_len]

            token = raw_payload.decode("utf-8", errors="replace").split(";", 1)[0].strip().lower()
            mapped = _OSC_7501_STATUS_MAP.get(token)
            if mapped is not None:
                statuses.append(mapped)

        return statuses


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
        mcp_config_store: Optional[PaneMcpConfigStore] = None,
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
        # Paneles con un spawn en curso (reserva bajo `_lock` entre la comprobacion y el registro, EGM-67).
        self._spawning: set[str] = set()
        # Configuracion MCP por panel (EGM-70): se barre lo que dejo una GUI anterior muerta sin cerrar.
        self._mcp_store = mcp_config_store if mcp_config_store is not None else PaneMcpConfigStore()
        try:
            self._mcp_store.sweep_orphans()
        except Exception as exc:  # pragma: no cover - defensivo: el barrido nunca debe impedir el arranque
            logger.debug("No se pudo barrer la configuracion MCP huerfana: %s", exc)

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

    @staticmethod
    def _compute_session_activity(
        session: Optional[_PtySession],
        *,
        pane_id: str = "",
        min_silence_s: float = 1.5,
    ) -> Dict[str, Any]:
        norm_pane = session.pane_id if session is not None else pane_id
        if session is None or not session.is_alive():
            return {
                "pane_id": norm_pane,
                "state": "exited",
                "source": "process_state",
                "idle": False,
                "osc_status": session.osc_status if session is not None else None,
                "silence_s": 0.0,
                "min_silence_s": float(min_silence_s),
            }

        if session.paused:
            return {
                "pane_id": norm_pane,
                "state": "paused",
                "source": "process_state",
                "idle": False,
                "osc_status": session.osc_status,
                "silence_s": round(max(0.0, time.monotonic() - session.last_output_at), 3),
                "min_silence_s": float(min_silence_s),
            }

        now = time.monotonic()
        silence_s = max(0.0, now - session.last_output_at)
        osc_status = session.osc_status
        osc_age = now - session.osc_status_at if osc_status is not None else float("inf")
        after_last_input = session.osc_status_at >= session.last_input_at

        if osc_status in ("idle", "done") and after_last_input and (osc_age <= 30.0 or session.last_input_at > 0.0):
            return {
                "pane_id": norm_pane,
                "state": osc_status,
                "source": "osc7501",
                "idle": True,
                "osc_status": osc_status,
                "silence_s": round(silence_s, 3),
                "min_silence_s": float(min_silence_s),
            }

        if osc_status in ("working", "blocked") and after_last_input and (osc_age < min_silence_s or silence_s < 30.0):
            return {
                "pane_id": norm_pane,
                "state": osc_status,
                "source": "osc7501",
                "idle": False,
                "osc_status": osc_status,
                "silence_s": round(silence_s, 3),
                "min_silence_s": float(min_silence_s),
            }

        if osc_status == "error" and after_last_input and osc_age <= 30.0:
            return {
                "pane_id": norm_pane,
                "state": "error",
                "source": "osc7501",
                "idle": False,
                "osc_status": osc_status,
                "silence_s": round(silence_s, 3),
                "min_silence_s": float(min_silence_s),
            }

        if silence_s >= min_silence_s:
            return {
                "pane_id": norm_pane,
                "state": "idle",
                "source": "silence_heuristic",
                "idle": True,
                "osc_status": osc_status,
                "silence_s": round(silence_s, 3),
                "min_silence_s": float(min_silence_s),
            }

        return {
            "pane_id": norm_pane,
            "state": "working",
            "source": "silence_heuristic",
            "idle": False,
            "osc_status": osc_status,
            "silence_s": round(silence_s, 3),
            "min_silence_s": float(min_silence_s),
        }

    def get_pane_activity(self, pane_id: Any, *, min_silence_s: float = 1.5) -> Dict[str, Any]:
        norm_pane, _ = self._normalize_pane(pane_id)
        with self._lock:
            session = self._sessions.get(norm_pane)
        return self._compute_session_activity(session, pane_id=norm_pane, min_silence_s=min_silence_s)

    def is_pane_idle(self, pane_id: Any, *, min_silence_s: float = 1.5) -> bool:
        return bool(self.get_pane_activity(pane_id, min_silence_s=min_silence_s).get("idle", False))

    @classmethod
    def _serialize_session(cls, session: _PtySession) -> Dict[str, Any]:
        alive = session.is_alive()
        activity = cls._compute_session_activity(session)
        return {
            "pane_id": session.pane_id,
            "session_id": session.session_id,
            "cli_profile": session.cli_profile,
            "role": session.role,
            "node_uuid": session.node_uuid,
            "pid": session.pid,
            "alive": alive,
            "paused": session.paused,
            "activity_state": activity["state"],
            "activity_source": activity["source"],
            "idle": activity["idle"],
            "rss_mb": _read_process_rss_mb(session.pid) if alive else 0.0,
            "cols": session.cols,
            "rows": session.rows,
            "started_at": session.started_at,
            "next_seq": session.buffer.next_seq,
            "cwd": session.cwd,
            "mcp_injection": dict(session.mcp_injection),
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
        resume: bool = False,
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
        launch_spec: Optional[CliSpec] = None
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
            launch_spec = spec

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

        if resume:
            # Solo el argv de reanudacion VERIFICADO del catalogo del repo; nunca texto del llamador.
            resume_argv = list(launch_spec.resume.get("argv", [])) if launch_spec is not None else []
            if launch_spec is None or not resume_argv or not launch_spec.resume.get("verified"):
                return {
                    "success": False,
                    "degraded": False,
                    "code": "RESUME_NOT_AVAILABLE",
                    "pane_id": norm_pane,
                    "cli_profile": profile_label,
                    "message": "Este CLI no tiene una reanudacion verificada en el catalogo.",
                }
            resolved_cmd = [*resolved_cmd, *resume_argv]

        stale_session: Optional[_PtySession] = None
        with self._lock:
            if norm_pane in self._spawning:
                return {
                    "success": False,
                    "degraded": False,
                    "code": "PANE_ALREADY_ACTIVE",
                    "pane_id": norm_pane,
                    "message": f"El panel {norm_pane} ya se esta abriendo.",
                }
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
            ) + sum(1 for pane_key in self._spawning if pane_key != norm_pane)
            self._spawning.add(norm_pane)

        try:
            return self._spawn_reserved(
                norm_pane=norm_pane, pane_index=pane_index, stale_session=stale_session,
                alive_others=alive_others, cli_profile=cli_profile, role=role, resolved_cmd=resolved_cmd,
                resolved_cwd=resolved_cwd, cols_int=cols_int, rows_int=rows_int, env_overrides=env_overrides,
                hub_url=hub_url, session_token=session_token, node_uuid=node_uuid, use_trampoline=use_trampoline,
                launch_spec=launch_spec,
            )
        finally:
            with self._lock:
                self._spawning.discard(norm_pane)

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
                "activity_state": "exited",
                "activity_source": "process_state",
                "idle": False,
                "rss_mb": 0.0,
                "since_seq": since_seq,
                "next_seq": since_seq,
                "truncated": False,
                "text": "",
            }

        delta = session.buffer.poll_since(since_seq)
        alive = session.is_alive()
        exit_code = session.exit_code()
        activity = self._compute_session_activity(session)
        rss_mb = _read_process_rss_mb(session.pid) if alive else 0.0
        return {
            "success": True,
            "pane_id": norm_pane,
            "session_id": session.session_id,
            "alive": alive,
            "exit_code": exit_code,
            "paused": session.paused,
            "activity_state": activity["state"],
            "activity_source": activity["source"],
            "idle": activity["idle"],
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
