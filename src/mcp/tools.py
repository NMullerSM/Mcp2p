"""
MCP2P Tools Implementation
Lógica de negocio de las herramientas expuestas a agentes de IA mediante Model Context Protocol (MCP).
Todas las operaciones son deterministas y se ejecutan a coste 0 de tokens sobre SQLite WAL.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from src.storage.db import DatabaseManager
from src.a2a.bus import A2ABus, A2AIntent
from src.storage.okf_adapter import OKFAdapter


class MCPToolsHandler:
    """Manejador de herramientas MCP desacoplado del transporte para facilitar testing e introspección."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None) -> None:
        self.db = db_manager or DatabaseManager.get_instance()
        self.bus = A2ABus(db=self.db)
        self.okf = OKFAdapter()

    def list_projects(self) -> str:
        """Lista todos los proyectos registrados con su UUID y rutas de doble repositorio."""
        projects = self.db.list_projects()
        return json.dumps({"success": True, "projects": projects}, indent=2, ensure_ascii=False)

    def get_kanban_board(self, project_uuid: str, status: Optional[str] = None) -> str:
        """
        Obtiene las tarjetas del tablero Kanban de un proyecto específico.
        Permite filtrar por estado: 'all', 'backlog', 'in_progress', 'in_review', 'disputed', 'merged', 'consolidated'.
        """
        try:
            cards = self.db.get_cards(project_uuid=project_uuid, status=status)
            return json.dumps({
                "success": True,
                "project_uuid": project_uuid,
                "status_filter": status or "all",
                "count": len(cards),
                "cards": cards
            }, indent=2, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, indent=2)

    def create_kanban_task(
        self,
        project_uuid: str,
        card_id: str,
        title: str,
        description: str = ""
    ) -> str:
        """
        Crea una nueva tarea en el estado 'backlog' dentro del proyecto indicado.
        No consume tokens de inferencia.
        """
        try:
            card = self.db.create_card(
                project_uuid=project_uuid,
                card_id=card_id,
                title=title,
                description=description
            )
            return json.dumps({"success": True, "card": card}, indent=2, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, indent=2)

    def claim_kanban_task(
        self,
        project_uuid: str,
        card_id: str,
        node_uuid: str,
        duration_minutes: int = 30
    ) -> str:
        """
        Adquiere una tarea atómicamente asignando un lease exclusivo con expiración temporal.
        Si otro agente mantiene un lease activo no expirado, la solicitud es rechazada para prevenir colisiones.
        """
        try:
            card = self.db.claim_card_lease(
                project_uuid=project_uuid,
                card_id=card_id,
                node_uuid=node_uuid,
                duration_minutes=duration_minutes
            )
            return json.dumps({
                "success": True,
                "message": f"Tarea {card_id} adquirida exitosamente por {node_uuid}",
                "lease_expires_at": card.get("lease_expires_at"),
                "card": card
            }, indent=2, ensure_ascii=False)
        except PermissionError as pe:
            return json.dumps({
                "success": False,
                "code": "LEASE_COLLISION",
                "error": str(pe)
            }, indent=2)
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, indent=2)

    def release_kanban_task(
        self,
        project_uuid: str,
        card_id: str,
        node_uuid: str,
        target_status: str = "in_review",
        branch_ref: Optional[str] = None
    ) -> str:
        """
        Transiciona una tarea a 'in_review', 'merged' o la libera nuevamente a 'backlog'.
        Permite adjuntar la referencia de rama o commit en el repositorio de código (app/).
        """
        try:
            card = self.db.get_card_by_id(project_uuid, card_id)
            if not card:
                return json.dumps({"success": False, "error": f"Tarjeta {card_id} no encontrada"}, indent=2)

            # Validar que quien libera sea el titular del lease o que el lease haya expirado
            if card.get("assignee_node_uuid") and card.get("assignee_node_uuid") != node_uuid:
                return json.dumps({
                    "success": False,
                    "code": "PERMISSION_DENIED",
                    "error": f"La tarea está asignada al nodo {card.get('assignee_node_uuid')}, no a {node_uuid}"
                }, indent=2)

            updated = self.db.update_card_status(
                project_uuid=project_uuid,
                card_id=card_id,
                new_status=target_status,
                branch_ref=branch_ref
            )
            return json.dumps({
                "success": True,
                "message": f"Tarea {card_id} actualizada a estado '{target_status}'",
                "card": updated
            }, indent=2, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, indent=2)

    def get_project_context(self, project_uuid: str) -> str:
        """
        Obtiene el contexto arquitectónico y las rutas físicas de los dos repositorios
        asociados al proyecto (app/ para código y governance/ para OKF y rama huérfana).
        """
        try:
            proj = self.db.get_project_by_uuid(project_uuid)
            if not proj:
                return json.dumps({"success": False, "error": f"Proyecto {project_uuid} no encontrado"}, indent=2)

            cards = self.db.get_cards(project_uuid=project_uuid)
            active_leases = [c for c in cards if c.get("status") == "in_progress"]

            return json.dumps({
                "success": True,
                "project": proj,
                "orphan_branch": f"orphan-conversa-memory-{project_uuid}",
                "active_leases_count": len(active_leases),
                "total_tasks_count": len(cards)
            }, indent=2, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, indent=2)

    def register_agent_node(
        self,
        node_uuid: str,
        developer_name: str,
        agent_id: str,
        reviewer_sla_hours: int = 72
    ) -> str:
        """
        Registra el nodo del agente en el enjambre o actualiza su latido (heartbeat).
        Establece el compromiso de tiempo (SLA) para revisión de PRs.
        """
        try:
            node = self.db.register_or_update_node(
                node_uuid=node_uuid,
                developer_name=developer_name,
                agent_id=agent_id,
                reviewer_sla_hours=reviewer_sla_hours,
                status="online"
            )
            return json.dumps({
                "success": True,
                "message": f"Nodo {node_uuid} registrado con éxito en el enjambre P2P",
                "node": node
            }, indent=2, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, indent=2)

    # ==========================================
    # Herramientas de Coordinación A2A y OKF
    # ==========================================

    def send_coordination_rfc(
        self,
        project_uuid: str,
        sender_node_uuid: str,
        stage: str,
        session_id: str,
        payload_json: str,
        card_id: Optional[str] = None
    ) -> str:
        """
        Envía un turno dentro del protocolo RFC Dialéctico de 3 turnos (PROPOSAL, COUNTER_PROPOSAL, RESOLUTION).
        """
        try:
            data = json.loads(payload_json)
            stage_upper = stage.upper()

            if stage_upper == "PROPOSAL":
                res = self.bus.send_proposal(
                    project_uuid=project_uuid,
                    sender_node_uuid=sender_node_uuid,
                    session_id=session_id,
                    component=data.get("component", "system"),
                    intent=data.get("intent", ""),
                    rationale=data.get("rationale", ""),
                    diff_ref=data.get("diff_ref", ""),
                    impact=data.get("impact", ""),
                    card_id=card_id
                )
            elif stage_upper == "COUNTER_PROPOSAL":
                res = self.bus.send_counter_proposal(
                    project_uuid=project_uuid,
                    sender_node_uuid=sender_node_uuid,
                    session_id=session_id,
                    critique=data.get("critique", ""),
                    alternative=data.get("alternative", {}),
                    verdict_signal=data.get("verdict_signal", "COMPROMISE"),
                    card_id=card_id
                )
            elif stage_upper == "RESOLUTION":
                res = self.bus.send_resolution(
                    project_uuid=project_uuid,
                    sender_node_uuid=sender_node_uuid,
                    session_id=session_id,
                    final_decision=data.get("final_decision", "AGREED"),
                    synthesis_notes=data.get("synthesis_notes", ""),
                    agreed_branch=data.get("agreed_branch"),
                    card_id=card_id
                )
            else:
                return json.dumps({
                    "success": False,
                    "error": f"Etapa RFC inválida: {stage}. Use PROPOSAL, COUNTER_PROPOSAL o RESOLUTION."
                }, indent=2)

            return json.dumps({"success": True, "event": res}, indent=2, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, indent=2)

    def get_coordination_session(self, project_uuid: str, session_id: str) -> str:
        """
        Consulta la secuencia completa de turnos intercambiados en una sesión dialéctica.
        """
        try:
            turns = self.bus.get_session_turns(project_uuid=project_uuid, session_id=session_id)
            return json.dumps({
                "success": True,
                "project_uuid": project_uuid,
                "session_id": session_id,
                "turns_count": len(turns),
                "turns": turns
            }, indent=2, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, indent=2)

    def inspect_okf_governance(self, project_uuid: str) -> str:
        """
        Inspecciona y valida la integridad de la gobernanza OKF para el repositorio de un proyecto.
        """
        try:
            proj = self.db.get_project_by_uuid(project_uuid)
            if not proj:
                return json.dumps({"success": False, "error": f"Proyecto {project_uuid} no encontrado"}, indent=2)

            gov_path = proj.get("gov_repo_path")
            if not gov_path:
                return json.dumps({"success": False, "error": "Ruta de gobernanza no configurada"}, indent=2)

            report = self.okf.validate_okf_integrity(gov_path)
            orphan_mem = self.okf.read_orphan_memory_snapshot(gov_path, project_uuid)

            return json.dumps({
                "success": True,
                "project_uuid": project_uuid,
                "gov_repo_path": gov_path,
                "validation": report,
                "orphan_memory_active": orphan_mem is not None,
                "orphan_memory_snapshot": orphan_mem
            }, indent=2, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, indent=2)
