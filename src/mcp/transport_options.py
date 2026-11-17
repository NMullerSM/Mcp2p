"""Opciones de transporte del servidor MCP (Gate 0d).

Módulo ligero (solo stdlib) para que `src.app.main` pueda declarar `--transport/--host/--port`
sin importar el SDK MCP ni la base de datos al arrancar la GUI.
"""

from __future__ import annotations

import argparse
import os
from typing import Any, Optional

# Transportes MCP soportados por los dos puntos de entrada (`python -m src.mcp.server` y
# `mcp2p --mcp-server`). `stdio` es el defecto; `streamable-http` deja disponible el transporte
# HTTP que necesita el módulo de terminales (Gate 0d). Sin autenticación todavía (Tarea 4.2), por
# eso los transportes HTTP solo aceptan direcciones de loopback.
MCP_TRANSPORTS = ("stdio", "sse", "streamable-http")
HTTP_TRANSPORTS = ("sse", "streamable-http")
DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8765


class NonLoopbackBindError(ValueError):
    """El host pedido para un transporte HTTP no es de loopback."""


def resolve_http_bind(host: Optional[str] = None, port: Optional[int] = None) -> tuple[str, int]:
    """Resuelve el (host, puerto) de los transportes HTTP del servidor MCP.

    Prioridad: argumento explícito > `MCP2P_MCP_HOST`/`MCP2P_MCP_PORT` > `127.0.0.1:8765`.
    El puerto `0` pide uno efímero al sistema operativo. Solo se aceptan direcciones de loopback
    (`127.0.0.0/8`, `::1`, `localhost`): sin autenticación por token (Tarea 4.2) exponer el
    servidor en `0.0.0.0` o en una interfaz de red daría control del enjambre a cualquiera.
    """
    import ipaddress

    resolved_host = (host or os.environ.get("MCP2P_MCP_HOST") or DEFAULT_HTTP_HOST).strip()
    if resolved_host.lower() != "localhost":
        try:
            is_loopback = ipaddress.ip_address(resolved_host.strip("[]")).is_loopback
        except ValueError:
            is_loopback = False
        if not is_loopback:
            raise NonLoopbackBindError(
                f"Host '{resolved_host}' rechazado: el servidor MCP HTTP solo escucha en loopback "
                "(127.0.0.1, ::1 o localhost) hasta que exista autenticación por token."
            )
    raw_port: Any = port if port is not None else os.environ.get("MCP2P_MCP_PORT", DEFAULT_HTTP_PORT)
    try:
        resolved_port = int(raw_port)
    except (TypeError, ValueError):
        raise ValueError(f"Puerto MCP inválido: {raw_port!r}") from None
    if not 0 <= resolved_port <= 65535:
        raise ValueError(f"Puerto MCP fuera de rango: {resolved_port}")
    return resolved_host, resolved_port


def add_transport_arguments(parser: argparse.ArgumentParser) -> None:
    """Argumentos de transporte compartidos por `src.mcp.server` y `src.app.main --mcp-server`."""
    parser.add_argument(
        "--transport", type=str, default="stdio", choices=list(MCP_TRANSPORTS),
        help="Transporte MCP (stdio por defecto)",
    )
    parser.add_argument(
        "--host", type=str, default=None,
        help=f"Host de loopback para sse/streamable-http (defecto {DEFAULT_HTTP_HOST}; nunca 0.0.0.0)",
    )
    parser.add_argument(
        "--port", type=int, default=None,
        help=f"Puerto para sse/streamable-http (defecto {DEFAULT_HTTP_PORT}; 0 = efímero)",
    )
    parser.add_argument(
        "--hub-proxy-only", action="store_true",
        help="Solo proxy al Hub de MCP2P (MCP2P_HUB_URL/MCP2P_SESSION_TOKEN) por stdio; nunca abre la base de datos. "
        "Sin Hub responde HUB_REQUIRED",
    )
