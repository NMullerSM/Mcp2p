"""Proxy automático `stdio -> EmbeddedMcpHub HTTP` para CLIs externos.

Permite que un CLI configurado globalmente con transporte `stdio` (por ejemplo
`agy` u otros clientes MCP) se conecte al `EmbeddedMcpHub` vivo de la ventana
mediante las variables de entorno `MCP2P_HUB_URL` y `MCP2P_SESSION_TOKEN`, sin
abrir una base de datos SQLite ni un `PaneRegistry` aislados.
"""

from __future__ import annotations

import contextlib
import ipaddress
import json
import logging
import os
from typing import Any, AsyncIterator, Awaitable, Callable, Mapping, Optional, Sequence
from urllib.parse import urlparse

import anyio
import httpx2
from mcp import types
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.lowlevel import Server
from mcp.server.lowlevel.server import ServerRequestContext
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import MCPError

from src.core.services.swarm_relay_service import MAX_AWAIT_TIMEOUT_MS

logger = logging.getLogger("mcp2p.mcp.stdio_proxy")

# Una llamada `await_next_assignment` puede mantener la respuesta abierta hasta MAX_AWAIT_TIMEOUT_MS: la lectura
# aguanta ese tope mas un margen (EGM-97). Antes regia el valor por defecto de 5 s y cortaba la espera "pull".
HUB_READ_TIMEOUT_S = MAX_AWAIT_TIMEOUT_MS / 1000 + 30
HUB_HTTP_TIMEOUT = httpx2.Timeout(connect=3, read=HUB_READ_TIMEOUT_S, write=10, pool=5)
# Tope por defecto de cada peticion (handshake, listas, prompts): un Hub que acepta y no contesta no cuelga al CLI.
HUB_REQUEST_TIMEOUT_S = 15.0
# Esperas entre intentos de CONEXION (backoff acotado: ~1,75 s en total). Una llamada ya enviada nunca se reintenta,
# porque `send_pane_message` y compania no son idempotentes.
RECONNECT_DELAYS_S: Sequence[float] = (0.0, 0.25, 0.5, 1.0)
# Presupuesto de TIEMPO para reconectar: no se empieza un intento nuevo si ya se gasto. En Linux un puerto cerrado
# responde al instante y caben todos los intentos; en Windows rechazar una conexion a loopback tarda ~2 s por intento,
# y 4 intentos dejaban al CLI casi 10 s esperando el aviso de "Hub caido".
RECONNECT_BUDGET_S = 2.0

HUB_UNREACHABLE = "HUB_UNREACHABLE"
HUB_REQUIRED = "HUB_REQUIRED"
RELAY_TOOL_NAMES = ("await_next_assignment", "list_swarm_panes", "send_pane_message")


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


def hub_proxy_misconfiguration(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """Mensaje de error si el entorno PIDE el proxy al Hub pero no es utilizable; None si no hay intencion o es valido.

    La intencion es proxy cuando `MCP2P_HUB_URL` o `MCP2P_SESSION_TOKEN` estan presentes (los fija MCP2P en los
    paneles). Una URL ausente, no `http://` o no loopback, o un token ausente, NO debe degradar en silencio a un
    servidor independiente con otra cola, otra BD e identidad autodeclarada (EGM-98/106). El modo independiente
    explicito es el de un entorno sin ninguna de las dos variables.
    """
    source = env if env is not None else os.environ
    hub_url = (source.get("MCP2P_HUB_URL") or "").strip()
    session_token = (source.get("MCP2P_SESSION_TOKEN") or "").strip()
    if not hub_url and not session_token:
        return None
    if should_use_hub_proxy(source) is not None:
        return None
    problems = []
    if not hub_url:
        problems.append("falta MCP2P_HUB_URL")
    elif not _is_loopback_http_url(hub_url):
        problems.append("MCP2P_HUB_URL debe ser una URL http:// de loopback (127.0.0.1, localhost o [::1])")
    if not session_token:
        problems.append("falta MCP2P_SESSION_TOKEN")
    return (
        "MCP2P: se pidio el modo proxy al Hub pero la configuracion no es valida ("
        + "; ".join(problems)
        + "). No se arranca un servidor independiente (EGM-98). Reabre el panel desde MCP2P o, para usar el "
        "servidor independiente, quita MCP2P_HUB_URL y MCP2P_SESSION_TOKEN del entorno."
    )




class HubUnavailableError(RuntimeError):
    """El Hub embebido no responde (caido, URL desactualizada o token rechazado)."""


def _unavailable_message(hub_url: str, reason: str) -> str:
    return (
        f"El Hub de MCP2P no responde en {hub_url} ({reason}). Si cerraste o reiniciaste MCP2P, vuelve a abrir "
        "este CLI desde un panel de MCP2P: la URL y el token del Hub cambian al reiniciarlo."
    )


def _error_payload(code: str, message: str) -> str:
    return json.dumps({"success": False, "code": code, "error": message}, indent=2, ensure_ascii=False)


def _error_result(code: str, message: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=_error_payload(code, message))], is_error=True)


class HubLink:
    """Abre sesiones MCP cortas contra el Hub, con reconexion acotada de la CONEXION (no de las llamadas).

    Cada operacion abre su sesion (`initialize` incluido) y la cierra al terminar. Asi una caida o un reinicio del
    Hub en la misma URL se recupera sin estado (EGM-97); los reintentos solo cubren conectar y negociar, nunca una
    llamada ya enviada, porque `send_pane_message` y compania no son idempotentes.
    """

    def __init__(self, hub_url: str, session_token: str, *, delays: Optional[Sequence[float]] = None) -> None:
        self.hub_url = hub_url
        self._token = session_token
        self._delays = delays

    @contextlib.asynccontextmanager
    async def session(self) -> AsyncIterator[ClientSession]:
        delays = tuple(self._delays if self._delays is not None else RECONNECT_DELAYS_S) or (0.0,)
        last_error: Optional[BaseException] = None
        started = anyio.current_time()
        for delay in delays:
            if last_error is not None and anyio.current_time() - started + delay >= RECONNECT_BUDGET_S:
                break
            if delay:
                await anyio.sleep(delay)
            connected = False
            try:
                # Todo el intento vive en un solo `async with` lexico: los task groups del transporte cancelan al
                # anfitrion cuando la conexion falla y solo su propio `__aexit__` convierte eso en excepcion.
                async with httpx2.AsyncClient(
                    headers={"Authorization": f"Bearer {self._token}"}, timeout=HUB_HTTP_TIMEOUT
                ) as http:
                    async with streamable_http_client(self.hub_url, http_client=http) as (remote_read, remote_write):
                        async with ClientSession(
                            remote_read, remote_write, read_timeout_seconds=HUB_REQUEST_TIMEOUT_S
                        ) as session:
                            await session.initialize()
                            connected = True
                            yield session
                            return
            except Exception as exc:  # incluye ExceptionGroup de los task groups del transporte
                if connected:
                    raise  # un fallo del cuerpo (la llamada) no se reintenta
                last_error = exc
        raise HubUnavailableError(_unavailable_message(self.hub_url, _describe(last_error))) from last_error


def _describe(exc: Optional[BaseException]) -> str:
    """Resumen corto y sin secretos de por que fallo la conexion."""
    if exc is None:
        return "sin detalle"
    inner = exc
    while isinstance(inner, BaseExceptionGroup) and inner.exceptions:
        inner = inner.exceptions[0]
    if isinstance(inner, httpx2.HTTPStatusError):
        return f"HTTP {inner.response.status_code}"
    text = str(inner).strip()
    return f"{type(inner).__name__}: {text}" if text else type(inner).__name__


def _is_transport_failure(exc: BaseException) -> bool:
    """True si `exc` es una conexion perdida o vencida (no un error de protocolo devuelto por el Hub)."""
    if isinstance(exc, MCPError):
        return exc.code in (types.CONNECTION_CLOSED, types.REQUEST_TIMEOUT)
    return isinstance(exc, (OSError, httpx2.HTTPError, anyio.ClosedResourceError, anyio.BrokenResourceError, BaseExceptionGroup))


def build_proxy_server(link: HubLink, hub_capabilities: Optional[types.ServerCapabilities]) -> Server:
    """Servidor `stdio` que reenvia al Hub: herramientas siempre; recursos y prompts si el Hub los ofrece."""

    async def _on_list_tools(
        _ctx: ServerRequestContext[Any], params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        return await _hub_op(link, lambda s: s.list_tools(params=params))

    async def _on_call_tool(
        ctx: ServerRequestContext[Any], params: types.CallToolRequestParams
    ) -> types.CallToolResult | types.InputRequiredResult:
        progress_token = (ctx.meta or {}).get("progress_token") if ctx.meta else None

        async def _forward_progress(progress: float, total: float | None, message: str | None) -> None:
            await ctx.session.send_progress_notification(progress_token, progress, total, message)

        try:
            async with link.session() as remote:
                return await remote.call_tool(
                    params.name,
                    arguments=params.arguments or {},
                    read_timeout_seconds=HUB_READ_TIMEOUT_S,
                    progress_callback=_forward_progress if progress_token is not None else None,
                )
        except HubUnavailableError as exc:
            return _error_result(HUB_UNREACHABLE, str(exc))
        except Exception as exc:
            if _is_transport_failure(exc):
                return _error_result(
                    HUB_UNREACHABLE,
                    _unavailable_message(link.hub_url, f"se perdio la conexion durante {params.name}: {_describe(exc)}"),
                )
            raise

    handlers: dict[str, Any] = {"on_list_tools": _on_list_tools, "on_call_tool": _on_call_tool}

    if hub_capabilities is not None and hub_capabilities.resources is not None:

        async def _on_list_resources(
            _ctx: ServerRequestContext[Any], params: types.PaginatedRequestParams | None
        ) -> types.ListResourcesResult:
            return await _hub_op(link, lambda s: s.list_resources(params=params))

        async def _on_list_templates(
            _ctx: ServerRequestContext[Any], params: types.PaginatedRequestParams | None
        ) -> types.ListResourceTemplatesResult:
            return await _hub_op(link, lambda s: s.list_resource_templates(params=params))

        async def _on_read_resource(
            _ctx: ServerRequestContext[Any], params: types.ReadResourceRequestParams
        ) -> types.ReadResourceResult:
            return await _hub_op(link, lambda s: s.read_resource(params.uri))

        handlers.update(
            on_list_resources=_on_list_resources,
            on_list_resource_templates=_on_list_templates,
            on_read_resource=_on_read_resource,
        )

    if hub_capabilities is not None and hub_capabilities.prompts is not None:

        async def _on_list_prompts(
            _ctx: ServerRequestContext[Any], params: types.PaginatedRequestParams | None
        ) -> types.ListPromptsResult:
            return await _hub_op(link, lambda s: s.list_prompts(params=params))

        async def _on_get_prompt(
            _ctx: ServerRequestContext[Any], params: types.GetPromptRequestParams
        ) -> types.GetPromptResult:
            return await _hub_op(link, lambda s: s.get_prompt(params.name, params.arguments))

        handlers.update(on_list_prompts=_on_list_prompts, on_get_prompt=_on_get_prompt)

    return Server("mcp2p-stdio-proxy", **handlers)


async def _hub_op(link: HubLink, op: Callable[[ClientSession], Awaitable[Any]]) -> Any:
    """Ejecuta una operacion de catalogo contra el Hub; un Hub caido es un error MCP inmediato, no un cuelgue."""
    try:
        async with link.session() as remote:
            return await op(remote)
    except HubUnavailableError as exc:
        raise MCPError(types.INTERNAL_ERROR, str(exc)) from exc
    except MCPError:
        raise
    except Exception as exc:
        if _is_transport_failure(exc):
            raise MCPError(
                types.INTERNAL_ERROR, _unavailable_message(link.hub_url, f"conexion perdida: {_describe(exc)}")
            ) from exc
        raise


async def _probe_capabilities(link: HubLink) -> Optional[types.ServerCapabilities]:
    """Capacidades que anuncia el Hub, para exponer recursos/prompts solo si existen. None si no responde."""
    try:
        async with link.session() as remote:
            result = remote.initialize_result
            return result.capabilities if result is not None else None
    except Exception as exc:
        logger.warning("MCP2P proxy: el Hub no respondio al arrancar (%s); se sirven solo herramientas.", _describe(exc))
        return None


async def _serve_stdio_proxy(hub_url: str, session_token: str) -> None:
    """Puentea solicitudes MCP `stdio` locales hacia el `EmbeddedMcpHub` HTTP."""
    link = HubLink(hub_url, session_token)
    capabilities = await _probe_capabilities(link)
    proxy_server = build_proxy_server(link, capabilities)
    async with stdio_server() as (stdio_read, stdio_write):
        await proxy_server.run(stdio_read, stdio_write, proxy_server.create_initialization_options())


def run_stdio_hub_proxy(hub_url: str, session_token: str) -> None:
    """Ejecuta el proxy `stdio -> HTTP` usando `anyio.run`."""
    anyio.run(_serve_stdio_proxy, hub_url, session_token)


# -- Modo `--hub-proxy-only` sin Hub (EGM-98) --------------------------------------------------------------------

_NO_HUB_MESSAGE = (
    "El relevo entre paneles exige el Hub de MCP2P. Este CLI se lanzo fuera de un panel de MCP2P, asi que no "
    "hay Hub ni token: abra este CLI desde un panel de MCP2P. No se abre ninguna base de datos ni se usa un "
    "node_uuid autodeclarado."
)

_RELAY_TOOLS: tuple[types.Tool, ...] = (
    types.Tool(
        name="await_next_assignment",
        description="Espera la siguiente asignacion o mensaje del enjambre. Requiere abrir el CLI desde un panel de MCP2P.",
        input_schema={
            "type": "object",
            "properties": {"project_uuid": {"type": "string"}, "timeout_ms": {"type": "integer"}},
            "additionalProperties": True,
        },
    ),
    types.Tool(
        name="list_swarm_panes",
        description="Lista los paneles activos del enjambre. Requiere abrir el CLI desde un panel de MCP2P.",
        input_schema={"type": "object", "properties": {"project_uuid": {"type": "string"}}, "additionalProperties": True},
    ),
    types.Tool(
        name="send_pane_message",
        description="Envia un mensaje a otro panel del enjambre. Requiere abrir el CLI desde un panel de MCP2P.",
        input_schema={
            "type": "object",
            "properties": {
                "project_uuid": {"type": "string"},
                "target_pane_id": {"type": "string"},
                "message": {"type": "string"},
            },
            "additionalProperties": True,
        },
    ),
)


def build_hub_required_server() -> Server:
    """Servidor `stdio` sin Hub: solo las tres herramientas del relevo, todas responden `HUB_REQUIRED`."""

    async def _on_list_tools(
        _ctx: ServerRequestContext[Any], _params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        return types.ListToolsResult(tools=list(_RELAY_TOOLS))

    async def _on_call_tool(
        _ctx: ServerRequestContext[Any], params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        if params.name not in RELAY_TOOL_NAMES:
            return _error_result("UNKNOWN_TOOL", f"Herramienta desconocida: {params.name}")
        return types.CallToolResult(content=[types.TextContent(type="text", text=_error_payload(HUB_REQUIRED, _NO_HUB_MESSAGE))])

    return Server("mcp2p-hub-required", on_list_tools=_on_list_tools, on_call_tool=_on_call_tool)


async def _serve_hub_required() -> None:
    server = build_hub_required_server()
    async with stdio_server() as (stdio_read, stdio_write):
        await server.run(stdio_read, stdio_write, server.create_initialization_options())


def run_hub_proxy_only(env: Optional[Mapping[str, str]] = None) -> int:
    """Punto de entrada de `--hub-proxy-only` (servidor y binario empaquetado). Nunca abre la base de datos.

    * Variables de Hub validas: proxy real al Hub.
    * Variables de Hub invalidas o a medias: error y codigo de salida 2 (fallo cerrado, EGM-98).
    * Sin variables: servidor minimo con las tres herramientas del relevo respondiendo `HUB_REQUIRED`.
    """
    proxy_cfg = should_use_hub_proxy(env)
    if proxy_cfg is not None:
        run_stdio_hub_proxy(proxy_cfg[0], proxy_cfg[1])
        return 0
    problem = hub_proxy_misconfiguration(env)
    if problem is not None:
        import sys

        print(problem, file=sys.stderr, flush=True)
        return 2
    anyio.run(_serve_hub_required)
    return 0
