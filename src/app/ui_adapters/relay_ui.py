"""Puente de la GUI para el relevo persistente (Ola 3, backend de F1.6; la UI la hace otra ola).

El HUMANO, desde la GUI, puede:

- ver su bandeja (mensajes `parked`: cadena agotada o confirmacion pedida) con el texto exacto;
- liberar o descartar un mensaje aparcado (con dialogo NATIVO: el JS de la ventana no puede pulsarlo, EGM-74);
- consultar la auditoria (`verify_chain()` + conteos) y el estado de la parada;
- activar la parada global (sin dialogo: bajar autonomia es la direccion segura) y desactivarla o reanudar un panel
  que se pauso a si mismo (con dialogo nativo).

Nada de esto existe como herramienta MCP: un agente no puede liberar, descartar ni reanudar.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from src.app.ui_adapters import _BaseUIAdapter

logger = logging.getLogger("mcp2p.app.ui_adapters.relay")

_PREVIEW_CHARS = 600


def _fail(code: str, message: str, **extra: Any) -> Dict[str, Any]:
    return {"success": False, "code": code, "message": message, **extra}


class RelayUIAdapter(_BaseUIAdapter):
    def _service(self) -> Any:
        kernel = self.core_kernel
        if kernel is None:
            return None
        from src.core.factory import ApplicationFactory

        registry = None
        extensions = getattr(self._bridge, "extensions_ui", None)
        getter = getattr(extensions, "_get_pane_registry", None)
        if callable(getter):
            registry = getter()  # el MISMO registro que usa el Hub embebido
        return ApplicationFactory.message_relay_service(kernel, registry)

    def _confirm_native(self, title: str, message: str) -> Optional[Dict[str, Any]]:
        confirmations = getattr(self._bridge, "confirmations", None)
        if confirmations is None:
            return _fail("CONFIRMATION_UNAVAILABLE", "No hay servicio de confirmacion nativa; la accion se rechaza.")
        return confirmations.confirm(title, message)

    @staticmethod
    def _who(label: Dict[str, Any]) -> str:
        pane = label.get("pane_id") or "?"
        name = label.get("agent_name") or label.get("node_uuid") or label.get("principal")
        return f"{name} ({pane})"

    def relay_list_human_inbox(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        try:
            service = self._service()
            if service is None:
                return _fail("RELAY_UNAVAILABLE", "El relevo no esta disponible.")
            target = self._resolve_project_uuid(project_uuid) if project_uuid != "*" else None
            items = service.list_human_inbox(target)
            return {"success": True, "project_uuid": target, "count": len(items), "items": items}
        except Exception as exc:
            logger.warning("relay_list_human_inbox fallo: %s", exc)
            return _fail("INTERNAL_ERROR", str(exc))

    def relay_resolve_parked(self, msg_id: Any, decision: Any) -> Dict[str, Any]:
        try:
            if not isinstance(msg_id, str) or not msg_id.strip() or decision not in ("release", "discard"):
                return _fail("INVALID_ARGUMENT", "Indique msg_id y decision ('release' o 'discard').")
            service = self._service()
            if service is None:
                return _fail("RELAY_UNAVAILABLE", "El relevo no esta disponible.")
            card = next((c for c in service.list_human_inbox(None) if c["msg_id"] == msg_id.strip()), None)
            if card is None:
                return _fail("MESSAGE_NOT_FOUND", "No hay ningun mensaje aparcado con ese identificador.")
            text = card.get("text") or "(texto borrado por retencion)"
            preview = text if len(text) <= _PREVIEW_CHARS else text[:_PREVIEW_CHARS] + " [...]"
            action = "Entregar" if decision == "release" else "Descartar"
            declined = self._confirm_native(
                f"{action} un mensaje entre agentes",
                f"De: {self._who(card['from'])}\nPara: {self._who(card['to'])}\n"
                f"Motivo de la retencion: {card.get('reason')}\nCadena: salto {card['chain']['hop']} "
                f"(maximo automatico {card['chain']['max_depth']})\n\nTexto exacto:\n{preview}\n\n"
                + (
                    "Si lo entrega, el agente destino podra leerlo en su bandeja. No se escribe nada en ninguna "
                    "terminal."
                    if decision == "release"
                    else "Si lo descarta, el agente destino no lo vera nunca. Queda constancia en la auditoria."
                ),
            )
            if declined is not None:
                return {**declined, "msg_id": msg_id}
            return service.resolve_parked(msg_id.strip(), decision)
        except Exception as exc:
            logger.warning("relay_resolve_parked fallo: %s", exc)
            return _fail("INTERNAL_ERROR", str(exc))

    def relay_audit_status(self) -> Dict[str, Any]:
        try:
            service = self._service()
            if service is None:
                return _fail("RELAY_UNAVAILABLE", "El relevo no esta disponible.")
            status = service.audit_status()
            loaded = getattr(service, "_identity", None)  # no fuerza crear la clave solo para mirar
            status["device"] = loaded.status() if loaded is not None and hasattr(loaded, "status") else None
            return status
        except Exception as exc:
            logger.warning("relay_audit_status fallo: %s", exc)
            return _fail("INTERNAL_ERROR", str(exc))

    def relay_pause_state(self) -> Dict[str, Any]:
        try:
            service = self._service()
            if service is None:
                return _fail("RELAY_UNAVAILABLE", "El relevo no esta disponible.")
            return service.pause_state()
        except Exception as exc:
            return _fail("INTERNAL_ERROR", str(exc))

    def relay_global_pause(self, enabled: Any) -> Dict[str, Any]:
        try:
            if not isinstance(enabled, bool):
                return _fail("INVALID_ARGUMENT", "enabled debe ser booleano.")
            service = self._service()
            if service is None:
                return _fail("RELAY_UNAVAILABLE", "El relevo no esta disponible.")
            if not enabled:
                declined = self._confirm_native(
                    "Reanudar el relevo entre agentes",
                    "Se desactiva la parada global: los agentes volveran a recibir en su bandeja los mensajes "
                    "retenidos y los nuevos. No se escribe nada en ninguna terminal.",
                )
                if declined is not None:
                    return declined
            return service.set_global_pause(enabled)
        except Exception as exc:
            return _fail("INTERNAL_ERROR", str(exc))

    def relay_resume_pane(self, node_uuid: Any) -> Dict[str, Any]:
        try:
            if not isinstance(node_uuid, str) or not node_uuid.strip():
                return _fail("INVALID_ARGUMENT", "Indique el node_uuid del panel.")
            service = self._service()
            if service is None:
                return _fail("RELAY_UNAVAILABLE", "El relevo no esta disponible.")
            if node_uuid.strip() not in service.pause_state()["paused_panes"]:
                return _fail("PANE_NOT_PAUSED", "Ese panel no tiene el relevo en pausa.")
            declined = self._confirm_native(
                "Reanudar el relevo de un panel",
                f"Panel: {node_uuid.strip()}\n\nEl agente de este panel puso en pausa su propio relevo. Si lo "
                "reanuda, volvera a recibir y enviar mensajes de su bandeja.",
            )
            if declined is not None:
                return declined
            return service.resume_pane(node_uuid.strip())
        except Exception as exc:
            return _fail("INTERNAL_ERROR", str(exc))
