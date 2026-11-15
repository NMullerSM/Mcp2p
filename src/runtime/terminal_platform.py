"""
Deteccion de plataformas sin terminal integrada y respuesta uniforme (`TERMINAL_PLATFORM_UNSUPPORTED`).

Windows (ConPTY) esta fuera de alcance por ahora: el puerto de terminales responde con un codigo
propio, un mensaje claro y el comando manual, sin lanzar excepcion y sin confundirse con el
"degradado" generico (`TERMINAL_ADAPTER_DEGRADED`).
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict

MANUAL_COMMAND = "mcp2p --mcp-server --transport streamable-http --host 127.0.0.1 --port 8765"


def is_unsupported_terminal_platform() -> bool:
    """True en Windows nativo, donde aun no existe conector ConPTY."""
    return sys.platform == "win32" or os.name == "nt"


def unsupported_platform_response() -> Dict[str, Any]:
    return {
        "success": False,
        "degraded": True,
        "code": "TERMINAL_PLATFORM_UNSUPPORTED",
        "unsupported_platform": True,
        "platform": "win32",
        "message": (
            "Las terminales integradas todavía no están disponibles en Windows (falta el conector ConPTY). "
            "Ejecute su CLI en una terminal propia y conéctela al servidor MCP con el comando manual."
        ),
        "manual_command": MANUAL_COMMAND,
        "mcp_config": {"mcpServers": {"mcp2p": {"url": "http://127.0.0.1:8765/mcp"}}},
    }
