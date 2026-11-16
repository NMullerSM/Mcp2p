"""Proxy automático `stdio -> EmbeddedMcpHub HTTP` para CLIs externos.

Permite que un CLI configurado globalmente con transporte `stdio` (por ejemplo
`agy` u otros clientes MCP) se conecte al `EmbeddedMcpHub` vivo de la ventana
mediante las variables de entorno `MCP2P_HUB_URL` y `MCP2P_SESSION_TOKEN`, sin
abrir una base de datos SQLite ni un `PaneRegistry` aislados.
"""

from __future__ import annotations

import ipaddress
import os
from typing import Mapping, Optional
from urllib.parse import urlparse

import anyio
import httpx2
from mcp import types
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.lowlevel import Server
from mcp.server.lowlevel.server import ServerRequestContext
from mcp.server.stdio import stdio_server


def _is_loopback_http_url(raw_url: str) -> bool:
    """Valida que `raw_url` sea una URL `http://` apuntando a loopback."""
    try:
        parsed = urlparse(raw_url)
        _ = parsed.port
    except ValueError:
        return False

    if parsed.scheme.lower() != "http" or not parsed.hostname:
        return False

    host = parsed.hostname.strip().lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def should_use_hub_proxy(
    env: Optional[Mapping[str, str]] = None,
) -> Optional[tuple[str, str]]:
    """Retorna `(hub_url, session_token)` si el entorno pide proxy al Hub loopback."""
    source = env if env is not None else os.environ
    hub_url = (source.get("MCP2P_HUB_URL") or "").strip()
    session_token = (source.get("MCP2P_SESSION_TOKEN") or "").strip()
    if not hub_url or not session_token:
        return None
    if not _is_loopback_http_url(hub_url):
        return None
    return hub_url, session_token


async def _serve_stdio_proxy(hub_url: str, session_token: str) -> None:
    """Puentea solicitudes MCP `stdio` locales hacia el `EmbeddedMcpHub` HTTP."""
    async with httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {session_token}"}
    ) as http_client:
        async with streamable_http_client(hub_url, http_client=http_client) as (
            remote_read,
            remote_write,
        ):
            async with ClientSession(remote_read, remote_write) as remote_session:
                await remote_session.initialize()

                async def _on_list_tools(
                    _ctx: ServerRequestContext[object],
                    params: types.PaginatedRequestParams | None,
                ) -> types.ListToolsResult:
                    return await remote_session.list_tools(params=params)

                async def _on_call_tool(
                    _ctx: ServerRequestContext[object],
                    params: types.CallToolRequestParams,
                ) -> types.CallToolResult | types.InputRequiredResult:
                    return await remote_session.call_tool(
                        params.name,
                        arguments=params.arguments or {},
                    )

                proxy_server = Server(
                    "mcp2p-stdio-proxy",
                    on_list_tools=_on_list_tools,
                    on_call_tool=_on_call_tool,
                )

                async with stdio_server() as (stdio_read, stdio_write):
                    await proxy_server.run(
                        stdio_read,
                        stdio_write,
                        proxy_server.create_initialization_options(),
                    )


def run_stdio_hub_proxy(hub_url: str, session_token: str) -> None:
    """Ejecuta el proxy `stdio -> HTTP` usando `anyio.run`."""
    anyio.run(_serve_stdio_proxy, hub_url, session_token)
