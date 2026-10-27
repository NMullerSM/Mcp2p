"""
MCP2P JavaScript API Bridge
Enlace asíncrono y seguro entre la interfaz gráfica (PyWebView) y el backend en Python.
Soporte nativo para multi-proyecto con aislamiento estricto de doble repositorio.
"""

from __future__ import annotations

import base64
import json
import os
import platform
import sys
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from src.storage.db import DatabaseManager
from src.a2a.bus import A2ABus, A2AIntent
from src.storage.okf_adapter import OKFAdapter


class ApiBridge:
    """Clase puente cuyos métodos son expuestos en window.pywebview.api en el frontend."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None) -> None:
        self.db = db_manager or DatabaseManager.get_instance()
        self.bus = A2ABus(db=self.db)
        self.okf = OKFAdapter()
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

    def delete_project(self, project_uuid: str) -> Dict[str, Any]:
        """Elimina un proyecto y todas sus tareas/mensajes asociados en cascada."""
        try:
            success = self.db.delete_project(project_uuid)
            if self.active_project_uuid == project_uuid:
                self.active_project_uuid = None
                self._ensure_active_project()
            return {"success": success}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def generate_p2p_invite(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        """Genera un enlace y token de invitación P2P para compartir el proyecto con otro colaborador."""
        try:
            target_uuid = project_uuid or self.active_project_uuid
            if not target_uuid:
                self._ensure_active_project()
                target_uuid = self.active_project_uuid

            proj = self.db.get_project_by_uuid(target_uuid)
            if not proj:
                return {"success": False, "error": "Proyecto no encontrado"}

            local_node = os.environ.get("MCP2P_NODE_UUID", f"node_{platform.node().lower()[:12]}")
            payload = {
                "project_uuid": proj["project_uuid"],
                "name": proj["name"],
                "code_repo_url": proj.get("code_repo_url", ""),
                "gov_repo_url": proj.get("gov_repo_url", ""),
                "sender_node_uuid": local_node,
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            json_bytes = json.dumps(payload).encode("utf-8")
            token = base64.urlsafe_b64encode(json_bytes).decode("ascii")
            invite_url = f"mcp2p://join?token={token}"

            return {
                "success": True,
                "project_uuid": proj["project_uuid"],
                "project_name": proj["name"],
                "token": token,
                "invite_url": invite_url,
                "payload": payload
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def join_p2p_project(self, invite_str: str) -> Dict[str, Any]:
        """Se une a un proyecto P2P a partir de un enlace o token de invitación."""
        try:
            raw = invite_str.strip()
            token = raw
            if "token=" in raw:
                parsed = urllib.parse.urlparse(raw)
                query = urllib.parse.parse_qs(parsed.query)
                token = query.get("token", [""])[0]

            if not token:
                return {"success": False, "error": "Token de invitación P2P no válido"}

            # Decodificar payload base64
            padded = token + "=" * (-len(token) % 4)
            data_bytes = base64.urlsafe_b64decode(padded)
            payload = json.loads(data_bytes.decode("utf-8"))

            p_uuid = payload.get("project_uuid")
            p_name = payload.get("name")
            if not p_uuid or not p_name:
                return {"success": False, "error": "El token no contiene los datos del proyecto requeridos"}

            # Verificar si ya existe localmente
            existing = self.db.get_project_by_uuid(p_uuid)
            if existing:
                self.active_project_uuid = p_uuid
                return {"success": True, "project": existing, "message": "Proyecto ya registrado. Se activó el contexto."}

            # Crear proyecto con el UUID compartido
            proj = self.db.create_project(
                name=p_name,
                code_repo_url=payload.get("code_repo_url", ""),
                gov_repo_url=payload.get("gov_repo_url", ""),
                project_uuid=p_uuid
            )
            self.active_project_uuid = p_uuid

            # Si viene un nodo emisor, registrarlo
            sender_node = payload.get("sender_node_uuid")
            if sender_node:
                self.db.register_or_update_node(
                    node_uuid=sender_node,
                    developer_name=f"Peer {sender_node[:8]}",
                    agent_id=f"agent_{sender_node[:8]}",
                    reviewer_sla_hours=72,
                    status="online"
                )

            return {"success": True, "project": proj, "message": "Conectado al proyecto P2P exitosamente"}
        except Exception as e:
            return {"success": False, "error": f"Error al unirse al proyecto P2P: {str(e)}"}

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

    def send_a2a_signal(self, signal_type: str, data: Dict[str, Any], card_id: Optional[str] = None) -> Dict[str, Any]:
        """Envía una señal operativa a 0 tokens por el canal A2A."""
        try:
            if not self.active_project_uuid:
                self._ensure_active_project()
            local_node = os.environ.get("MCP2P_NODE_UUID", f"node_{platform.node().lower()[:12]}")
            packet = self.bus.send_informative(
                project_uuid=self.active_project_uuid,
                sender_node_uuid=local_node,
                signal_type=signal_type,
                data=data,
                card_id=card_id
            )
            return {"success": True, "packet": packet}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_a2a_session_turns(self, session_id: str) -> Dict[str, Any]:
        """Consulta los turnos del RFC dialéctico de una sesión."""
        try:
            if not self.active_project_uuid:
                self._ensure_active_project()
            turns = self.bus.get_session_turns(project_uuid=self.active_project_uuid, session_id=session_id)
            return {"success": True, "turns": turns}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def validate_project_okf(self) -> Dict[str, Any]:
        """Valida la integridad de la documentación OKF del proyecto activo."""
        try:
            if not self.active_project_uuid:
                self._ensure_active_project()
            proj = self.db.get_project_by_uuid(self.active_project_uuid)
            if not proj:
                return {"success": False, "error": "Proyecto activo no encontrado"}
            gov_path = proj.get("gov_repo_path")
            if not gov_path:
                return {"success": False, "error": "Ruta de gobernanza no configurada"}
            report = self.okf.validate_okf_integrity(gov_path)
            return {"success": True, "report": report}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def write_memory_snapshot(self, snapshot_data: Dict[str, Any]) -> Dict[str, Any]:
        """Escribe un snapshot en la rama huérfana de memoria del proyecto activo."""
        try:
            if not self.active_project_uuid:
                self._ensure_active_project()
            proj = self.db.get_project_by_uuid(self.active_project_uuid)
            if not proj:
                return {"success": False, "error": "Proyecto activo no encontrado"}
            gov_path = proj.get("gov_repo_path")
            res = self.okf.write_orphan_memory_snapshot(
                gov_base_path=gov_path,
                project_uuid=self.active_project_uuid,
                snapshot_data=snapshot_data
            )
            return {"success": True, "result": res}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def read_memory_snapshot(self) -> Dict[str, Any]:
        """Lee el último snapshot de la rama huérfana del proyecto activo."""
        try:
            if not self.active_project_uuid:
                self._ensure_active_project()
            proj = self.db.get_project_by_uuid(self.active_project_uuid)
            if not proj:
                return {"success": False, "error": "Proyecto activo no encontrado"}
            gov_path = proj.get("gov_repo_path")
            mem = self.okf.read_orphan_memory_snapshot(
                gov_base_path=gov_path,
                project_uuid=self.active_project_uuid
            )
            return {"success": True, "snapshot": mem}
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
