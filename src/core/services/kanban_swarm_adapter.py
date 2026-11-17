"""Adaptador de proyección Kanban <-> Swarm (paneles PTY activos y tarjetas asignadas)."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("mcp2p.core.services.kanban_swarm_adapter")

# Datos del panel que NUNCA se proyectan hacia agentes (F0.4): el pid permite leer /proc/<pid>/environ de otro
# panel (EGM-105) y la marca de tecleo es telemetria del humano. El token tampoco sale jamas por aqui. La carpeta
# de trabajo de otro panel tampoco hace falta a un agente.
_PRIVATE_PANE_KEYS = ("pid", "last_human_keystroke_at", "session_token", "cwd")


class KanbanSwarmAdapter:
    """Proyecta el estado de los paneles del enjambre junto con sus tarjetas Kanban.

    Alcance (Ola 2, T2.5): cada consulta ve SOLO los paneles del proyecto pedido. Ver los de todos los proyectos
    exige construir el adaptador con `allow_all_projects=True`, cosa que solo hace el puente de la GUI; las
    herramientas MCP nunca (hay prueba AST).
    """

    def __init__(
        self,
        *,
        kanban_repo: Optional[Any] = None,
        pane_registry: Optional[Any] = None,
        terminal_connector: Optional[Any] = None,
        allow_all_projects: bool = False,
    ) -> None:
        self.kanban_repo = kanban_repo
        self.pane_registry = pane_registry
        self.terminal_connector = terminal_connector
        self.allow_all_projects = bool(allow_all_projects)

    def _get_active_panes(self, project_uuid: Optional[str]) -> List[Dict[str, Any]]:
        """Paneles activos del proyecto exacto `project_uuid` ('' = sin proyecto); None = todos (solo GUI)."""
        if project_uuid is None and not self.allow_all_projects:
            project_uuid = ""
        if self.pane_registry is None:
            return []
        list_fn = getattr(self.pane_registry, "list_panes", None)
        if not callable(list_fn):
            return []
        raw_panes = list_fn() or []
        panes: List[Dict[str, Any]] = []
        for raw in raw_panes:
            if isinstance(raw, dict):
                entry = dict(raw)
            elif hasattr(raw, "to_dict"):
                entry = raw.to_dict(include_token=False)
            else:
                continue
            status = str(entry.get("status") or "active").lower()
            if status == "closed":
                continue
            if project_uuid is not None and str(entry.get("project_uuid") or "") != project_uuid:
                continue
            for private_key in _PRIVATE_PANE_KEYS:
                entry.pop(private_key, None)
            panes.append(entry)
        return panes

    def list_swarm_panes(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        """Lista los paneles activos/pausados DEL PROYECTO con actividad PTY y tarjetas Kanban.

        `project_uuid=None` solo lista todos los proyectos si el adaptador lo permite (GUI); si no, equivale a
        los paneles sin proyecto.
        """
        raw_panes = self._get_active_panes(project_uuid)

        cards: List[Dict[str, Any]] = []
        if project_uuid and self.kanban_repo is not None:
            get_cards_fn = getattr(self.kanban_repo, "get_cards", None)
            if callable(get_cards_fn):
                cards = list(get_cards_fn(project_uuid) or [])

        activity_fn = (
            getattr(self.terminal_connector, "get_pane_activity", None)
            if self.terminal_connector is not None
            else None
        )

        enriched: List[Dict[str, Any]] = []
        for pane in raw_panes:
            item = dict(pane)
            pane_id = item.get("pane_id")
            node_uuid = str(item.get("node_uuid") or "")

            if callable(activity_fn) and pane_id is not None:
                try:
                    activity = activity_fn(pane_id)
                except Exception:
                    logger.debug("Error al consultar get_pane_activity(%s)", pane_id, exc_info=True)
                    activity = None
                if isinstance(activity, dict):
                    state = activity.get("activity_state", activity.get("state", "unknown"))
                    source = activity.get("activity_source", activity.get("source", ""))
                    idle = activity.get("idle")
                    if idle is None:
                        idle = state == "idle"
                    item["activity_state"] = state
                    item["activity_source"] = source
                    item["idle"] = bool(idle)

            if node_uuid and cards:
                item["assigned_cards"] = [
                    dict(c) for c in cards if c.get("assignee_node_uuid") == node_uuid
                ]
                item["claimed_cards"] = [
                    dict(c) for c in cards if c.get("lease_holder_node_uuid") == node_uuid
                ]
            else:
                item["assigned_cards"] = []
                item["claimed_cards"] = []

            enriched.append(item)

        return {
            "success": True,
            "project_uuid": project_uuid,
            "count": len(enriched),
            "panes": enriched,
        }

    def resolve_card_target(self, project_uuid: str, card_id: str) -> Optional[Dict[str, Any]]:
        """Resuelve qué panel activo (`pane_id`, `node_uuid`) corresponde a una tarjeta."""
        if self.kanban_repo is None or not project_uuid or not card_id:
            return None
        get_card_fn = getattr(self.kanban_repo, "get_card_by_id", None)
        if not callable(get_card_fn):
            return None
        card = get_card_fn(project_uuid, card_id)
        if not isinstance(card, dict):
            return None

        panes = self._get_active_panes(project_uuid)
        if not panes:
            return None

        by_node: Dict[str, Dict[str, Any]] = {}
        for pane in panes:
            n_uuid = str(pane.get("node_uuid") or "").strip()
            if n_uuid and n_uuid not in by_node:
                by_node[n_uuid] = pane

        for field_name in ("lease_holder_node_uuid", "assignee_node_uuid"):
            node_uuid = str(card.get(field_name) or "").strip()
            if node_uuid and node_uuid in by_node:
                pane = by_node[node_uuid]
                return {
                    "pane_id": pane.get("pane_id"),
                    "node_uuid": node_uuid,
                    "card_id": card_id,
                    "matched_by": field_name,
                    "status": pane.get("status", "active"),
                }

        return None
