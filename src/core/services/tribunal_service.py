"""
Servicio de Tribunal Dialéctico y Bandeja HITL del núcleo hexagonal.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.a2a.bus import A2ABus
from src.core.domain.context import ActorContext, DomainEvent
from src.core.events import DomainEventBus
from src.core.ports.repositories import TribunalRepositoryPort


class DialecticTribunalService:
    """Orquesta disputas, evaluaciones Borda, votos humanos Ed25519 y laudos de árbitro."""

    def __init__(
        self,
        tribunal_repo: TribunalRepositoryPort,
        bus: A2ABus,
        event_bus: Optional[DomainEventBus] = None,
    ) -> None:
        self.tribunal_repo = tribunal_repo
        self.bus = bus
        self.event_bus = event_bus

    def _emit(self, event_type: str, project_uuid: str, payload: Dict[str, Any]) -> None:
        if self.event_bus is not None:
            self.event_bus.publish(DomainEvent(event_type=event_type, project_uuid=project_uuid, payload=payload))

    def open_dispute(
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
        domain: Optional[str] = None,
        escalation_recommendation: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Abre un ticket de disputa técnica con Matriz Neutral Anti-Sycophancy."""
        ticket = self.bus.open_dispute_ticket(
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
            domain=domain,
            escalation_recommendation=escalation_recommendation,
        )
        self._emit(
            "tribunal.dispute_opened",
            project_uuid,
            {"ticket_id": ticket.get("ticket_id"), "card_id": card_id},
        )
        return ticket

    def submit_agent_evaluation(
        self,
        ticket_id: str,
        juror_agent_id: str,
        scores_per_option: Dict[str, Dict[str, float]],
        rationale: str = "",
    ) -> List[Dict[str, Any]]:
        """Registra evaluaciones cardinales Borda de un subagente jurado neutral."""
        return self.bus.submit_agent_borda_evaluation(
            ticket_id=ticket_id,
            juror_agent_id=juror_agent_id,
            scores_per_option=scores_per_option,
            rationale=rationale,
        )

    def submit_human_vote(
        self,
        ticket_id: str,
        node_uuid: str,
        developer_name: str,
        voted_option: str,
        signature_ed25519: str,
        public_key: str = "",
        signed_at: str = "",
        content_sha256: str = "",
        project_uuid: str = "",
        actor: Optional[ActorContext] = None,
    ) -> Dict[str, Any]:
        """Registra un voto humano validando canal HITL (si se pasa actor) y firma Ed25519 contra BD."""
        if actor is not None and not actor.is_human_ui():
            return {
                "success": False,
                "code": "HUMAN_SIGNATURE_REQUIRED",
                "error": "El voto humano solo puede emitirse desde actor.kind == 'human_ui'.",
            }
        res = self.bus.submit_human_jury_vote(
            ticket_id=ticket_id,
            node_uuid=node_uuid,
            developer_name=developer_name,
            voted_option=voted_option,
            signature_ed25519=signature_ed25519,
            public_key=public_key,
            signed_at=signed_at,
            content_sha256=content_sha256,
            project_uuid=project_uuid,
        )
        if res.get("success"):
            self._emit(
                "tribunal.human_vote_recorded",
                project_uuid or "",
                {"ticket_id": ticket_id, "node_uuid": node_uuid, "voted_option": voted_option},
            )
        return res

    def submit_arbiter_ruling(
        self,
        ticket_id: str,
        arbiter_node_uuid: str,
        winning_option: str,
        signature_ed25519: str,
        rationale: str = "",
        public_key: str = "",
        signed_at: str = "",
        content_sha256: str = "",
        project_uuid: str = "",
        developer_name: str = "",
        actor: Optional[ActorContext] = None,
    ) -> Dict[str, Any]:
        """Registra el laudo vinculante de un árbitro externo verificado criptográficamente (EGM-01-R)."""
        if actor is not None and not actor.is_human_ui():
            return {
                "success": False,
                "code": "HUMAN_SIGNATURE_REQUIRED",
                "error": "El laudo de árbitro requiere actor.kind == 'human_ui'.",
            }
        res = self.bus.submit_external_arbiter_ruling(
            ticket_id=ticket_id,
            arbiter_node_uuid=arbiter_node_uuid,
            winning_option=winning_option,
            signature_ed25519=signature_ed25519,
            rationale=rationale,
            public_key=public_key,
            signed_at=signed_at,
            content_sha256=content_sha256,
            project_uuid=project_uuid,
            developer_name=developer_name,
        )
        if res.get("success"):
            self._emit(
                "tribunal.arbiter_ruling_ratified",
                res.get("project_uuid", project_uuid),
                {"ticket_id": ticket_id, "winning_option": winning_option, "arbiter_node_uuid": arbiter_node_uuid},
            )
        return res

    def compute_verdict(self, project_uuid: str, ticket_id: str) -> Dict[str, Any]:
        """Calcula el veredicto ponderado y persiste ruling YAML si queda ratificado."""
        verdict = self.bus.compute_jury_verdict(project_uuid, ticket_id)
        self._emit(
            "tribunal.verdict_computed",
            project_uuid,
            {"ticket_id": ticket_id, "status": verdict.get("status"), "winning_option": verdict.get("winning_option")},
        )
        return verdict

    def get_dispute_ticket(self, ticket_id: str) -> Optional[Dict[str, Any]]:
        return self.tribunal_repo.get_dispute_ticket(ticket_id)

    def list_disputes(
        self, project_uuid: str, status: Optional[str] = None, card_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        return self.tribunal_repo.list_dispute_tickets(project_uuid, status=status, card_id=card_id)

    def get_pending_hitl_bundle(self, project_uuid: str, local_node_uuid: Optional[str] = None) -> Dict[str, Any]:
        return self.tribunal_repo.get_pending_hitl_bundle(project_uuid, local_node=local_node_uuid)

    def get_hitl_ticket_detail(self, project_uuid: str, ticket_id: str) -> Optional[Dict[str, Any]]:
        return self.tribunal_repo.get_hitl_ticket_detail(project_uuid, ticket_id)

    def set_project_arbiter(self, project_uuid: str, node_uuid: str) -> bool:
        return self.tribunal_repo.set_project_arbiter(project_uuid, node_uuid)

    def clear_project_arbiter(self, project_uuid: str, node_uuid: str) -> bool:
        return self.tribunal_repo.clear_project_arbiter(project_uuid, node_uuid)

    def list_project_arbiters(self, project_uuid: str) -> List[Dict[str, Any]]:
        return self.tribunal_repo.list_project_arbiters(project_uuid)
