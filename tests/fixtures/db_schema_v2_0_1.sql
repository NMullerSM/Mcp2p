CREATE TABLE a2a_messages (
                    message_uuid TEXT PRIMARY KEY,
                    project_uuid TEXT NOT NULL,
                    sender_node_uuid TEXT NOT NULL,
                    intent TEXT CHECK(intent IN ('INFORMATIVE', 'COORDINATION', 'SEMANTIC_COLLISION', 'SUMMARY_SYNTHESIS', 'DIALOGUE', 'CHECKPOINT_COMPACT')) NOT NULL,
                    card_id TEXT,
                    session_id TEXT DEFAULT '',
                    seq INTEGER DEFAULT 0,
                    payload_rfc_yaml TEXT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (project_uuid) REFERENCES projects(project_uuid) ON DELETE CASCADE
                );

CREATE TABLE app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );

CREATE TABLE dispute_tickets (
                    ticket_id TEXT PRIMARY KEY,
                    project_uuid TEXT NOT NULL,
                    card_id TEXT,
                    title TEXT NOT NULL,
                    collision_reason TEXT NOT NULL,
                    contender_a_node TEXT NOT NULL,
                    contender_b_node TEXT NOT NULL,
                    proposal_a_ref TEXT NOT NULL,
                    proposal_b_ref TEXT NOT NULL,
                    proposal_c_ref TEXT DEFAULT '',
                    escalation_recommendation TEXT DEFAULT '',
                    jury_matrix_json TEXT DEFAULT '[]',
                    status TEXT CHECK(status IN ('open', 'provisional_ruling', 'ratified', 'challenged', 'resolved_hitl')) NOT NULL DEFAULT 'open',
                    resolution_mode TEXT DEFAULT '',
                    arbiter_node_uuid TEXT DEFAULT '',
                    injection_suspected INTEGER DEFAULT 0,
                    winning_quadrant TEXT CHECK(winning_quadrant IN ('OPTION_A', 'OPTION_B', 'ASYMMETRIC_HYBRID', 'THIRD_WAY_C')),
                    provisional_branch TEXT,
                    objection_deadline DATETIME,
                    final_adr_path TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (project_uuid) REFERENCES projects(project_uuid) ON DELETE CASCADE
                );

CREATE TABLE dispute_vote_delivery (
                    ticket_id TEXT NOT NULL,
                    node_uuid TEXT NOT NULL,
                    delivered_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (ticket_id, node_uuid),
                    FOREIGN KEY (ticket_id) REFERENCES dispute_tickets(ticket_id) ON DELETE CASCADE
                );

CREATE TABLE extension_settings (
                    namespace TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (namespace, key)
                );

CREATE TABLE human_jury_votes (
                    vote_uuid TEXT PRIMARY KEY,
                    ticket_id TEXT NOT NULL,
                    node_uuid TEXT NOT NULL,
                    developer_name TEXT NOT NULL,
                    voted_option TEXT CHECK(voted_option IN ('OPTION_A', 'OPTION_B', 'THIRD_WAY_C')) NOT NULL,
                    weight REAL DEFAULT 2.0,
                    signature_ed25519 TEXT NOT NULL,
                    public_key TEXT DEFAULT '',
                    signed_at TEXT DEFAULT '',
                    content_sha256 TEXT DEFAULT '',
                    project_uuid TEXT DEFAULT '',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (ticket_id) REFERENCES dispute_tickets(ticket_id) ON DELETE CASCADE
                );

CREATE TABLE jury_borda_scores (
                    vote_uuid TEXT PRIMARY KEY,
                    ticket_id TEXT NOT NULL,
                    juror_id TEXT NOT NULL,
                    evaluated_option TEXT CHECK(evaluated_option IN ('OPTION_A', 'OPTION_B', 'THIRD_WAY_C')) NOT NULL,
                    score_kiss REAL NOT NULL CHECK(score_kiss BETWEEN 0 AND 100),
                    score_performance REAL NOT NULL CHECK(score_performance BETWEEN 0 AND 100),
                    score_security REAL NOT NULL CHECK(score_security BETWEEN 0 AND 100),
                    score_ux_hicks REAL NOT NULL CHECK(score_ux_hicks BETWEEN 0 AND 100),
                    score_testability REAL NOT NULL CHECK(score_testability BETWEEN 0 AND 100),
                    weighted_total REAL NOT NULL,
                    rationale_summary TEXT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (ticket_id) REFERENCES dispute_tickets(ticket_id) ON DELETE CASCADE
                );

CREATE TABLE kanban_cards (
                    card_id TEXT NOT NULL,
                    project_uuid TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    acceptance_criteria TEXT DEFAULT '',
                    status TEXT CHECK(status IN ('backlog', 'in_progress', 'in_review', 'disputed', 'merged', 'consolidated', 'discarded', 'rejected')) NOT NULL DEFAULT 'backlog',
                    assignee_node_uuid TEXT,
                    lease_expires_at DATETIME,
                    branch_ref TEXT,
                    commit_refs_json TEXT DEFAULT '[]',
                    content_hash TEXT DEFAULT '',
                    related_ids_json TEXT DEFAULT '[]',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (card_id, project_uuid),
                    FOREIGN KEY (project_uuid) REFERENCES projects(project_uuid) ON DELETE CASCADE,
                    FOREIGN KEY (assignee_node_uuid) REFERENCES swarm_nodes(node_uuid)
                );

CREATE TABLE operator_sessions (
            session_id TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            state_reason TEXT,
            harness_pid INTEGER NOT NULL,
            harness_create_time REAL NOT NULL,
            harness_exe TEXT NOT NULL,
            harness_name TEXT,
            harness_user TEXT,
            harness_cwd TEXT,
            ancestors_json TEXT NOT NULL,
            projects_json TEXT NOT NULL,
            autonomy_level TEXT NOT NULL,
            transport TEXT NOT NULL,
            created_at_ms INTEGER NOT NULL,
            last_seen_at_ms INTEGER NOT NULL,
            idle_timeout_ms INTEGER NOT NULL,
            ended_at_ms INTEGER,
            ended_by TEXT
        );

CREATE TABLE outbox_events (
                    event_uuid TEXT PRIMARY KEY,
                    project_uuid TEXT NOT NULL,
                    node_uuid TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_raw TEXT NOT NULL,
                    sync_status TEXT CHECK(sync_status IN ('pending', 'flushed_to_branch', 'acked')) DEFAULT 'pending',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (project_uuid) REFERENCES projects(project_uuid) ON DELETE CASCADE
                );

CREATE TABLE p2p_seeds (
                    project_uuid TEXT NOT NULL,
                    seed_url TEXT NOT NULL,
                    label TEXT DEFAULT '',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (project_uuid, seed_url)
                );

CREATE TABLE pane_identities (
                project_uuid TEXT NOT NULL,
                pane_slot TEXT NOT NULL,
                node_uuid TEXT NOT NULL UNIQUE,
                auto_relay_enabled INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                last_spawned_at TEXT, worker_mode INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (project_uuid, pane_slot)
            );

CREATE TABLE project_arbiters (
                    project_uuid TEXT NOT NULL,
                    node_uuid TEXT NOT NULL,
                    registered_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (project_uuid, node_uuid),
                    FOREIGN KEY (project_uuid) REFERENCES projects(project_uuid) ON DELETE CASCADE
                );

CREATE TABLE project_collaborators (
                    project_uuid TEXT NOT NULL,
                    node_uuid TEXT NOT NULL,
                    github_username TEXT DEFAULT '',
                    public_key TEXT DEFAULT '',
                    role TEXT CHECK(role IN ('owner', 'collaborator', 'guest')) DEFAULT 'collaborator',
                    status TEXT CHECK(status IN ('pending_approval', 'approved', 'rejected')) DEFAULT 'pending_approval',
                    rejection_count INTEGER DEFAULT 0,
                    last_rejected_at TEXT,
                    request_window_start TEXT,
                    request_window_count INTEGER DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    approved_at DATETIME,
                    PRIMARY KEY (project_uuid, node_uuid),
                    FOREIGN KEY (project_uuid) REFERENCES projects(project_uuid) ON DELETE CASCADE
                );

CREATE TABLE projects (
                    project_uuid TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    base_path TEXT NOT NULL,
                    code_repo_path TEXT DEFAULT '',
                    gov_repo_path TEXT DEFAULT '',
                    code_repo_url TEXT DEFAULT '',
                    gov_repo_url TEXT DEFAULT '',
                    default_sla_hours INTEGER DEFAULT 24,
                    okf_alignment_status TEXT DEFAULT 'aligned_okf',
                    alignment_dossier_markdown TEXT DEFAULT '',
                    join_status TEXT DEFAULT 'active',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );

CREATE TABLE relay_audit (
            seq INTEGER PRIMARY KEY,
            ts_ms INTEGER NOT NULL,
            event TEXT NOT NULL,
            msg_id TEXT,
            chain_id TEXT,
            hop INTEGER,
            project_uuid TEXT,
            from_principal TEXT,
            to_principal TEXT,
            trust TEXT,
            content_hash TEXT,
            decision TEXT,
            reason_code TEXT,
            actor TEXT,
            prev_hash TEXT NOT NULL,
            row_hash TEXT NOT NULL
        );

CREATE TABLE relay_messages (
            msg_id TEXT PRIMARY KEY,
            project_uuid TEXT NOT NULL,
            from_principal TEXT NOT NULL,
            to_principal TEXT NOT NULL,
            kind TEXT NOT NULL,
            chain_id TEXT NOT NULL,
            hop INTEGER NOT NULL,
            parent_msg_id TEXT,
            trust TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            content TEXT,
            envelope_json TEXT NOT NULL,
            state TEXT NOT NULL,
            state_reason TEXT,
            card_id TEXT,
            created_at_ms INTEGER NOT NULL,
            expires_at_ms INTEGER NOT NULL,
            updated_at_ms INTEGER NOT NULL,
            delivered_at_ms INTEGER,
            acked_at_ms INTEGER,
            purged_at_ms INTEGER,
            pointer_state TEXT,
            pointer_pty_incarnation TEXT
        );

CREATE TABLE relay_pause (
            scope TEXT PRIMARY KEY,
            paused INTEGER NOT NULL DEFAULT 0,
            actor TEXT,
            reason TEXT,
            updated_at_ms INTEGER NOT NULL
        );

CREATE TABLE swarm_nodes (
                    node_uuid TEXT PRIMARY KEY,
                    developer_name TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    node_kind TEXT DEFAULT 'agent',
                    public_key TEXT,
                    reviewer_sla_hours INTEGER DEFAULT 24,
                    status TEXT CHECK(status IN ('online', 'offline', 'busy')) DEFAULT 'offline',
                    last_heartbeat DATETIME,
                    p2p_endpoint TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );

CREATE INDEX idx_a2a_project ON a2a_messages(project_uuid);

CREATE INDEX idx_a2a_project_card ON a2a_messages(project_uuid, card_id);

CREATE INDEX idx_a2a_project_created ON a2a_messages(project_uuid, created_at DESC);

CREATE INDEX idx_a2a_project_session_seq ON a2a_messages(project_uuid, session_id, seq);

CREATE UNIQUE INDEX idx_borda_unique ON jury_borda_scores(ticket_id, juror_id, evaluated_option);

CREATE INDEX idx_cards_project_status ON kanban_cards(project_uuid, status);

CREATE INDEX idx_cards_project_status_updated ON kanban_cards(project_uuid, status, updated_at DESC);

CREATE INDEX idx_collab_project ON project_collaborators(project_uuid, status);

CREATE UNIQUE INDEX idx_human_votes_unique ON human_jury_votes(ticket_id, node_uuid);

CREATE INDEX idx_operator_sessions_harness ON operator_sessions(harness_pid, state);

CREATE INDEX idx_operator_sessions_state ON operator_sessions(state);

CREATE INDEX idx_outbox_pending ON outbox_events(project_uuid, sync_status, created_at);

CREATE INDEX idx_pane_identities_node ON pane_identities(node_uuid);

CREATE INDEX idx_relay_audit_msg ON relay_audit(msg_id);

CREATE INDEX idx_relay_messages_chain ON relay_messages(chain_id);

CREATE INDEX idx_relay_messages_created ON relay_messages(created_at_ms);

CREATE INDEX idx_relay_messages_inbox ON relay_messages(to_principal, state, created_at_ms);

CREATE INDEX idx_relay_messages_project ON relay_messages(project_uuid, state);

CREATE INDEX idx_tickets_project_card ON dispute_tickets(project_uuid, card_id);

CREATE INDEX idx_tickets_project_created ON dispute_tickets(project_uuid, created_at DESC);
