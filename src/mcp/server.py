"""
MCP2P Local Model Context Protocol Server
Servidor MCP local sobre transporte stdio para conectar IDEs y agentes autónomos.
Expone herramientas deterministas a coste 0 de tokens para el enjambre P2P.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional
from mcp.server.mcpserver import MCPServer
from src.mcp.tools import MCPToolsHandler
from src.storage.db import DatabaseManager


def create_mcp_server(db_path: Optional[str] = None) -> MCPServer:
    """Crea e inicializa el servidor MCP con todas las herramientas registradas."""
    if db_path:
        os.environ["MCP2P_DB_PATH"] = db_path

    db = DatabaseManager.get_instance()
    handler = MCPToolsHandler(db_manager=db)

    server = MCPServer("mcp2p-server")

    # 1. Herramienta: Listar Proyectos
    @server.tool()
    def list_projects() -> str:
        """Lista todos los proyectos registrados con su UUID y rutas de doble repositorio."""
        return handler.list_projects()

    # 2. Herramienta: Consultar Tablero Kanban
    @server.tool()
    def get_kanban_board(project_uuid: str, status: Optional[str] = None) -> str:
        """
        Obtiene las tarjetas del tablero Kanban para un proyecto específico a coste 0 de tokens.
        Filtros disponibles: 'all', 'backlog', 'in_progress', 'in_review', 'disputed', 'merged', 'consolidated'.
        """
        return handler.get_kanban_board(project_uuid=project_uuid, status=status)

    # 3. Herramienta: Crear Tarea en Backlog
    @server.tool()
    def create_kanban_task(
        project_uuid: str,
        card_id: str,
        title: str,
        description: str = ""
    ) -> str:
        """Crea una nueva tarea en el estado 'backlog' del proyecto indicado."""
        return handler.create_kanban_task(
            project_uuid=project_uuid,
            card_id=card_id,
            title=title,
            description=description
        )

    # 4. Herramienta: Adquirir Tarea con Lease Atómico
    @server.tool()
    def claim_kanban_task(
        project_uuid: str,
        card_id: str,
        node_uuid: str,
        duration_minutes: int = 30
    ) -> str:
        """
        Reclama una tarea atómicamente asignando un candado de lease temporal exclusivo.
        Previene colisiones entre agentes concurrentes rechazando reclamos sobre tareas con lease activo.
        """
        return handler.claim_kanban_task(
            project_uuid=project_uuid,
            card_id=card_id,
            node_uuid=node_uuid,
            duration_minutes=duration_minutes
        )

    # 5. Herramienta: Liberar o Transicionar Tarea
    @server.tool()
    def release_kanban_task(
        project_uuid: str,
        card_id: str,
        node_uuid: str,
        target_status: str = "in_review",
        branch_ref: Optional[str] = None
    ) -> str:
        """
        Transiciona una tarea a 'in_review', 'merged' o la libera a 'backlog' adjuntando la rama del código.
        """
        return handler.release_kanban_task(
            project_uuid=project_uuid,
            card_id=card_id,
            node_uuid=node_uuid,
            target_status=target_status,
            branch_ref=branch_ref
        )

    # 6. Herramienta: Obtener Contexto del Proyecto
    @server.tool()
    def get_project_context(project_uuid: str) -> str:
        """
        Obtiene el contexto arquitectónico y las rutas del doble repositorio (app/ y governance/)
        asociados al proyecto para evitar contaminación cruzada entre repositorios.
        """
        return handler.get_project_context(project_uuid=project_uuid)

    # 7. Herramienta: Registrar Nodo del Agente
    @server.tool()
    def register_agent_node(
        node_uuid: str,
        developer_name: str,
        agent_id: str,
        reviewer_sla_hours: int = 72
    ) -> str:
        """
        Registra el nodo del agente en el enjambre o actualiza su latido (heartbeat)
        con su SLA autodeclarado para revisión de Pull Requests.
        """
        return handler.register_agent_node(
            node_uuid=node_uuid,
            developer_name=developer_name,
            agent_id=agent_id,
            reviewer_sla_hours=reviewer_sla_hours
        )

    # 8. Herramienta: Enviar Turno RFC Dialéctico A2A
    @server.tool()
    def send_coordination_rfc(
        project_uuid: str,
        sender_node_uuid: str,
        stage: str,
        session_id: str,
        payload_json: str,
        card_id: Optional[str] = None
    ) -> str:
        """
        Envía un turno dialéctico de coordinación entre agentes (PROPOSAL, COUNTER_PROPOSAL, RESOLUTION).
        """
        return handler.send_coordination_rfc(
            project_uuid=project_uuid,
            sender_node_uuid=sender_node_uuid,
            stage=stage,
            session_id=session_id,
            payload_json=payload_json,
            card_id=card_id
        )

    # 9. Herramienta: Consultar Sesión de Coordinación A2A
    @server.tool()
    def get_coordination_session(project_uuid: str, session_id: str) -> str:
        """
        Consulta la secuencia de turnos dialécticos intercambiados en una sesión de coordinación.
        """
        return handler.get_coordination_session(project_uuid=project_uuid, session_id=session_id)

    # 10. Herramienta: Inspeccionar Gobernanza OKF
    @server.tool()
    def inspect_okf_governance(project_uuid: str) -> str:
        """
        Inspecciona y valida la integridad de la gobernanza OKF y el estado de la memoria huérfana del proyecto.
        """
        return handler.inspect_okf_governance(project_uuid=project_uuid)

    # 11. Herramienta: Contexto Integral de Tarea (Protocolo de lectura previa)
    @server.tool()
    def get_task_full_context(project_uuid: str, card_id: str) -> str:
        """
        Obtiene en una sola llamada la tarjeta YAML, ADRs enlazados, reglas de diseño y worktree asociado.
        """
        return handler.get_task_full_context(project_uuid=project_uuid, card_id=card_id)

    # 12. Herramienta: Administrar Worktree por Tarea
    @server.tool()
    def manage_task_worktree(
        project_uuid: str,
        card_id: str,
        node_uuid: str,
        action: str,
        files_scope_json: Optional[str] = None
    ) -> str:
        """
        Crea o remueve el worktree aislado de una tarea en app/.worktrees/{card_id} verificando colisiones de scope.
        """
        return handler.manage_task_worktree(
            project_uuid=project_uuid,
            card_id=card_id,
            node_uuid=node_uuid,
            action=action,
            files_scope_json=files_scope_json
        )

    # 13. Herramienta: Micro-scripts del OKF
    @server.tool()
    def run_okf_micro_script(
        project_uuid: str,
        script_action: str,
        params_json: str
    ) -> str:
        """
        Ejecuta micro-scripts sobre la gobernanza OKF (scaffold_decision, promote_decision, update_card, reconcile_kanban).
        """
        return handler.run_okf_micro_script(
            project_uuid=project_uuid,
            script_action=script_action,
            params_json=params_json
        )

    # 14. Herramienta: Proponer o Aplicar Reparto de Tareas
    @server.tool()
    def propose_or_apply_task_distribution(
        project_uuid: str,
        sender_node_uuid: str,
        session_id: str,
        distribution_plan_json: str,
        apply_immediately: bool = False
    ) -> str:
        """
        Permite a los agentes coordinar la distribución de tareas del backlog y reclamar leases sin pisarse.
        """
        return handler.propose_or_apply_task_distribution(
            project_uuid=project_uuid,
            sender_node_uuid=sender_node_uuid,
            session_id=session_id,
            distribution_plan_json=distribution_plan_json,
            apply_immediately=apply_immediately
        )

    # 15. Herramienta: Inspeccionar PR y Recomendar al Humano (HITL Guardrail)
    @server.tool()
    def inspect_pr_and_recommend(
        project_uuid: str,
        pr_id_or_branch: str,
        reviewer_node_uuid: str,
        recommendation: str,
        analysis_summary: str,
        suggested_reply: str
    ) -> str:
        """
        Analiza una Pull Request contra el OKF y deja lista su recomendación en la bandeja del humano sin auto-firmar.
        """
        return handler.inspect_pr_and_recommend(
            project_uuid=project_uuid,
            pr_id_or_branch=pr_id_or_branch,
            reviewer_node_uuid=reviewer_node_uuid,
            recommendation=recommendation,
            analysis_summary=analysis_summary,
            suggested_reply=suggested_reply
        )

    # 16. Herramienta: Emitir Evaluación de Agente en Juicio de Honor (1.5)
    @server.tool()
    def submit_agent_jury_evaluation(
        project_uuid: str,
        ticket_id: str,
        agent_id: str,
        evaluations_json: str,
        rationale: str = ""
    ) -> str:
        """
        Emite la evaluación técnica ponderada de un agente en un ticket de juicio de honor (peso 1.5).
        """
        return handler.submit_agent_jury_evaluation(
            project_uuid=project_uuid,
            ticket_id=ticket_id,
            agent_id=agent_id,
            evaluations_json=evaluations_json,
            rationale=rationale
        )

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="MCP2P: Servidor Local Model Context Protocol")
    parser.add_argument("--db-path", type=str, default=None, help="Ruta a la base de datos SQLite")
    parser.add_argument("--transport", type=str, default="stdio", choices=["stdio", "sse"], help="Tipo de transporte MCP")
    args = parser.parse_args()

    server = create_mcp_server(db_path=args.db_path)
    # Ejecutar sobre el transporte especificado
    server.run(transport=args.transport)


if __name__ == "__main__":
    main()
