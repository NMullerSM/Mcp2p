"""
Repositorios modulares SQLite que envuelven DatabaseManager implementando los
puertos de persistencia hexagonales sin SQL crudo fuera de src/storage/db.py.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.storage.db import DatabaseManager


class _BaseSqliteRepository:
    """Base compartida que sostiene la referencia al DatabaseManager inyectado."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db


class SqliteProjectRepository(_BaseSqliteRepository):
    """Repositorio SQLite para proyectos, stubs de unión y app_settings."""

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
    ) -> Dict[str, Any]:
        return self.db.create_project(
            name=name,
            base_path=base_path,
            code_repo_url=code_repo_url,
            gov_repo_url=gov_repo_url,
            init_git=init_git,
            project_uuid=project_uuid,
            reviewer_sla_hours=reviewer_sla_hours,
            okf_alignment_status=okf_alignment_status,
            alignment_dossier_markdown=alignment_dossier_markdown,
            owner_public_key=owner_public_key,
        )

    def list_projects(self) -> List[Dict[str, Any]]:
        return self.db.list_projects()

    def get_project_by_uuid(self, project_uuid: str) -> Optional[Dict[str, Any]]:
        return self.db.get_project_by_uuid(project_uuid)

    def delete_project(self, project_uuid: str) -> bool:
        return self.db.delete_project(project_uuid)

    def update_project_alignment(
        self, project_uuid: str, status: str, dossier_markdown: Optional[str] = None
    ) -> bool:
        return self.db.update_project_alignment(project_uuid, status, dossier_markdown)

    def update_project_sla(self, project_uuid: str, sla_hours: int) -> bool:
        return self.db.update_project_sla(project_uuid, sla_hours)

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
    ) -> Dict[str, Any]:
        return self.db.upsert_pending_join_stub(
            project_uuid=project_uuid,
            name=name,
            base_path=base_path,
            code_repo_path=code_repo_path,
            gov_repo_path=gov_repo_path,
            code_repo_url=code_repo_url,
            gov_repo_url=gov_repo_url,
            default_sla_hours=default_sla_hours,
        )

    def activate_joined_project(
        self,
        project_uuid: str,
        code_repo_path: str,
        gov_repo_path: str,
        local_node: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.db.activate_joined_project(
            project_uuid=project_uuid,
            code_repo_path=code_repo_path,
            gov_repo_path=gov_repo_path,
            local_node=local_node,
        )

    def update_project_repo_urls(
        self,
        project_uuid: str,
        code_repo_url: Optional[str] = None,
        gov_repo_url: Optional[str] = None,
    ) -> bool:
        return self.db.update_project_repo_urls(
            project_uuid=project_uuid,
            code_repo_url=code_repo_url,
            gov_repo_url=gov_repo_url,
        )

    def get_project_sync_fingerprint(self, project_uuid: str) -> Dict[str, Any]:
        return self.db.get_project_sync_fingerprint(project_uuid)

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        return self.db.get_setting(key, default)

    def set_setting(self, key: str, value: str) -> None:
        self.db.set_setting(key, value)

    def delete_setting(self, key: str) -> bool:
        return self.db.delete_setting(key)


class SqliteKanbanRepository(_BaseSqliteRepository):
    """Repositorio SQLite para tarjetas Kanban y leases."""

    def get_cards(self, project_uuid: str, status: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.db.get_cards(project_uuid, status=status)

    def get_card_by_id(self, project_uuid: str, card_id: str) -> Optional[Dict[str, Any]]:
        return self.db.get_card_by_id(project_uuid, card_id)

    def create_card(
        self,
        project_uuid: str,
        card_id: str,
        title: str,
        description: str = "",
        acceptance_criteria: str = "",
        assignee_node_uuid: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.db.create_card(
            project_uuid=project_uuid,
            card_id=card_id,
            title=title,
            description=description,
            acceptance_criteria=acceptance_criteria,
            assignee_node_uuid=assignee_node_uuid,
        )

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
    ) -> Dict[str, Any]:
        return self.db.update_card_details(
            project_uuid=project_uuid,
            card_id=card_id,
            title=title,
            description=description,
            acceptance_criteria=acceptance_criteria,
            status=status,
            branch_ref=branch_ref,
            assignee_node_uuid=assignee_node_uuid,
        )

    def claim_card_lease(
        self,
        project_uuid: str,
        card_id: str,
        node_uuid: str,
        duration_minutes: int = 30,
    ) -> Dict[str, Any]:
        return self.db.claim_card_lease(project_uuid, card_id, node_uuid, duration_minutes)

    def release_card_lease(
        self,
        project_uuid: str,
        card_id: str,
        node_uuid: str,
        target_status: str = "in_review",
        branch_ref: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.db.release_card_lease(
            project_uuid=project_uuid,
            card_id=card_id,
            node_uuid=node_uuid,
            target_status=target_status,
            branch_ref=branch_ref,
        )

    def update_card_status(
        self,
        project_uuid: str,
        card_id: str,
        new_status: str,
        branch_ref: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.db.update_card_status(project_uuid, card_id, new_status, branch_ref)

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
    ) -> Dict[str, Any]:
        return self.db.upsert_card_from_okf(
            project_uuid=project_uuid,
            card_id=card_id,
            title=title,
            status=status,
            description=description,
            acceptance_criteria=acceptance_criteria,
            assignee_node_uuid=assignee_node_uuid,
            lease_expires_at=lease_expires_at,
            branch_ref=branch_ref,
            related_ids_json=related_ids_json,
            updated_at=updated_at,
        )

    def get_project_cards_and_disputes_summary(self, project_uuid: str) -> Dict[str, Any]:
        return self.db.get_project_cards_and_disputes_summary(project_uuid)


class SqliteTribunalRepository(_BaseSqliteRepository):
    """Repositorio SQLite para tickets de disputa, votos del jurado, árbitros y entregas."""

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
    ) -> Dict[str, Any]:
        return self.db.create_dispute_ticket(
            ticket_id=ticket_id,
            project_uuid=project_uuid,
            title=title,
            collision_reason=collision_reason,
            contender_a_node=contender_a_node,
            contender_b_node=contender_b_node,
            proposal_a_ref=proposal_a_ref,
            proposal_b_ref=proposal_b_ref,
            card_id=card_id,
            proposal_c_ref=proposal_c_ref,
            jury_matrix_json=jury_matrix_json,
            escalation_recommendation=escalation_recommendation,
            arbiter_node_uuid=arbiter_node_uuid,
            injection_suspected=injection_suspected,
        )

    def get_dispute_ticket(self, ticket_id: str) -> Optional[Dict[str, Any]]:
        return self.db.get_dispute_ticket(ticket_id)

    def list_dispute_tickets(
        self,
        project_uuid: str,
        status: Optional[str] = None,
        card_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return self.db.list_dispute_tickets(project_uuid, status=status, card_id=card_id)

    def get_dispute_ticket_by_card(self, project_uuid: str, card_id: str) -> Optional[Dict[str, Any]]:
        return self.db.get_dispute_ticket_by_card(project_uuid, card_id)

    def update_dispute_synthesis_and_matrix(
        self,
        ticket_id: str,
        proposal_c_ref: Optional[str] = None,
        jury_matrix: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        return self.db.update_dispute_synthesis_and_matrix(
            ticket_id, proposal_c_ref=proposal_c_ref, jury_matrix=jury_matrix
        )

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
    ) -> Dict[str, Any]:
        return self.db.add_jury_borda_score(
            vote_uuid=vote_uuid,
            ticket_id=ticket_id,
            juror_id=juror_id,
            evaluated_option=evaluated_option,
            score_kiss=score_kiss,
            score_performance=score_performance,
            score_security=score_security,
            score_ux_hicks=score_ux_hicks,
            score_testability=score_testability,
            rationale_summary=rationale_summary,
        )

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
    ) -> Dict[str, Any]:
        return self.db.add_human_jury_vote(
            vote_uuid=vote_uuid,
            ticket_id=ticket_id,
            node_uuid=node_uuid,
            developer_name=developer_name,
            voted_option=voted_option,
            signature_ed25519=signature_ed25519,
            weight=weight,
            public_key=public_key,
            signed_at=signed_at,
            content_sha256=content_sha256,
            project_uuid=project_uuid,
        )

    def get_jury_votes_summary(self, ticket_id: str) -> Dict[str, Any]:
        return self.db.get_jury_votes_summary(ticket_id)

    def update_dispute_status(
        self,
        ticket_id: str,
        status: str,
        resolution_mode: str = "",
        winning_option: Optional[str] = None,
        final_adr_path: Optional[str] = None,
    ) -> bool:
        return self.db.update_dispute_status(
            ticket_id=ticket_id,
            status=status,
            resolution_mode=resolution_mode,
            winning_option=winning_option,
            final_adr_path=final_adr_path,
        )

    def set_project_arbiter(self, project_uuid: str, node_uuid: str) -> bool:
        return self.db.set_project_arbiter(project_uuid, node_uuid)

    def clear_project_arbiter(self, project_uuid: str, node_uuid: str) -> bool:
        return self.db.clear_project_arbiter(project_uuid, node_uuid)

    def list_project_arbiters(self, project_uuid: str) -> List[Dict[str, Any]]:
        return self.db.list_project_arbiters(project_uuid)

    def record_vote_delivery(self, ticket_id: str, node_uuid: str, delivered_at: Optional[str] = None) -> bool:
        return self.db.record_vote_delivery(ticket_id, node_uuid, delivered_at)

    def get_vote_deliveries(self, ticket_id: str) -> List[Dict[str, Any]]:
        return self.db.get_vote_deliveries(ticket_id)

    def get_pending_hitl_bundle(self, project_uuid: str, local_node: Optional[str] = None) -> Dict[str, Any]:
        return self.db.get_pending_hitl_bundle(project_uuid, local_node=local_node)

    def get_hitl_ticket_detail(self, project_uuid: str, ticket_id: str) -> Optional[Dict[str, Any]]:
        return self.db.get_hitl_ticket_detail(project_uuid, ticket_id)


class SqliteCollaborationRepository(_BaseSqliteRepository):
    """Repositorio SQLite para nodos del enjambre y colaboradores HITL."""

    def register_or_update_node(
        self,
        node_uuid: str,
        developer_name: str,
        agent_id: str,
        reviewer_sla_hours: int = 72,
        status: str = "online",
        p2p_endpoint: Optional[str] = None,
        node_kind: str = "agent",
    ) -> Dict[str, Any]:
        return self.db.register_or_update_node(
            node_uuid=node_uuid,
            developer_name=developer_name,
            agent_id=agent_id,
            reviewer_sla_hours=reviewer_sla_hours,
            status=status,
            p2p_endpoint=p2p_endpoint,
            node_kind=node_kind,
        )

    def get_swarm_nodes(self) -> List[Dict[str, Any]]:
        return self.db.get_swarm_nodes()

    def get_node_by_uuid(self, node_uuid: str) -> Optional[Dict[str, Any]]:
        return self.db.get_node_by_uuid(node_uuid)

    def mark_node_offline(self, node_uuid: str) -> bool:
        return self.db.mark_node_offline(node_uuid)

    def add_or_request_collaborator(
        self,
        project_uuid: str,
        node_uuid: str,
        github_username: str = "",
        public_key: str = "",
        role: str = "collaborator",
        status: str = "pending_approval",
    ) -> Dict[str, Any]:
        return self.db.add_or_request_collaborator(
            project_uuid=project_uuid,
            node_uuid=node_uuid,
            github_username=github_username,
            public_key=public_key,
            role=role,
            status=status,
        )

    def get_collaborator(self, project_uuid: str, node_uuid: str) -> Optional[Dict[str, Any]]:
        return self.db.get_collaborator(project_uuid, node_uuid)

    def get_collaborator_by_pubkey(self, project_uuid: str, public_key: str) -> Optional[Dict[str, Any]]:
        return self.db.get_collaborator_by_pubkey(project_uuid, public_key)

    def get_unresolved_or_approved_collaborator_by_pubkey(
        self, project_uuid: str, public_key: str
    ) -> Optional[Dict[str, Any]]:
        return self.db.get_unresolved_or_approved_collaborator_by_pubkey(project_uuid, public_key)

    def get_project_collaborators(
        self, project_uuid: str, online_only: bool = False
    ) -> List[Dict[str, Any]]:
        return self.db.get_project_collaborators(project_uuid, online_only=online_only)

    def list_collaborators(
        self, project_uuid: str, online_only: bool = False
    ) -> List[Dict[str, Any]]:
        return self.db.list_collaborators(project_uuid, online_only=online_only)

    def get_project_collaborators_map(self, project_uuid: str) -> Dict[str, Dict[str, Any]]:
        return self.db.get_project_collaborators_map(project_uuid)

    def approve_collaborator(self, project_uuid: str, node_uuid: str) -> bool:
        return self.db.approve_collaborator(project_uuid, node_uuid)

    def reject_collaborator(self, project_uuid: str, node_uuid: str) -> bool:
        return self.db.reject_collaborator(project_uuid, node_uuid)

    def remove_collaborator(self, project_uuid: str, node_uuid: str) -> bool:
        return self.db.remove_collaborator(project_uuid, node_uuid)

    def is_trusted_public_key(self, public_key: str) -> bool:
        return self.db.is_trusted_public_key(public_key)

    def get_join_rate_limit_state(self, project_uuid: str, public_key: str) -> Dict[str, Any]:
        return self.db.get_join_rate_limit_state(project_uuid, public_key)

    def record_join_attempt(self, project_uuid: str, public_key: str) -> None:
        self.db.record_join_attempt(project_uuid, public_key)

    def record_rejection(self, project_uuid: str, node_uuid: str) -> Dict[str, Any]:
        return self.db.record_rejection(project_uuid, node_uuid)


class SqliteOkfMetadataRepository(_BaseSqliteRepository):
    """Repositorio SQLite para mensajes A2A/sesiones y cola outbox."""

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
    ) -> None:
        self.db.log_a2a_message(
            project_uuid=project_uuid,
            message_uuid=message_uuid,
            sender_node_uuid=sender_node_uuid,
            intent=intent,
            payload_rfc_yaml=payload_rfc_yaml,
            card_id=card_id,
            session_id=session_id,
            seq=seq,
        )

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
    ) -> Dict[str, Any]:
        return self.db.append_session_message(
            project_uuid=project_uuid,
            sender_node_uuid=sender_node_uuid,
            session_id=session_id,
            intent=intent,
            payload_dict=payload_dict,
            card_id=card_id,
            event_type=event_type,
            message_uuid=message_uuid,
            seq_field=seq_field,
        )

    def get_session_messages(
        self, project_uuid: str, session_id: str, intent: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        return self.db.get_session_messages(project_uuid, session_id, intent=intent)

    def get_recent_a2a_messages(self, project_uuid: str, limit: int = 50) -> List[Dict[str, Any]]:
        return self.db.get_recent_a2a_messages(project_uuid, limit=limit)

    def get_a2a_message_by_uuid(self, message_uuid: str) -> Optional[Dict[str, Any]]:
        return self.db.get_a2a_message_by_uuid(message_uuid)

    def has_a2a_message(self, message_uuid: str) -> bool:
        return self.db.has_a2a_message(message_uuid)

    def enqueue_outbox_event(
        self,
        project_uuid: str,
        node_uuid: str,
        event_type: str,
        payload_raw: str,
        event_uuid: Optional[str] = None,
    ) -> str:
        return self.db.enqueue_outbox_event(
            project_uuid=project_uuid,
            node_uuid=node_uuid,
            event_type=event_type,
            payload_raw=payload_raw,
            event_uuid=event_uuid,
        )

    def get_pending_outbox_events(self, project_uuid: str, limit: int = 100) -> List[Dict[str, Any]]:
        return self.db.get_pending_outbox_events(project_uuid, limit=limit)

    def mark_outbox_event_status(self, event_uuid: str, sync_status: str) -> None:
        self.db.mark_outbox_event_status(event_uuid, sync_status)


class SqliteExtensionRegistryRepository(_BaseSqliteRepository):
    """Repositorio SQLite para tablas de extensión, settings y seeds P2P."""

    def register_extension_table(self, table_name: str, ddl_sql: str) -> None:
        self.db.register_extension_table(table_name, ddl_sql)

    def get_extension_setting(self, namespace: str, key: str, default: Optional[str] = None) -> Optional[str]:
        return self.db.get_extension_setting(namespace, key, default)

    def set_extension_setting(self, namespace: str, key: str, value: str) -> None:
        self.db.set_extension_setting(namespace, key, value)

    def delete_extension_setting(self, namespace: str, key: str) -> bool:
        return self.db.delete_extension_setting(namespace, key)

    def add_seed_node(self, project_uuid: Optional[str], seed_url: str, label: str = "") -> List[Dict[str, Any]]:
        return self.db.add_seed_node(project_uuid, seed_url, label=label)

    def remove_seed_node(self, project_uuid: Optional[str], seed_url: str) -> bool:
        return self.db.remove_seed_node(project_uuid, seed_url)

    def list_seed_nodes(self, project_uuid: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.db.list_seed_nodes(project_uuid)


class SqlitePaneIdentityRepository(_BaseSqliteRepository):
    """Repositorio SQLite de la identidad estable y el Auto-Relay de cada (proyecto, ranura de panel)."""

    def get_pane_identity(self, project_uuid: str, pane_slot: str) -> Optional[Dict[str, Any]]:
        return self.db.get_pane_identity(project_uuid, pane_slot)

    def get_or_create_pane_identity(
        self, project_uuid: str, pane_slot: str, *, touch: bool = False
    ) -> Dict[str, Any]:
        return self.db.get_or_create_pane_identity(project_uuid, pane_slot, touch=touch)

    def set_pane_auto_relay(self, project_uuid: str, pane_slot: str, enabled: bool) -> Dict[str, Any]:
        return self.db.set_pane_auto_relay(project_uuid, pane_slot, enabled)

    def list_pane_identities(self, project_uuid: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.db.list_pane_identities(project_uuid)
