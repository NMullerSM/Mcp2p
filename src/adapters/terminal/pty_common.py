"""
Piezas del host de terminales que NO dependen del sistema operativo y que comparten el conector POSIX
(`pty_host.py`) y el de Windows/ConPTY (`winpty_host.py`): buffer circular de salida, parser OSC 7501,
normalizacion de `pane_id`, limites de paneles, entorno del hijo, calculo del estado de actividad,
serializacion de sesiones y la clase base `PtyHostBase` (validacion, reserva de panel, admision por
memoria, sondeo y cierre), de modo que ambos conectores devuelvan EXACTAMENTE los mismos codigos de error.

Sin imports POSIX (`fcntl`, `pty`, `termios`, `select`, `signal.SIGSTOP`...): este modulo se importa tal cual
en Windows. `pty_host.py` re-exporta los nombres historicos para no romper imports existentes.
"""

from __future__ import annotations

import codecs
import logging
import os
import re
import sys
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Mapping, Optional, Tuple, Union

import psutil

from src.adapters.terminal.cli_catalog import (
    CliCatalogService,
    CliSpec,
    SettingsStore,
    current_platform,
    legacy_profiles,
    load_repo_catalog,
)
from src.adapters.terminal.launch_augment import (
    LaunchAddition,
    LaunchContext,
    LaunchServices,
    run_augmenters,
)
from src.adapters.terminal.mcp_injection import McpInjectionError, PaneMcpConfigStore

logger = logging.getLogger("mcp2p.adapters.terminal.pty_common")

DEFAULT_MAX_PANES = 4
HARD_MAX_PANES = 6  # tope absoluto configurable (EGM-22 / plan 2026-11-15, seccion 5b)
DEFAULT_MIN_AVAILABLE_MB = 1024  # admision por memoria: MemAvailable minima para abrir otro panel
MAX_PANES_SETTING_NAMESPACE = "terminal_matrix"
MAX_PANES_SETTING_KEY = "max_panes"
DEFAULT_RING_BYTES = 262144  # 256 KB
DEFAULT_MANUAL_COMMAND = "mcp2p --mcp-server --transport streamable-http --host 127.0.0.1 --port 8765"

# Shell por defecto del perfil `custom_shell`: `$SHELL` en POSIX, `%COMSPEC%` (cmd.exe) en Windows.
if sys.platform == "win32":
    SHELL_DEFAULT = os.environ.get("COMSPEC") or "cmd.exe"
else:
    SHELL_DEFAULT = os.environ.get("SHELL") or "/bin/sh"


def build_cli_profiles(shell_default: str = SHELL_DEFAULT) -> Dict[str, List[str]]:
    """`CLI_PROFILES` se deriva del catalogo versionado del repo (`cli_catalog.json`)."""
    try:
        return legacy_profiles(load_repo_catalog(), shell_default)
    except Exception as exc:  # pragma: no cover - solo si el artefacto del build esta corrupto
        logger.error("cli_catalog.json no se pudo cargar; usando perfiles minimos: %s", exc)
        return {"claude": ["claude"], "custom_shell": [shell_default]}


REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def default_mcp_config() -> Dict[str, Any]:
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


def read_process_rss_mb(pid: Optional[int]) -> float:
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


OSC_7501_PREFIX = b"\x1b]7501;"
OSC_7501_PROBE_REPLY = b"\x1b]7501;?1\x07"
OSC_7501_MAX_PENDING = 512
OSC_7501_MAX_PROBE_REPLIES_PER_READ = 1
OSC_7501_MAX_PROBE_REPLIES_PER_SEC = 2
_ANSI_CSI_RE = re.compile(rb"\x1b\[[0-?]*[ -/]*[@-~]")
_INCOMPLETE_ANSI_TAIL_RE = re.compile(rb"\x1b(?:\[[0-?]*[ -/]*)?$")
OSC_7501_STATUS_MAP: Dict[str, str] = {
    "idle": "idle",
    "working": "working",
    "blocked": "blocked",
    "permission": "blocked",
    "done": "done",
    "error": "error",
}


class Osc7501Parser:
    """Parser incremental de secuencias OSC 7501 (\\x1b]7501;<payload>\\x07 o \\x1b\\\\)."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self._trail_buf = bytearray()
        self.last_kind: Optional[str] = None
        self.trailing_output: bool = False
        self.cleared: bool = False
        self._probes: int = 0

    def take_probes(self) -> int:
        """Devuelve el numero de sondas OSC 7501 pendientes y lo resetea a 0."""
        count = self._probes
        self._probes = 0
        return count

    @staticmethod
    def _has_visible_output(data: bytes) -> bool:
        if not data:
            return False
        cleaned = _ANSI_CSI_RE.sub(b"", data)
        return any(byte > 0x20 for byte in cleaned)

    def _consume_non_osc(self, data: bytes) -> None:
        """Acumula bytes fuera de OSC 7501 reteniendo escapes/CSI incompletos al final."""
        if not data:
            return
        self._trail_buf.extend(data)
        if len(self._trail_buf) > OSC_7501_MAX_PENDING:
            if self._has_visible_output(bytes(self._trail_buf)):
                self.trailing_output = True
            self._trail_buf.clear()
            return
        match = _INCOMPLETE_ANSI_TAIL_RE.search(self._trail_buf)
        if match is not None:
            split = match.start()
            complete = bytes(self._trail_buf[:split])
            del self._trail_buf[:split]
        else:
            complete = bytes(self._trail_buf)
            self._trail_buf.clear()
        if complete and self._has_visible_output(complete):
            self.trailing_output = True

    @staticmethod
    def _parse_payload(raw_payload: bytes) -> Tuple[Optional[str], Optional[str], bool, bool]:
        """Devuelve (status_mapeado, kind, is_probe, is_clear) para el payload crudo de OSC 7501."""
        text = raw_payload.decode("utf-8", errors="replace").strip()
        if text == "?":
            return None, None, True, False

        parts = [p.strip() for p in re.split(r"[;:]", text.lower()) if p.strip()]
        if not parts:
            return None, None, False, False

        state_token: Optional[str] = None
        kind_value: Optional[str] = None
        has_kv = False
        for part in parts:
            if "=" in part:
                has_kv = True
                key, val = part.split("=", 1)
                key = key.strip()
                val = val.strip()
                if key == "state" and val:
                    state_token = val
                elif key == "kind" and val:
                    kind_value = val

        if not has_kv:
            state_token = parts[0]
        elif state_token is None and "=" not in parts[0]:
            state_token = parts[0]

        if state_token is None:
            return None, None, False, False

        if state_token == "clear":
            return None, None, False, True

        mapped = OSC_7501_STATUS_MAP.get(state_token)
        if mapped is None:
            return None, None, False, False
        if kind_value is None and state_token == "permission":
            kind_value = "permission"
        return mapped, kind_value, False, False

    def feed(self, chunk: bytes) -> List[str]:
        if not chunk:
            return []
        self._buf.extend(chunk)
        statuses: List[str] = []
        max_buf_len = OSC_7501_MAX_PENDING + len(OSC_7501_PREFIX)

        while self._buf:
            start = self._buf.find(OSC_7501_PREFIX)
            if start == -1:
                # Conservar solo el sufijo más largo que pueda ser prefijo de "\x1b]7501;".
                max_prefix = len(OSC_7501_PREFIX) - 1
                kept = b""
                for k in range(min(len(self._buf), max_prefix), 0, -1):
                    tail = bytes(self._buf[-k:])
                    if OSC_7501_PREFIX.startswith(tail):
                        kept = tail
                        break
                consumed = bytes(self._buf[: len(self._buf) - len(kept)])
                self._consume_non_osc(consumed)
                self._buf.clear()
                if kept:
                    self._buf.extend(kept)
                break

            if start > 0:
                self._consume_non_osc(bytes(self._buf[:start]))
                del self._buf[:start]

            body_start = len(OSC_7501_PREFIX)
            bel_idx = self._buf.find(b"\x07", body_start)
            st_idx = self._buf.find(b"\x1b\\", body_start)

            if bel_idx != -1 and (st_idx == -1 or bel_idx < st_idx):
                term_idx, term_len = bel_idx, 1
            elif st_idx != -1:
                term_idx, term_len = st_idx, 2
            else:
                if len(self._buf) > max_buf_len:
                    del self._buf[:body_start]
                    continue
                break

            if term_idx - body_start > OSC_7501_MAX_PENDING:
                del self._buf[:body_start]
                continue

            raw_payload = bytes(self._buf[body_start:term_idx])
            del self._buf[: term_idx + term_len]

            mapped, kind_value, is_probe, is_clear = self._parse_payload(raw_payload)
            if is_probe:
                self._probes += 1
            elif is_clear:
                self.cleared = True
                self.last_kind = None
            elif mapped is not None:
                self.cleared = False
                self.last_kind = kind_value
                self._trail_buf.clear()
                self.trailing_output = False
                statuses.append(mapped)

        return statuses


# Nombres historicos (los importan tests y otros modulos).
_Osc7501Parser = Osc7501Parser
_read_process_rss_mb = read_process_rss_mb


# ---------------------------------------------------------------------------
# Estado de actividad (puro: no toca el SO)
# ---------------------------------------------------------------------------


def compute_session_activity(
    session: Optional[Any],
    *,
    pane_id: str = "",
    min_silence_s: float = 1.5,
) -> Dict[str, Any]:
    norm_pane = session.pane_id if session is not None else pane_id
    osc_kind = getattr(session, "osc_kind", None) if session is not None else None
    if session is None or not session.is_alive():
        return {
            "pane_id": norm_pane,
            "state": "exited",
            "source": "process_state",
            "idle": False,
            "osc_status": session.osc_status if session is not None else None,
            "osc_kind": osc_kind,
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
            "osc_kind": osc_kind,
            "silence_s": round(max(0.0, time.monotonic() - session.last_output_at), 3),
            "min_silence_s": float(min_silence_s),
        }

    now = time.monotonic()
    silence_s = max(0.0, now - session.last_output_at)
    osc_status = session.osc_status
    osc_age = now - session.osc_status_at if osc_status is not None else float("inf")
    after_last_input = session.osc_status_at >= session.last_input_at

    if osc_status in ("idle", "done") and after_last_input and osc_age <= 30.0:
        return {
            "pane_id": norm_pane,
            "state": osc_status,
            "source": "osc7501",
            "idle": True,
            "osc_status": osc_status,
            "osc_kind": osc_kind,
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
            "osc_kind": osc_kind,
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
            "osc_kind": osc_kind,
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
            "osc_kind": osc_kind,
            "silence_s": round(silence_s, 3),
            "min_silence_s": float(min_silence_s),
        }

    return {
        "pane_id": norm_pane,
        "state": "working",
        "source": "silence_heuristic",
        "idle": False,
        "osc_status": osc_status,
        "osc_kind": osc_kind,
        "silence_s": round(silence_s, 3),
        "min_silence_s": float(min_silence_s),
    }


# ---------------------------------------------------------------------------
# Entorno del hijo
# ---------------------------------------------------------------------------


def build_child_env(
    *,
    norm_pane: str,
    hub_url: Optional[str],
    session_token: Optional[str],
    node_uuid: Optional[str],
    env_overrides: Optional[Dict[str, Optional[str]]],
    pythonpath_prepend: Optional[str] = None,
    base_env: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """
    Entorno del proceso hijo de un panel: hereda el del host sin secretos propios (passphrases de firma, token
    de otra sesion, restos de PyInstaller), fija `TERM`/`MCP2P_*` del panel y aplica `env_overrides` al final
    (`None` borra la variable). `pythonpath_prepend` solo lo usa el trampolin POSIX en desarrollo.
    """
    child_env = dict(os.environ if base_env is None else base_env)
    is_frozen = bool(getattr(sys, "frozen", False))

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

    if pythonpath_prepend:
        existing_pp = child_env.get("PYTHONPATH", "")
        pp_parts = [p for p in existing_pp.split(os.pathsep) if p] if existing_pp else []
        if pythonpath_prepend not in pp_parts:
            child_env["PYTHONPATH"] = (
                os.pathsep.join([pythonpath_prepend, *pp_parts]) if pp_parts else pythonpath_prepend
            )

    if env_overrides:
        for k, v in env_overrides.items():
            key_str = str(k)
            if v is None:
                child_env.pop(key_str, None)
            else:
                child_env[key_str] = str(v)

    return child_env


def serialize_session(session: Any, *, rss_probe: Optional[Callable[[Optional[int]], float]] = None) -> Dict[str, Any]:
    """Vista publica de una sesion (la misma forma en POSIX y Windows)."""
    probe = rss_probe or read_process_rss_mb
    alive = session.is_alive()
    activity = compute_session_activity(session)
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
        "rss_mb": probe(session.pid) if alive else 0.0,
        "cols": session.cols,
        "rows": session.rows,
        "started_at": session.started_at,
        "next_seq": session.buffer.next_seq,
        "cwd": session.cwd,
        "mcp_injection": dict(session.mcp_injection),
    }


# ---------------------------------------------------------------------------
# Clase base de los conectores
# ---------------------------------------------------------------------------


class PtyHostBase:
    """
    Logica comun de `AgentTerminalPort` para los conectores PTY: limites de paneles, validacion y resolucion del
    CLI, reserva del panel, admision por memoria, sondeo de salida y cierre. Las subclases aportan lo especifico
    del SO: `is_available`, `_platform_block`, `_split_command_string`, `_which_executable`, `_spawn_reserved`
    y `_shutdown_session`. Las sesiones son objetos con la forma de `serialize_session` (is_alive, exit_code,
    pid, buffer, paused, last_output_at, last_input_at, osc_status, osc_status_at...).
    """

    MODE = "pty"

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
        launch_services: Optional[LaunchServices] = None,
    ) -> None:
        if int(max_panes) < 1 or int(max_panes) > HARD_MAX_PANES:
            raise ValueError(f"max_panes debe estar entre 1 y {HARD_MAX_PANES}.")
        self._max_panes = int(max_panes)
        self.ring_max_bytes = int(ring_max_bytes)
        self.gate = gate
        self.connector_id = connector_id
        self.catalog_service = catalog_service if catalog_service is not None else CliCatalogService()
        self.launch_services: Optional[LaunchServices] = launch_services
        self.min_available_mb = max(0, int(min_available_mb))
        self._memory_probe = memory_probe or self._default_memory_probe
        self._lock = threading.RLock()
        self._sessions: Dict[str, Any] = {}
        # Paneles con un spawn en curso (reserva bajo `_lock` entre la comprobacion y el registro, EGM-67).
        self._spawning: set[str] = set()
        # Configuracion MCP por panel (EGM-70): se barre lo que dejo una GUI anterior muerta sin cerrar.
        self._mcp_store = mcp_config_store if mcp_config_store is not None else PaneMcpConfigStore()
        try:
            self._mcp_store.sweep_orphans()
        except Exception as exc:  # pragma: no cover - defensivo: el barrido nunca debe impedir el arranque
            logger.debug("No se pudo barrer la configuracion MCP huerfana: %s", exc)

    # -- ganchos que implementa cada SO ----------------------------------

    @staticmethod
    def is_available() -> bool:  # pragma: no cover - lo redefine la subclase
        return False

    def _platform_block(self, norm_pane: str) -> Optional[Dict[str, Any]]:
        """Respuesta de error si el conector no puede funcionar aqui; None si todo bien."""
        return None

    @staticmethod
    def _split_command_string(command: str) -> List[str]:
        import shlex

        return shlex.split(command)

    @staticmethod
    def _which_executable(binary: str, cwd: Optional[str]) -> Optional[str]:  # pragma: no cover - subclase
        return None

    def _spawn_reserved(self, **kwargs: Any) -> Dict[str, Any]:  # pragma: no cover - subclase
        raise NotImplementedError

    def _shutdown_session(self, session: Any) -> Optional[int]:  # pragma: no cover - subclase
        raise NotImplementedError

    # -- comun ------------------------------------------------------------

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

    def _normalize_pane(self, pane_id: Any) -> Tuple[str, int]:
        return normalize_pane_id(pane_id, max_panes=self.max_panes)

    @classmethod
    def _parse_command(cls, command: Union[List[str], str]) -> List[str]:
        if isinstance(command, str):
            parts = cls._split_command_string(command)
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
    def _session_not_found(norm_pane: str) -> Dict[str, Any]:
        return {
            "success": False,
            "code": "SESSION_NOT_FOUND",
            "pane_id": norm_pane,
            "message": f"No hay sesión registrada en {norm_pane}.",
        }

    @staticmethod
    def _session_not_alive(norm_pane: str, session: Any, message: str) -> Dict[str, Any]:
        return {
            "success": False,
            "code": "SESSION_NOT_ALIVE",
            "pane_id": norm_pane,
            "session_id": session.session_id,
            "exit_code": session.exit_code(),
            "message": message,
        }

    # -- actividad ---------------------------------------------------------

    @staticmethod
    def _compute_session_activity(
        session: Optional[Any],
        *,
        pane_id: str = "",
        min_silence_s: float = 1.5,
    ) -> Dict[str, Any]:
        return compute_session_activity(session, pane_id=pane_id, min_silence_s=min_silence_s)

    def get_pane_activity(self, pane_id: Any, *, min_silence_s: float = 1.5) -> Dict[str, Any]:
        norm_pane, _ = self._normalize_pane(pane_id)
        with self._lock:
            session = self._sessions.get(norm_pane)
        return self._compute_session_activity(session, pane_id=norm_pane, min_silence_s=min_silence_s)

    def is_pane_idle(self, pane_id: Any, *, min_silence_s: float = 1.5) -> bool:
        return bool(self.get_pane_activity(pane_id, min_silence_s=min_silence_s).get("idle", False))

    @classmethod
    def _serialize_session(cls, session: Any) -> Dict[str, Any]:
        return serialize_session(session, rss_probe=cls._rss_probe())

    @classmethod
    def _rss_probe(cls) -> Callable[[Optional[int]], float]:
        return read_process_rss_mb

    # -- AgentTerminalPort (parte comun) -----------------------------------

    def get_status(self) -> Dict[str, Any]:
        sessions = self.list_sessions()
        return {
            "connector_id": self.connector_id,
            "available": self.is_available(),
            "mode": self.MODE,
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
        launch_request: Optional[Mapping[str, Any]] = None,
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

        blocked = self._platform_block(norm_pane)
        if blocked is not None:
            return blocked

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
                "mcp_config": default_mcp_config(),
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

        stale_session: Optional[Any] = None
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
                launch_spec=launch_spec, launch_request=launch_request,
            )
        finally:
            with self._lock:
                self._spawning.discard(norm_pane)

    def _admission_error(self, norm_pane: str, alive_others: int) -> Optional[Dict[str, Any]]:
        """Limite de paneles y admision por memoria (identicos en todas las plataformas)."""
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
        return None

    def _new_session_id(self, norm_pane: str) -> str:
        return f"{norm_pane}-{uuid.uuid4().hex[:12]}"

    def _augment_launch(
        self,
        launch_spec: Optional[CliSpec],
        session_id: str,
        norm_pane: str,
        hub_url: Optional[str],
        launch_request: Optional[Mapping[str, Any]],
    ) -> LaunchAddition:
        """Ejecuta los aumentadores de lanzamiento sobre `launch_spec`; sin spec devuelve vacío."""
        if launch_spec is None:
            return LaunchAddition()
        plat = getattr(self.catalog_service, "platform", None) or current_platform()
        ctx = LaunchContext(
            spec=launch_spec,
            platform=plat,
            pane_slot=norm_pane,
            session_id=session_id,
            hub_url=hub_url,
            request=dict(launch_request or {}),
            session_file=lambda name: self._mcp_store.session_file_path(session_id, name),
            services=self.launch_services,
        )
        return run_augmenters(ctx)

    def _prepare_launch_augmentation(
        self,
        launch_spec: Optional[CliSpec],
        session_id: str,
        norm_pane: str,
        hub_url: Optional[str],
        launch_request: Optional[Mapping[str, Any]],
    ) -> Tuple[LaunchAddition, Optional[Dict[str, Any]]]:
        """Ejecuta aumentadores y escribe `aug.files` (`0600`) antes del spawn."""
        aug = self._augment_launch(launch_spec, session_id, norm_pane, hub_url, launch_request)
        if aug.files:
            try:
                for fname, data in aug.files.items():
                    self._mcp_store.write_session_file(session_id, fname, data)
            except McpInjectionError as exc:
                self._mcp_store.cleanup(session_id)
                return aug, {
                    "success": False,
                    "degraded": False,
                    "code": "MCP_CONFIG_FAILED",
                    "pane_id": norm_pane,
                    "message": f"No se pudo preparar la configuracion del panel: {exc}",
                }
        return aug, None

    def _prepare_mcp(
        self, launch_spec: Optional[CliSpec], session_id: str, hub_url: Optional[str], session_token: Optional[str],
        norm_pane: str,
    ) -> Tuple[Optional[Any], Optional[Dict[str, Any]]]:
        try:
            injection = self._mcp_store.prepare(
                launch_spec, session_id=session_id, hub_url=hub_url, token=session_token
            )
        except McpInjectionError as exc:
            return None, {
                "success": False,
                "degraded": False,
                "code": "MCP_CONFIG_FAILED",
                "pane_id": norm_pane,
                "message": f"No se pudo preparar la conexion MCP del panel: {exc}",
            }
        return injection, None

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

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
        rss_mb = self._rss_probe()(session.pid) if alive else 0.0
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
