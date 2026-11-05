"""
MCP2P Tools Implementation
Lógica de negocio de las herramientas expuestas a agentes de IA mediante Model Context Protocol (MCP).
Todas las operaciones son deterministas y se ejecutan a coste 0 de tokens sobre SQLite WAL.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
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

    def _wrap_response(self, project_uuid: Optional[str], result_data: Dict[str, Any]) -> str:
        """
        Envoltura universal de respuestas MCP.
        Agrega notificaciones piggyback (turnos dialécticos entrantes, disputas o tareas pendientes)
        para que los agentes se enteren proactivamente sin necesidad de sondeos manuales.
        """
        unread: List[Dict[str, Any]] = []
        if project_uuid:
            try:
                recent = self.db.get_recent_a2a_messages(project_uuid, limit=5)
                unread = [{"sender": m.get("sender_node_uuid"), "intent": m.get("intent"), "at": m.get("created_at")} for m in recent]
            except Exception:
                unread = []

        result_data["unread_notifications"] = unread
        return json.dumps(result_data, indent=2, ensure_ascii=False)

    def list_projects(self) -> str:
        """Lista todos los proyectos registrados con su UUID y rutas de doble repositorio."""
        projects = self.db.list_projects()
        return self._wrap_response(None, {"success": True, "projects": projects})

    def get_kanban_board(self, project_uuid: str, status: Optional[str] = None) -> str:
        """
        Obtiene las tarjetas del tablero Kanban de un proyecto específico.
        Permite filtrar por estado: 'all', 'backlog', 'in_progress', 'in_review', 'disputed', 'merged', 'consolidated'.
        """
        try:
            cards = self.db.get_cards(project_uuid=project_uuid, status=status)
            return self._wrap_response(project_uuid, {
                "success": True,
                "project_uuid": project_uuid,
                "status_filter": status or "all",
                "count": len(cards),
                "cards": cards
            })
        except Exception as e:
            return self._wrap_response(project_uuid, {"success": False, "error": str(e)})

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

    def edit_kanban_task(
        self,
        project_uuid: str,
        card_id: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        acceptance_criteria: Optional[str] = None,
        assignee_node_uuid: Optional[str] = None
    ) -> str:
        """
        Edita el título, descripción o criterios de aceptación de una tarjeta Kanban existente
        sin alterar su columna/estado en el tablero. Opcionalmente también permite reasignarla
        a otro colaborador (assignee_node_uuid: None no la toca, '' la desasigna, cualquier
        otro valor se verifica contra los colaboradores aprobados reales del proyecto antes
        de escribir — nunca se puede asignar a un subagente efímero del jurado, porque esos
        nunca quedan registrados como colaborador). Sincroniza atómicamente con la base viva
        SQLite y el repositorio de gobernanza OKF.
        """
        try:
            card = self.db.get_card_by_id(project_uuid, card_id)
            if not card:
                return json.dumps({
                    "success": False,
                    "error": f"Tarjeta '{card_id}' no encontrada en el proyecto '{project_uuid}'."
                }, indent=2, ensure_ascii=False)

            updated = self.db.update_card_details(
                project_uuid=project_uuid,
                card_id=card_id,
                title=title,
                description=description,
                acceptance_criteria=acceptance_criteria,
                assignee_node_uuid=assignee_node_uuid
            )
            return json.dumps({
                "success": True,
                "card": updated
            }, indent=2, ensure_ascii=False)
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
        branch_ref: Optional[str] = None,
        dispute_reason: Optional[str] = None,
        contender_node_uuid: Optional[str] = None,
        auto_open_ticket: bool = True
    ) -> str:
        """
        Transiciona una tarea a 'in_review', 'merged' o la libera nuevamente a 'backlog'.
        Permite adjuntar la referencia de rama o commit en el repositorio de código (app/).
        Si se escala a 'disputed', dispara automáticamente la apertura de un ticket de juicio
        técnico en el Tribunal de Honor Borda (salvo que auto_open_ticket=False), vinculando la tarea
        y evitando disputas huérfanas sin arbitraje.
        """
        try:
            valid_statuses = ("backlog", "in_progress", "in_review", "disputed", "merged", "rejected", "discarded")
            if target_status not in valid_statuses:
                return json.dumps({
                    "success": False,
                    "error": f"target_status inválido: '{target_status}'. Valores permitidos: {list(valid_statuses)}"
                }, indent=2, ensure_ascii=False)

            card = self.db.get_card_by_id(project_uuid, card_id)
            if not card:
                return json.dumps({"success": False, "error": f"Tarjeta {card_id} no encontrada"}, indent=2)

            # Validar que quien libera sea el titular del lease o que el lease haya expirado,
            # salvo cuando se escala a 'disputed' (cualquier nodo revisor o auditor puede señalar una disputa).
            if card.get("assignee_node_uuid") and card.get("assignee_node_uuid") != node_uuid:
                if target_status != "disputed":
                    return json.dumps({
                        "success": False,
                        "code": "PERMISSION_DENIED",
                        "error": f"La tarea está asignada al nodo {card.get('assignee_node_uuid')}, no a {node_uuid}. Solo el titular puede transicionar a '{target_status}', salvo para escalar a 'disputed'."
                    }, indent=2)

            updated = self.db.update_card_status(
                project_uuid=project_uuid,
                card_id=card_id,
                new_status=target_status,
                branch_ref=branch_ref
            )

            ticket = None
            if target_status == "disputed" and auto_open_ticket:
                existing = self.db.get_dispute_ticket_by_card(project_uuid, card_id)
                if existing and existing.get("status") == "open":
                    ticket = existing
                else:
                    reason = dispute_reason or f"Tarea {card_id} escalada a 'disputed' por el revisor {node_uuid}"
                    contender_a = card.get("assignee_node_uuid") or "unassigned"
                    contender_b = contender_node_uuid or node_uuid
                    prop_a = branch_ref or card.get("branch_ref") or f"branch_{card_id}"
                    prop_b = f"review_dispute_{card_id}_{node_uuid}"
                    title = f"Disputa en {card_id}: {card.get('title', 'Tarea')}"
                    ticket = self.bus.open_dispute_ticket(
                        project_uuid=project_uuid,
                        title=title,
                        collision_reason=reason,
                        contender_a_node=contender_a,
                        contender_b_node=contender_b,
                        proposal_a_ref=prop_a,
                        proposal_b_ref=prop_b,
                        card_id=card_id
                    )

            resp: Dict[str, Any] = {
                "success": True,
                "message": f"Tarea {card_id} actualizada a estado '{target_status}'" + (f" con ticket de arbitraje {ticket['ticket_id']}" if ticket else ""),
                "card": updated
            }
            if ticket:
                resp["dispute_ticket"] = ticket

            return json.dumps(resp, indent=2, ensure_ascii=False)
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
        payload_json: Any,
        card_id: Optional[str] = None
    ) -> str:
        """
        Envía un turno dentro del protocolo RFC Dialéctico de 3 turnos (PROPOSAL, COUNTER_PROPOSAL, RESOLUTION).
        """
        try:
            if isinstance(payload_json, dict):
                data = payload_json
            elif isinstance(payload_json, str):
                data = json.loads(payload_json) if payload_json.strip() else {}
            else:
                data = {}

            if not session_id:
                session_id = data.get("session_id") or data.get("sessionId") or f"session_rfc_{card_id or uuid.uuid4().hex[:8]}"

            if sender_node_uuid:
                existing_node = self.db.get_node_by_uuid(sender_node_uuid)
                if not existing_node:
                    return json.dumps({
                        "success": False,
                        "error": f"El nodo remitente '{sender_node_uuid}' no está registrado en el enjambre. Regístrelo primero con 'register_agent_node'."
                    }, indent=2, ensure_ascii=False)

            stage_upper = (stage or "PROPOSAL").upper()

            if stage_upper == "PROPOSAL":
                component = data.get("component")
                intent = data.get("intent")
                rationale = data.get("rationale")
                if not component or not intent or not rationale:
                    return json.dumps({
                        "success": False,
                        "error": "Etapa PROPOSAL requiere los campos obligatorios: 'component', 'intent', 'rationale'."
                    }, indent=2, ensure_ascii=False)

                res = self.bus.send_proposal(
                    project_uuid=project_uuid,
                    sender_node_uuid=sender_node_uuid,
                    session_id=session_id,
                    component=component,
                    intent=intent,
                    rationale=rationale,
                    diff_ref=data.get("diff_ref", ""),
                    impact=data.get("impact", ""),
                    card_id=card_id
                )
            elif stage_upper == "COUNTER_PROPOSAL":
                critique = data.get("critique")
                verdict_signal = data.get("verdict_signal")
                if not critique:
                    return json.dumps({
                        "success": False,
                        "error": "Etapa COUNTER_PROPOSAL requiere el campo obligatorio 'critique'."
                    }, indent=2, ensure_ascii=False)
                if not verdict_signal or verdict_signal not in ("ACCEPT", "REJECT", "COMPROMISE"):
                    return json.dumps({
                        "success": False,
                        "error": f"verdict_signal es requerido en COUNTER_PROPOSAL y debe ser uno de: ('ACCEPT', 'REJECT', 'COMPROMISE'). Recibido: '{verdict_signal}'."
                    }, indent=2, ensure_ascii=False)

                res = self.bus.send_counter_proposal(
                    project_uuid=project_uuid,
                    sender_node_uuid=sender_node_uuid,
                    session_id=session_id,
                    critique=critique,
                    alternative=data.get("alternative", {}),
                    verdict_signal=verdict_signal,
                    card_id=card_id
                )
            elif stage_upper == "RESOLUTION":
                final_decision = data.get("final_decision")
                synthesis_notes = data.get("synthesis_notes")
                if not final_decision or final_decision not in ("AGREED", "ESCALATE_TO_JURY"):
                    return json.dumps({
                        "success": False,
                        "error": f"final_decision es requerido en RESOLUTION y debe ser uno de: ('AGREED', 'ESCALATE_TO_JURY'). Recibido: '{final_decision}'."
                    }, indent=2, ensure_ascii=False)
                if not synthesis_notes:
                    return json.dumps({
                        "success": False,
                        "error": "Etapa RESOLUTION requiere el campo obligatorio 'synthesis_notes'."
                    }, indent=2, ensure_ascii=False)

                res = self.bus.send_resolution(
                    project_uuid=project_uuid,
                    sender_node_uuid=sender_node_uuid,
                    session_id=session_id,
                    final_decision=final_decision,
                    synthesis_notes=synthesis_notes,
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

    # ==========================================
    # Nuevas Herramientas MCP Etapa 3: "Las Manos del Agente"
    # ==========================================

    def get_task_full_context(self, project_uuid: str, card_id: str) -> str:
        """
        Protocolo de lectura previa: Devuelve en 1 sola llamada la tarjeta YAML,
        ADRs y decisiones enlazadas, reglas UX, el worktree asociado y el diff de rama.
        """
        try:
            proj = self.db.get_project_by_uuid(project_uuid)
            if not proj:
                return self._wrap_response(project_uuid, {"success": False, "error": "Proyecto no encontrado"})

            card = self.db.get_card_by_id(project_uuid, card_id)
            if not card:
                return self._wrap_response(project_uuid, {"success": False, "error": f"Tarjeta {card_id} no encontrada"})

            gov_path = Path(proj["gov_repo_path"])
            app_path = Path(proj["code_repo_path"])

            # Cargar tarjeta YAML fresca
            yaml_card = {}
            yaml_path = gov_path / "kanban" / "cards" / f"{card_id}.yaml"
            if not yaml_path.exists() and (gov_path / "docs" / "okf" / "kanban" / "cards" / f"{card_id}.yaml").exists():
                yaml_path = gov_path / "docs" / "okf" / "kanban" / "cards" / f"{card_id}.yaml"

            if yaml_path.exists():
                import yaml
                yaml_card = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}

            # Cargar ADRs y Decisiones relacionadas
            related_ids = yaml_card.get("related_ids", [])
            docs_base = gov_path / "docs" / "okf" if (gov_path / "docs" / "okf").exists() else gov_path
            linked_docs = {}
            for rid in related_ids:
                matches = list(docs_base.glob(f"**/{rid}*.md"))
                if matches:
                    linked_docs[rid] = matches[0].read_text(encoding="utf-8", errors="ignore")[:2000]

            # Worktree info
            wt_path = app_path / ".worktrees" / card_id
            has_worktree = wt_path.exists()

            return self._wrap_response(project_uuid, {
                "success": True,
                "card": card,
                "yaml_data": yaml_card,
                "linked_governance_docs": linked_docs,
                "has_worktree": has_worktree,
                "worktree_path": str(wt_path) if has_worktree else None
            })
        except Exception as e:
            return self._wrap_response(project_uuid, {"success": False, "error": str(e)})

    def manage_task_worktree(
        self,
        project_uuid: str,
        card_id: str,
        node_uuid: str,
        action: str,
        files_scope_json: Optional[str] = None
    ) -> str:
        """
        Crea o limpia el worktree aislado de una tarea en app/.worktrees/{card_id} coordinado con la rama huérfana.
        action: 'create' o 'remove'.
        """
        try:
            proj = self.db.get_project_by_uuid(project_uuid)
            if not proj:
                return self._wrap_response(project_uuid, {"success": False, "error": "Proyecto no encontrado"})

            # Resolver scope limpiando cualquier doble-serialización accidental
            scope = files_scope_json
            while isinstance(scope, str):
                s_strip = scope.strip()
                if (s_strip.startswith("[") and s_strip.endswith("]")) or (s_strip.startswith('"') and s_strip.endswith('"')):
                    try:
                        scope = json.loads(s_strip)
                    except Exception:
                        break
                else:
                    break

            if scope is None or scope == "":
                scope = []
            elif isinstance(scope, str):
                scope = [scope]

            if not isinstance(scope, list):
                return self._wrap_response(project_uuid, {
                    "success": False,
                    "error": f"files_scope_json debe resolverse a una lista de rutas de archivos, recibido: {type(scope).__name__}"
                })

            res = self.okf.manage_task_worktree(
                code_repo_path=proj["code_repo_path"],
                gov_base_path=proj["gov_repo_path"],
                project_uuid=project_uuid,
                card_id=card_id,
                node_uuid=node_uuid,
                action=action,
                files_scope=scope
            )
            return self._wrap_response(project_uuid, res)
        except Exception as e:
            return self._wrap_response(project_uuid, {"success": False, "error": str(e)})

    def run_okf_micro_script(
        self,
        project_uuid: str,
        script_action: str,
        params_json: str
    ) -> str:
        """
        Micro-scripts seguros sobre el OKF dentro del Path Jail.
        Acciones:
        - 'scaffold_decision': crea decisión técnica
        - 'promote_decision': promueve a ADR o discarded
        - 'update_card': escribe tarjeta YAML
        - 'reconcile_kanban': sincroniza YAML a SQLite
        """
        try:
            proj = self.db.get_project_by_uuid(project_uuid)
            if not proj:
                return self._wrap_response(project_uuid, {"success": False, "error": "Proyecto no encontrado"})

            gov_path = Path(proj["gov_repo_path"])
            params = json.loads(params_json) if isinstance(params_json, str) else (params_json or {})

            if script_action == "scaffold_decision":
                artifact_type = params.get("artifact_type")
                if not artifact_type and params.get("item_id"):
                    item_id_upper = str(params["item_id"]).upper()
                    if item_id_upper.startswith("DEC"):
                        artifact_type = "decision"
                    elif item_id_upper.startswith("ADR"):
                        artifact_type = "adr"
                    elif item_id_upper.startswith("RF") or item_id_upper.startswith("RNF"):
                        artifact_type = "requirement"
                    elif item_id_upper.startswith("CONV"):
                        artifact_type = "convention"

                valid_types = ("decision", "adr", "requirement", "convention")
                if not artifact_type or artifact_type not in valid_types:
                    return self._wrap_response(project_uuid, {
                        "success": False,
                        "error": f"artifact_type es requerido y debe ser uno de: {list(valid_types)}. Recibido: '{artifact_type}'"
                    })
                if not params.get("item_id") or not params.get("title") or not params.get("content"):
                    return self._wrap_response(project_uuid, {
                        "success": False,
                        "error": "scaffold_decision requiere los campos: 'item_id', 'title', 'content'."
                    })

                p = self.okf.scaffold_okf_artifact(
                    gov_base_path=gov_path,
                    artifact_type=artifact_type,
                    item_id=params["item_id"],
                    title=params["title"],
                    content=params["content"],
                    related_ids=params.get("related_ids")
                )
                return self._wrap_response(project_uuid, {"success": True, "created_path": str(p)})

            elif script_action == "promote_decision":
                if not params.get("decision_id"):
                    return self._wrap_response(project_uuid, {
                        "success": False,
                        "error": "promote_decision requiere el campo obligatorio 'decision_id'."
                    })

                target_state = params.get("target_state")
                valid_states = ("approved", "discarded", "superseded")
                if not target_state or target_state not in valid_states:
                    return self._wrap_response(project_uuid, {
                        "success": False,
                        "error": f"target_state es requerido en promote_decision y debe ser uno de: {list(valid_states)}. Recibido: '{target_state}'"
                    })

                p = self.okf.move_decision_lifecycle(
                    gov_base_path=gov_path,
                    dec_id=params["decision_id"],
                    target_state=target_state,
                    reason_or_consequences=params.get("resolution_notes", ""),
                    signature_block=params.get("signature_block")
                )
                return self._wrap_response(project_uuid, {"success": True, "promoted_path": str(p)})

            elif script_action == "update_card":
                card_data = params.get("card_data")
                if not card_data or not isinstance(card_data, dict):
                    return self._wrap_response(project_uuid, {
                        "success": False,
                        "error": "update_card requiere el campo obligatorio 'card_data' como objeto dict."
                    })
                card_id = card_data.get("card_id") or card_data.get("id")
                if not card_id:
                    return self._wrap_response(project_uuid, {
                        "success": False,
                        "error": "card_data debe contener un identificador 'card_id' o 'id'."
                    })

                existing = self.db.get_card_by_id(project_uuid, card_id)
                if existing:
                    updated = self.db.update_card_details(
                        project_uuid=project_uuid,
                        card_id=card_id,
                        title=card_data.get("title"),
                        description=card_data.get("description"),
                        acceptance_criteria=card_data.get("acceptance_criteria"),
                        status=card_data.get("status"),
                        branch_ref=card_data.get("branch_ref")
                    )
                else:
                    updated = self.db.create_card(
                        project_uuid=project_uuid,
                        card_id=card_id,
                        title=card_data.get("title") or "Sin título",
                        description=card_data.get("description", ""),
                        acceptance_criteria=card_data.get("acceptance_criteria", "")
                    )
                written_path = gov_path / "docs" / "okf" / "kanban" / ("discarded" if updated.get("status") in ("discarded", "rejected") else "cards") / f"{card_id}.yaml"
                if not written_path.exists():
                    written_path = self.okf.write_card_yaml(gov_path, updated)
                return self._wrap_response(project_uuid, {
                    "success": True,
                    "written_path": str(written_path),
                    "card": updated
                })

            elif script_action == "reconcile_kanban":
                rec = self.okf.reconcile_kanban_from_okf(gov_path, project_uuid, self.db)
                return self._wrap_response(project_uuid, {"success": True, "reconciled": rec})

            else:
                return self._wrap_response(project_uuid, {"success": False, "error": f"Acción desconocida: {script_action}"})
        except Exception as e:
            return self._wrap_response(project_uuid, {"success": False, "error": str(e)})

    def propose_or_apply_task_distribution(
        self,
        project_uuid: str,
        sender_node_uuid: str,
        session_id: str,
        distribution_plan_json: Any,
        apply_immediately: bool = False
    ) -> str:
        """
        Permite a los agentes negociar o repartirse las tarjetas del backlog.
        Si apply_immediately es True, adquiere los leases en SQLite/YAML de inmediato.
        """
        try:
            if isinstance(distribution_plan_json, (dict, list)):
                plan = distribution_plan_json
            elif isinstance(distribution_plan_json, str):
                plan = json.loads(distribution_plan_json) if distribution_plan_json.strip() else {}
            else:
                plan = {}

            if not session_id:
                if isinstance(plan, dict):
                    session_id = plan.get("session_id") or plan.get("sessionId")
                if not session_id:
                    session_id = f"session_dist_{uuid.uuid4().hex[:8]}"

            # Publicar en el bus dialéctico
            res = self.bus.post_dialogue_message(
                project_uuid=project_uuid,
                sender_node_uuid=sender_node_uuid,
                session_id=session_id,
                message_type="TASK_DISTRIBUTION",
                reasoning_text="Propuesta de distribución de responsabilidades",
                structured_proposal=plan
            )

            applied_leases = []
            if apply_immediately:
                tasks_map = plan
                if isinstance(plan, dict):
                    if "tasks" in plan and isinstance(plan["tasks"], dict):
                        tasks_map = plan["tasks"]
                    elif "plan" in plan and isinstance(plan["plan"], dict):
                        tasks_map = plan["plan"]

                if isinstance(tasks_map, dict):
                    for card_id, target_node in tasks_map.items():
                        if card_id not in ("session_id", "sessionId", "intent", "rationale") and isinstance(target_node, str):
                            claimed = self.db.claim_card_lease(project_uuid, card_id, target_node, duration_minutes=45)
                            applied_leases.append(claimed)

            return self._wrap_response(project_uuid, {
                "success": True,
                "session_id": session_id,
                "message_event": res,
                "applied_leases": applied_leases
            })
        except Exception as e:
            return self._wrap_response(project_uuid, {"success": False, "error": str(e)})

    def inspect_pr_and_recommend(
        self,
        project_uuid: str,
        pr_id_or_branch: str,
        reviewer_node_uuid: str,
        recommendation: str,
        analysis_summary: str,
        suggested_reply: str
    ) -> str:
        """
        El agente lee la PR contra el OKF y genera una recomendación para el humano:
        ('RECOMMEND_ACCEPT', 'RECOMMEND_CHANGES', 'RECOMMEND_REJECT', 'RECOMMEND_JURY').
        NO aprueba directamente ni accede a la firma Ed25519 privada del desarrollador.
        """
        try:
            valid_recs = ("RECOMMEND_ACCEPT", "RECOMMEND_CHANGES", "RECOMMEND_REJECT", "RECOMMEND_JURY")
            if recommendation not in valid_recs:
                return self._wrap_response(project_uuid, {
                    "success": False,
                    "error": f"Recomendación no válida: '{recommendation}'. Valores permitidos: {list(valid_recs)}"
                })

            # Encolar como mensaje dialéctico / evento HITL para visualización en UI
            event_res = self.bus.post_dialogue_message(
                project_uuid=project_uuid,
                sender_node_uuid=reviewer_node_uuid,
                session_id=f"pr_review_{pr_id_or_branch}",
                message_type="REASONING_REPLY",
                reasoning_text=analysis_summary,
                structured_proposal={
                    "recommendation": recommendation,
                    "target_pr": pr_id_or_branch,
                    "suggested_reply": suggested_reply
                }
            )

            return self._wrap_response(project_uuid, {
                "success": True,
                "recommendation_logged": True,
                "event": event_res
            })
        except Exception as e:
            return self._wrap_response(project_uuid, {"success": False, "error": str(e)})

    def open_dispute_ticket(
        self,
        project_uuid: str,
        title: str,
        collision_reason: str,
        contender_a_node: str,
        contender_b_node: str,
        proposal_a_ref: str,
        proposal_b_ref: str,
        card_id: Optional[str] = None,
        ticket_id: Optional[str] = None,
        proposal_c_ref: str = "",
        num_evaluators: int = 3,
        escalation_recommendation: Optional[str] = None
    ) -> str:
        """
        Abre formalmente un ticket de juicio técnico en disputa e inicializa su Matriz Neutral Anti-Sycophancy.
        Permite a los agentes o humanos escalar discrepancias dialécticas al Tribunal de Honor Borda.
        Permite incluir una 'escalation_recommendation' ('sugiero jurado' o 'no amerita jurado' con motivo).
        """
        try:
            res = self.bus.open_dispute_ticket(
                project_uuid=project_uuid,
                title=title,
                collision_reason=collision_reason,
                contender_a_node=contender_a_node,
                contender_b_node=contender_b_node,
                proposal_a_ref=proposal_a_ref,
                proposal_b_ref=proposal_b_ref,
                card_id=card_id,
                ticket_id=ticket_id,
                proposal_c_ref=proposal_c_ref,
                num_evaluators=num_evaluators,
                escalation_recommendation=escalation_recommendation
            )
            return self._wrap_response(project_uuid, {"success": True, "ticket": res})
        except Exception as e:
            return self._wrap_response(project_uuid, {"success": False, "error": str(e)})

    def get_dispute_ticket(
        self,
        project_uuid: str,
        ticket_id: Optional[str] = None,
        card_id: Optional[str] = None,
        caller_node_uuid: Optional[str] = None,
        upgrade_if_outdated: bool = False
    ) -> str:
        """
        Recupera los detalles de un ticket de arbitraje técnico por ticket_id o por card_id,
        incluyendo la matriz neutral anti-sycophancy y el resumen de votos emitidos.
        Protección Anti-Sesgo Cognitivo (§2.6): Si el ticket está abierto y el nodo consultante
        no ha emitido su voto humano, las puntuaciones numéricas de los agentes permanecen ocultas.
        """
        try:
            ticket = None
            if ticket_id:
                ticket = self.db.get_dispute_ticket(ticket_id)
            elif card_id:
                ticket = self.db.get_dispute_ticket_by_card(project_uuid, card_id)
            else:
                return self._wrap_response(project_uuid, {
                    "success": False,
                    "error": "Debe especificar 'ticket_id' o 'card_id' para consultar la disputa."
                })

            if not ticket:
                return self._wrap_response(project_uuid, {
                    "success": False,
                    "error": f"No se encontró ticket de disputa para ticket_id='{ticket_id}' / card_id='{card_id}'."
                })

            t_id = ticket["ticket_id"]
            votes_summary = self.db.get_jury_votes_summary(t_id)

            # Protección Anti-Sesgo Cognitivo (§2.6)
            is_open = ticket.get("status") == "open"
            has_voted = False
            if caller_node_uuid:
                has_voted = any(hv.get("node_uuid") == caller_node_uuid for hv in votes_summary.get("human_votes", []))

            # Si el ticket está abierto y no ha votado, ocultar puntajes numéricos de subagentes
            redact_scores = is_open and not has_voted
            if redact_scores:
                delivered_summary = {
                    "human_votes": votes_summary.get("human_votes", []),
                    "agent_scores": [],
                    "scores_hidden_until_human_vote": True,
                    "notice": "Las puntuaciones numéricas de los subagentes están ocultas hasta que el humano emita su voto para prevenir sesgo de complacencia (anti-sycophancy)."
                }
            else:
                delivered_summary = {
                    "human_votes": votes_summary.get("human_votes", []),
                    "agent_scores": votes_summary.get("agent_scores", []),
                    "scores_hidden_until_human_vote": False
                }

            # Extraer lista de discusión mayéutica cualitativa (siempre visible para debate constructivo)
            raw_matrix = ticket.get("jury_matrix_json") or "[]"
            try:
                matrix_list = json.loads(raw_matrix) if raw_matrix else []
            except Exception:
                matrix_list = []

            mayeutic_discussions = []
            grouped_agent_scores: Dict[str, List[Dict[str, Any]]] = {}
            for sc in votes_summary.get("agent_scores", []):
                grouped_agent_scores.setdefault(sc.get("juror_id", "subagent_neutral"), []).append(sc)

            for idx, m_spec in enumerate(matrix_list):
                j_id = m_spec.get("juror_id", f"neutral_juror_{idx+1:02d}")
                j_evals = grouped_agent_scores.get(j_id, [])
                rationale_text = ""
                for ev in j_evals:
                    if ev.get("rationale_summary"):
                        rationale_text = ev["rationale_summary"]
                        break

                entry: Dict[str, Any] = {
                    "juror_id": j_id,
                    "role": m_spec.get("role", "NEUTRAL_MATRIX_JUROR"),
                    "temperature": m_spec.get("temperature", 0.2),
                    "focus_vectors": m_spec.get("focus_vectors", []),
                    "mayeutic_argument": rationale_text or "Pendiente de evaluación por el subagente jurado."
                }
                if not redact_scores and j_evals:
                    entry["scores"] = j_evals
                mayeutic_discussions.append(entry)

            # Detección y actualización de matriz desactualizada (Gap #2 Plan Unificado)
            is_outdated_matrix = False
            if matrix_list:
                has_matrix_version = any(m.get("matrix_version") == "2.0.0" for m in matrix_list)
                has_clean_room = all("clean_room_prompt" in m for m in matrix_list)
                if not (has_matrix_version and has_clean_room):
                    is_outdated_matrix = True

            if is_outdated_matrix and upgrade_if_outdated and is_open and not votes_summary.get("human_votes") and not votes_summary.get("agent_scores"):
                all_nodes = self.db.get_swarm_nodes()
                fresh_matrix = self.bus.jury_engine.generate_neutral_jury_matrix(
                    eligible_humans_count=len(all_nodes),
                    ticket_id=t_id,
                    seed=t_id
                )
                matrix_list = fresh_matrix.get("evaluators", [])
                proposal_c = (
                    f"Síntesis Híbrida (Tercera Vía C): Adoptar la arquitectura base de Tesis ({ticket.get('proposal_a_ref', '')[:80]}) "
                    f"incorporando las salvaguardas de rendimiento y aislamiento propuestas en Antítesis ({ticket.get('proposal_b_ref', '')[:80]}), "
                    f"mediante un adaptador desacoplado verificable con contratos de interfaz."
                )
                ticket = self.db.update_dispute_synthesis_and_matrix(
                    ticket_id=t_id,
                    proposal_c_ref=ticket.get("proposal_c_ref") or proposal_c,
                    jury_matrix=matrix_list
                )
                is_outdated_matrix = False
                mayeutic_discussions = []
                for idx, m_spec in enumerate(matrix_list):
                    j_id = m_spec.get("juror_id", f"neutral_juror_{idx+1:02d}")
                    mayeutic_discussions.append({
                        "juror_id": j_id,
                        "role": m_spec.get("role", "NEUTRAL_MATRIX_JUROR"),
                        "temperature": m_spec.get("temperature", 0.2),
                        "focus_vectors": m_spec.get("focus_vectors", []),
                        "mayeutic_argument": "Matriz neutral actualizada con directivas de cámara limpia 2.0.0."
                    })

            res_payload = {
                "success": True,
                "ticket": ticket,
                "mayeutic_discussions": mayeutic_discussions,
                "votes_summary": delivered_summary,
                "is_outdated_matrix": is_outdated_matrix,
                "escalation_recommendation": ticket.get("escalation_recommendation", "")
            }
            if is_outdated_matrix:
                res_payload["matrix_status"] = "outdated"
                res_payload["matrix_warning"] = "Esta matriz de jurado fue generada con una versión previa del motor y carece de las directivas actuales de cámara limpia."

            return self._wrap_response(project_uuid, res_payload)
        except Exception as e:
            return self._wrap_response(project_uuid, {"success": False, "error": str(e)})

    def get_jury_evaluation_prompt(
        self,
        project_uuid: str,
        ticket_id: str,
        juror_id: Optional[str] = None
    ) -> str:
        """
        Retorna el paquete de instrucciones y directivas en cámara limpia (Clean-Room)
        para uno o todos los jurados asignados en la Matriz Neutral de un ticket de disputa.
        Permite a agentes y subagentes emitir evaluaciones mayéuticas independientes
        bajo el foco y temperatura asignados.
        """
        try:
            ticket = self.db.get_dispute_ticket(ticket_id)
            if not ticket:
                return self._wrap_response(project_uuid, {
                    "success": False,
                    "error": f"Ticket de disputa '{ticket_id}' no encontrado"
                })

            raw_matrix = ticket.get("jury_matrix_json") or "[]"
            try:
                matrix_list = json.loads(raw_matrix) if raw_matrix else []
            except Exception:
                matrix_list = []

            if not matrix_list:
                return self._wrap_response(project_uuid, {
                    "success": False,
                    "error": f"El ticket '{ticket_id}' no posee una matriz neutral generada"
                })

            selected_evaluators = matrix_list
            if juror_id:
                selected_evaluators = [m for m in matrix_list if m.get("juror_id") == juror_id]
                if not selected_evaluators:
                    return self._wrap_response(project_uuid, {
                        "success": False,
                        "error": f"Jurado '{juror_id}' no encontrado en la matriz del ticket '{ticket_id}'"
                    })

            evaluation_packages = []
            for spec in selected_evaluators:
                evaluation_packages.append({
                    "juror_id": spec.get("juror_id"),
                    "role": spec.get("role"),
                    "temperature": spec.get("temperature"),
                    "focus_vectors": spec.get("focus_vectors", []),
                    "focus_labels": spec.get("focus_labels", []),
                    "clean_room_system_prompt": spec.get("clean_room_prompt") or spec.get("prompt"),
                    "anonymized_dispute": {
                        "collision_reason": ticket.get("collision_reason"),
                        "thesis_option_a": ticket.get("proposal_a_ref"),
                        "antithesis_option_b": ticket.get("proposal_b_ref"),
                        "synthesis_third_way_c": ticket.get("proposal_c_ref")
                    },
                    "submission_instructions": {
                        "target_tool": "submit_agent_jury_evaluation",
                        "scoring_dimensions": ["kiss", "performance", "security", "ux_hicks", "testability"],
                        "score_range": "0 a 100",
                        "evaluated_options": ["OPTION_A", "OPTION_B", "THIRD_WAY_C"]
                    }
                })

            return self._wrap_response(project_uuid, {
                "success": True,
                "ticket_id": ticket_id,
                "project_uuid": project_uuid,
                "evaluators_count": len(evaluation_packages),
                "evaluation_packages": evaluation_packages
            })
        except Exception as e:
            return self._wrap_response(project_uuid, {"success": False, "error": str(e)})

    def sync_coordination_memory(self, project_uuid: str) -> str:
        """
        Vuelca eventos dialécticos y transacciones del outbox en la rama huérfana de memoria
        ('orphan-conversa-memory-{project_uuid}'), permitiendo a los agentes sincronizar
        la memoria conversacional sin depender de la UI.
        """
        try:
            proj = self.db.get_project_by_uuid(project_uuid)
            if not proj:
                return self._wrap_response(project_uuid, {"success": False, "error": f"Proyecto {project_uuid} no encontrado"})

            gov_repo_path = proj.get("gov_repo_path")
            if not gov_repo_path or not Path(gov_repo_path).exists():
                return self._wrap_response(project_uuid, {"success": False, "error": "Repositorio de gobernanza inexistente o inválido"})

            pending_events = self.db.get_pending_outbox_events(project_uuid, limit=500)
            flushed_ids = []
            events_data = []
            for ev in pending_events:
                flushed_ids.append(ev["event_uuid"])
                events_data.append({
                    "event_uuid": ev["event_uuid"],
                    "event_type": ev["event_type"],
                    "node_uuid": ev["node_uuid"],
                    "created_at": ev.get("created_at"),
                    "payload": ev.get("payload_raw")
                })

            # Guardar snapshot de memoria en rama huérfana
            snapshot_res = self.okf.write_orphan_memory_snapshot(
                gov_base_path=Path(gov_repo_path),
                project_uuid=project_uuid,
                snapshot_data={
                    "last_synced_at": datetime.now(timezone.utc).isoformat(),
                    "total_flushed_events": len(flushed_ids),
                    "events": events_data
                },
                commit_message=f"sync: consolidar memoria de {len(flushed_ids)} eventos dialécticos en rama huérfana"
            )

            # Marcar eventos como sincronizados
            for eid in flushed_ids:
                try:
                    self.db.mark_outbox_event_status(eid, "flushed_to_branch")
                except Exception:
                    pass

            return self._wrap_response(project_uuid, {
                "success": True,
                "project_uuid": project_uuid,
                "flushed_count": len(flushed_ids),
                "orphan_branch": self.okf.get_orphan_branch_name(project_uuid),
                "snapshot": snapshot_res
            })
        except Exception as e:
            return self._wrap_response(project_uuid, {"success": False, "error": str(e)})

    def list_dispute_tickets(
        self,
        project_uuid: str,
        status: Optional[str] = None,
        card_id: Optional[str] = None
    ) -> str:
        """
        Lista los tickets de arbitraje técnico de un proyecto, con filtros opcionales de estado o card_id.
        """
        try:
            tickets = self.db.list_dispute_tickets(project_uuid, status=status, card_id=card_id)
            return self._wrap_response(project_uuid, {
                "success": True,
                "tickets": tickets,
                "count": len(tickets)
            })
        except Exception as e:
            return self._wrap_response(project_uuid, {"success": False, "error": str(e)})

    def submit_agent_jury_evaluation(
        self,
        project_uuid: str,
        ticket_id: str,
        agent_id: str,
        evaluations_json: str,
        rationale: str = ""
    ) -> str:
        """
        Emite la evaluación técnica ponderada (1.5) del agente en un Juicio de Honor.
        evaluations_json: Dict con opciones ('OPTION_A', 'OPTION_B', 'THIRD_WAY_C')
        y sus notas (kiss, performance, security, ux_hicks, testability).
        """
        try:
            scores = json.loads(evaluations_json) if isinstance(evaluations_json, str) else (evaluations_json or {})
            res = self.bus.submit_agent_borda_evaluation(
                ticket_id=ticket_id,
                juror_agent_id=agent_id,
                scores_per_option=scores,
                rationale=rationale
            )
            return self._wrap_response(project_uuid, {"success": True, "evaluations": res})
        except Exception as e:
            return self._wrap_response(project_uuid, {"success": False, "error": str(e)})

    def scan_and_bootstrap_okf(self, project_uuid: str) -> str:
        """
        Analiza de forma exhaustiva el código y documentación del repositorio en app/
        para un proyecto nuevo o importado, extrayendo todas las ADRs, requisitos y convenciones,
        y generando el Dossier Unificado de Alineación OKF para revisión HITL.
        """
        try:
            proj = self.db.get_project_by_uuid(project_uuid)
            if not proj:
                return self._wrap_response(project_uuid, {"success": False, "error": f"Proyecto {project_uuid} no encontrado"})

            code_path = proj.get("code_repo_path")
            if not code_path or not Path(code_path).exists():
                return self._wrap_response(project_uuid, {"success": False, "error": f"Ruta de código inválida o inexistente: {code_path}"})

            from src.storage.code_scanner import CodebaseAnalyzer
            analyzer = CodebaseAnalyzer(code_path)
            analysis = analyzer.analyze_full_repository()
            dossier_md = analyzer.generate_alignment_dossier(proj.get("name", "Proyecto"), project_uuid)

            # Guardar en base de datos local
            self.db.update_project_alignment(
                project_uuid=project_uuid,
                status="pending_bootstrap",
                dossier_markdown=dossier_md
            )

            stack_data = analysis["stack"].__dict__ if hasattr(analysis["stack"], "__dict__") else analysis["stack"]

            return self._wrap_response(project_uuid, {
                "success": True,
                "project_uuid": project_uuid,
                "status": "pending_bootstrap",
                "stack": stack_data,
                "total_adrs_extracted": len(analysis["adrs"]),
                "total_requirements": len(analysis["requirements"]),
                "backlog_cards_suggested": len(analysis["backlog_suggestions"]),
                "dossier_markdown": dossier_md
            })
        except Exception as e:
            return self._wrap_response(project_uuid, {"success": False, "error": str(e)})

    def get_alignment_dossier(self, project_uuid: str) -> str:
        """
        Obtiene el Dossier Unificado de Alineación OKF para un proyecto en estado pending_bootstrap
        o aligned_okf para inspección técnica por Claude Pro u otros agentes.
        """
        try:
            proj = self.db.get_project_by_uuid(project_uuid)
            if not proj:
                return self._wrap_response(project_uuid, {"success": False, "error": f"Proyecto {project_uuid} no encontrado"})

            dossier = proj.get("alignment_dossier_markdown")
            status = proj.get("okf_alignment_status", "aligned_okf")

            if not dossier and proj.get("code_repo_path"):
                from src.storage.code_scanner import CodebaseAnalyzer
                analyzer = CodebaseAnalyzer(proj["code_repo_path"])
                dossier = analyzer.generate_alignment_dossier(proj.get("name", "Proyecto"), project_uuid)
                self.db.update_project_alignment(project_uuid, status, dossier)

            return self._wrap_response(project_uuid, {
                "success": True,
                "project_uuid": project_uuid,
                "alignment_status": status,
                "dossier_markdown": dossier or "No hay dossier disponible."
            })
        except Exception as e:
            return self._wrap_response(project_uuid, {"success": False, "error": str(e)})

    def audit_okf_drift(self, project_uuid: str) -> str:
        """
        Modo 2: Compara el código real en app/ contra la gobernanza OKF ratificada.
        Detecta módulos no mapeados, dependencias no documentadas y desalineaciones de arquitectura.
        """
        try:
            proj = self.db.get_project_by_uuid(project_uuid)
            if not proj:
                return self._wrap_response(project_uuid, {"success": False, "error": f"Proyecto {project_uuid} no encontrado"})

            status = proj.get("okf_alignment_status", "aligned_okf")
            if status == "pending_bootstrap":
                return self._wrap_response(project_uuid, {
                    "success": True,
                    "is_aligned": False,
                    "status": "pending_bootstrap",
                    "message": "El proyecto se encuentra pendiente de alineación inicial (Bootstrap). Requiere firma HITL del Dossier antes de auditar derivas."
                })

            drift_report = self.okf.audit_okf_drift(
                code_repo_path=proj.get("code_repo_path", ""),
                gov_base_path=proj.get("gov_repo_path", "")
            )
            return self._wrap_response(project_uuid, {
                "success": True,
                "project_uuid": project_uuid,
                "drift_report": drift_report
            })
        except Exception as e:
            return self._wrap_response(project_uuid, {"success": False, "error": str(e)})

    def get_server_version(
        self,
        transport: Optional[str] = None,
        tools_count: Optional[int] = None,
        tools_list: Optional[List[str]] = None
    ) -> str:
        """
        Retorna la versión activa, commit de git y metadatos de runtime del servidor MCP2P.
        Calcula dinámicamente el transporte, conteo de herramientas y commit Git en tiempo de ejecución.
        """
        import subprocess
        git_hash = "unknown"
        try:
            repo_root = Path(__file__).resolve().parent.parent.parent
            git_hash = subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=str(repo_root),
                stderr=subprocess.DEVNULL
            ).decode().strip()
        except Exception:
            pass

        resolved_transport = transport or os.environ.get("MCP2P_TRANSPORT", "stdio")

        # Si no se pasó tools_count explícitamente desde el servidor MCP,
        # calcular dinámicamente inspeccionando los métodos públicos de herramientas en MCPToolsHandler
        if tools_count is None:
            handler_tools = [
                m for m in dir(self)
                if not m.startswith("_") and callable(getattr(self, m))
                and m not in ("bus", "db", "okf")
            ]
            count = len(handler_tools)
        else:
            count = tools_count

        resp: Dict[str, Any] = {
            "success": True,
            "server_name": "mcp2p-server",
            "server_version": "0.3.0",
            "git_commit": git_hash,
            "transport": resolved_transport,
            "tools_count": count,
            "status": "ready"
        }
        if tools_list:
            resp["tools"] = tools_list

        return json.dumps(resp, indent=2, ensure_ascii=False)
