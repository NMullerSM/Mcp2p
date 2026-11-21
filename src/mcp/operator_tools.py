"""Herramientas MCP del operador (Ola 4, F2.2 del dossier 37). Viven en un servidor MCP APARTE, servido solo por el
canal local del operador (`operator_channel.py`):

- el Hub HTTP de los paneles NO tiene estas herramientas (un token de panel no puede invocarlas), y
- este servidor NO tiene las de los paneles (una sesion de operador no puede leer bandejas ni hablar como un panel).

Ningun parametro fija la identidad, la confianza ni la cadena: `from = operator:<sesion>`, `trust`, `chain_id` y `hop`
los pone el Hub (hay prueba AST). El texto que envia el operador llega al agente como dato (`payload_untrusted_text`).
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

import anyio
from mcp.server.mcpserver import Context, MCPServer

from src.core.services.message_relay_service import MAX_WAIT_MS

OPERATOR_TOOLS = ("operator_list_targets", "operator_send_task", "operator_message_status", "operator_await_reply")
_AWAIT_SLICE_MS = 10_000  # cada tramo de espera; entre tramos se informa progreso (inactividad de 5 min en clientes)
_DEFAULT_AWAIT_MS = 60_000

INSTRUCTIONS = (
    "Canal del operador de MCP2P. Envias tareas a los agentes de las terminales de MCP2P y lees sus respuestas. "
    "1) operator_list_targets para ver destinos (target = identidad estable del panel). 2) operator_send_task. "
    "3) operator_await_reply o operator_message_status. Tus mensajes llegan a la bandeja del agente con confianza "
    "operator_delegated; y, con entrega directa activa en ese destino, tambien se escribe en su terminal. Los destinos con permisos amplios o shells, y los envios con "
    "confirm_with_human=true, esperan a que el humano los apruebe en MCP2P."
)


def _json(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _clamp(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        number = int(value if value is not None else _DEFAULT_AWAIT_MS)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, min(number, MAX_WAIT_MS))


def build_operator_server(service: Any, op: Any, *, version: str = "") -> MCPServer:
    """Servidor MCP de UNA conexion del operador: sus herramientas quedan ligadas a esa conexion (`op`)."""
    if not version:
        from src.mcp.tools import MCP_API_VERSION

        version = MCP_API_VERSION
    server = MCPServer("mcp2p-operator", version=version, instructions=INSTRUCTIONS)

    @server.tool()
    def operator_list_targets(project_uuid: Optional[str] = None) -> str:
        """Lista los paneles activos a los que puedes enviar tareas (solo los proyectos de tu sesion): target (identidad
        estable), CLI, estado, Auto-Relay, clase de privilegio y si exige confirmacion humana."""
        return _json(service.call(op, "list_targets", project_uuid=project_uuid))

    @server.tool()
    def operator_send_task(
        target: str,
        text: str,
        project_uuid: str,
        kind: str = "task.request",
        confirm_with_human: bool = False,
        card_id: Optional[str] = None,
    ) -> str:
        """Envia una tarea (sobre firmado por el Hub, maximo 8 KiB) a la bandeja de un panel. target: el campo target de
        operator_list_targets (o el pane_id). kind: task.request | note. confirm_with_human=true la retiene hasta que
        el humano la apruebe en MCP2P. Devuelve msg_id y estado (queued | parked)."""
        return _json(
            service.call(
                op,
                "send_task",
                target=target,
                text=text,
                project_uuid=project_uuid,
                kind=kind,
                confirm_with_human=confirm_with_human,
                card_id=card_id,
            )
        )

    @server.tool()
    def operator_message_status(msg_id: str) -> str:
        """Estado de un mensaje que enviaste: queued | delivered | acked | replied | parked | expired | discarded."""
        return _json(service.call(op, "message_status", msg_id=msg_id))

    @server.tool()
    async def operator_await_reply(ctx: Context, msg_id: str, timeout_ms: int = _DEFAULT_AWAIT_MS) -> str:
        """Espera (tope 300000 ms) la respuesta del agente a tu mensaje (la envia con inbox_reply) y la devuelve con su
        procedencia verificada. Informa progreso mientras espera."""
        total = _clamp(timeout_ms)
        started = time.monotonic()
        while True:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            chunk = max(0, min(_AWAIT_SLICE_MS, total - elapsed_ms))
            result = await anyio.to_thread.run_sync(
                lambda: service.call(op, "await_reply", msg_id=msg_id, timeout_ms=chunk)
            )
            elapsed_ms = int((time.monotonic() - started) * 1000)
            done = (
                not result.get("success")
                or result.get("replies")
                or result.get("message_state") in ("expired", "discarded")
                or elapsed_ms >= total
            )
            if done:
                result["timeout_ms_applied"] = total
                return _json(result)
            try:
                await ctx.report_progress(elapsed_ms, total, "Esperando la respuesta del agente")
            except Exception:
                pass  # el cliente no pidio progreso o ya no escucha: la espera sigue

    setattr(server, "mcp2p_operator_conn", op)
    return server
