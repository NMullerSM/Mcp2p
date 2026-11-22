"""Adaptador UI del módulo de chat (WP-8): el ÚNICO sitio que conoce el relevo y el puente para cablear `src/chat`.

Construye el `ChatService`, registra el adaptador de agentes locales con funciones del relevo y del registro de
paneles, y expone `chat_*` al frontend. Los nodos P2P o el servidor se conectarán registrando aquí sus propios
adaptadores (`service.register_source/register_transport/register_roster`) sin tocar el módulo.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.app.ui_adapters import _BaseUIAdapter
from src.chat.adapters.local_agents import LocalAgentsAdapter
from src.chat.service import ChatService

logger = logging.getLogger("mcp2p.app.ui_adapters.chat")


def _fail(code: str, message: str, **extra: Any) -> Dict[str, Any]:
    return {"success": False, "code": code, "message": message, **extra}


class ChatUIAdapter(_BaseUIAdapter):
    def __init__(self, bridge: Any) -> None:
        super().__init__(bridge)
        self._svc: Optional[ChatService] = None

    def _relay(self) -> Any:
        relay_ui = getattr(self._bridge, "relay_ui", None)
        return relay_ui._service() if relay_ui is not None else None

    def _roster(self, project_uuid: str) -> List[Dict[str, Any]]:
        from src.core.domain.roles import ROLE_LABELS_ES, normalize_role

        registry = getattr(self._bridge.extensions_ui, "_get_pane_registry", lambda: None)()
        if registry is None:
            return []
        out: List[Dict[str, Any]] = []
        for pane in registry.list_panes(include_closed=False, project_uuid=project_uuid):
            role = normalize_role(pane.get("role"))
            out.append(
                {
                    "pane_id": pane.get("pane_id"),
                    "node_uuid": pane.get("node_uuid"),
                    "cli_profile": pane.get("cli_profile"),
                    "role": role.value,
                    "role_label": ROLE_LABELS_ES[role],
                }
            )
        return out

    def _service(self) -> ChatService:
        if self._svc is not None:
            return self._svc
        service = ChatService()

        def chat_rows(project_uuid: str, since_rev_ms: int, limit: int) -> List[Dict[str, Any]]:
            relay = self._relay()
            if relay is None:
                raise RuntimeError("El relevo no está disponible.")
            return relay.chat_view(project_uuid, since_rev_ms=since_rev_ms, limit=limit)

        def send(project_uuid: str, pane_id: str, text: str, kind: str) -> Dict[str, Any]:
            return self._bridge.relay_ui.relay_send_as_human(project_uuid, pane_id, text, kind)

        adapter = LocalAgentsAdapter(chat_rows=chat_rows, roster=self._roster, send=send)
        service.register_source(adapter)
        service.register_roster(adapter)
        service.register_transport("agent", adapter)
        self._svc = service
        return service

    # -- puente ------------------------------------------------------------------------------------------------------

    def chat_get_channels(self, project_uuid: Any = None) -> Dict[str, Any]:
        try:
            service = self._service()
            project = str(project_uuid or "")
            return {
                "success": True,
                "code": "OK",
                "channels": [c.to_dict() for c in service.channels()],
                "participants": [p.to_dict() for p in service.participants(project)],
            }
        except Exception as exc:
            logger.warning("chat_get_channels falló: %s", exc)
            return _fail("INTERNAL_ERROR", str(exc))

    def chat_get_feed(self, project_uuid: Any = None, since_ms: Any = 0, limit: Any = 200) -> Dict[str, Any]:
        try:
            out = self._service().feed(str(project_uuid or ""), int(since_ms or 0), int(limit or 200))
            return {"success": True, "code": "OK", **out}
        except Exception as exc:
            logger.warning("chat_get_feed falló: %s", exc)
            return _fail("INTERNAL_ERROR", str(exc))

    def chat_send(self, project_uuid: Any = None, text: Any = None) -> Dict[str, Any]:
        try:
            project = str(project_uuid or "").strip()
            if not project:
                return _fail("INVALID_ARGUMENT", "Elige un proyecto antes de escribir.")
            return self._service().send(project, text)
        except Exception as exc:
            logger.warning("chat_send falló: %s", exc)
            return _fail("INTERNAL_ERROR", str(exc))
