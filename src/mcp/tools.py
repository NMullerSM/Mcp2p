"""
MCP2P Tools Implementation
Lógica de negocio de las herramientas expuestas a agentes de IA mediante Model Context Protocol (MCP).
Todas las operaciones son deterministas y se ejecutan a coste 0 de tokens sobre SQLite WAL.
"""

from __future__ import annotations

import json
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

            scope = json.loads(files_scope_json) if files_scope_json else []
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
            params = json.loads(params_json) if isinstance(params_json, str) else params_json

            if script_action == "scaffold_decision":
                p = self.okf.scaffold_okf_artifact(
                    gov_base_path=gov_path,
                    artifact_type=params.get("artifact_type", "decision"),
                    item_id=params["item_id"],
                    title=params["title"],
                    content=params["content"],
                    related_ids=params.get("related_ids")
                )
                return self._wrap_response(project_uuid, {"success": True, "created_path": str(p)})

            elif script_action == "promote_decision":
                p = self.okf.move_decision_lifecycle(
                    gov_base_path=gov_path,
                    dec_id=params["decision_id"],
                    target_state=params.get("target_state", "approved"),
                    reason_or_consequences=params.get("resolution_notes", ""),
                    signature_block=params.get("signature_block")
                )
                return self._wrap_response(project_uuid, {"success": True, "promoted_path": str(p)})

            elif script_action == "update_card":
                p = self.okf.write_card_yaml(gov_path, params["card_data"])
                return self._wrap_response(project_uuid, {"success": True, "written_path": str(p)})

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
        distribution_plan_json: str,
        apply_immediately: bool = False
    ) -> str:
        """
        Permite a los agentes negociar o repartirse las tarjetas del backlog.
        Si apply_immediately es True, adquiere los leases en SQLite/YAML de inmediato.
        """
        try:
            plan = json.loads(distribution_plan_json)
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
                # plan = {"TASK-01": "node_alpha", "TASK-02": "node_beta"}
                for card_id, target_node in plan.items():
                    if isinstance(target_node, str):
                        claimed = self.db.claim_card_lease(project_uuid, card_id, target_node, duration_minutes=45)
                        applied_leases.append(claimed)

            return self._wrap_response(project_uuid, {
                "success": True,
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
        ('RECOMMEND_ACCEPT', 'RECOMMEND_CHANGES', 'RECOMMEND_JURY').
        NO aprueba directamente ni accede a la firma Ed25519 privada del desarrollador.
        """
        try:
            valid_recs = ("RECOMMEND_ACCEPT", "RECOMMEND_CHANGES", "RECOMMEND_JURY")
            if recommendation not in valid_recs:
                return self._wrap_response(project_uuid, {"success": False, "error": f"Recomendación no válida: {recommendation}"})

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
            scores = json.loads(evaluations_json)
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
