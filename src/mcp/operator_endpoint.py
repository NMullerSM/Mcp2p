"""Donde escucha el canal del operador y como lo encuentra el proxy (Ola 4, F2.1). Sin dependencias pesadas: lo
importan tanto el Hub como `mcp2p --operator-proxy`.

- **POSIX**: socket Unix `~/.mcp2p/run/operator.sock`, directorio `0700` del usuario y socket `0600`. Si la ruta supera
  el limite de `sun_path`, `<tmp>/mcp2p-<uid>/operator.sock` con las mismas comprobaciones. Solo el mismo usuario
  puede atravesar el directorio, y el Hub ademas compara el uid del par (`SO_PEERCRED`).
- **Windows**: TCP en `127.0.0.1:<puerto efimero>`, publicado en `~/.mcp2p/run/operator.endpoint` (perfil del usuario,
  ACL heredada solo del usuario). El puerto no es un secreto: el Hub identifica el proceso par con la tabla de
  conexiones del sistema y exige el mismo usuario.

Variables para pruebas o instalaciones especiales: `MCP2P_OPERATOR_SOCKET` (ruta del socket) y
`MCP2P_OPERATOR_ENDPOINT` (archivo del extremo TCP).
"""

from __future__ import annotations

import json
import os
import socket
import stat
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

SOCKET_ENV = "MCP2P_OPERATOR_SOCKET"
ENDPOINT_ENV = "MCP2P_OPERATOR_ENDPOINT"
HELLO_KEY = "mcp2p_operator_hello"
WELCOME_KEY = "mcp2p_operator_welcome"
PROTOCOL_VERSION = 1
MAX_SUN_PATH = 100  # Linux admite 108 bytes y macOS 104; se deja margen
MAX_LINE_BYTES = 1024 * 1024


def default_transport() -> str:
    return "unix" if os.name == "posix" and hasattr(socket, "AF_UNIX") else "tcp"


def run_dir() -> Path:
    return Path.home() / ".mcp2p" / "run"


def ensure_private_dir(path: Path) -> Path:
    """Crea `path` con modo 0700 y comprueba (POSIX) que es un directorio real del usuario y nadie mas entra."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "posix":
        return path
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise PermissionError(f"{path} no es un directorio propio del usuario")
    if stat.S_IMODE(info.st_mode) != 0o700:
        os.chmod(path, 0o700)
    return path


def default_socket_path() -> Path:
    override = (os.environ.get(SOCKET_ENV) or "").strip()
    if override:
        return Path(override)
    candidate = run_dir() / "operator.sock"
    if len(str(candidate).encode("utf-8")) <= MAX_SUN_PATH:
        return candidate
    uid = os.getuid() if hasattr(os, "getuid") else 0
    return Path(tempfile.gettempdir()) / f"mcp2p-{uid}" / "operator.sock"


def default_endpoint_file() -> Path:
    override = (os.environ.get(ENDPOINT_ENV) or "").strip()
    return Path(override) if override else run_dir() / "operator.endpoint"


def write_endpoint(path: Path, *, host: str, port: int, hub_pid: int, hub_create_time: Optional[float]) -> None:
    ensure_private_dir(path.parent)
    payload = {"v": PROTOCOL_VERSION, "transport": "tcp", "host": host, "port": int(port), "hub_pid": int(hub_pid),
               "hub_create_time": hub_create_time}
    fd, tmp = tempfile.mkstemp(prefix=".operator-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        if os.name == "posix":
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_endpoint(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("transport") != "tcp":
        raise ValueError("archivo de extremo del operador no valido")
    host = str(data.get("host") or "")
    if host not in ("127.0.0.1", "::1"):
        raise ValueError("el canal del operador solo admite loopback")
    return data
