"""
MCP2P JavaScript API Bridge
Enlace asíncrono y seguro entre la interfaz gráfica (PyWebView) y el backend en Python.
Soporte nativo para multi-proyecto con aislamiento estricto de doble repositorio.
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
        self._ensure_local_node()
        self.active_project_uuid: Optional[str] = None
        self._ensure_active_project()

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

    def _ensure_active_project(self) -> None:
        projects = self.db.list_projects()
        if projects:
            self.active_project_uuid = projects[0]["project_uuid"]

    # ==========================
    # Gestión de Proyectos
    # ==========================

    def list_projects(self) -> Dict[str, Any]:
        """Lista todos los proyectos disponibles."""
        try:
            projects = self.db.list_projects()
            return {"success": True, "projects": projects, "active_uuid": self.active_project_uuid}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def create_project(self, name: str, code_repo_url: str = "", gov_repo_url: str = "") -> Dict[str, Any]:
        """Crea un nuevo proyecto con doble repositorio y rama huérfana."""
        try:
            if not name.strip():
                return {"success": False, "error": "El nombre del proyecto es obligatorio"}
            proj = self.db.create_project(name=name.strip(), code_repo_url=code_repo_url, gov_repo_url=gov_repo_url)
            self.active_project_uuid = proj["project_uuid"]
            return {"success": True, "project": proj}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def select_project(self, project_uuid: str) -> Dict[str, Any]:
        """Cambia el contexto activo de la aplicación al proyecto seleccionado."""
        try:
            proj = self.db.get_project_by_uuid(project_uuid)
            if not proj:
                return {"success": False, "error": "Proyecto no encontrado"}
            self.active_project_uuid = project_uuid
            return {"success": True, "project": proj}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_active_project(self) -> Dict[str, Any]:
        """Devuelve el proyecto actualmente activo."""
        if not self.active_project_uuid:
            self._ensure_active_project()
        proj = self.db.get_project_by_uuid(self.active_project_uuid) if self.active_project_uuid else None
        return {"success": True, "project": proj}

    # ==========================
    # Tablero Kanban por Proyecto
    # ==========================

    def get_kanban_board(self, status: Optional[str] = None) -> Dict[str, Any]:
        """Obtiene las tarjetas del proyecto activo."""
        try:
            if not self.active_project_uuid:
                self._ensure_active_project()
            cards = self.db.get_cards(project_uuid=self.active_project_uuid, status=status)
            return {"success": True, "cards": cards, "project_uuid": self.active_project_uuid}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def create_kanban_card(self, card_id: str, title: str, description: str = "") -> Dict[str, Any]:
        """Crea una nueva tarjeta en el proyecto activo."""
        try:
            if not self.active_project_uuid:
                self._ensure_active_project()
            if not card_id.strip() or not title.strip():
                return {"success": False, "error": "El ID y el título son obligatorios"}
            card = self.db.create_card(
                project_uuid=self.active_project_uuid,
                card_id=card_id.strip(),
                title=title.strip(),
                description=description.strip()
            )
            return {"success": True, "card": card}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def claim_task_lease(self, card_id: str, node_uuid: str, duration_minutes: int = 30) -> Dict[str, Any]:
        """Reclama un lease atómico en el proyecto activo."""
        try:
            if not self.active_project_uuid:
                self._ensure_active_project()
            card = self.db.claim_card_lease(
                project_uuid=self.active_project_uuid,
                card_id=card_id,
                node_uuid=node_uuid,
                duration_minutes=duration_minutes
            )
            return {"success": True, "card": card}
        except PermissionError as pe:
            return {"success": False, "error": str(pe), "code": "LEASE_COLLISION"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def update_task_status(self, card_id: str, new_status: str, branch_ref: Optional[str] = None) -> Dict[str, Any]:
        """Actualiza el estado de una tarjeta en el proyecto activo."""
        try:
            if not self.active_project_uuid:
                self._ensure_active_project()
            card = self.db.update_card_status(
                project_uuid=self.active_project_uuid,
                card_id=card_id,
                new_status=new_status,
                branch_ref=branch_ref
            )
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
        """Obtiene los eventos recientes del canal A2A del proyecto activo."""
        try:
            if not self.active_project_uuid:
                self._ensure_active_project()
            messages = self.db.get_recent_a2a_messages(project_uuid=self.active_project_uuid, limit=limit)
            return {"success": True, "messages": messages}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_system_info(self) -> Dict[str, Any]:
        """Información del sistema local y del proyecto activo."""
        active_proj = self.db.get_project_by_uuid(self.active_project_uuid) if self.active_project_uuid else None
        return {
            "success": True,
            "os": platform.system(),
            "os_release": platform.release(),
            "python_version": sys.version.split()[0],
            "db_path": str(self.db.db_path),
            "wal_mode": True,
            "heuristics": "Nielsen 10 + Hicks Law",
            "active_node": os.environ.get("MCP2P_NODE_UUID", f"node_{platform.node().lower()[:12]}"),
            "active_project": active_proj
        }
