"""
Disponibilidad REAL de las terminales integradas y respuesta uniforme (`TERMINAL_PLATFORM_UNSUPPORTED`).

Windows usa ConPTY a traves de `pywinpty` (`WinPtyHostConnector`): la plataforma solo se declara no soportada
cuando ese conector no puede funcionar (no se pudo importar `winpty`, o el Windows es anterior a la build 17763 /
1809, que es cuando aparecio ConPTY). El criterio es la capacidad del conector, no el sistema operativo.

Ante la no disponibilidad el puerto responde con un codigo propio, un mensaje claro y el comando manual, sin lanzar
excepcion y sin confundirse con el "degradado" generico (`TERMINAL_ADAPTER_DEGRADED`).
"""

from __future__ import annotations

import importlib
import os
import sys
from typing import Any, Dict, Optional, Tuple

MANUAL_COMMAND = "mcp2p --mcp-server --transport streamable-http --host 127.0.0.1 --port 8765"

# Primera build de Windows 10 con la API de pseudoconsola (ConPTY): 1809.
MIN_CONPTY_BUILD = 17763

REASON_PYWINPTY_MISSING = "PYWINPTY_MISSING"
REASON_WINDOWS_TOO_OLD = "WINDOWS_TOO_OLD"


def is_windows_platform() -> bool:
    """True en Windows nativo (o simulado por `sys.platform`/`os.name` en los tests)."""
    return sys.platform == "win32" or os.name == "nt"


def import_winpty() -> Optional[Any]:
    """El modulo `winpty` (pywinpty) si se puede importar y expone la API de bajo nivel; si no, None."""
    try:
        module = importlib.import_module("winpty")
    except Exception:  # ImportError, pero tambien un fallo al cargar la DLL nativa (OSError)
        return None
    if getattr(module, "PTY", None) is None:
        return None
    return module


def windows_build_number() -> Optional[int]:
    """Numero de build de Windows; None si no se puede determinar (no es Windows real)."""
    getter = getattr(sys, "getwindowsversion", None)
    if getter is None:
        return None
    try:
        return int(getter().build)
    except Exception:
        return None


def windows_conpty_status() -> Tuple[bool, Optional[str]]:
    """`(disponible, motivo)`: motivo es None si hay ConPTY utilizable; solo tiene sentido en Windows."""
    build = windows_build_number()
    if build is not None and build < MIN_CONPTY_BUILD:
        return False, REASON_WINDOWS_TOO_OLD
    if import_winpty() is None:
        return False, REASON_PYWINPTY_MISSING
    return True, None


def is_unsupported_terminal_platform() -> bool:
    """True si estamos en Windows y NO hay un conector ConPTY utilizable (pywinpty ausente o Windows < 1809)."""
    if not is_windows_platform():
        return False
    return not windows_conpty_status()[0]


def unsupported_reason() -> Optional[str]:
    """Motivo de la no disponibilidad en Windows (`PYWINPTY_MISSING`, `WINDOWS_TOO_OLD`) o None."""
    if not is_windows_platform():
        return None
    return windows_conpty_status()[1]


_MESSAGES = {
    REASON_PYWINPTY_MISSING: (
        "Las terminales integradas no están disponibles en este Windows: falta el componente ConPTY (pywinpty) o "
        "no se pudo cargar. Reinstale la aplicación o ejecute su CLI en una terminal propia y conéctela al "
        "servidor MCP con el comando manual."
    ),
    REASON_WINDOWS_TOO_OLD: (
        "Las terminales integradas requieren Windows 10 versión 1809 (build 17763) o superior (ConPTY). "
        "Ejecute su CLI en una terminal propia y conéctela al servidor MCP con el comando manual."
    ),
}
_DEFAULT_MESSAGE = (
    "Las terminales integradas todavía no están disponibles en Windows (falta el conector ConPTY). "
    "Ejecute su CLI en una terminal propia y conéctela al servidor MCP con el comando manual."
)


def unsupported_platform_response(reason: Optional[str] = None) -> Dict[str, Any]:
    """Respuesta uniforme; `reason` (si se conoce) afina el mensaje. Si no se pasa, se calcula."""
    if reason is None:
        reason = unsupported_reason()
    response: Dict[str, Any] = {
        "success": False,
        "degraded": True,
        "code": "TERMINAL_PLATFORM_UNSUPPORTED",
        "unsupported_platform": True,
        "platform": "win32",
        "message": _MESSAGES.get(reason or "", _DEFAULT_MESSAGE),
        "manual_command": MANUAL_COMMAND,
        "mcp_config": {"mcpServers": {"mcp2p": {"url": "http://127.0.0.1:8765/mcp"}}},
    }
    if reason:
        response["reason"] = reason
    return response
