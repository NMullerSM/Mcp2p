"""Composición y validación de aumentadores de lanzamiento de terminales (v2.0.2)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from src.adapters.terminal import hooks_launch, role_launch, session_launch
from src.adapters.terminal.cli_catalog import _MCP_ENV_DENY, CliSpec, has_control_chars

logger = logging.getLogger("mcp2p.adapters.terminal.launch_augment")

_AUGMENT_ENV_NAME_RE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_SESSION_FILE_NAME_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_RESERVED_MCP2P_ENV = frozenset({
    "MCP2P_SESSION_TOKEN",
    "MCP2P_HUB_URL",
    "MCP2P_NODE_UUID",
    "MCP2P_PANE_ID",
})
_MAX_ARGV_ITEM_LEN = 4096
_MAX_SESSION_FILE_BYTES = 256 * 1024


@dataclass(frozen=True)
class LaunchContext:
    """Contexto de solo lectura que reciben los aumentadores al abrir un panel."""

    spec: CliSpec
    platform: str
    pane_slot: str
    session_id: str
    hub_url: Optional[str]
    request: Mapping[str, Any]
    session_file: Callable[[str], Path]
    services: Any = None


@dataclass(frozen=True)
class LaunchAddition:
    """Aportación validable de un aumentador al comando, entorno y archivos de sesión."""

    argv: Tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    files: Mapping[str, bytes] = field(default_factory=dict)
    meta: Mapping[str, str] = field(default_factory=dict)
    notes: Tuple[str, ...] = ()


@dataclass
class LaunchServices:
    """Servicios compartidos que `ExtensionsLabUIAdapter` y `wire_*` exponen al lanzamiento."""

    identity_repo: Any = None
    hook_argv: Optional[Callable[[str], List[str]]] = None
    extra: Dict[str, Any] = field(default_factory=dict)


AUGMENTERS: Tuple[Callable[[LaunchContext], LaunchAddition], ...] = (
    role_launch.augment,
    session_launch.augment,
    hooks_launch.augment,
)


def _augmenter_label(fn: Any) -> str:
    mod = getattr(fn, "__module__", "") or ""
    if mod:
        return str(mod).rsplit(".", 1)[-1]
    return str(getattr(fn, "__name__", "augmenter"))


def _validate_addition(addition: Any, existing_files: Mapping[str, bytes]) -> Tuple[
    Tuple[str, ...], Dict[str, str], Dict[str, bytes], Dict[str, str], Tuple[str, ...]
]:
    if not isinstance(addition, LaunchAddition):
        raise ValueError("se esperaba LaunchAddition")

    raw_argv = addition.argv
    if isinstance(raw_argv, (str, bytes)) or not isinstance(raw_argv, Sequence):
        raise ValueError("argv debe ser una secuencia de cadenas")
    argv: List[str] = []
    for item in raw_argv:
        if not isinstance(item, str):
            raise ValueError("argv contiene un elemento que no es texto")
        if len(item) > _MAX_ARGV_ITEM_LEN or has_control_chars(item):
            raise ValueError("argv contiene controles o supera 4096 caracteres")
        argv.append(item)

    if not isinstance(addition.env, Mapping):
        raise ValueError("env debe ser un mapeo")
    env: Dict[str, str] = {}
    for k, v in addition.env.items():
        if not isinstance(k, str) or _AUGMENT_ENV_NAME_RE.fullmatch(k) is None:
            raise ValueError(f"nombre de variable env invalido: {k!r}")
        if k in _MCP_ENV_DENY or k in _RESERVED_MCP2P_ENV:
            raise ValueError(f"variable env prohibida: {k!r}")
        if not isinstance(v, str) or len(v) > _MAX_ARGV_ITEM_LEN or has_control_chars(v):
            raise ValueError(f"valor env invalido para {k!r}")
        env[k] = v

    if not isinstance(addition.files, Mapping):
        raise ValueError("files debe ser un mapeo")
    files: Dict[str, bytes] = {}
    for fname, content in addition.files.items():
        if not isinstance(fname, str) or fname in (".", "..") or _SESSION_FILE_NAME_RE.fullmatch(fname) is None:
            raise ValueError(f"nombre de archivo invalido: {fname!r}")
        if fname in existing_files:
            raise ValueError(f"archivo duplicado: {fname!r}")
        if not isinstance(content, (bytes, bytearray)):
            raise ValueError(f"contenido no binario para {fname!r}")
        raw_bytes = bytes(content)
        if len(raw_bytes) > _MAX_SESSION_FILE_BYTES:
            raise ValueError(f"archivo {fname!r} excede 256 KiB")
        files[fname] = raw_bytes

    if not isinstance(addition.meta, Mapping):
        raise ValueError("meta debe ser un mapeo")
    meta: Dict[str, str] = {}
    for k, v in addition.meta.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise ValueError("meta debe mapear str a str")
        meta[k] = v

    if isinstance(addition.notes, (str, bytes)) or not isinstance(addition.notes, Sequence):
        raise ValueError("notes debe ser una secuencia de cadenas")
    notes = tuple(str(n) for n in addition.notes)

    return tuple(argv), env, files, meta, notes


def run_augmenters(ctx: LaunchContext) -> LaunchAddition:
    """Ejecuta los aumentadores registrados, valida sus salidas y fusiona los válidos."""
    merged_argv: List[str] = []
    merged_env: Dict[str, str] = {}
    merged_files: Dict[str, bytes] = {}
    merged_meta: Dict[str, str] = {}
    merged_notes: List[str] = []

    for fn in AUGMENTERS:
        label = _augmenter_label(fn)
        try:
            addition = fn(ctx)
            argv, env, files, meta, notes = _validate_addition(addition, merged_files)
        except Exception as exc:
            logger.warning("Aumentador de lanzamiento '%s' descartado: %s", label, exc)
            merged_notes.append(f"{label}: descartado ({exc})")
            continue
        merged_argv.extend(argv)
        merged_env.update(env)
        merged_files.update(files)
        merged_meta.update(meta)
        merged_notes.extend(notes)

    return LaunchAddition(
        argv=tuple(merged_argv),
        env=merged_env,
        files=merged_files,
        meta=merged_meta,
        notes=tuple(merged_notes),
    )
