"""
MCP2P JavaScript API Bridge
Enlace asíncrono y seguro entre la interfaz gráfica (PyWebView) y el backend en Python.
Expone funciones invocables desde el frontend mediante `window.pywebview.api.*`.
"""

from __future__ import annotations

import os
import platform
import sys
from typing import Any, Dict, List, Optional
from src.storage.db import DatabaseManager


class ApiBridge:
    """Clase puente cuyos métodos son expuestos en window.pywebview.api en el frontend."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None) -> None:
        self.db = db_manager or DatabaseManager.get_instance()
        # Registrar nodo local por defecto si no existe
        self._ensure_local_node()

    def _ensure_local_node(self) -> None:
        default_node = os.environ.get("MCP2P_NODE_UUID", f"node_{platform.node().lower()[:12]}")
        default_user = os.environ.get("USER", os.environ.get("USERNAME", "developer"))
        self.db.register_or_update_node(
            node_uuid=default_node,
            developer_name=default_user,
            agent_id=f"agent_{default_user}",
            reviewer_sla_hours=72,
            status="online"
        )

    # ==========================
    # Tablero Kanban
    # ==========================

    def get_kanban_board(self, status: Optional[str] = None) -> Dict[str, Any]:
        """Obtiene las tarjetas agrupadas por columnas o filtradas."""
        try:
            cards = self.db.get_cards(status=status)
            return {"success": True, "cards": cards}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def create_kanban_card(self, card_id: str, title: str, description: str = "") -> Dict[str, Any]:
        """Crea una nueva tarjeta en el backlog a costo 0 de tokens."""
        try:
            if not card_id.strip() or not title.strip():
                return {"success": False, "error": "El ID y el título son obligatorios"}
            card = self.db.create_card(card_id.strip(), title.strip(), description.strip())
            return {"success": True, "card": card}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def claim_task_lease(self, card_id: str, node_uuid: str, duration_minutes: int = 30) -> Dict[str, Any]:
        """Reclama un lease atómico para una tarea."""
        try:
            card = self.db.claim_card_lease(card_id, node_uuid, duration_minutes=duration_minutes)
            return {"success": True, "card": card}
        except PermissionError as pe:
            return {"success": False, "error": str(pe), "code": "LEASE_COLLISION"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def update_task_status(self, card_id: str, new_status: str, branch_ref: Optional[str] = None) -> Dict[str, Any]:
        """Actualiza el estado de una tarea (in_review, merged, etc.)."""
        try:
            card = self.db.update_card_status(card_id, new_status, branch_ref)
            return {"success": True, "card": card}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ==========================
    # Enjambre y Telemetría
    # ==========================

    def get_swarm_nodes(self) -> Dict[str, Any]:
        """Obtiene los nodos registrados del enjambre P2P."""
        try:
            nodes = self.db.get_swarm_nodes()
            return {"success": True, "nodes": nodes}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_a2a_feed(self, limit: int = 30) -> Dict[str, Any]:
        """Obtiene los eventos recientes del canal A2A."""
        try:
            messages = self.db.get_recent_a2a_messages(limit=limit)
            return {"success": True, "messages": messages}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_system_info(self) -> Dict[str, Any]:
        """Información del sistema local para la barra de estado inferior."""
        return {
            "success": True,
            "os": platform.system(),
            "os_release": platform.release(),
            "python_version": sys.version.split()[0],
            "db_path": str(self.db.db_path),
            "wal_mode": True,
            "heuristics": "Nielsen 10 + Hicks Law",
            "active_node": os.environ.get("MCP2P_NODE_UUID", f"node_{platform.node().lower()[:12]}")
        }
