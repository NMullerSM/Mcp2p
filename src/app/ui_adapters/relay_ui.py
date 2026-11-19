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
# Banderas de lanzamiento que saltan los permisos del CLI (clase `elevated` del 37 §F3.2): el destino ya no pide
# confirmacion a su propio usuario, asi que un mensaje del humano exige el dialogo nativo con el texto exacto.
_ELEVATED_FLAGS = frozenset(
    {"--dangerously-skip-permissions", "--yolo", "--dangerously-bypass-approvals-and-sandbox"}
)


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

    def _launch_flags(self, rec: Any) -> tuple:
        """Banderas de lanzamiento del CLI del panel segun el catalogo (vacio si no se conocen)."""
        try:
            spec = self._bridge.extensions_ui._catalog_service().get_spec(rec.cli_profile)
            return tuple(getattr(spec, "launch_flags", ()) or ())
        except Exception:
            return ()

    def relay_send_as_human(self, project_uuid: Any, target_pane_id: Any, text: Any, kind: Any = "note") -> Dict[str, Any]:
        """El humano escribe a un agente POR SU BANDEJA (nunca por el teclado de su terminal).

        Sobre firmado con `from = human:local`; confianza `human_confirmed` si la clave humana esta desbloqueada y,
        si no, `pane_authenticated`. Si el destino es `elevated` (CLI lanzado con permisos saltados) abre el dialogo
        nativo con el texto exacto. El agente no puede responder a este remitente: la respuesta se lee en su terminal.
        """
        try:
            if target_pane_id is None or not str(target_pane_id).strip():
                return _fail("INVALID_ARGUMENT", "Elija la terminal de destino del mensaje.")
            service = self._service()
            if service is None:
                return _fail("RELAY_UNAVAILABLE", "El relevo no esta disponible; reinicie MCP2P e intentelo de nuevo.")
            registry = getattr(self._bridge.extensions_ui, "_get_pane_registry", lambda: None)()
            try:
                rec = registry.get_pane(target_pane_id) if registry is not None else None
            except (TypeError, ValueError):
                rec = None
            if rec is None or rec.status == "closed":
                return _fail(
                    "TARGET_NOT_FOUND",
                    "No hay ninguna terminal abierta con ese identificador. Abra la terminal y vuelva a enviar.",
                )
            wanted = project_uuid.strip() if isinstance(project_uuid, str) and project_uuid.strip() else None
            if wanted is not None and wanted != rec.project_uuid:
                return _fail(
                    "PROJECT_SCOPE_FORBIDDEN",
                    "Esa terminal pertenece a otro proyecto. Elija una terminal del proyecto activo.",
                )
            if not isinstance(text, str) or not text.strip():
                return _fail("EMPTY_MESSAGE", "Escriba el mensaje antes de enviarlo.")
            if any(flag in _ELEVATED_FLAGS for flag in self._launch_flags(rec)):
                preview = text if len(text) <= _PREVIEW_CHARS else text[:_PREVIEW_CHARS] + " [...]"
                declined = self._confirm_native(
                    "Enviar un mensaje a un agente con permisos saltados",
                    f"Destino: {rec.pane_id} ({rec.cli_profile}) en el proyecto {rec.project_uuid or '(sin proyecto)'}\n\n"
                    "Este agente se lanzo con los permisos saltados: puede ejecutar acciones sin pedir confirmacion.\n\n"
                    f"Texto exacto:\n{preview}\n\nSe entrega a su bandeja y, si esa terminal tiene la entrega directa, tambien se escribe en ella.",
                )
                if declined is not None:
                    return {**declined, "target_pane_id": rec.pane_id}
            res = service.submit_as_human(
                text=text, target_rec=rec, kind=kind if isinstance(kind, str) else "note", project_uuid=rec.project_uuid
            )
            if res.get("success") is False:
                return _fail(str(res.get("code") or "SEND_FAILED"), str(res.get("error") or "No se pudo enviar."))
            return {**res, "target_pane_id": rec.pane_id}
        except Exception as exc:
            logger.warning("relay_send_as_human fallo: %s", exc)
            return _fail("INTERNAL_ERROR", str(exc))

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
            clean_id = msg_id.strip()
            service = self._service()
            if service is None:
                return _fail("RELAY_UNAVAILABLE", "El relevo no esta disponible.")
            card = next((c for c in service.list_human_inbox(None) if c["msg_id"] == clean_id), None)
            if card is None:
                return _fail("MESSAGE_NOT_FOUND", "No hay ningun mensaje aparcado con ese identificador.")
            text = card.get("text") or "(texto borrado por retencion)"
            preview = text if len(text) <= _PREVIEW_CHARS else text[:_PREVIEW_CHARS] + " [...]"
            if decision == "release":
                declined = self._confirm_native(
                    "Entregar un mensaje entre agentes",
                    f"De: {self._who(card['from'])}\nPara: {self._who(card['to'])}\n"
                    f"Motivo de la retencion: {card.get('reason')}\nCadena: salto {card['chain']['hop']} "
                    f"(maximo automatico {card['chain']['max_depth']})\n\nTexto exacto:\n{preview}\n\n"
                    "Si lo entrega, el agente destino podra leerlo en su bandeja y, si esa terminal tiene la entrega "
                    "directa, tambien se escribe en ella.",
                )
                if declined is not None:
                    return {**declined, "msg_id": msg_id}
                return service.resolve_parked(clean_id, decision)

            gate = getattr(self._bridge, "confirm_gate", None)
            if gate is None:
                return _fail("CONFIRMATION_UNAVAILABLE", "No hay servicio de confirmacion; la accion se rechaza.")
            res = gate.require(
                "relay.discard_parked",
                "Descartar un mensaje entre agentes",
                f"De: {self._who(card['from'])}\nPara: {self._who(card['to'])}\n"
                f"Motivo de la retencion: {card.get('reason')}\nCadena: salto {card['chain']['hop']} "
                f"(maximo automatico {card['chain']['max_depth']})\n\nTexto exacto:\n{preview}\n\n"
                "Si lo descarta, el agente destino no lo vera nunca. Queda constancia en la auditoria.",
                lambda: service.resolve_parked(clean_id, "discard"),
                args_digest=f"discard:{clean_id}",
            )
            if res.get("success") is False and "msg_id" not in res:
                return {**res, "msg_id": msg_id}
            return res
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
                    "retenidos y los nuevos. Las terminales con entrega directa activa tambien reciben el texto.",
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
