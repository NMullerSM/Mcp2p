"""
MCP2P Local Storage Engine
Persistencia Local-First con SQLite en modo WAL (Write-Ahead Logging).
Diseñado para lecturas concurrentes sin bloqueo y escrituras ACID transaccionales.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional


DEFAULT_DB_PATH = os.environ.get("MCP2P_DB_PATH", "mcp2p_local.db")


class DatabaseManager:
    """Gestor de base de datos local SQLite con soporte WAL y bloqueo de concurrencia."""

    _instance: Optional[DatabaseManager] = None
    _lock = threading.Lock()

    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()
        self._initialize_schema()

    @classmethod
    def get_instance(cls, db_path: str = DEFAULT_DB_PATH) -> DatabaseManager:
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(db_path)
            return cls._instance

    @contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Provee una conexión con configuración pragmática optimizada para alta velocidad."""
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=10.0,
            check_same_thread=False
        )
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute("PRAGMA busy_timeout = 5000;")
            yield conn
        finally:
            conn.close()

    def _initialize_schema(self) -> None:
        """Inicializa todas las tablas del enjambre P2P si no existen."""
        with self._write_lock, self.get_connection() as conn:
            with conn:
                conn.executescript("""
                -- 1. Nodos del Enjambre P2P (Adaptable a N participantes)
                CREATE TABLE IF NOT EXISTS swarm_nodes (
                    node_uuid TEXT PRIMARY KEY,
                    developer_name TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    public_key TEXT,
                    reviewer_sla_hours INTEGER DEFAULT 72,
                    status TEXT CHECK(status IN ('online', 'offline', 'busy')) DEFAULT 'offline',
                    last_heartbeat DATETIME,
                    p2p_endpoint TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                -- 2. Tarjetas Kanban con Leases de Concurrencia
                CREATE TABLE IF NOT EXISTS kanban_cards (
                    card_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    status TEXT CHECK(status IN ('backlog', 'in_progress', 'in_review', 'disputed', 'merged', 'consolidated')) NOT NULL DEFAULT 'backlog',
                    assignee_node_uuid TEXT,
                    lease_expires_at DATETIME,
                    branch_ref TEXT,
                    commit_refs_json TEXT DEFAULT '[]',
                    content_hash TEXT DEFAULT '',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (assignee_node_uuid) REFERENCES swarm_nodes(node_uuid)
                );

                -- 3. Canal A2A y Manifiestos Semánticos
                CREATE TABLE IF NOT EXISTS a2a_messages (
                    message_uuid TEXT PRIMARY KEY,
                    sender_node_uuid TEXT NOT NULL,
                    intent TEXT CHECK(intent IN ('INFORMATIVE', 'COORDINATION', 'SEMANTIC_COLLISION', 'SUMMARY_SYNTHESIS')) NOT NULL,
                    card_id TEXT,
                    payload_rfc_yaml TEXT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                -- 4. Tickets de Disputa y Arbitraje del Jurado
                CREATE TABLE IF NOT EXISTS dispute_tickets (
                    ticket_id TEXT PRIMARY KEY,
                    card_id TEXT,
                    title TEXT NOT NULL,
                    collision_reason TEXT NOT NULL,
                    contender_a_node TEXT NOT NULL,
                    contender_b_node TEXT NOT NULL,
                    proposal_a_ref TEXT NOT NULL,
                    proposal_b_ref TEXT NOT NULL,
                    status TEXT CHECK(status IN ('open', 'provisional_ruling', 'ratified', 'challenged', 'resolved_hitl')) NOT NULL DEFAULT 'open',
                    winning_quadrant TEXT CHECK(winning_quadrant IN ('OPTION_A', 'OPTION_B', 'ASYMMETRIC_HYBRID', 'THIRD_WAY_C')),
                    provisional_branch TEXT,
                    objection_deadline DATETIME,
                    final_adr_path TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                -- 5. Votos Cardinales Borda del Jurado (0 a 100 por dimensión)
                CREATE TABLE IF NOT EXISTS jury_borda_scores (
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

                -- 6. Cola Outbox Append-Only para Sincronización Git
                CREATE TABLE IF NOT EXISTS outbox_events (
                    event_uuid TEXT PRIMARY KEY,
                    node_uuid TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_raw TEXT NOT NULL,
                    sync_status TEXT CHECK(sync_status IN ('pending', 'flushed_to_branch', 'acked')) DEFAULT 'pending',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                -- Índices de ultra alta velocidad (<1ms)
                CREATE INDEX IF NOT EXISTS idx_cards_status ON kanban_cards(status);
                CREATE INDEX IF NOT EXISTS idx_a2a_intent ON a2a_messages(intent);
                CREATE INDEX IF NOT EXISTS idx_outbox_pending ON outbox_events(sync_status, created_at);
                """)

    # ==========================
    # Métodos de Tarjetas Kanban
    # ==========================

    def get_cards(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """Recupera tarjetas Kanban con o sin filtro de estado."""
        with self.get_connection() as conn:
            if status and status != "all":
                cur = conn.execute("SELECT * FROM kanban_cards WHERE status = ? ORDER BY updated_at DESC;", (status,))
            else:
                cur = conn.execute("SELECT * FROM kanban_cards ORDER BY updated_at DESC;")
            return [dict(row) for row in cur.fetchall()]

    def create_card(self, card_id: str, title: str, description: str = "") -> Dict[str, Any]:
        """Crea una nueva tarjeta en estado 'backlog' a costo cero de tokens."""
        now = datetime.now(timezone.utc).isoformat()
        with self._write_lock, self.get_connection() as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO kanban_cards (card_id, title, description, status, created_at, updated_at)
                    VALUES (?, ?, ?, 'backlog', ?, ?);
                    """,
                    (card_id, title, description, now, now)
                )
        return self.get_card_by_id(card_id) or {}

    def get_card_by_id(self, card_id: str) -> Optional[Dict[str, Any]]:
        """Obtiene una tarjeta por su ID unívoco."""
        with self.get_connection() as conn:
            cur = conn.execute("SELECT * FROM kanban_cards WHERE card_id = ?;", (card_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def claim_card_lease(self, card_id: str, node_uuid: str, duration_minutes: int = 30) -> Dict[str, Any]:
        """
        Adquiere una tarjeta atómicamente asignando un lease con expiración temporal.
        Previene colisiones y condiciones de carrera entre agentes concurrentes.
        """
        now = datetime.now(timezone.utc)
        expires_at = datetime.fromtimestamp(now.timestamp() + (duration_minutes * 60), tz=timezone.utc).isoformat()
        now_str = now.isoformat()

        with self._write_lock, self.get_connection() as conn:
            with conn:
                # Comprobar si ya existe un lease activo no expirado por otro nodo
                cur = conn.execute(
                    """
                    SELECT assignee_node_uuid, lease_expires_at, status 
                    FROM kanban_cards WHERE card_id = ?;
                    """,
                    (card_id,)
                )
                card = cur.fetchone()
                if not card:
                    raise ValueError(f"Tarjeta no encontrada: {card_id}")

                current_assignee = card["assignee_node_uuid"]
                current_expiry = card["lease_expires_at"]

                if current_assignee and current_assignee != node_uuid and current_expiry:
                    # Verificar si el lease sigue vigente
                    expiry_dt = datetime.fromisoformat(current_expiry)
                    if expiry_dt > now:
                        raise PermissionError(
                            f"La tarjeta está bloqueada por el nodo {current_assignee} hasta {current_expiry}"
                        )

                # Asignar lease atómicamente
                conn.execute(
                    """
                    UPDATE kanban_cards 
                    SET status = 'in_progress', assignee_node_uuid = ?, lease_expires_at = ?, updated_at = ?
                    WHERE card_id = ?;
                    """,
                    (node_uuid, expires_at, now_str, card_id)
                )

        return self.get_card_by_id(card_id) or {}

    def update_card_status(
        self,
        card_id: str,
        new_status: str,
        branch_ref: Optional[str] = None
    ) -> Dict[str, Any]:
        """Actualiza el estado de una tarjeta."""
        now_str = datetime.now(timezone.utc).isoformat()
        with self._write_lock, self.get_connection() as conn:
            with conn:
                if branch_ref:
                    conn.execute(
                        """
                        UPDATE kanban_cards 
                        SET status = ?, branch_ref = ?, updated_at = ?
                        WHERE card_id = ?;
                        """,
                        (new_status, branch_ref, now_str, card_id)
                    )
                else:
                    conn.execute(
                        """
                        UPDATE kanban_cards 
                        SET status = ?, updated_at = ?
                        WHERE card_id = ?;
                        """,
                        (new_status, now_str, card_id)
                    )
        return self.get_card_by_id(card_id) or {}

    # ==========================
    # Métodos de Nodos del Enjambre
    # ==========================

    def register_or_update_node(
        self,
        node_uuid: str,
        developer_name: str,
        agent_id: str,
        reviewer_sla_hours: int = 72,
        status: str = "online",
        p2p_endpoint: Optional[str] = None
    ) -> Dict[str, Any]:
        """Registra o actualiza el pulso de un nodo colaborador."""
        now_str = datetime.now(timezone.utc).isoformat()
        with self._write_lock, self.get_connection() as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO swarm_nodes (node_uuid, developer_name, agent_id, reviewer_sla_hours, status, last_heartbeat, p2p_endpoint)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(node_uuid) DO UPDATE SET
                        developer_name = excluded.developer_name,
                        agent_id = excluded.agent_id,
                        reviewer_sla_hours = excluded.reviewer_sla_hours,
                        status = excluded.status,
                        last_heartbeat = excluded.last_heartbeat,
                        p2p_endpoint = excluded.p2p_endpoint;
                    """,
                    (node_uuid, developer_name, agent_id, reviewer_sla_hours, status, now_str, p2p_endpoint)
                )
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM swarm_nodes WHERE node_uuid = ?;", (node_uuid,)).fetchone()
            return dict(row) if row else {}

    def get_swarm_nodes(self) -> List[Dict[str, Any]]:
        """Obtiene la lista de todos los nodos del enjambre registrados."""
        with self.get_connection() as conn:
            cur = conn.execute("SELECT * FROM swarm_nodes ORDER BY last_heartbeat DESC;")
            return [dict(row) for row in cur.fetchall()]

    # ==========================
    # Mensajería A2A y Disputas
    # ==========================

    def log_a2a_message(
        self,
        message_uuid: str,
        sender_node_uuid: str,
        intent: str,
        payload_rfc_yaml: str,
        card_id: Optional[str] = None
    ) -> None:
        """Registra un mensaje o evento del canal Agente a Agente."""
        with self._write_lock, self.get_connection() as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO a2a_messages (message_uuid, sender_node_uuid, intent, card_id, payload_rfc_yaml)
                    VALUES (?, ?, ?, ?, ?);
                    """,
                    (message_uuid, sender_node_uuid, intent, card_id, payload_rfc_yaml)
                )

    def get_recent_a2a_messages(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Obtiene los mensajes recientes para el monitor de telemetría de la UI."""
        with self.get_connection() as conn:
            cur = conn.execute(
                """
                SELECT m.*, n.developer_name, n.agent_id
                FROM a2a_messages m
                LEFT JOIN swarm_nodes n ON m.sender_node_uuid = n.node_uuid
                ORDER BY m.created_at DESC LIMIT ?;
                """,
                (limit,)
            )
            return [dict(row) for row in cur.fetchall()]
