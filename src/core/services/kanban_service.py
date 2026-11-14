"""
Servicio de Gobernanza Kanban con aplicación estricta de invariantes HITL (EGM-35).

Bloquea transiciones terminales ('merged', 'rejected', 'discarded', 'consolidated')
y promoción de decisiones a 'approved' cuando `actor.kind != 'human_ui'`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from src.core.domain.context import ActorContext, DomainEvent
from src.core.events import DomainEventBus
from src.core.ports.connectors import OkfGovernancePort
from src.core.ports.repositories import KanbanRepositoryPort, ProjectRepositoryPort

HUMAN_ONLY_CARD_STATES = frozenset({"merged", "rejected", "discarded", "consolidated"})
HUMAN_ONLY_DECISION_STATES = frozenset({"approved", "rejected"})


class KanbanGovernanceService:
    """Casos de uso Kanban y promoción de decisiones bajo reglas HITL."""

    def __init__(
        self,
        kanban_repo: KanbanRepositoryPort,
        project_repo: ProjectRepositoryPort,
        okf_port: Optional[OkfGovernancePort] = None,
        event_bus: Optional[DomainEventBus] = None,
    ) -> None:
        self.kanban_repo = kanban_repo
        self.project_repo = project_repo
        self.okf_port = okf_port
        self.event_bus = event_bus

    def _emit(self, event_type: str, project_uuid: str, payload: Dict[str, Any]) -> None:
        if self.event_bus is not None:
            self.event_bus.publish(DomainEvent(event_type=event_type, project_uuid=project_uuid, payload=payload))

    @staticmethod
    def _human_required_error(action_desc: str) -> Dict[str, Any]:
        return {
            "success": False,
            "code": "HUMAN_SIGNATURE_REQUIRED",
            "error": (
                f"Operación '{action_desc}' bloqueada: requiere actor humano autenticado "
                "(actor.kind == 'human_ui') con firma Ed25519."
            ),
        }

    def get_board(self, project_uuid: str, status: Optional[str] = None) -> Dict[str, Any]:
        """Obtiene las tarjetas Kanban de un proyecto agrupadas y en lista."""
        cards = self.kanban_repo.get_cards(project_uuid, status=status)
        return {"success": True, "project_uuid": project_uuid, "cards": cards, "count": len(cards)}

    def create_task(
        self,
        project_uuid: str,
        card_id: str,
        title: str,
        description: str = "",
        acceptance_criteria: str = "",
        assignee_node_uuid: Optional[str] = None,
        actor: Optional[ActorContext] = None,
    ) -> Dict[str, Any]:
        """Crea una tarjeta Kanban en backlog."""
        card = self.kanban_repo.create_card(
            project_uuid=project_uuid,
            card_id=card_id,
            title=title,
            description=description,
            acceptance_criteria=acceptance_criteria,
            assignee_node_uuid=assignee_node_uuid,
        )
        self._emit(
            "kanban.card_created",
            project_uuid,
            {"card_id": card_id, "actor_kind": actor.kind if actor else "system"},
        )
        return {"success": True, "card": card}

    def update_task(
        self,
        project_uuid: str,
        card_id: str,
        actor: ActorContext,
        title: Optional[str] = None,
        description: Optional[str] = None,
        acceptance_criteria: Optional[str] = None,
        status: Optional[str] = None,
        branch_ref: Optional[str] = None,
        assignee_node_uuid: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Actualiza metadatos de tarjeta; bloquea transición a estados terminales si no es human_ui."""
        if status is not None and status in HUMAN_ONLY_CARD_STATES and not actor.is_human_ui():
            return self._human_required_error(f"update_task(status={status})")
        card = self.kanban_repo.update_card_details(
            project_uuid=project_uuid,
            card_id=card_id,
            title=title,
            description=description,
            acceptance_criteria=acceptance_criteria,
            status=status,
            branch_ref=branch_ref,
            assignee_node_uuid=assignee_node_uuid,
        )
        self._emit(
            "kanban.card_updated",
            project_uuid,
            {"card_id": card_id, "status": card.get("status"), "actor_node": actor.node_uuid},
        )
        return {"success": True, "card": card}

    def claim_lease(
        self,
        project_uuid: str,
        card_id: str,
        actor: ActorContext,
        duration_minutes: int = 30,
    ) -> Dict[str, Any]:
        """Adquiere atómicamente el lease de una tarjeta para el actor."""
        card = self.kanban_repo.claim_card_lease(
            project_uuid=project_uuid,
            card_id=card_id,
            node_uuid=actor.node_uuid,
            duration_minutes=duration_minutes,
        )
        self._emit(
            "kanban.lease_claimed",
            project_uuid,
            {"card_id": card_id, "node_uuid": actor.node_uuid},
        )
        return {"success": True, "card": card}

    def release_lease(
        self,
        project_uuid: str,
        card_id: str,
        actor: ActorContext,
        target_status: str = "in_review",
        branch_ref: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Libera/transiciona una tarjeta con lease; bloquea estados terminales si actor.kind != 'human_ui'."""
        if target_status in HUMAN_ONLY_CARD_STATES and not actor.is_human_ui():
            return self._human_required_error(f"release_lease(target_status={target_status})")

        card = self.kanban_repo.release_card_lease(
            project_uuid=project_uuid,
            card_id=card_id,
            node_uuid=actor.node_uuid,
            target_status=target_status,
            branch_ref=branch_ref,
        )
        self._emit(
            "kanban.lease_released",
            project_uuid,
            {"card_id": card_id, "status": target_status, "node_uuid": actor.node_uuid},
        )
        return {"success": True, "card": card}

    def transition_card_status(
        self,
        project_uuid: str,
        card_id: str,
        new_status: str,
        actor: ActorContext,
        branch_ref: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Transiciona el estado de una tarjeta aplicando el guardrail HITL (EGM-35)."""
        if new_status in HUMAN_ONLY_CARD_STATES and not actor.is_human_ui():
            return self._human_required_error(f"transition_card_status({new_status})")

        card = self.kanban_repo.update_card_status(
            project_uuid=project_uuid,
            card_id=card_id,
            new_status=new_status,
            branch_ref=branch_ref,
        )
        self._emit(
            "kanban.status_changed",
            project_uuid,
            {"card_id": card_id, "status": new_status, "actor_node": actor.node_uuid},
        )
        return {"success": True, "card": card}

    def promote_decision(
        self,
        project_uuid: str,
        decision_id: str,
        target_state: str,
        actor: ActorContext,
        signature_block: Optional[Dict[str, Any]] = None,
        reason: str = "",
    ) -> Dict[str, Any]:
        """
        Promueve una decisión arquitectónica OKF.
        Bloquea target_state == 'approved' (y 'rejected') si actor.kind != 'human_ui' (EGM-35).
        """
        normalized_state = (target_state or "").strip().lower()
        if normalized_state in HUMAN_ONLY_DECISION_STATES and not actor.is_human_ui():
            return self._human_required_error(f"promote_decision(target_state={normalized_state})")

        proj = self.project_repo.get_project_by_uuid(project_uuid)
        if not proj or not proj.get("gov_repo_path"):
            return {"success": False, "error": f"Proyecto {project_uuid} sin repositorio OKF configurado"}

        if self.okf_port is None:
            return {"success": False, "error": "OkfGovernancePort no conectado al núcleo"}

        gov_path = Path(proj["gov_repo_path"])
        res = self.okf_port.move_decision_lifecycle(
            gov_base_path=gov_path,
            decision_id=decision_id,
            target_state=normalized_state,
            signature_block=signature_block,
            reason=reason,
        )
        if res.get("success"):
            self._emit(
                "okf.decision_promoted",
                project_uuid,
                {"decision_id": decision_id, "target_state": normalized_state, "actor_node": actor.node_uuid},
            )
        return res
