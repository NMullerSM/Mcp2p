"""
Puertos de persistencia (Protocolos) que desacoplan los servicios del núcleo respecto a SQLite.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class ProjectRepositoryPort(Protocol):
    """Puerto de persistencia para proyectos y su configuración local."""

    def create_project(
        self,
        name: str,
        base_path: Optional[str] = None,
        code_repo_url: str = "",
        gov_repo_url: str = "",
        init_git: bool = True,
        project_uuid: Optional[str] = None,
        reviewer_sla_hours: int = 24,
        okf_alignment_status: str = "aligned_okf",
        alignment_dossier_markdown: str = "",
        owner_public_key: str = "",
    ) -> Dict[str, Any]: ...

    def list_projects(self) -> List[Dict[str, Any]]: ...

    def get_project_by_uuid(self, project_uuid: str) -> Optional[Dict[str, Any]]: ...

    def delete_project(self, project_uuid: str) -> bool: ...

    def update_project_alignment(
        self,
        project_uuid: str,
        status: str,
        dossier_markdown: Optional[str] = None,
    ) -> bool: ...

    def update_project_sla(self, project_uuid: str, sla_hours: int) -> bool: ...

    def upsert_pending_join_stub(
        self,
        project_uuid: str,
        name: str,
        base_path: str,
        code_repo_path: str,
        gov_repo_path: str,
        code_repo_url: str = "",
        gov_repo_url: str = "",
        default_sla_hours: int = 24,
    ) -> Dict[str, Any]: ...

    def activate_joined_project(
        self,
        project_uuid: str,
        code_repo_path: str,
        gov_repo_path: str,
        local_node: Optional[str] = None,
    ) -> Dict[str, Any]: ...

    def update_project_repo_urls(
        self,
        project_uuid: str,
        code_repo_url: Optional[str] = None,
        gov_repo_url: Optional[str] = None,
    ) -> bool: ...

    def get_project_sync_fingerprint(self, project_uuid: str) -> Dict[str, Any]: ...

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]: ...

    def set_setting(self, key: str, value: str) -> None: ...

    def delete_setting(self, key: str) -> bool: ...


@runtime_checkable
class KanbanRepositoryPort(Protocol):
    """Puerto de persistencia para tarjetas Kanban y leases."""

    def get_cards(self, project_uuid: str, status: Optional[str] = None) -> List[Dict[str, Any]]: ...

    def get_card_by_id(self, project_uuid: str, card_id: str) -> Optional[Dict[str, Any]]: ...

    def create_card(
        self,
        project_uuid: str,
        card_id: str,
        title: str,
        description: str = "",
        acceptance_criteria: str = "",
        assignee_node_uuid: Optional[str] = None,
    ) -> Dict[str, Any]: ...

    def update_card_details(
        self,
        project_uuid: str,
        card_id: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        acceptance_criteria: Optional[str] = None,
        status: Optional[str] = None,
        branch_ref: Optional[str] = None,
        assignee_node_uuid: Optional[str] = None,
    ) -> Dict[str, Any]: ...

    def claim_card_lease(
        self,
        project_uuid: str,
        card_id: str,
        node_uuid: str,
        duration_minutes: int = 30,
    ) -> Dict[str, Any]: ...

    def release_card_lease(
        self,
        project_uuid: str,
        card_id: str,
        node_uuid: str,
        target_status: str = "in_review",
        branch_ref: Optional[str] = None,
    ) -> Dict[str, Any]: ...

    def update_card_status(
        self,
        project_uuid: str,
        card_id: str,
        new_status: str,
        branch_ref: Optional[str] = None,
    ) -> Dict[str, Any]: ...

    def upsert_card_from_okf(
        self,
        project_uuid: str,
        card_id: str,
        title: str,
        status: str,
        description: str = "",
        acceptance_criteria: str = "",
        assignee_node_uuid: Optional[str] = None,
        lease_expires_at: Optional[str] = None,
        branch_ref: Optional[str] = None,
        related_ids_json: str = "[]",
        updated_at: Optional[str] = None,
    ) -> Dict[str, Any]: ...

    def get_project_cards_and_disputes_summary(self, project_uuid: str) -> Dict[str, Any]: ...


@runtime_checkable
class TribunalRepositoryPort(Protocol):
    """Puerto de persistencia para tickets de disputa, votos del jurado y árbitros."""

    def create_dispute_ticket(
        self,
        ticket_id: str,
        project_uuid: str,
        title: str,
        collision_reason: str,
        contender_a_node: str,
        contender_b_node: str,
        proposal_a_ref: str,
        proposal_b_ref: str,
        card_id: Optional[str] = None,
        proposal_c_ref: str = "",
        jury_matrix_json: str = "[]",
        escalation_recommendation: Optional[str] = None,
        arbiter_node_uuid: str = "",
        injection_suspected: bool = False,
    ) -> Dict[str, Any]: ...

    def get_dispute_ticket(self, ticket_id: str) -> Optional[Dict[str, Any]]: ...

    def list_dispute_tickets(
        self,
        project_uuid: str,
        status: Optional[str] = None,
        card_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]: ...

    def get_dispute_ticket_by_card(self, project_uuid: str, card_id: str) -> Optional[Dict[str, Any]]: ...

    def update_dispute_synthesis_and_matrix(
        self,
        ticket_id: str,
        proposal_c_ref: Optional[str] = None,
        jury_matrix: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]: ...

    def add_jury_borda_score(
        self,
        vote_uuid: str,
        ticket_id: str,
        juror_id: str,
        evaluated_option: str,
        score_kiss: float,
        score_performance: float,
        score_security: float,
        score_ux_hicks: float,
        score_testability: float,
        rationale_summary: str = "",
    ) -> Dict[str, Any]: ...

    def add_human_jury_vote(
        self,
        vote_uuid: str,
        ticket_id: str,
        node_uuid: str,
        developer_name: str,
        voted_option: str,
        signature_ed25519: str,
        weight: float = 2.0,
        public_key: str = "",
        signed_at: str = "",
        content_sha256: str = "",
        project_uuid: str = "",
    ) -> Dict[str, Any]: ...

    def get_jury_votes_summary(self, ticket_id: str) -> Dict[str, Any]: ...

    def update_dispute_status(
        self,
        ticket_id: str,
        status: str,
        resolution_mode: str = "",
        winning_option: Optional[str] = None,
        final_adr_path: Optional[str] = None,
    ) -> bool: ...

    def set_project_arbiter(self, project_uuid: str, node_uuid: str) -> bool: ...

    def clear_project_arbiter(self, project_uuid: str, node_uuid: str) -> bool: ...

    def list_project_arbiters(self, project_uuid: str) -> List[Dict[str, Any]]: ...

    def record_vote_delivery(self, ticket_id: str, node_uuid: str, delivered_at: Optional[str] = None) -> bool: ...

    def get_vote_deliveries(self, ticket_id: str) -> List[Dict[str, Any]]: ...

    def get_pending_hitl_bundle(self, project_uuid: str, local_node: Optional[str] = None) -> Dict[str, Any]: ...

    def get_hitl_ticket_detail(self, project_uuid: str, ticket_id: str) -> Optional[Dict[str, Any]]: ...


@runtime_checkable
class CollaborationRepositoryPort(Protocol):
    """Puerto de persistencia para nodos, colaboradores HITL y outbox."""

    def register_or_update_node(
        self,
        node_uuid: str,
        developer_name: str,
        agent_id: str,
        reviewer_sla_hours: int = 72,
        status: str = "online",
        p2p_endpoint: Optional[str] = None,
        node_kind: str = "agent",
    ) -> Dict[str, Any]: ...

    def get_swarm_nodes(self) -> List[Dict[str, Any]]: ...

    def get_node_by_uuid(self, node_uuid: str) -> Optional[Dict[str, Any]]: ...

    def mark_node_offline(self, node_uuid: str) -> bool: ...

    def add_or_request_collaborator(
        self,
        project_uuid: str,
        node_uuid: str,
        github_username: str = "",
        public_key: str = "",
        role: str = "collaborator",
        status: str = "pending_approval",
    ) -> Dict[str, Any]: ...

    def get_collaborator(self, project_uuid: str, node_uuid: str) -> Optional[Dict[str, Any]]: ...

    def get_collaborator_by_pubkey(self, project_uuid: str, public_key: str) -> Optional[Dict[str, Any]]: ...

    def get_unresolved_or_approved_collaborator_by_pubkey(
        self, project_uuid: str, public_key: str
    ) -> Optional[Dict[str, Any]]: ...

    def get_project_collaborators(
        self, project_uuid: str, online_only: bool = False
    ) -> List[Dict[str, Any]]: ...

    def list_collaborators(
        self, project_uuid: str, online_only: bool = False
    ) -> List[Dict[str, Any]]: ...

    def get_project_collaborators_map(self, project_uuid: str) -> Dict[str, Dict[str, Any]]: ...

    def approve_collaborator(self, project_uuid: str, node_uuid: str) -> bool: ...

    def reject_collaborator(self, project_uuid: str, node_uuid: str) -> bool: ...

    def remove_collaborator(self, project_uuid: str, node_uuid: str) -> bool: ...

    def is_trusted_public_key(self, public_key: str) -> bool: ...

    def get_join_rate_limit_state(self, project_uuid: str, public_key: str) -> Dict[str, Any]: ...

    def record_join_attempt(self, project_uuid: str, public_key: str) -> None: ...

    def record_rejection(self, project_uuid: str, node_uuid: str) -> Dict[str, Any]: ...


@runtime_checkable
class OkfMetadataRepositoryPort(Protocol):
    """Puerto de persistencia para mensajería A2A y cola Outbox asociada a OKF."""

    def log_a2a_message(
        self,
        project_uuid: str,
        message_uuid: str,
        sender_node_uuid: str,
        intent: str,
        payload_rfc_yaml: str,
        card_id: Optional[str] = None,
        session_id: Optional[str] = None,
        seq: Optional[int] = None,
    ) -> None: ...

    def append_session_message(
        self,
        project_uuid: str,
        sender_node_uuid: str,
        session_id: str,
        intent: str,
        payload_dict: Dict[str, Any],
        card_id: Optional[str] = None,
        event_type: Optional[str] = None,
        message_uuid: Optional[str] = None,
        seq_field: str = "seq",
    ) -> Dict[str, Any]: ...

    def get_session_messages(
        self, project_uuid: str, session_id: str, intent: Optional[str] = None
    ) -> List[Dict[str, Any]]: ...

    def get_recent_a2a_messages(self, project_uuid: str, limit: int = 50) -> List[Dict[str, Any]]: ...

    def get_a2a_message_by_uuid(self, message_uuid: str) -> Optional[Dict[str, Any]]: ...

    def has_a2a_message(self, message_uuid: str) -> bool: ...

    def enqueue_outbox_event(
        self,
        project_uuid: str,
        node_uuid: str,
        event_type: str,
        payload_raw: str,
        event_uuid: Optional[str] = None,
    ) -> str: ...

    def get_pending_outbox_events(self, project_uuid: str, limit: int = 100) -> List[Dict[str, Any]]: ...

    def mark_outbox_event_status(self, event_uuid: str, sync_status: str) -> None: ...


@runtime_checkable
class ExtensionRegistryRepositoryPort(Protocol):
    """Puerto de persistencia para tablas dinámicas de conectores, ajustes y seeds P2P."""

    def register_extension_table(self, table_name: str, ddl_sql: str) -> None: ...

    def get_extension_setting(self, namespace: str, key: str, default: Optional[str] = None) -> Optional[str]: ...

    def set_extension_setting(self, namespace: str, key: str, value: str) -> None: ...

    def delete_extension_setting(self, namespace: str, key: str) -> bool: ...

    def add_seed_node(self, project_uuid: Optional[str], seed_url: str, label: str = "") -> List[Dict[str, Any]]: ...

    def remove_seed_node(self, project_uuid: Optional[str], seed_url: str) -> bool: ...

    def list_seed_nodes(self, project_uuid: Optional[str] = None) -> List[Dict[str, Any]]: ...
