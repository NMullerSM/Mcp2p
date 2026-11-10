"""
MCP2P Local Model Context Protocol Server
Servidor MCP local sobre transporte stdio para conectar IDEs y agentes autónomos.
Expone herramientas deterministas a coste 0 de tokens para el enjambre P2P.
Robusto y resiliente ante variaciones de nombres de argumentos (snake_case / camelCase)
y formatos de payloads (JSON strings vs objetos dict/list) enviados por diversos LLMs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from typing import Any, Dict, List, Literal, Optional, Union
from mcp.server.mcpserver import MCPServer
from src.mcp.tools import MCPToolsHandler
from src.storage.db import DatabaseManager


SERVER_VERSION = "0.3.0"


def create_mcp_server(db_path: Optional[str] = None, transport: str = "stdio") -> MCPServer:
    """Crea e inicializa el servidor MCP con todas las herramientas registradas."""
    if db_path:
        os.environ["MCP2P_DB_PATH"] = str(db_path)
        db = DatabaseManager.get_instance(str(db_path))
    else:
        db = DatabaseManager.get_instance()
    handler = MCPToolsHandler(db_manager=db)

    server = MCPServer("mcp2p-server", version=SERVER_VERSION)

    # 1. Herramienta: Listar Proyectos
    @server.tool()
    def list_projects() -> str:
        """Lista todos los proyectos registrados con su UUID y rutas de doble repositorio."""
        return handler.list_projects()

    # 2. Herramienta: Consultar Tablero Kanban
    @server.tool()
    def get_kanban_board(
        project_uuid: Optional[str] = None,
        status: Optional[str] = None,
        projectUuid: Optional[str] = None
    ) -> str:
        """
        Obtiene las tarjetas del tablero Kanban para un proyecto específico a coste 0 de tokens.
        Filtros disponibles: 'all', 'backlog', 'in_progress', 'in_review', 'disputed', 'merged', 'consolidated'.
        """
        resolved_proj = project_uuid or projectUuid or ""
        return handler.get_kanban_board(project_uuid=resolved_proj, status=status)

    # 3. Herramienta: Crear Tarea en Backlog
    @server.tool()
    def create_kanban_task(
        project_uuid: Optional[str] = None,
        card_id: Optional[str] = None,
        title: Optional[str] = None,
        description: str = "",
        projectUuid: Optional[str] = None,
        cardId: Optional[str] = None,
        task_id: Optional[str] = None,
        taskId: Optional[str] = None
    ) -> str:
        """Crea una nueva tarea en el estado 'backlog' del proyecto indicado. card_id es OBLIGATORIO y lo elige el llamador: 1-64 caracteres [A-Za-z0-9_-], empezando por letra o número (ej. 'CARD-PARMA-27'). Si falta o es inválido, la tarea NO se crea."""
        resolved_proj = project_uuid or projectUuid or ""
        resolved_card = card_id or cardId or task_id or taskId or ""
        resolved_title = title or ""
        return handler.create_kanban_task(
            project_uuid=resolved_proj,
            card_id=resolved_card,
            title=resolved_title,
            description=description
        )

    # Herramienta: Editar Tarea Kanban (sin tocar lease ni estado)
    @server.tool()
    def edit_kanban_task(
        project_uuid: Optional[str] = None,
        card_id: Optional[str] = None,
        title: Optional[str] = None,
        description: Optional[str] = None,
        acceptance_criteria: Optional[str] = None,
        assignee_node_uuid: Optional[str] = None,
        projectUuid: Optional[str] = None,
        cardId: Optional[str] = None,
        task_id: Optional[str] = None,
        taskId: Optional[str] = None,
        acceptanceCriteria: Optional[str] = None,
        assigneeNodeUuid: Optional[str] = None
    ) -> str:
        """
        Edita el título, descripción o criterios de aceptación de una tarea existente sin
        alterar su lease ni su estado/columna en el tablero. Opcionalmente reasigna la tarea
        a otro colaborador aprobado con assignee_node_uuid (None no la toca, '' la desasigna;
        se verifica que sea un colaborador real conectado por MCP, nunca un subagente de
        jurado). Sincroniza a SQLite y OKF.
        """
        resolved_proj = project_uuid or projectUuid or ""
        resolved_card = card_id or cardId or task_id or taskId or ""
        resolved_crit = acceptance_criteria if acceptance_criteria is not None else acceptanceCriteria
        resolved_assignee = assignee_node_uuid if assignee_node_uuid is not None else assigneeNodeUuid
        return handler.edit_kanban_task(
            project_uuid=resolved_proj,
            card_id=resolved_card,
            title=title,
            description=description,
            acceptance_criteria=resolved_crit,
            assignee_node_uuid=resolved_assignee
        )

    # 4. Herramienta: Adquirir Tarea con Lease Atómico
    @server.tool()
    def claim_kanban_task(
        project_uuid: Optional[str] = None,
        card_id: Optional[str] = None,
        node_uuid: Optional[str] = None,
        duration_minutes: int = 30,
        projectUuid: Optional[str] = None,
        cardId: Optional[str] = None,
        task_id: Optional[str] = None,
        taskId: Optional[str] = None,
        nodeUuid: Optional[str] = None,
        durationMinutes: Optional[int] = None
    ) -> str:
        """
        Reclama una tarea atómicamente asignando un candado de lease temporal exclusivo.
        Previene colisiones entre agentes concurrentes rechazando reclamos sobre tareas con lease activo.
        """
        resolved_proj = project_uuid or projectUuid or ""
        resolved_card = card_id or cardId or task_id or taskId or ""
        resolved_node = node_uuid or nodeUuid or ""
        resolved_dur = durationMinutes if durationMinutes is not None else duration_minutes
        return handler.claim_kanban_task(
            project_uuid=resolved_proj,
            card_id=resolved_card,
            node_uuid=resolved_node,
            duration_minutes=resolved_dur
        )

    # 5. Herramienta: Liberar o Transicionar Tarea
    @server.tool()
    def release_kanban_task(
        project_uuid: Optional[str] = None,
        card_id: Optional[str] = None,
        node_uuid: Optional[str] = None,
        target_status: Optional[Literal["backlog", "in_progress", "in_review", "disputed", "merged", "rejected", "discarded"]] = "in_review",
        branch_ref: Optional[str] = None,
        dispute_reason: Optional[str] = None,
        contender_node_uuid: Optional[str] = None,
        auto_open_ticket: bool = True,
        projectUuid: Optional[str] = None,
        cardId: Optional[str] = None,
        task_id: Optional[str] = None,
        taskId: Optional[str] = None,
        nodeUuid: Optional[str] = None,
        targetStatus: Optional[Literal["backlog", "in_progress", "in_review", "disputed", "merged", "rejected", "discarded"]] = None,
        branchRef: Optional[str] = None,
        disputeReason: Optional[str] = None,
        contenderNodeUuid: Optional[str] = None,
        autoOpenTicket: Optional[bool] = None
    ) -> str:
        """
        Transiciona una tarea a 'in_review', 'merged' o la libera a 'backlog' adjuntando la rama del código.
        Si se escala a 'disputed', dispara automáticamente la apertura de un ticket en el Tribunal de Honor Borda.
        """
        resolved_proj = project_uuid or projectUuid or ""
        resolved_card = card_id or cardId or task_id or taskId or ""
        resolved_node = node_uuid or nodeUuid or ""
        resolved_status = targetStatus or target_status
        resolved_branch = branchRef or branch_ref
        resolved_reason = disputeReason if disputeReason is not None else dispute_reason
        resolved_contender = contenderNodeUuid if contenderNodeUuid is not None else contender_node_uuid
        resolved_auto = autoOpenTicket if autoOpenTicket is not None else auto_open_ticket
        return handler.release_kanban_task(
            project_uuid=resolved_proj,
            card_id=resolved_card,
            node_uuid=resolved_node,
            target_status=resolved_status,
            branch_ref=resolved_branch,
            dispute_reason=resolved_reason,
            contender_node_uuid=resolved_contender,
            auto_open_ticket=resolved_auto
        )

    # 6. Herramienta: Obtener Contexto del Proyecto
    @server.tool()
    def get_project_context(
        project_uuid: Optional[str] = None,
        projectUuid: Optional[str] = None
    ) -> str:
        """
        Obtiene el contexto arquitectónico y las rutas del doble repositorio (app/ y governance/)
        asociados al proyecto para evitar contaminación cruzada entre repositorios.
        """
        resolved_proj = project_uuid or projectUuid or ""
        return handler.get_project_context(project_uuid=resolved_proj)

    # 7. Herramienta: Registrar Nodo del Agente
    @server.tool()
    def register_agent_node(
        node_uuid: Optional[str] = None,
        developer_name: Optional[str] = None,
        agent_id: Optional[str] = None,
        reviewer_sla_hours: int = 72,
        nodeUuid: Optional[str] = None,
        developerName: Optional[str] = None,
        agentId: Optional[str] = None,
        reviewerSlaHours: Optional[int] = None
    ) -> str:
        """
        Registra el nodo del agente en el enjambre o actualiza su latido (heartbeat)
        con su SLA autodeclarado para revisión de Pull Requests.
        """
        resolved_node = node_uuid or nodeUuid or ""
        resolved_dev = developer_name or developerName or ""
        resolved_agent = agent_id or agentId or ""
        resolved_sla = reviewerSlaHours if reviewerSlaHours is not None else reviewer_sla_hours
        return handler.register_agent_node(
            node_uuid=resolved_node,
            developer_name=resolved_dev,
            agent_id=resolved_agent,
            reviewer_sla_hours=resolved_sla
        )

    # 8. Herramienta: Enviar Turno RFC Dialéctico A2A
    @server.tool()
    def send_coordination_rfc(
        project_uuid: Optional[str] = None,
        sender_node_uuid: Optional[str] = None,
        stage: Optional[str] = "PROPOSAL",
        session_id: Optional[str] = None,
        payload_json: Optional[Union[str, Dict[str, Any]]] = None,
        card_id: Optional[str] = None,
        sessionId: Optional[str] = None,
        projectUuid: Optional[str] = None,
        senderNodeUuid: Optional[str] = None,
        cardId: Optional[str] = None,
        task_id: Optional[str] = None,
        taskId: Optional[str] = None,
        payload: Optional[Union[str, Dict[str, Any]]] = None
    ) -> str:
        """
        Envía un turno dialéctico de coordinación entre agentes (PROPOSAL, COUNTER_PROPOSAL, RESOLUTION).
        Acepta tanto cadenas JSON como diccionarios estructurados, e infiere session_id del payload si es necesario.
        """
        resolved_proj = project_uuid or projectUuid or ""
        resolved_sender = sender_node_uuid or senderNodeUuid or ""
        resolved_stage = stage or "PROPOSAL"
        resolved_card = card_id or cardId or task_id or taskId
        resolved_payload = payload_json if payload_json is not None else (payload if payload is not None else "{}")

        # Inferencia resiliente de session_id
        resolved_session = session_id or sessionId
        if not resolved_session:
            if isinstance(resolved_payload, dict):
                resolved_session = resolved_payload.get("session_id") or resolved_payload.get("sessionId")
            elif isinstance(resolved_payload, str) and resolved_payload.strip():
                try:
                    p_dict = json.loads(resolved_payload)
                    if isinstance(p_dict, dict):
                        resolved_session = p_dict.get("session_id") or p_dict.get("sessionId")
                except Exception:
                    pass

        if not resolved_session:
            resolved_session = f"session_rfc_{resolved_card or uuid.uuid4().hex[:8]}"

        payload_str = json.dumps(resolved_payload, ensure_ascii=False) if isinstance(resolved_payload, (dict, list)) else (resolved_payload or "{}")

        return handler.send_coordination_rfc(
            project_uuid=resolved_proj,
            sender_node_uuid=resolved_sender,
            stage=resolved_stage,
            session_id=resolved_session,
            payload_json=payload_str,
            card_id=resolved_card
        )

    # 9. Herramienta: Consultar Sesión de Coordinación A2A
    @server.tool()
    def get_coordination_session(
        project_uuid: Optional[str] = None,
        session_id: Optional[str] = None,
        projectUuid: Optional[str] = None,
        sessionId: Optional[str] = None
    ) -> str:
        """
        Consulta la secuencia de turnos dialécticos intercambiados en una sesión de coordinación.
        """
        resolved_proj = project_uuid or projectUuid or ""
        resolved_session = session_id or sessionId or ""
        return handler.get_coordination_session(project_uuid=resolved_proj, session_id=resolved_session)

    # 10. Herramienta: Inspeccionar Gobernanza OKF
    @server.tool()
    def inspect_okf_governance(
        project_uuid: Optional[str] = None,
        projectUuid: Optional[str] = None
    ) -> str:
        """
        Inspecciona y valida la integridad de la gobernanza OKF y el estado de la memoria huérfana del proyecto.
        """
        resolved_proj = project_uuid or projectUuid or ""
        return handler.inspect_okf_governance(project_uuid=resolved_proj)

    # 11. Herramienta: Contexto Integral de Tarea (Protocolo de lectura previa)
    @server.tool()
    def get_task_full_context(
        project_uuid: Optional[str] = None,
        card_id: Optional[str] = None,
        projectUuid: Optional[str] = None,
        cardId: Optional[str] = None,
        task_id: Optional[str] = None,
        taskId: Optional[str] = None
    ) -> str:
        """
        Obtiene en una sola llamada la tarjeta YAML, ADRs enlazados, reglas de diseño y worktree asociado.
        """
        resolved_proj = project_uuid or projectUuid or ""
        resolved_card = card_id or cardId or task_id or taskId or ""
        return handler.get_task_full_context(project_uuid=resolved_proj, card_id=resolved_card)

    # 12. Herramienta: Administrar Worktree por Tarea
    @server.tool()
    def manage_task_worktree(
        project_uuid: Optional[str] = None,
        card_id: Optional[str] = None,
        node_uuid: Optional[str] = None,
        action: Optional[str] = "create",
        files_scope_json: Optional[Union[str, List[str], Any]] = None,
        projectUuid: Optional[str] = None,
        cardId: Optional[str] = None,
        task_id: Optional[str] = None,
        taskId: Optional[str] = None,
        nodeUuid: Optional[str] = None,
        files_scope: Optional[Union[str, List[str], Any]] = None,
        filesScope: Optional[Union[str, List[str], Any]] = None,
        filesScopeJson: Optional[Union[str, List[str], Any]] = None
    ) -> str:
        """
        Crea o remueve el worktree aislado de una tarea en app/.worktrees/{card_id} verificando colisiones de scope.
        Acepta lista nativa o string JSON para files_scope.
        """
        resolved_proj = project_uuid or projectUuid or ""
        resolved_card = card_id or cardId or task_id or taskId or ""
        resolved_node = node_uuid or nodeUuid or ""
        resolved_action = action or "create"
        raw_scope = (
            files_scope_json if files_scope_json is not None
            else (files_scope if files_scope is not None
            else (filesScope if filesScope is not None
            else filesScopeJson))
        )
        scope_str = json.dumps(raw_scope, ensure_ascii=False) if isinstance(raw_scope, (dict, list)) else raw_scope
        return handler.manage_task_worktree(
            project_uuid=resolved_proj,
            card_id=resolved_card,
            node_uuid=resolved_node,
            action=resolved_action,
            files_scope_json=scope_str
        )

    # 13. Herramienta: Micro-scripts del OKF
    @server.tool()
    def run_okf_micro_script(
        project_uuid: Optional[str] = None,
        script_action: Optional[str] = None,
        params_json: Optional[Union[str, Dict[str, Any]]] = None,
        projectUuid: Optional[str] = None,
        scriptAction: Optional[str] = None,
        params: Optional[Union[str, Dict[str, Any]]] = None,
        paramsJson: Optional[Union[str, Dict[str, Any]]] = None
    ) -> str:
        """
        Ejecuta micro-scripts sobre la gobernanza OKF (scaffold_decision, promote_decision, update_card, reconcile_kanban).
        Acepta tanto diccionario python como JSON string para params.
        """
        resolved_proj = project_uuid or projectUuid or ""
        resolved_act = script_action or scriptAction or ""
        raw_params = (
            params_json if params_json is not None
            else (params if params is not None
            else paramsJson)
        )
        params_str = json.dumps(raw_params, ensure_ascii=False) if isinstance(raw_params, (dict, list)) else (raw_params or "{}")
        return handler.run_okf_micro_script(
            project_uuid=resolved_proj,
            script_action=resolved_act,
            params_json=params_str
        )

    # 14. Herramienta: Proponer o Aplicar Reparto de Tareas
    @server.tool()
    def propose_or_apply_task_distribution(
        project_uuid: Optional[str] = None,
        sender_node_uuid: Optional[str] = None,
        session_id: Optional[str] = None,
        distribution_plan_json: Optional[Union[str, Dict[str, Any]]] = None,
        apply_immediately: bool = False,
        sessionId: Optional[str] = None,
        projectUuid: Optional[str] = None,
        senderNodeUuid: Optional[str] = None,
        distribution_plan: Optional[Union[str, Dict[str, Any]]] = None,
        distributionPlan: Optional[Union[str, Dict[str, Any]]] = None,
        distributionPlanJson: Optional[Union[str, Dict[str, Any]]] = None,
        applyImmediately: Optional[bool] = None
    ) -> str:
        """
        Permite a los agentes coordinar la distribución de tareas del backlog y reclamar leases sin pisarse.
        Acepta tanto plan en diccionario JSON como string JSON, e infiere session_id del plan si es necesario.
        """
        resolved_proj = project_uuid or projectUuid or ""
        resolved_sender = sender_node_uuid or senderNodeUuid or ""
        raw_plan = (
            distribution_plan_json if distribution_plan_json is not None
            else (distribution_plan if distribution_plan is not None
            else (distributionPlan if distributionPlan is not None
            else distributionPlanJson))
        )
        resolved_apply = applyImmediately if applyImmediately is not None else apply_immediately

        resolved_session = session_id or sessionId
        if not resolved_session:
            if isinstance(raw_plan, dict):
                resolved_session = raw_plan.get("session_id") or raw_plan.get("sessionId")
            elif isinstance(raw_plan, str) and raw_plan.strip():
                try:
                    p_dict = json.loads(raw_plan)
                    if isinstance(p_dict, dict):
                        resolved_session = p_dict.get("session_id") or p_dict.get("sessionId")
                except Exception:
                    pass

        if not resolved_session:
            resolved_session = f"session_dist_{uuid.uuid4().hex[:8]}"

        plan_str = json.dumps(raw_plan, ensure_ascii=False) if isinstance(raw_plan, (dict, list)) else (raw_plan or "{}")

        return handler.propose_or_apply_task_distribution(
            project_uuid=resolved_proj,
            sender_node_uuid=resolved_sender,
            session_id=resolved_session,
            distribution_plan_json=plan_str,
            apply_immediately=resolved_apply
        )

    # 15. Herramienta: Inspeccionar PR y Recomendar al Humano (HITL Guardrail)
    @server.tool()
    def inspect_pr_and_recommend(
        project_uuid: Optional[str] = None,
        pr_id_or_branch: Optional[str] = None,
        reviewer_node_uuid: Optional[str] = None,
        recommendation: Optional[Literal["RECOMMEND_ACCEPT", "RECOMMEND_CHANGES", "RECOMMEND_REJECT", "RECOMMEND_JURY"]] = "RECOMMEND_ACCEPT",
        analysis_summary: str = "",
        suggested_reply: str = "",
        projectUuid: Optional[str] = None,
        prIdOrBranch: Optional[str] = None,
        reviewerNodeUuid: Optional[str] = None,
        analysisSummary: Optional[str] = None,
        suggestedReply: Optional[str] = None
    ) -> str:
        """
        Analiza una Pull Request contra el OKF y deja lista su recomendación en la bandeja del humano sin auto-firmar.
        """
        resolved_proj = project_uuid or projectUuid or ""
        resolved_pr = pr_id_or_branch or prIdOrBranch or ""
        resolved_reviewer = reviewer_node_uuid or reviewerNodeUuid or ""
        resolved_rec = recommendation or "RECOMMEND_ACCEPT"
        resolved_summary = analysisSummary if analysisSummary is not None else analysis_summary
        resolved_reply = suggestedReply if suggestedReply is not None else suggested_reply
        return handler.inspect_pr_and_recommend(
            project_uuid=resolved_proj,
            pr_id_or_branch=resolved_pr,
            reviewer_node_uuid=resolved_reviewer,
            recommendation=resolved_rec,
            analysis_summary=resolved_summary,
            suggested_reply=resolved_reply
        )

    # 16. Herramienta: Abrir Ticket de Juicio Técnico en Disputa (Tribunal de Honor Borda)
    @server.tool()
    def open_dispute_ticket(
        project_uuid: Optional[str] = None,
        title: Optional[str] = None,
        collision_reason: Optional[str] = None,
        contender_a_node: Optional[str] = None,
        contender_b_node: Optional[str] = None,
        proposal_a_ref: Optional[str] = None,
        proposal_b_ref: Optional[str] = None,
        card_id: Optional[str] = None,
        ticket_id: Optional[str] = None,
        proposal_c_ref: str = "",
        num_evaluators: int = 3,
        escalation_recommendation: Optional[str] = None,
        projectUuid: Optional[str] = None,
        collisionReason: Optional[str] = None,
        contenderANode: Optional[str] = None,
        contenderBNode: Optional[str] = None,
        proposalARef: Optional[str] = None,
        proposalBRef: Optional[str] = None,
        cardId: Optional[str] = None,
        ticketId: Optional[str] = None,
        proposalCRef: Optional[str] = None,
        numEvaluators: Optional[int] = None,
        escalationRecommendation: Optional[str] = None
    ) -> str:
        """
        Abre formalmente un ticket de juicio técnico en disputa e inicializa su Matriz Neutral Anti-Sycophancy.
        Permite a los agentes o humanos someter discrepancias de arquitectura/código al Tribunal de Honor.
        Permite sugerir si amerita o no despliegue de jurado (escalation_recommendation).
        """
        resolved_proj = project_uuid or projectUuid or ""
        resolved_title = title or "Disputa Técnica de Arquitectura"
        resolved_reason = collision_reason or collisionReason or ""
        resolved_a = contender_a_node or contenderANode or ""
        resolved_b = contender_b_node or contenderBNode or ""
        resolved_pa = proposal_a_ref or proposalARef or ""
        resolved_pb = proposal_b_ref or proposalBRef or ""
        resolved_card = card_id or cardId
        resolved_ticket = ticket_id or ticketId
        resolved_pc = proposal_c_ref if proposal_c_ref else (proposalCRef or "")
        resolved_num = numEvaluators if numEvaluators is not None else num_evaluators
        resolved_esc = escalation_recommendation if escalation_recommendation is not None else escalationRecommendation
        return handler.open_dispute_ticket(
            project_uuid=resolved_proj,
            title=resolved_title,
            collision_reason=resolved_reason,
            contender_a_node=resolved_a,
            contender_b_node=resolved_b,
            proposal_a_ref=resolved_pa,
            proposal_b_ref=resolved_pb,
            card_id=resolved_card,
            ticket_id=resolved_ticket,
            proposal_c_ref=resolved_pc,
            num_evaluators=resolved_num,
            escalation_recommendation=resolved_esc
        )

    # 17. Herramienta: Emitir Evaluación de Agente en Juicio de Honor (1.5)
    @server.tool()
    def submit_agent_jury_evaluation(
        project_uuid: Optional[str] = None,
        ticket_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        evaluations_json: Optional[Union[str, Dict[str, Any]]] = None,
        rationale: str = "",
        projectUuid: Optional[str] = None,
        ticketId: Optional[str] = None,
        agentId: Optional[str] = None,
        evaluations: Optional[Union[str, Dict[str, Any]]] = None,
        evaluationsJson: Optional[Union[str, Dict[str, Any]]] = None
    ) -> str:
        """
        Emite la evaluación técnica ponderada de un agente en un ticket de juicio de honor (peso 1.5).
        """
        resolved_proj = project_uuid or projectUuid or ""
        resolved_ticket = ticket_id or ticketId or ""
        resolved_agent = agent_id or agentId or ""
        raw_eval = (
            evaluations_json if evaluations_json is not None
            else (evaluations if evaluations is not None
            else (evaluations if evaluations is not None
            else evaluationsJson))
        )
        eval_str = json.dumps(raw_eval, ensure_ascii=False) if isinstance(raw_eval, (dict, list)) else (raw_eval or "{}")
        return handler.submit_agent_jury_evaluation(
            project_uuid=resolved_proj,
            ticket_id=resolved_ticket,
            agent_id=resolved_agent,
            evaluations_json=eval_str,
            rationale=rationale
        )

    # 18. Herramienta: Escanear e Iniciar Bootstrap OKF (Modo 1 Onboarding)
    @server.tool()
    def scan_and_bootstrap_okf(
        project_uuid: Optional[str] = None,
        projectUuid: Optional[str] = None
    ) -> str:
        """
        Escanea exhaustivamente código y documentación existente para extraer todas las ADRs,
        requisitos y convenciones, generando el Dossier Unificado de Alineación OKF.
        """
        resolved_proj = project_uuid or projectUuid or ""
        return handler.scan_and_bootstrap_okf(project_uuid=resolved_proj)

    # 19. Herramienta: Obtener Dossier Unificado de Alineación OKF
    @server.tool()
    def get_alignment_dossier(
        project_uuid: Optional[str] = None,
        projectUuid: Optional[str] = None
    ) -> str:
        """
        Recupera el Dossier Unificado de Alineación para inspección de agentes (Claude Pro) y humanos.
        """
        resolved_proj = project_uuid or projectUuid or ""
        return handler.get_alignment_dossier(project_uuid=resolved_proj)

    # 20. Herramienta: Auditar Deriva OKF (Modo 2 Drift Audit)
    @server.tool()
    def audit_okf_drift(
        project_uuid: Optional[str] = None,
        projectUuid: Optional[str] = None
    ) -> str:
        """
        Compara el código real en app/ contra la gobernanza OKF ratificada y reporta discrepancias.
        """
        resolved_proj = project_uuid or projectUuid or ""
        return handler.audit_okf_drift(project_uuid=resolved_proj)

    # 21. Herramienta: Consultar Ticket de Disputa
    @server.tool()
    def get_dispute_ticket(
        project_uuid: Optional[str] = None,
        ticket_id: Optional[str] = None,
        card_id: Optional[str] = None,
        caller_node_uuid: Optional[str] = None,
        upgrade_if_outdated: bool = False,
        projectUuid: Optional[str] = None,
        ticketId: Optional[str] = None,
        cardId: Optional[str] = None,
        callerNodeUuid: Optional[str] = None,
        upgradeIfOutdated: Optional[bool] = None
    ) -> str:
        """
        Recupera los detalles de un ticket de arbitraje técnico por ticket_id o por card_id,
        incluyendo la matriz neutral anti-sycophancy y las evaluaciones emitidas.
        Aplica protección anti-sesgo: oculta puntuaciones numéricas si el humano no ha votado.
        Si upgrade_if_outdated es True y el ticket está abierto sin votos, actualiza la matriz si era obsoleta.
        """
        resolved_proj = project_uuid or projectUuid or ""
        resolved_ticket = ticket_id or ticketId
        resolved_card = card_id or cardId
        resolved_caller = caller_node_uuid or callerNodeUuid
        resolved_upgrade = upgrade_if_outdated if upgradeIfOutdated is None else upgradeIfOutdated
        return handler.get_dispute_ticket(
            project_uuid=resolved_proj,
            ticket_id=resolved_ticket,
            card_id=resolved_card,
            caller_node_uuid=resolved_caller,
            upgrade_if_outdated=resolved_upgrade
        )

    # 22. Herramienta: Listar Tickets de Disputa
    @server.tool()
    def list_dispute_tickets(
        project_uuid: Optional[str] = None,
        status: Optional[str] = None,
        card_id: Optional[str] = None,
        projectUuid: Optional[str] = None,
        cardId: Optional[str] = None
    ) -> str:
        """
        Lista los tickets de arbitraje técnico de un proyecto, opcionalmente filtrados por status o card_id.
        """
        resolved_proj = project_uuid or projectUuid or ""
        resolved_card = card_id or cardId
        return handler.list_dispute_tickets(
            project_uuid=resolved_proj,
            status=status,
            card_id=resolved_card
        )

    # 23. Herramienta: Consultar Versión del Servidor
    @server.tool()
    def get_server_version() -> str:
        """
        Retorna la versión activa, commit de git y metadatos de runtime del servidor MCP2P.
        Calcula dinámicamente el transporte y el conteo de herramientas registradas en tiempo de ejecución.
        """
        tools_registered = []
        if hasattr(server, "_tool_manager"):
            try:
                tools_registered = [t.name for t in server._tool_manager.list_tools()]
            except Exception:
                pass
        return handler.get_server_version(
            transport=transport,
            tools_count=len(tools_registered) if tools_registered else None,
            tools_list=tools_registered if tools_registered else None
        )

    # 24. Herramienta: Obtener Paquete de Prompt para Subagente Jurado (Clean-Room)
    @server.tool()
    def get_jury_evaluation_prompt(
        project_uuid: Optional[str] = None,
        ticket_id: Optional[str] = None,
        juror_id: Optional[str] = None,
        projectUuid: Optional[str] = None,
        ticketId: Optional[str] = None,
        jurorId: Optional[str] = None
    ) -> str:
        """
        Retorna el paquete de instrucciones en cámara limpia (Clean-Room) para los jurados de un ticket.
        Permite a los agentes (Claude/Antigravity) orquestar subagentes evaluadores con sus propias herramientas.
        """
        resolved_proj = project_uuid or projectUuid or ""
        resolved_ticket = ticket_id or ticketId or ""
        resolved_juror = juror_id or jurorId
        return handler.get_jury_evaluation_prompt(
            project_uuid=resolved_proj,
            ticket_id=resolved_ticket,
            juror_id=resolved_juror
        )

    # 25. Herramienta: Sincronizar Memoria Conversacional en Rama Huérfana
    @server.tool()
    def sync_coordination_memory(
        project_uuid: Optional[str] = None,
        projectUuid: Optional[str] = None
    ) -> str:
        """
        Vuelca eventos dialécticos y transacciones outbox en la rama huérfana de memoria
        ('orphan-conversa-memory-{project_uuid}') sin requerir interacción manual con la UI.
        """
        resolved_proj = project_uuid or projectUuid or ""
        return handler.sync_coordination_memory(project_uuid=resolved_proj)

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="MCP2P: Servidor Local Model Context Protocol")
    parser.add_argument("--db-path", type=str, default=None, help="Ruta a la base de datos SQLite")
    parser.add_argument("--transport", type=str, default="stdio", choices=["stdio", "sse"], help="Tipo de transporte MCP")
    args = parser.parse_args()

    server = create_mcp_server(db_path=args.db_path, transport=args.transport)
    # Ejecutar sobre el transporte especificado
    server.run(transport=args.transport)


if __name__ == "__main__":
    main()
