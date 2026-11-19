"""
MCP2P Local Storage Engine
Persistencia Local-First con SQLite en modo WAL (Write-Ahead Logging).
Subsistema Multi-Proyecto con Aislamiento Estricto de Doble Repositorio (app y governance).
"""

from __future__ import annotations

import os
import json
import logging
import queue
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple


# EGM-55: identidad y rutas vienen del paquete neutral `src.runtime`, no de la capa `app`.
from src.runtime.identity import local_node_uuid
from src.runtime.paths import get_default_db_path
from src.storage.validators import validate_safe_id

logger = logging.getLogger("mcp2p.storage.db")


def _default_workspace_effects() -> Tuple[Any, Any]:
    """Cableado por defecto de los efectos en disco (OKF real + git) cuando nadie inyecta nada.

    Import diferido a propósito: este módulo no depende estáticamente de OKF ni de git (EGM-55);
    el dueño del cableado es `src.adapters.provisioning.defaults`.
    """
    from src.adapters.provisioning.defaults import build_default_workspace_effects

    return build_default_workspace_effects()


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB_PATH = os.environ.get("MCP2P_DB_PATH", get_default_db_path())


def resolve_db_path(db_path: Optional[str | Path] = None) -> Path:
    """
    Resuelve la ruta de la base de datos SQLite en tiempo de ejecución (EGM-45).
    Prioridad: argumento explícito -> variable de entorno MCP2P_DB_PATH -> get_default_db_path().
    """
    if db_path is not None:
        raw = str(db_path).strip()
        if raw:
            return Path(raw).resolve()
    env_path = os.environ.get("MCP2P_DB_PATH", "").strip()
    if env_path:
        return Path(env_path).resolve()
    return Path(get_default_db_path()).resolve()

# Umbrales de rate-limit para solicitudes de colaboración (join requests) P2P.
# Clave de identidad: public_key Ed25519 persistente del solicitante (nunca node_uuid
# ni github_username, que pueden colisionar o cambiar trivialmente).
_JOIN_LOCK_THRESHOLD_SHORT = 3   # a partir de este número de rechazos, bloqueo corto
_JOIN_LOCK_THRESHOLD_LONG = 5    # a partir de este número de rechazos, bloqueo largo
_JOIN_LOCK_DURATION_SHORT = timedelta(minutes=30)
_JOIN_LOCK_DURATION_LONG = timedelta(hours=24)
_JOIN_HOURLY_CAP = 3            # máximo de intentos permitidos dentro de la ventana de 1 hora
_JOIN_HOURLY_WINDOW = timedelta(hours=1)


def _compute_lock_until(
    rejection_count: int,
    last_rejected_at: Optional[datetime],
    now: datetime
) -> Optional[datetime]:
    """
    Calcula el instante (UTC) hasta el cual un solicitante permanece bloqueado para
    volver a pedir unirse a un proyecto, en función de su historial de rechazos.

    - <= 2 rechazos: sin bloqueo.
    - 3-4 rechazos: bloqueo de 30 minutos desde el último rechazo.
    - >= 5 rechazos: bloqueo de 24 horas desde el último rechazo.

    Devuelve None si no hay bloqueo vigente (incluyendo el caso en que la ventana
    de bloqueo ya expiró).
    """
    if rejection_count < _JOIN_LOCK_THRESHOLD_SHORT or last_rejected_at is None:
        return None

    if rejection_count < _JOIN_LOCK_THRESHOLD_LONG:
        duration = _JOIN_LOCK_DURATION_SHORT
    else:
        duration = _JOIN_LOCK_DURATION_LONG

    lock_until = last_rejected_at + duration
    if now > lock_until:
        return None
    return lock_until


def _hourly_cap_exceeded(
    rejection_count: int,
    request_window_start: Optional[datetime],
    request_window_count: int,
    now: datetime
) -> bool:
    """
    True si el solicitante ha superado el máximo de intentos de unión permitidos
    dentro de la ventana deslizante de 1 hora. Solo se aplica una vez que el
    solicitante acumula al menos `_JOIN_LOCK_THRESHOLD_SHORT` rechazos previos.
    """
    if rejection_count < _JOIN_LOCK_THRESHOLD_SHORT:
        return False
    if request_window_start is None:
        return False
    if now - request_window_start > _JOIN_HOURLY_WINDOW:
        return False
    return request_window_count > _JOIN_HOURLY_CAP


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    """Convierte una cadena ISO8601 (o None) en un datetime aware UTC, o None."""
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class DatabaseManager:
    """Gestor de base de datos local SQLite con pool WAL LIFO de 4 conexiones + ancla read-only."""

    _instance: Optional[DatabaseManager] = None
    _lock = threading.RLock()
    _POOL_MAX_SIZE: int = 4

    def __init__(
        self,
        db_path: Optional[str | Path] = None,
        *,
        card_projector: Optional[Any] = None,
        project_provisioner: Optional[Any] = None,
    ) -> None:
        """`card_projector` (`CardProjectionPort`) y `project_provisioner` (`ProjectProvisioningPort`)
        reciben los efectos en disco que antes estaban aquí dentro (EGM-55). Si no se inyectan se
        usa el cableado por defecto, que conserva el comportamiento histórico: toda mutación de
        tarjeta escribe su YAML OKF y `create_project` andamia OKF y repos git."""
        self._card_projector = card_projector
        self._project_provisioner = project_provisioner
        self.db_path = resolve_db_path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()
        self._pool_lock = threading.Lock()
        self._anchor_lock = threading.Lock()
        self._pool: queue.LifoQueue[sqlite3.Connection] = queue.LifoQueue(maxsize=self._POOL_MAX_SIZE)
        self._created_conns: int = 0
        self._closed: bool = False
        self._thread_local = threading.local()
        self._anchor_conn: Optional[sqlite3.Connection] = None
        self._fp_cache: Dict[str, Tuple[int, Tuple[Any, ...]]] = {}
        self._conn_connect_fn = sqlite3.connect

        self._initialize_schema()
        self._anchor_conn = self._create_anchor_connection()
        self._ensure_default_project()

    def configure_workspace_effects(
        self,
        *,
        card_projector: Optional[Any] = None,
        project_provisioner: Optional[Any] = None,
    ) -> None:
        """Sustituye en caliente los colaboradores de efectos en disco (solo los que se pasan)."""
        if card_projector is not None:
            self._card_projector = card_projector
        if project_provisioner is not None:
            self._project_provisioner = project_provisioner

    def _workspace_effects(self) -> Tuple[Any, Any]:
        if self._card_projector is None or self._project_provisioner is None:
            projector, provisioner = _default_workspace_effects()
            if self._card_projector is None:
                self._card_projector = projector
            if self._project_provisioner is None:
                self._project_provisioner = provisioner
        return self._card_projector, self._project_provisioner

    @classmethod
    def get_instance(cls, db_path: Optional[str | Path] = None) -> DatabaseManager:
        """
        Retorna la instancia singleton de DatabaseManager.
        Si se especifica explícitamente una ruta distinta (p. ej. en suites de tests automatizados
        con bases de datos temporales), se re-instancia el singleton para garantizar aislamiento.
        """
        with cls._lock:
            resolved = resolve_db_path(db_path)
            if cls._instance is None or cls._instance._closed or cls._instance.db_path != resolved:
                if cls._instance is not None and not cls._instance._closed:
                    try:
                        cls._instance.close()
                    except Exception:
                        pass
                cls._instance = cls(str(resolved))
            return cls._instance

    def _create_pooled_connection(self) -> sqlite3.Connection:
        """Abre una conexión SQLite configurada para WAL y alta concurrencia."""
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=5.0,
            check_same_thread=False
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        return conn

    def _create_anchor_connection(self) -> sqlite3.Connection:
        """Abre una conexión ancla persistente de solo lectura para anclar -shm y consultar data_version."""
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=5.0,
            check_same_thread=False
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        conn.execute("PRAGMA query_only = ON;")
        return conn

    @contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Provee una conexión del pool LIFO acotado (máx 4) con rollback defensivo al devolverla."""
        if self._closed:
            raise RuntimeError("DatabaseManager ya fue cerrado")

        current_connect = sqlite3.connect
        if current_connect is not self._conn_connect_fn:
            with self._pool_lock:
                if current_connect is not self._conn_connect_fn:
                    while True:
                        try:
                            old_conn = self._pool.get_nowait()
                        except queue.Empty:
                            break
                        try:
                            old_conn.close()
                        except sqlite3.Error:
                            pass
                    self._created_conns = 0
                    self._conn_connect_fn = current_connect

        depth = getattr(self._thread_local, "conn_depth", 0)
        ephemeral = False
        conn: Optional[sqlite3.Connection] = None

        if depth > 0:
            # Llamada reentrante desde el mismo hilo: usar conexión efímera para evitar deadlock del pool
            conn = self._create_pooled_connection()
            ephemeral = True
        else:
            try:
                conn = self._pool.get_nowait()
            except queue.Empty:
                with self._pool_lock:
                    if self._created_conns < self._POOL_MAX_SIZE:
                        conn = self._create_pooled_connection()
                        self._created_conns += 1
                if conn is None:
                    try:
                        conn = self._pool.get(timeout=5.0)
                    except queue.Empty:
                        conn = self._create_pooled_connection()
                        ephemeral = True

        self._thread_local.conn_depth = depth + 1
        try:
            yield conn
        finally:
            self._thread_local.conn_depth = depth
            if conn is not None:
                if conn.in_transaction:
                    try:
                        conn.rollback()
                    except sqlite3.Error:
                        pass
                if ephemeral or self._closed:
                    try:
                        conn.close()
                    except sqlite3.Error:
                        pass
                else:
                    try:
                        self._pool.put_nowait(conn)
                    except queue.Full:
                        try:
                            conn.close()
                        except sqlite3.Error:
                            pass

    def close(self) -> None:
        """Cierra limpiamente las conexiones del pool LIFO y la conexión ancla read-only."""
        with self._pool_lock:
            self._closed = True
            while True:
                try:
                    c = self._pool.get_nowait()
                except queue.Empty:
                    break
                try:
                    if c.in_transaction:
                        c.rollback()
                    c.close()
                except sqlite3.Error:
                    pass
            self._created_conns = 0

        with self._anchor_lock:
            if self._anchor_conn is not None:
                try:
                    self._anchor_conn.close()
                except sqlite3.Error:
                    pass
                self._anchor_conn = None

        self._fp_cache.clear()
        with DatabaseManager._lock:
            if DatabaseManager._instance is self:
                DatabaseManager._instance = None

    def _initialize_schema(self) -> None:
        """Inicializa todas las tablas del enjambre P2P y proyectos."""
        with self._write_lock, self.get_connection() as conn:
            with conn:
                # Comprobar y migrar tablas existentes si falta project_uuid o acceptance_criteria o related_ids_json o discarded
                cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='kanban_cards';")
                if cur.fetchone():
                    cols = [row["name"] for row in conn.execute("PRAGMA table_info(kanban_cards);").fetchall()]
                    if "project_uuid" not in cols:
                        # Recrear tabla para soportar primary key compuesta
                        conn.executescript("""
                        DROP TABLE IF EXISTS kanban_cards;
                        DROP TABLE IF EXISTS a2a_messages;
                        DROP TABLE IF EXISTS dispute_tickets;
                        DROP TABLE IF EXISTS outbox_events;
                        """)
                    else:
                        if "acceptance_criteria" not in cols:
                            conn.execute("ALTER TABLE kanban_cards ADD COLUMN acceptance_criteria TEXT DEFAULT '';")
                        if "related_ids_json" not in cols:
                            conn.execute("ALTER TABLE kanban_cards ADD COLUMN related_ids_json TEXT DEFAULT '[]';")
                        sql_schema = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='kanban_cards';").fetchone()[0]
                        if "'discarded'" not in sql_schema or "'rejected'" not in sql_schema:
                            conn.executescript("""
                            CREATE TABLE IF NOT EXISTS kanban_cards_dg_tmp (
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
                            INSERT INTO kanban_cards_dg_tmp (card_id, project_uuid, title, description, acceptance_criteria, status, assignee_node_uuid, lease_expires_at, branch_ref, commit_refs_json, content_hash, related_ids_json, created_at, updated_at)
                            SELECT card_id, project_uuid, title, description, COALESCE(acceptance_criteria, ''), status, assignee_node_uuid, lease_expires_at, branch_ref, commit_refs_json, content_hash, COALESCE(related_ids_json, '[]'), created_at, updated_at FROM kanban_cards;
                            DROP TABLE kanban_cards;
                            ALTER TABLE kanban_cards_dg_tmp RENAME TO kanban_cards;
                            """)

                    # Migración para a2a_messages (soporte para DIALOGUE y CHECKPOINT_COMPACT, session_id y seq)
                    a2a_info = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='a2a_messages';").fetchone()
                    if a2a_info and "'DIALOGUE'" not in a2a_info[0]:
                        conn.executescript("""
                        CREATE TABLE IF NOT EXISTS a2a_messages_dg_tmp (
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
                        INSERT INTO a2a_messages_dg_tmp (message_uuid, project_uuid, sender_node_uuid, intent, card_id, payload_rfc_yaml, created_at)
                        SELECT message_uuid, project_uuid, sender_node_uuid, intent, card_id, payload_rfc_yaml, created_at FROM a2a_messages;
                        DROP TABLE a2a_messages;
                        ALTER TABLE a2a_messages_dg_tmp RENAME TO a2a_messages;
                        """)

                    a2a_cols = [c["name"] for c in conn.execute("PRAGMA table_info(a2a_messages);").fetchall()]
                    if a2a_cols:
                        added_session_cols = False
                        if "session_id" not in a2a_cols:
                            conn.execute("ALTER TABLE a2a_messages ADD COLUMN session_id TEXT DEFAULT '';")
                            added_session_cols = True
                        if "seq" not in a2a_cols:
                            conn.execute("ALTER TABLE a2a_messages ADD COLUMN seq INTEGER DEFAULT 0;")
                            added_session_cols = True
                        if added_session_cols:
                            rows = conn.execute(
                                "SELECT message_uuid, payload_rfc_yaml FROM a2a_messages WHERE session_id = '' OR session_id IS NULL;"
                            ).fetchall()
                            for r in rows:
                                try:
                                    p_obj = json.loads(r["payload_rfc_yaml"])
                                    if isinstance(p_obj, dict) and p_obj.get("session_id"):
                                        s_id = str(p_obj.get("session_id"))
                                        s_seq = int(p_obj.get("seq") or p_obj.get("turn") or 0)
                                        conn.execute(
                                            "UPDATE a2a_messages SET session_id = ?, seq = ? WHERE message_uuid = ?;",
                                            (s_id, s_seq, r["message_uuid"])
                                        )
                                except Exception:
                                    pass

                    # Migración para projects.default_sla_hours, okf_alignment_status, alignment_dossier_markdown
                    p_info = conn.execute("PRAGMA table_info(projects);").fetchall()
                    p_cols = [c["name"] for c in p_info]
                    if "default_sla_hours" not in p_cols:
                        conn.execute("ALTER TABLE projects ADD COLUMN default_sla_hours INTEGER DEFAULT 24;")
                    if "okf_alignment_status" not in p_cols:
                        conn.execute("ALTER TABLE projects ADD COLUMN okf_alignment_status TEXT DEFAULT 'aligned_okf';")
                    if "alignment_dossier_markdown" not in p_cols:
                        conn.execute("ALTER TABLE projects ADD COLUMN alignment_dossier_markdown TEXT DEFAULT '';")
                    if "join_status" not in p_cols:
                        conn.execute("ALTER TABLE projects ADD COLUMN join_status TEXT DEFAULT 'active';")

                    # Migración para dispute_tickets (Síntesis Tercera Vía, Matriz Neutral, Árbitro y EGM)
                    dt_info = conn.execute("PRAGMA table_info(dispute_tickets);").fetchall()
                    dt_cols = [c["name"] for c in dt_info]
                    if dt_cols:
                        if "proposal_c_ref" not in dt_cols:
                            conn.execute("ALTER TABLE dispute_tickets ADD COLUMN proposal_c_ref TEXT DEFAULT '';")
                        if "jury_matrix_json" not in dt_cols:
                            conn.execute("ALTER TABLE dispute_tickets ADD COLUMN jury_matrix_json TEXT DEFAULT '[]';")
                        if "escalation_recommendation" not in dt_cols:
                            conn.execute("ALTER TABLE dispute_tickets ADD COLUMN escalation_recommendation TEXT DEFAULT '';")
                        if "resolution_mode" not in dt_cols:
                            conn.execute("ALTER TABLE dispute_tickets ADD COLUMN resolution_mode TEXT DEFAULT '';")
                        if "arbiter_node_uuid" not in dt_cols:
                            conn.execute("ALTER TABLE dispute_tickets ADD COLUMN arbiter_node_uuid TEXT DEFAULT '';")
                        if "injection_suspected" not in dt_cols:
                            conn.execute("ALTER TABLE dispute_tickets ADD COLUMN injection_suspected INTEGER DEFAULT 0;")

                    # Migración para human_jury_votes (autenticación Ed25519)
                    cur_hj = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='human_jury_votes';")
                    if cur_hj.fetchone():
                        hj_info = conn.execute("PRAGMA table_info(human_jury_votes);").fetchall()
                        hj_cols = [c["name"] for c in hj_info]
                        if "public_key" not in hj_cols:
                            conn.execute("ALTER TABLE human_jury_votes ADD COLUMN public_key TEXT DEFAULT '';")
                        if "signed_at" not in hj_cols:
                            conn.execute("ALTER TABLE human_jury_votes ADD COLUMN signed_at TEXT DEFAULT '';")
                        if "content_sha256" not in hj_cols:
                            conn.execute("ALTER TABLE human_jury_votes ADD COLUMN content_sha256 TEXT DEFAULT '';")
                        if "project_uuid" not in hj_cols:
                            conn.execute("ALTER TABLE human_jury_votes ADD COLUMN project_uuid TEXT DEFAULT '';")

                    # Migración para swarm_nodes.node_kind (distingue humano vs. agente IA)
                    cur_sn = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='swarm_nodes';")
                    if cur_sn.fetchone():
                        sn_cols = [c["name"] for c in conn.execute("PRAGMA table_info(swarm_nodes);").fetchall()]
                        if "node_kind" not in sn_cols:
                            conn.execute("ALTER TABLE swarm_nodes ADD COLUMN node_kind TEXT DEFAULT 'agent';")

                    # Migración para project_collaborators (rate-limit de solicitudes de unión HITL)
                    cur_pc = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='project_collaborators';")
                    if cur_pc.fetchone():
                        pc_cols = [c["name"] for c in conn.execute("PRAGMA table_info(project_collaborators);").fetchall()]
                        if "rejection_count" not in pc_cols:
                            conn.execute("ALTER TABLE project_collaborators ADD COLUMN rejection_count INTEGER DEFAULT 0;")
                        if "last_rejected_at" not in pc_cols:
                            conn.execute("ALTER TABLE project_collaborators ADD COLUMN last_rejected_at TEXT;")
                        if "request_window_start" not in pc_cols:
                            conn.execute("ALTER TABLE project_collaborators ADD COLUMN request_window_start TEXT;")
                        if "request_window_count" not in pc_cols:
                            conn.execute("ALTER TABLE project_collaborators ADD COLUMN request_window_count INTEGER DEFAULT 0;")

                conn.executescript("""
                -- 1. Subsistema de Proyectos (Multi-Tenant Local)
                CREATE TABLE IF NOT EXISTS projects (
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

                -- 2. Nodos del Enjambre P2P (Adaptable a N participantes)
                CREATE TABLE IF NOT EXISTS swarm_nodes (
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

                -- 3. Tarjetas Kanban Vinculadas a Proyecto y Leases
                CREATE TABLE IF NOT EXISTS kanban_cards (
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

                -- 4. Canal A2A y Manifiestos Semánticos por Proyecto
                CREATE TABLE IF NOT EXISTS a2a_messages (
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

                -- 5. Tickets de Disputa y Arbitraje del Jurado por Proyecto
                CREATE TABLE IF NOT EXISTS dispute_tickets (
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

                -- 6. Votos Cardinales Borda del Jurado (0 a 100 por dimensión)
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

                -- 6b. Votos Humanos de Juicio de Honor (Peso 2.0 frente a 1.5 de Agentes)
                CREATE TABLE IF NOT EXISTS human_jury_votes (
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

                -- 7. Cola Outbox Append-Only para Sincronización Git
                CREATE TABLE IF NOT EXISTS outbox_events (
                    event_uuid TEXT PRIMARY KEY,
                    project_uuid TEXT NOT NULL,
                    node_uuid TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_raw TEXT NOT NULL,
                    sync_status TEXT CHECK(sync_status IN ('pending', 'flushed_to_branch', 'acked')) DEFAULT 'pending',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (project_uuid) REFERENCES projects(project_uuid) ON DELETE CASCADE
                );

                -- 8. Colaboradores de Proyecto y Permisos HITL
                CREATE TABLE IF NOT EXISTS project_collaborators (
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

                -- 8b. Árbitros Humanos Externos Pre-registrados (EGM-01)
                CREATE TABLE IF NOT EXISTS project_arbiters (
                    project_uuid TEXT NOT NULL,
                    node_uuid TEXT NOT NULL,
                    registered_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (project_uuid, node_uuid),
                    FOREIGN KEY (project_uuid) REFERENCES projects(project_uuid) ON DELETE CASCADE
                );

                -- 8c. Registro de Entrega de Votación y Ventana de Gracia (EGM-01)
                CREATE TABLE IF NOT EXISTS dispute_vote_delivery (
                    ticket_id TEXT NOT NULL,
                    node_uuid TEXT NOT NULL,
                    delivered_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (ticket_id, node_uuid),
                    FOREIGN KEY (ticket_id) REFERENCES dispute_tickets(ticket_id) ON DELETE CASCADE
                );

                -- 9. Configuraciones Locales de la Aplicación (Key-Value)
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                -- 10. Configuraciones de Conectores/Extensiones y Seeds P2P
                CREATE TABLE IF NOT EXISTS extension_settings (
                    namespace TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (namespace, key)
                );

                CREATE TABLE IF NOT EXISTS p2p_seeds (
                    project_uuid TEXT NOT NULL,
                    seed_url TEXT NOT NULL,
                    label TEXT DEFAULT '',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (project_uuid, seed_url)
                );

                -- Deduplicación defensiva antes de crear índices UNIQUE (EGM-38)
                DELETE FROM jury_borda_scores
                WHERE rowid NOT IN (
                    SELECT MAX(rowid)
                    FROM jury_borda_scores
                    GROUP BY ticket_id, juror_id, evaluated_option
                );

                DELETE FROM human_jury_votes
                WHERE rowid NOT IN (
                    SELECT MIN(rowid)
                    FROM human_jury_votes
                    GROUP BY ticket_id, node_uuid
                );

                -- Índices de ultra alta velocidad (<1ms) y unicidad (EGM-33 / EGM-34 / EGM-38)
                CREATE INDEX IF NOT EXISTS idx_cards_project_status ON kanban_cards(project_uuid, status);
                CREATE INDEX IF NOT EXISTS idx_cards_project_status_updated ON kanban_cards(project_uuid, status, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_a2a_project ON a2a_messages(project_uuid);
                CREATE INDEX IF NOT EXISTS idx_a2a_project_created ON a2a_messages(project_uuid, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_a2a_project_card ON a2a_messages(project_uuid, card_id);
                CREATE INDEX IF NOT EXISTS idx_a2a_project_session_seq ON a2a_messages(project_uuid, session_id, seq);
                CREATE INDEX IF NOT EXISTS idx_tickets_project_created ON dispute_tickets(project_uuid, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_tickets_project_card ON dispute_tickets(project_uuid, card_id);
                CREATE INDEX IF NOT EXISTS idx_outbox_pending ON outbox_events(project_uuid, sync_status, created_at);
                CREATE INDEX IF NOT EXISTS idx_collab_project ON project_collaborators(project_uuid, status);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_borda_unique ON jury_borda_scores(ticket_id, juror_id, evaluated_option);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_human_votes_unique ON human_jury_votes(ticket_id, node_uuid);
                """)
                self._migrate_pane_identities(conn)
                from src.storage.relay_schema import migrate_relay_tables

                migrate_relay_tables(conn)  # Ola 3: bandeja persistente y auditoria del relevo
                from src.storage.operator_schema import migrate_operator_tables

                migrate_operator_tables(conn)  # Ola 4: sesiones del operador ligadas al proceso del harness
                from src.storage.schema_v202 import migrate_v202

                migrate_v202(conn)  # 2.0.2: roles, entrega, sesiones y nodos (cada parte en su módulo)

    @staticmethod
    def _migrate_pane_identities(conn: sqlite3.Connection) -> None:
        """Tabla `pane_identities` (Ola 2): identidad estable y Auto-Relay por (proyecto, ranura de panel).

        Idempotente: crea la tabla si falta y añade las columnas que una version anterior no tuviera. No toca filas.
        `project_uuid = ''` es un panel abierto sin proyecto activo.
        """
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pane_identities (
                project_uuid TEXT NOT NULL,
                pane_slot TEXT NOT NULL,
                node_uuid TEXT NOT NULL UNIQUE,
                auto_relay_enabled INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                last_spawned_at TEXT,
                PRIMARY KEY (project_uuid, pane_slot)
            );
            """
        )
        cols = {row[1] for row in conn.execute("PRAGMA table_info(pane_identities);").fetchall()}
        if "auto_relay_enabled" not in cols:
            conn.execute("ALTER TABLE pane_identities ADD COLUMN auto_relay_enabled INTEGER NOT NULL DEFAULT 0;")
        if "last_spawned_at" not in cols:
            conn.execute("ALTER TABLE pane_identities ADD COLUMN last_spawned_at TEXT;")
        if "worker_mode" not in cols:  # modo trabajador (F2.4): el agente se queda en inbox_wait
            conn.execute("ALTER TABLE pane_identities ADD COLUMN worker_mode INTEGER NOT NULL DEFAULT 0;")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pane_identities_node ON pane_identities(node_uuid);")

    def _ensure_default_project(self) -> None:
        """Asegura la existencia de un proyecto por defecto si la base está vacía."""
        with self.get_connection() as conn:
            cnt = conn.execute("SELECT COUNT(*) as cnt FROM projects;").fetchone()["cnt"]
        if cnt == 0:
            self.create_project(
                name="Proyecto Semilla",
                base_path=str(self.db_path.parent / "workspace" / "semilla"),
                init_git=False
            )

    # ==========================
    # Métodos de Proyectos
    # ==========================

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
        owner_public_key: str = ""
    ) -> Dict[str, Any]:
        """
        Crea un nuevo proyecto en el subsistema.
        Genera UUIDv4 único y estructura de doble repositorio (app_{name} y okf_{name}_{uuid}).
        """
        if project_uuid is not None:
            validate_safe_id(project_uuid, "project_uuid")
        p_uuid = project_uuid or f"proj_{uuid.uuid4().hex[:12]}"
        now_str = datetime.now(timezone.utc).isoformat()

        clean_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name.lower())
        short_id = p_uuid.replace("proj_", "")[:8]
        if not base_path:
            base_dir = self.db_path.parent / "workspace" / f"{clean_name}_{short_id}"
        else:
            base_dir = Path(base_path).resolve()

        app_dir = base_dir / f"app_{clean_name}"
        gov_dir = base_dir / f"okf_{clean_name}_{short_id}"

        # Crear carpetas del doble repositorio
        app_dir.mkdir(parents=True, exist_ok=True)
        gov_dir.mkdir(parents=True, exist_ok=True)

        # EGM-55: estructura OKF y doble repo git los prepara el aprovisionador inyectado.
        _, provisioner = self._workspace_effects()
        provisioner.provision_project(
            name=name,
            project_uuid=p_uuid,
            clean_name=clean_name,
            app_dir=app_dir,
            gov_dir=gov_dir,
            init_git=init_git,
        )

        with self._write_lock, self.get_connection() as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO projects (
                        project_uuid, name, base_path, code_repo_path, gov_repo_path,
                        code_repo_url, gov_repo_url, default_sla_hours, okf_alignment_status,
                        alignment_dossier_markdown, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        p_uuid,
                        name,
                        str(base_dir),
                        str(app_dir),
                        str(gov_dir),
                        code_repo_url,
                        gov_repo_url,
                        reviewer_sla_hours,
                        okf_alignment_status,
                        alignment_dossier_markdown,
                        now_str,
                        now_str
                    )
                )

        local_node = local_node_uuid()
        self.add_or_request_collaborator(
            project_uuid=p_uuid,
            node_uuid=local_node,
            public_key=owner_public_key,
            role="owner",
            status="approved"
        )

        return self.get_project_by_uuid(p_uuid)

    def update_project_alignment(
        self,
        project_uuid: str,
        status: str,
        dossier_markdown: Optional[str] = None
    ) -> bool:
        """Actualiza el estado de alineación OKF y el contenido del dossier Markdown."""
        now_str = datetime.now(timezone.utc).isoformat()
        with self._write_lock, self.get_connection() as conn:
            with conn:
                if dossier_markdown is not None:
                    cur = conn.execute(
                        "UPDATE projects SET okf_alignment_status = ?, alignment_dossier_markdown = ?, updated_at = ? WHERE project_uuid = ?;",
                        (status, dossier_markdown, now_str, project_uuid)
                    )
                else:
                    cur = conn.execute(
                        "UPDATE projects SET okf_alignment_status = ?, updated_at = ? WHERE project_uuid = ?;",
                        (status, now_str, project_uuid)
                    )
                return cur.rowcount > 0

    def update_project_sla(self, project_uuid: str, sla_hours: int) -> bool:
        """Actualiza la ventana de revisión / SLA por defecto del proyecto."""
        now_str = datetime.now(timezone.utc).isoformat()
        with self._write_lock, self.get_connection() as conn:
            with conn:
                cur = conn.execute(
                    "UPDATE projects SET default_sla_hours = ?, updated_at = ? WHERE project_uuid = ?;",
                    (max(1, int(sla_hours)), now_str, project_uuid)
                )
                return cur.rowcount > 0

    def delete_project(self, project_uuid: str) -> bool:
        """
        Elimina un proyecto del subsistema.
        Por restricción CASCADE de SQLite, se eliminan kanban_cards,
        a2a_messages, dispute_tickets y outbox_events asociados.
        """
        with self._write_lock, self.get_connection() as conn:
            with conn:
                cur = conn.execute("DELETE FROM projects WHERE project_uuid = ?;", (project_uuid,))
                return cur.rowcount > 0

    def list_projects(self) -> List[Dict[str, Any]]:
        """Lista todos los proyectos registrados."""
        with self.get_connection() as conn:
            cur = conn.execute("SELECT * FROM projects ORDER BY updated_at DESC;")
            return [dict(row) for row in cur.fetchall()]

    def get_project_by_uuid(self, project_uuid: str) -> Optional[Dict[str, Any]]:
        """Obtiene un proyecto por su UUID."""
        with self.get_connection() as conn:
            cur = conn.execute("SELECT * FROM projects WHERE project_uuid = ?;", (project_uuid,))
            row = cur.fetchone()
            return dict(row) if row else None

    def _get_anchor_data_version(self) -> Optional[int]:
        """Consulta PRAGMA data_version en la conexión ancla read-only para invalidar caché sin escanear tablas."""
        if self._anchor_conn is None or self._closed:
            return None
        with self._anchor_lock:
            if self._anchor_conn is None:
                return None
            row = self._anchor_conn.execute("PRAGMA data_version;").fetchone()
            return int(row[0]) if row else None

    def get_project_sync_fingerprint(self, project_uuid: str) -> Dict[str, Any]:
        """Calcula una huella de sincronización atómica con caché por PRAGMA data_version (EGM-20)."""
        data_ver = self._get_anchor_data_version()
        cached = self._fp_cache.get(project_uuid) if data_ver is not None else None

        if cached is not None and cached[0] == data_ver:
            cards_count, cards_max, a2a_count, a2a_max, tickets_count, tickets_max = cached[1]
        else:
            with self.get_connection() as conn:
                row = conn.execute(
                    """
                    SELECT 
                        COUNT(card_id),
                        COALESCE(MAX(updated_at), ''),
                        (SELECT COUNT(message_uuid) FROM a2a_messages WHERE project_uuid = ?),
                        (SELECT COALESCE(MAX(created_at), '') FROM a2a_messages WHERE project_uuid = ?),
                        (SELECT COUNT(ticket_id) FROM dispute_tickets WHERE project_uuid = ?),
                        (SELECT COALESCE(MAX(created_at), '') FROM dispute_tickets WHERE project_uuid = ?)
                    FROM kanban_cards
                    WHERE project_uuid = ?;
                    """,
                    (project_uuid, project_uuid, project_uuid, project_uuid, project_uuid)
                ).fetchone()
            counts_tuple = tuple(row) if row else (0, "", 0, "", 0, "")
            cards_count, cards_max, a2a_count, a2a_max, tickets_count, tickets_max = counts_tuple
            if data_ver is not None:
                # Releer data_version por si hubo cambio justo antes de nuestra consulta
                post_ver = self._get_anchor_data_version()
                if post_ver == data_ver:
                    self._fp_cache[project_uuid] = (data_ver, counts_tuple)

        # Detección de pulso Git
        proj = self.get_project_by_uuid(project_uuid)
        app_pulse = 0
        gov_pulse = 0
        app_head = ""
        gov_head = ""
        if proj:
            app_p = Path(proj.get("code_repo_path", ""))
            gov_p = Path(proj.get("gov_repo_path", ""))
            if (app_p / ".git_pulse").exists():
                try:
                    app_pulse = int((app_p / ".git_pulse").stat().st_mtime)
                except Exception:
                    pass
            if (gov_p / ".git_pulse").exists():
                try:
                    gov_pulse = int((gov_p / ".git_pulse").stat().st_mtime)
                except Exception:
                    pass
            
            # Refs de HEAD de repositorios
            app_head_file = app_p / ".git" / "HEAD"
            if app_head_file.exists():
                try:
                    app_head = app_head_file.read_text(encoding="utf-8").strip()
                except Exception:
                    pass
            gov_head_file = gov_p / ".git" / "HEAD"
            if gov_head_file.exists():
                try:
                    gov_head = gov_head_file.read_text(encoding="utf-8").strip()
                except Exception:
                    pass

        cards_fp = f"{cards_count}:{cards_max}"
        a2a_fp = f"{a2a_count}:{a2a_max}:{tickets_count}:{tickets_max}"
        git_fp = f"{app_pulse}:{gov_pulse}:{app_head}:{gov_head}"
        fingerprint = f"{cards_fp}:{a2a_fp}:{git_fp}"

        return {
            "success": True,
            "project_uuid": project_uuid,
            "fingerprint": fingerprint,
            "cards_fingerprint": cards_fp,
            "a2a_fingerprint": a2a_fp,
            "git_fingerprint": git_fp,
            "cards_count": cards_count,
            "cards_updated_at": cards_max,
            "a2a_count": a2a_count,
            "a2a_updated_at": a2a_max,
            "tickets_count": tickets_count
        }

    # ==========================
    # Métodos de Tarjetas Kanban
    # ==========================

    def _sync_card_to_okf_yaml(self, project_uuid: str, card_data: Dict[str, Any]) -> None:
        """Proyecta la tarjeta modificada a su YAML en governance/kanban/cards/ (y su commit de
        gobernanza) a través del `CardProjectionPort` inyectado (EGM-55). Un fallo de la proyección
        se registra y nunca revierte la escritura SQL."""
        try:
            proj = self.get_project_by_uuid(project_uuid)
            if not proj or not proj.get("gov_repo_path"):
                return
            gov_dir = Path(proj["gov_repo_path"])
            if not gov_dir.exists():
                return
            projector, _ = self._workspace_effects()
            projector.project_card(gov_dir, card_data)
        except Exception as exc:
            logger.warning(f"Error sincronizando tarjeta {card_data.get('card_id')} a YAML OKF: {exc}")

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
        updated_at: Optional[str] = None
    ) -> Dict[str, Any]:
        """Inserta o actualiza una tarjeta directamente desde un archivo YAML del OKF (sin re-emitir escritura a YAML)."""
        validate_safe_id(card_id, "card_id")
        now = updated_at or datetime.now(timezone.utc).isoformat()
        with self._write_lock, self.get_connection() as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO kanban_cards (
                        card_id, project_uuid, title, description, acceptance_criteria,
                        status, assignee_node_uuid, lease_expires_at, branch_ref,
                        related_ids_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(project_uuid, card_id) DO UPDATE SET
                        title = excluded.title,
                        description = excluded.description,
                        acceptance_criteria = excluded.acceptance_criteria,
                        status = excluded.status,
                        assignee_node_uuid = excluded.assignee_node_uuid,
                        lease_expires_at = excluded.lease_expires_at,
                        branch_ref = excluded.branch_ref,
                        related_ids_json = excluded.related_ids_json,
                        updated_at = excluded.updated_at;
                    """,
                    (
                        card_id, project_uuid, title, description, acceptance_criteria,
                        status, assignee_node_uuid, lease_expires_at, branch_ref,
                        related_ids_json, now, now
                    )
                )
        return self.get_card_by_id(project_uuid, card_id) or {}

    def get_cards(self, project_uuid: str, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """Recupera tarjetas Kanban para un proyecto específico."""
        with self.get_connection() as conn:
            if status and status != "all":
                cur = conn.execute(
                    "SELECT * FROM kanban_cards WHERE project_uuid = ? AND status = ? ORDER BY updated_at DESC;",
                    (project_uuid, status)
                )
            else:
                cur = conn.execute(
                    "SELECT * FROM kanban_cards WHERE project_uuid = ? ORDER BY updated_at DESC;",
                    (project_uuid,)
                )
            return [dict(row) for row in cur.fetchall()]

    def _validate_assignable_node(self, project_uuid: str, node_uuid: str) -> None:
        """
        Verifica que un nodo sea asignable a una tarjeta: debe ser un colaborador aprobado
        del proyecto, registrado como nodo real conectado por MCP (swarm_nodes). Esto
        excluye de forma natural a los subagentes efímeros del jurado del Tribunal de
        Honor, que nunca se registran en swarm_nodes ni en project_collaborators.
        """
        collab = self.get_collaborator(project_uuid, node_uuid)
        if not collab or collab.get("status") != "approved":
            raise ValueError(
                f"'{node_uuid}' no es un colaborador aprobado de este proyecto; no se puede asignar la tarea."
            )

    def create_card(
        self,
        project_uuid: str,
        card_id: str,
        title: str,
        description: str = "",
        acceptance_criteria: str = "",
        assignee_node_uuid: Optional[str] = None
    ) -> Dict[str, Any]:
        """Crea una nueva tarjeta en el backlog asociada a un proyecto y sincroniza a YAML.
        Si se indica assignee_node_uuid, la tarjeta nace ya asignada a ese colaborador."""
        validate_safe_id(card_id, "card_id")
        if assignee_node_uuid:
            self._validate_assignable_node(project_uuid, assignee_node_uuid)
        now = datetime.now(timezone.utc).isoformat()
        with self._write_lock, self.get_connection() as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO kanban_cards (card_id, project_uuid, title, description, acceptance_criteria, status, assignee_node_uuid, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 'backlog', ?, ?, ?);
                    """,
                    (card_id, project_uuid, title, description, acceptance_criteria, assignee_node_uuid or None, now, now)
                )
        card = self.get_card_by_id(project_uuid, card_id) or {}
        self._sync_card_to_okf_yaml(project_uuid, card)
        return card

    def update_card_details(
        self,
        project_uuid: str,
        card_id: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        acceptance_criteria: Optional[str] = None,
        status: Optional[str] = None,
        branch_ref: Optional[str] = None,
        assignee_node_uuid: Optional[str] = None
    ) -> Dict[str, Any]:
        """Actualiza los detalles completos de una tarjeta atómicamente en BEGIN IMMEDIATE y sincroniza a YAML (EGM-38)."""
        now_str = datetime.now(timezone.utc).isoformat()
        with self._write_lock, self.get_connection() as conn:
            with conn:
                conn.execute("BEGIN IMMEDIATE;")
                row = conn.execute(
                    "SELECT * FROM kanban_cards WHERE project_uuid = ? AND card_id = ?;",
                    (project_uuid, card_id)
                ).fetchone()
                if not row:
                    raise ValueError(f"Tarjeta no encontrada: {card_id}")
                card = dict(row)

                new_title = title if title is not None else card["title"]
                new_desc = description if description is not None else card.get("description", "")
                new_crit = acceptance_criteria if acceptance_criteria is not None else card.get("acceptance_criteria", "")
                new_status = status if status is not None else card["status"]
                new_branch = branch_ref if branch_ref is not None else card.get("branch_ref")

                if assignee_node_uuid is None:
                    new_assignee = card.get("assignee_node_uuid")
                elif assignee_node_uuid == "":
                    new_assignee = None
                else:
                    collab_row = conn.execute(
                        "SELECT status FROM project_collaborators WHERE project_uuid = ? AND node_uuid = ?;",
                        (project_uuid, assignee_node_uuid)
                    ).fetchone()
                    if not collab_row or collab_row["status"] != "approved":
                        raise ValueError(
                            f"'{assignee_node_uuid}' no es un colaborador aprobado de este proyecto; no se puede asignar la tarea."
                        )
                    new_assignee = assignee_node_uuid

                conn.execute(
                    """
                    UPDATE kanban_cards
                    SET title = ?, description = ?, acceptance_criteria = ?, status = ?, branch_ref = ?, assignee_node_uuid = ?, updated_at = ?
                    WHERE project_uuid = ? AND card_id = ?;
                    """,
                    (new_title, new_desc, new_crit, new_status, new_branch, new_assignee, now_str, project_uuid, card_id)
                )
                updated_row = conn.execute(
                    "SELECT * FROM kanban_cards WHERE project_uuid = ? AND card_id = ?;",
                    (project_uuid, card_id)
                ).fetchone()
                updated = dict(updated_row) if updated_row else {}

        self._sync_card_to_okf_yaml(project_uuid, updated)
        return updated

    def get_card_by_id(self, project_uuid: str, card_id: str) -> Optional[Dict[str, Any]]:
        """Obtiene una tarjeta por su ID y proyecto."""
        with self.get_connection() as conn:
            cur = conn.execute(
                "SELECT * FROM kanban_cards WHERE project_uuid = ? AND card_id = ?;",
                (project_uuid, card_id)
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def claim_card_lease(self, project_uuid: str, card_id: str, node_uuid: str, duration_minutes: int = 30) -> Dict[str, Any]:
        """Adquiere una tarjeta atómicamente asignando un lease con expiración temporal y sincroniza a YAML."""
        now = datetime.now(timezone.utc)
        expires_at = datetime.fromtimestamp(now.timestamp() + (duration_minutes * 60), tz=timezone.utc).isoformat()
        now_str = now.isoformat()

        with self._write_lock, self.get_connection() as conn:
            with conn:
                # BEGIN IMMEDIATE toma el lock RESERVED de SQLite ANTES de leer, serializando el
                # ciclo leer-comprobar-escribir entre PROCESOS distintos (cada agente MCP es un proceso).
                # self._write_lock solo protege hilos del mismo proceso.
                conn.execute("BEGIN IMMEDIATE;")
                cur = conn.execute(
                    """
                    SELECT assignee_node_uuid, lease_expires_at, status
                    FROM kanban_cards WHERE project_uuid = ? AND card_id = ?;
                    """,
                    (project_uuid, card_id)
                )
                card = cur.fetchone()
                if not card:
                    raise ValueError(f"Tarjeta no encontrada: {card_id} en proyecto {project_uuid}")

                current_assignee = card["assignee_node_uuid"]
                current_expiry = card["lease_expires_at"]
                current_status = card["status"]

                if current_assignee and current_assignee != node_uuid and current_expiry and current_status == "in_progress":
                    expiry_dt = datetime.fromisoformat(current_expiry)
                    if expiry_dt > now:
                        raise PermissionError(
                            f"La tarjeta está bloqueada por el nodo {current_assignee} hasta {current_expiry}"
                        )

                conn.execute(
                    """
                    UPDATE kanban_cards 
                    SET status = 'in_progress', assignee_node_uuid = ?, lease_expires_at = ?, updated_at = ?
                    WHERE project_uuid = ? AND card_id = ?;
                    """,
                    (node_uuid, expires_at, now_str, project_uuid, card_id)
                )

        updated = self.get_card_by_id(project_uuid, card_id) or {}
        self._sync_card_to_okf_yaml(project_uuid, updated)
        return updated

    def release_card_lease(
        self,
        project_uuid: str,
        card_id: str,
        node_uuid: str,
        target_status: str = "in_review",
        branch_ref: Optional[str] = None
    ) -> Dict[str, Any]:
        """Libera o transiciona atómicamente una tarjeta con lease en BEGIN IMMEDIATE (EGM-38)."""
        now = datetime.now(timezone.utc)
        now_str = now.isoformat()
        with self._write_lock, self.get_connection() as conn:
            with conn:
                conn.execute("BEGIN IMMEDIATE;")
                card = conn.execute(
                    """
                    SELECT assignee_node_uuid, lease_expires_at, status
                    FROM kanban_cards WHERE project_uuid = ? AND card_id = ?;
                    """,
                    (project_uuid, card_id)
                ).fetchone()
                if not card:
                    raise ValueError(f"Tarjeta no encontrada: {card_id} en proyecto {project_uuid}")

                current_assignee = card["assignee_node_uuid"]
                current_expiry = card["lease_expires_at"]
                lease_active = False
                if current_assignee and current_assignee != node_uuid and current_expiry:
                    expiry_dt = _parse_iso_datetime(current_expiry)
                    if expiry_dt is not None and expiry_dt > now:
                        lease_active = True
                elif current_assignee and current_assignee != node_uuid and not current_expiry:
                    lease_active = True

                if lease_active and target_status != "disputed":
                    raise PermissionError(
                        f"La tarea está asignada al nodo {current_assignee}, no a {node_uuid}."
                    )

                if target_status == "backlog":
                    conn.execute(
                        """
                        UPDATE kanban_cards
                        SET status = ?, assignee_node_uuid = NULL, lease_expires_at = NULL, branch_ref = NULL, updated_at = ?
                        WHERE project_uuid = ? AND card_id = ?;
                        """,
                        (target_status, now_str, project_uuid, card_id)
                    )
                elif branch_ref is not None:
                    conn.execute(
                        """
                        UPDATE kanban_cards
                        SET status = ?, branch_ref = ?, lease_expires_at = NULL, updated_at = ?
                        WHERE project_uuid = ? AND card_id = ?;
                        """,
                        (target_status, branch_ref, now_str, project_uuid, card_id)
                    )
                else:
                    conn.execute(
                        """
                        UPDATE kanban_cards
                        SET status = ?, lease_expires_at = NULL, updated_at = ?
                        WHERE project_uuid = ? AND card_id = ?;
                        """,
                        (target_status, now_str, project_uuid, card_id)
                    )

                updated_row = conn.execute(
                    "SELECT * FROM kanban_cards WHERE project_uuid = ? AND card_id = ?;",
                    (project_uuid, card_id)
                ).fetchone()
                updated = dict(updated_row) if updated_row else {}

        self._sync_card_to_okf_yaml(project_uuid, updated)
        return updated

    def update_card_status(
        self,
        project_uuid: str,
        card_id: str,
        new_status: str,
        branch_ref: Optional[str] = None
    ) -> Dict[str, Any]:
        """Actualiza el estado de una tarjeta en un proyecto y sincroniza a YAML."""
        now_str = datetime.now(timezone.utc).isoformat()
        with self._write_lock, self.get_connection() as conn:
            with conn:
                if new_status == "backlog":
                    conn.execute(
                        """
                        UPDATE kanban_cards 
                        SET status = ?, assignee_node_uuid = NULL, lease_expires_at = NULL, branch_ref = NULL, updated_at = ?
                        WHERE project_uuid = ? AND card_id = ?;
                        """,
                        (new_status, now_str, project_uuid, card_id)
                    )
                elif branch_ref:
                    conn.execute(
                        """
                        UPDATE kanban_cards 
                        SET status = ?, branch_ref = ?, updated_at = ?
                        WHERE project_uuid = ? AND card_id = ?;
                        """,
                        (new_status, branch_ref, now_str, project_uuid, card_id)
                    )
                else:
                    conn.execute(
                        """
                        UPDATE kanban_cards 
                        SET status = ?, updated_at = ?
                        WHERE project_uuid = ? AND card_id = ?;
                        """,
                        (new_status, now_str, project_uuid, card_id)
                    )
        updated = self.get_card_by_id(project_uuid, card_id) or {}
        self._sync_card_to_okf_yaml(project_uuid, updated)
        return updated

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
        p2p_endpoint: Optional[str] = None,
        node_kind: str = "agent"
    ) -> Dict[str, Any]:
        """Registra o actualiza el pulso de un nodo colaborador."""
        now_str = datetime.now(timezone.utc).isoformat()
        with self._write_lock, self.get_connection() as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO swarm_nodes (node_uuid, developer_name, agent_id, reviewer_sla_hours, status, last_heartbeat, p2p_endpoint, node_kind)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(node_uuid) DO UPDATE SET
                        developer_name = excluded.developer_name,
                        agent_id = excluded.agent_id,
                        reviewer_sla_hours = excluded.reviewer_sla_hours,
                        status = excluded.status,
                        last_heartbeat = excluded.last_heartbeat,
                        p2p_endpoint = excluded.p2p_endpoint,
                        node_kind = excluded.node_kind;
                    """,
                    (node_uuid, developer_name, agent_id, reviewer_sla_hours, status, now_str, p2p_endpoint, node_kind)
                )
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM swarm_nodes WHERE node_uuid = ?;", (node_uuid,))
            r = row.fetchone()
            return dict(r) if r else {}

    def get_swarm_nodes(self) -> List[Dict[str, Any]]:
        """Obtiene la lista de todos los nodos del enjambre registrados."""
        with self.get_connection() as conn:
            cur = conn.execute("SELECT * FROM swarm_nodes ORDER BY last_heartbeat DESC;")
            return [dict(row) for row in cur.fetchall()]

    def get_node_by_uuid(self, node_uuid: str) -> Optional[Dict[str, Any]]:
        """Obtiene la información de un nodo del enjambre por su UUID."""
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM swarm_nodes WHERE node_uuid = ?;", (node_uuid,)).fetchone()
            return dict(row) if row else None

    def mark_node_offline(self, node_uuid: str) -> bool:
        """Marca un nodo del enjambre como offline actualizando su último heartbeat (EGM-22)."""
        now_str = datetime.now(timezone.utc).isoformat()
        with self._write_lock, self.get_connection() as conn:
            with conn:
                cur = conn.execute(
                    "UPDATE swarm_nodes SET status = 'offline', last_heartbeat = ? WHERE node_uuid = ?;",
                    (now_str, node_uuid),
                )
                return cur.rowcount > 0

    # ==========================
    # Mensajería A2A y Disputas
    # ==========================

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
        """Registra un mensaje o evento del canal Agente a Agente dentro de un proyecto."""
        resolved_session = session_id if session_id is not None else ""
        resolved_seq = seq if seq is not None else 0
        if session_id is None or seq is None:
            try:
                parsed = json.loads(payload_rfc_yaml)
                if isinstance(parsed, dict):
                    if session_id is None and parsed.get("session_id"):
                        resolved_session = str(parsed.get("session_id"))
                    if seq is None and (parsed.get("seq") is not None or parsed.get("turn") is not None):
                        resolved_seq = int(parsed.get("seq") or parsed.get("turn") or 0)
            except Exception:
                pass

        with self._write_lock, self.get_connection() as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO a2a_messages (message_uuid, project_uuid, sender_node_uuid, intent, card_id, session_id, seq, payload_rfc_yaml)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (message_uuid, project_uuid, sender_node_uuid, intent, card_id, resolved_session, resolved_seq, payload_rfc_yaml)
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
        """Inserta atómicamente un mensaje de sesión con seq correlativo en BEGIN IMMEDIATE (EGM-34)."""
        msg_uuid = message_uuid or str(uuid.uuid4())
        payload = dict(payload_dict)
        payload["session_id"] = session_id

        with self._write_lock, self.get_connection() as conn:
            with conn:
                conn.execute("BEGIN IMMEDIATE;")
                row = conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) AS max_seq FROM a2a_messages WHERE project_uuid = ? AND session_id = ?;",
                    (project_uuid, session_id)
                ).fetchone()
                next_seq = int(row["max_seq"]) + 1 if row else 1
                if seq_field == "seq":
                    payload["seq"] = next_seq
                payload_str = json.dumps(payload, ensure_ascii=False)
                effective_seq = int(payload.get("seq") or payload.get("turn") or next_seq)

                conn.execute(
                    """
                    INSERT INTO a2a_messages (message_uuid, project_uuid, sender_node_uuid, intent, card_id, session_id, seq, payload_rfc_yaml)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (msg_uuid, project_uuid, sender_node_uuid, intent, card_id, session_id, effective_seq, payload_str)
                )
                if event_type:
                    conn.execute(
                        """
                        INSERT INTO outbox_events (event_uuid, project_uuid, node_uuid, event_type, payload_raw, sync_status)
                        VALUES (?, ?, ?, ?, ?, 'pending');
                        """,
                        (msg_uuid, project_uuid, sender_node_uuid, event_type, payload_str)
                    )

        return {
            "message_uuid": msg_uuid,
            "seq": effective_seq,
            "payload": payload,
            "payload_str": payload_str,
        }

    def get_session_messages(
        self,
        project_uuid: str,
        session_id: str,
        intent: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Recupera todos los mensajes de una sesión ordenados por seq ASC sin límite de ventana (EGM-34)."""
        query = (
            "SELECT m.*, n.developer_name, n.agent_id "
            "FROM a2a_messages m "
            "LEFT JOIN swarm_nodes n ON m.sender_node_uuid = n.node_uuid "
            "WHERE m.project_uuid = ? AND m.session_id = ? "
        )
        params: List[Any] = [project_uuid, session_id]
        if intent:
            query += "AND m.intent = ? "
            params.append(intent)
        query += "ORDER BY m.seq ASC, m.created_at ASC, m.rowid ASC;"
        with self.get_connection() as conn:
            cur = conn.execute(query, tuple(params))
            return [dict(row) for row in cur.fetchall()]

    def get_a2a_message_by_uuid(self, message_uuid: str) -> Optional[Dict[str, Any]]:
        """Obtiene un mensaje A2A específico por su message_uuid."""
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM a2a_messages WHERE message_uuid = ?;",
                (message_uuid,)
            ).fetchone()
            return dict(row) if row else None

    def has_a2a_message(self, message_uuid: str) -> bool:
        """True si existe un mensaje A2A con ese message_uuid."""
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM a2a_messages WHERE message_uuid = ? LIMIT 1;",
                (message_uuid,)
            ).fetchone()
            return row is not None

    def get_recent_a2a_messages(self, project_uuid: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Obtiene los mensajes recientes para el monitor de telemetría de un proyecto."""
        with self.get_connection() as conn:
            cur = conn.execute(
                """
                SELECT m.*, n.developer_name, n.agent_id
                FROM a2a_messages m
                LEFT JOIN swarm_nodes n ON m.sender_node_uuid = n.node_uuid
                WHERE m.project_uuid = ?
                ORDER BY m.created_at DESC, m.rowid DESC LIMIT ?;
                """,
                (project_uuid, limit)
            )
            return [dict(row) for row in cur.fetchall()]

    # ==========================
    # Cola Outbox Append-Only
    # ==========================

    def enqueue_outbox_event(
        self,
        project_uuid: str,
        node_uuid: str,
        event_type: str,
        payload_raw: str,
        event_uuid: Optional[str] = None
    ) -> str:
        """Encola un evento transaccional en el outbox para sincronización P2P."""
        uid = event_uuid or str(uuid.uuid4())
        with self._write_lock, self.get_connection() as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO outbox_events (event_uuid, project_uuid, node_uuid, event_type, payload_raw, sync_status)
                    VALUES (?, ?, ?, ?, ?, 'pending');
                    """,
                    (uid, project_uuid, node_uuid, event_type, payload_raw)
                )
        return uid

    def get_pending_outbox_events(self, project_uuid: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Recupera los eventos pendientes de sincronización de un proyecto."""
        with self.get_connection() as conn:
            cur = conn.execute(
                """
                SELECT * FROM outbox_events
                WHERE project_uuid = ? AND sync_status = 'pending'
                ORDER BY created_at ASC LIMIT ?;
                """,
                (project_uuid, limit)
            )
            return [dict(row) for row in cur.fetchall()]

    def mark_outbox_event_status(self, event_uuid: str, sync_status: str) -> None:
        """Actualiza el estado de sincronización de un evento outbox ('pending', 'flushed_to_branch', 'acked')."""
        if sync_status not in ('pending', 'flushed_to_branch', 'acked'):
            raise ValueError(f"Estado de sincronización outbox no válido: {sync_status}")
        with self._write_lock, self.get_connection() as conn:
            with conn:
                conn.execute(
                    "UPDATE outbox_events SET sync_status = ? WHERE event_uuid = ?;",
                    (sync_status, event_uuid)
                )

    update_outbox_sync_status = mark_outbox_event_status

    # ==========================
    # Gestión de Colaboradores HITL
    # ==========================

    def add_or_request_collaborator(
        self,
        project_uuid: str,
        node_uuid: str,
        github_username: str = "",
        public_key: str = "",
        role: str = "collaborator",
        status: str = "pending_approval"
    ) -> Dict[str, Any]:
        """Registra o actualiza la solicitud de colaboración de un usuario en un proyecto."""
        now_str = datetime.now(timezone.utc).isoformat()
        approved_at = now_str if status == "approved" else None
        with self._write_lock, self.get_connection() as conn:
            with conn:
                existing = conn.execute(
                    "SELECT public_key FROM project_collaborators WHERE project_uuid = ? AND node_uuid = ?;",
                    (project_uuid, node_uuid)
                ).fetchone()
                existing_public_key = existing["public_key"] if existing else ""
                if existing_public_key and public_key and existing_public_key != public_key:
                    # Review Focus: un node_uuid colisionado (p. ej. mismo hostname) nunca debe
                    # permitir que una clave pública distinta tome el estado de rate-limit de otra.
                    raise ValueError("node_uuid ya está asociado a otra clave pública")
                conn.execute(
                    """
                    INSERT INTO project_collaborators (project_uuid, node_uuid, github_username, public_key, role, status, created_at, approved_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(project_uuid, node_uuid) DO UPDATE SET
                        github_username = COALESCE(NULLIF(excluded.github_username, ''), project_collaborators.github_username),
                        public_key = COALESCE(NULLIF(excluded.public_key, ''), project_collaborators.public_key),
                        role = excluded.role,
                        status = excluded.status,
                        approved_at = COALESCE(excluded.approved_at, project_collaborators.approved_at);
                    """,
                    (project_uuid, node_uuid, github_username, public_key, role, status, now_str, approved_at)
                )
        return self.get_collaborator(project_uuid, node_uuid) or {}

    def is_trusted_public_key(self, public_key: str) -> bool:
        """True si la clave pública Ed25519 pertenece a un colaborador APROBADO de algún proyecto local."""
        if not isinstance(public_key, str) or not public_key.strip():
            return False
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM project_collaborators WHERE public_key = ? AND status = 'approved' LIMIT 1;",
                (public_key.strip(),)
            ).fetchone()
        return row is not None

    def get_collaborator(self, project_uuid: str, node_uuid: str) -> Optional[Dict[str, Any]]:
        """Recupera la información de un colaborador en un proyecto."""
        with self.get_connection() as conn:
            cur = conn.execute(
                "SELECT * FROM project_collaborators WHERE project_uuid = ? AND node_uuid = ?;",
                (project_uuid, node_uuid)
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def get_collaborator_by_pubkey(self, project_uuid: str, public_key: str) -> Optional[Dict[str, Any]]:
        """
        Recupera la fila de colaborador/solicitud por su clave pública Ed25519 persistente.
        Esta es la identidad canónica para el rate-limit de solicitudes de unión: a diferencia
        de node_uuid (derivado del hostname, puede colisionar) o github_username (cambiable
        trivialmente), la clave pública identifica de forma estable al solicitante.

        Hallazgo de revisión Task 11: más de una fila puede compartir la misma
        public_key en un proyecto -- p. ej. el mismo solicitante reintenta unirse desde
        un node_uuid distinto (otro hostname/dispositivo, o MCP2P_NODE_UUID cambiado)
        mientras aún no está bloqueado; add_or_request_collaborator solo rechaza una
        colisión cuando el node_uuid YA existe con una public_key DISTINTA, así que un
        node_uuid nuevo con la MISMA public_key inserta una fila adicional en vez de
        actualizar la existente. Sin un ORDER BY determinista, `fetchone()` podía
        devolver CUALQUIERA de esas filas -- potencialmente la "limpia" (rejection_count
        0), ocultando en silencio el historial de rechazos de la otra fila y burlando el
        rate-limit. Se ordena para preferir siempre la fila más relevante para el
        rate-limit: primero las que no están en estado terminal 'approved', luego por
        mayor rejection_count, luego por el rechazo más reciente -- así una fila con
        historial de rechazos nunca queda escondida detrás de una fila nueva y limpia.
        """
        with self.get_connection() as conn:
            cur = conn.execute(
                """
                SELECT * FROM project_collaborators
                WHERE project_uuid = ? AND public_key = ?
                ORDER BY
                    CASE WHEN status = 'approved' THEN 1 ELSE 0 END ASC,
                    rejection_count DESC,
                    last_rejected_at DESC
                LIMIT 1;
                """,
                (project_uuid, public_key)
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def get_unresolved_or_approved_collaborator_by_pubkey(
        self, project_uuid: str, public_key: str
    ) -> Optional[Dict[str, Any]]:
        """
        Busca, para esta public_key, una fila que ya esté resuelta en sentido positivo --
        'pending_approval' (ya registrada, aún sin decisión) o 'approved' (ya aceptada) --
        usada EXCLUSIVAMENTE por la comprobación de idempotencia de
        ingest_join_request/join_p2p_project (ver api_bridge.py).

        Hallazgo de revisión Task 11 (ronda 3 -- regresión introducida por el propio fix
        de get_collaborator_by_pubkey en la ronda anterior): esa función ahora ordena a
        propósito para priorizar la fila con MÁS historial de rechazos -- correcto para
        get_join_rate_limit_state, pero SI ingest_join_request reutilizara ese mismo
        resultado para su chequeo de idempotencia, un escenario realista con varias filas
        bajo la misma public_key (una por dispositivo/node_uuid, por el mecanismo de
        desambiguación de Tareas 5/6) -- p. ej. una fila 'approved' bajo node_aaa_dev1 y
        una fila 'rejected' no relacionada bajo node_zzz_dev2 -- haría que
        get_collaborator_by_pubkey devolviera la fila RECHAZADA (tiene historial de
        rechazos) en vez de la fila APROBADA real que se busca al reingerir el
        request_code de node_aaa_dev1, concluyendo erróneamente que la solicitud sigue
        sin resolver y degradando en silencio la fila ya aprobada -- exactamente la clase
        de bug de degradación que las Tareas 5/6 arreglaron.

        Esta consulta filtra por status ANTES de ordenar (nunca puede devolver una fila
        'rejected'), así que es inmune a esa regresión independientemente de cómo
        get_collaborator_by_pubkey decida ordenar para el rate-limiting. Si más de una
        fila calificara (approved + pending_approval simultáneos para la misma clave --
        no debería ocurrir en un flujo real, pero se ordena igualmente de forma
        determinista por si acaso), se prefiere 'approved' sobre 'pending_approval', y
        luego la más reciente.
        """
        with self.get_connection() as conn:
            cur = conn.execute(
                """
                SELECT * FROM project_collaborators
                WHERE project_uuid = ? AND public_key = ? AND status IN ('pending_approval', 'approved')
                ORDER BY
                    CASE WHEN status = 'approved' THEN 0 ELSE 1 END ASC,
                    created_at DESC
                LIMIT 1;
                """,
                (project_uuid, public_key)
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def get_join_rate_limit_state(self, project_uuid: str, public_key: str) -> Dict[str, Any]:
        """
        Evalúa si un solicitante (identificado por su clave pública) puede intentar unirse
        a un proyecto ahora mismo, en función de su historial de rechazos y de la ventana
        horaria de intentos.
        """
        now = datetime.now(timezone.utc)
        row = self.get_collaborator_by_pubkey(project_uuid, public_key)
        if row is None:
            return {"allowed": True, "locked_until": None, "reason": "no_previous_attempts"}

        rejection_count = row.get("rejection_count") or 0
        last_rejected_at = _parse_iso_datetime(row.get("last_rejected_at"))
        request_window_start = _parse_iso_datetime(row.get("request_window_start"))
        request_window_count = row.get("request_window_count") or 0

        lock_until = _compute_lock_until(rejection_count, last_rejected_at, now)
        if lock_until is not None:
            return {"allowed": False, "locked_until": lock_until.isoformat(), "reason": "locked_due_to_rejections"}

        if _hourly_cap_exceeded(rejection_count, request_window_start, request_window_count, now):
            return {"allowed": False, "locked_until": None, "reason": "hourly_request_cap_exceeded"}

        return {"allowed": True, "locked_until": None, "reason": "ok"}

    def record_join_attempt(self, project_uuid: str, public_key: str) -> None:
        """
        Registra un intento de solicitud de unión permitido (allowed=True), actualizando
        la ventana deslizante horaria de conteo de intentos para ese solicitante.
        """
        now = datetime.now(timezone.utc)
        now_str = now.isoformat()
        with self._write_lock, self.get_connection() as conn:
            with conn:
                row = conn.execute(
                    "SELECT request_window_start, request_window_count FROM project_collaborators "
                    "WHERE project_uuid = ? AND public_key = ?;",
                    (project_uuid, public_key)
                ).fetchone()
                if row is None:
                    return
                window_start = _parse_iso_datetime(row["request_window_start"])
                window_count = row["request_window_count"] or 0
                if window_start is None or (now - window_start) > _JOIN_HOURLY_WINDOW:
                    new_window_start = now_str
                    new_window_count = 1
                else:
                    new_window_start = row["request_window_start"]
                    new_window_count = window_count + 1
                conn.execute(
                    "UPDATE project_collaborators SET request_window_start = ?, request_window_count = ? "
                    "WHERE project_uuid = ? AND public_key = ?;",
                    (new_window_start, new_window_count, project_uuid, public_key)
                )

    def record_rejection(self, project_uuid: str, node_uuid: str) -> Dict[str, Any]:
        """
        Rechaza explícitamente la solicitud de un colaborador e incrementa su contador
        de rechazos, recalculando el bloqueo resultante -- todo en UNA SOLA transacción
        atómica bajo `_write_lock`.

        Hallazgo de revisión Task 11 (TOCTOU): esto antes hacía el UPDATE de status
        (vía una llamada separada a `reject_collaborator`, que adquiere y libera su
        propio `_write_lock`) y el incremento del contador como DOS adquisiciones
        independientes de `_write_lock`. Entre ambas había una ventana en la que otro
        hilo -- p. ej. un `join_request` en vivo llegando por el transporte P2P --
        podía ejecutar `add_or_request_collaborator` y reinsertar la fila como
        'pending_approval' con el `rejection_count` todavía SIN incrementar, deshaciendo
        en silencio el rechazo recién emitido por el dueño. Fusionar ambas escrituras en
        una única transacción bajo el mismo `_write_lock` elimina esa ventana: nada
        puede interleavarse entre el UPDATE de status y el incremento del contador.
        """
        now = datetime.now(timezone.utc)
        now_str = now.isoformat()
        with self._write_lock, self.get_connection() as conn:
            with conn:
                conn.execute(
                    "UPDATE project_collaborators SET status = 'rejected' WHERE project_uuid = ? AND node_uuid = ?;",
                    (project_uuid, node_uuid)
                )
                conn.execute(
                    """
                    UPDATE project_collaborators
                    SET rejection_count = rejection_count + 1, last_rejected_at = ?
                    WHERE project_uuid = ? AND node_uuid = ?;
                    """,
                    (now_str, project_uuid, node_uuid)
                )
                row = conn.execute(
                    "SELECT rejection_count, last_rejected_at FROM project_collaborators "
                    "WHERE project_uuid = ? AND node_uuid = ?;",
                    (project_uuid, node_uuid)
                ).fetchone()

        if row is None:
            return {"success": False, "rejection_count": 0, "locked_until": None}

        rejection_count = row["rejection_count"]
        last_rejected_at = _parse_iso_datetime(row["last_rejected_at"])
        lock_until = _compute_lock_until(rejection_count, last_rejected_at, now)
        return {
            "success": True,
            "rejection_count": rejection_count,
            "locked_until": lock_until.isoformat() if lock_until is not None else None
        }

    def get_project_collaborators(
        self,
        project_uuid: str,
        online_only: bool = False,
    ) -> List[Dict[str, Any]]:
        """Lista todos los colaboradores y solicitudes pendientes de un proyecto."""
        query = """
            SELECT c.*, n.developer_name, n.status as node_status, n.reviewer_sla_hours, n.node_kind
            FROM project_collaborators c
            LEFT JOIN swarm_nodes n ON c.node_uuid = n.node_uuid
            WHERE c.project_uuid = ?
        """
        if online_only:
            query += " AND COALESCE(n.status, 'offline') IN ('online', 'busy')\n"
        query += " ORDER BY c.created_at ASC;"
        with self.get_connection() as conn:
            cur = conn.execute(query, (project_uuid,))
            return [dict(row) for row in cur.fetchall()]

    def list_collaborators(
        self,
        project_uuid: str,
        online_only: bool = False,
    ) -> List[Dict[str, Any]]:
        """Alias compatible para listar colaboradores de un proyecto."""
        return self.get_project_collaborators(project_uuid, online_only=online_only)

    def approve_collaborator(self, project_uuid: str, node_uuid: str) -> bool:
        """Aprobación explícita (Human-in-the-Loop) de un colaborador por el dueño del proyecto."""
        now_str = datetime.now(timezone.utc).isoformat()
        with self._write_lock, self.get_connection() as conn:
            with conn:
                cur = conn.execute(
                    """
                    UPDATE project_collaborators
                    SET status = 'approved', approved_at = ?,
                        rejection_count = 0, last_rejected_at = NULL,
                        request_window_start = NULL, request_window_count = 0
                    WHERE project_uuid = ? AND node_uuid = ?;
                    """,
                    (now_str, project_uuid, node_uuid)
                )
                return cur.rowcount > 0

    def reject_collaborator(self, project_uuid: str, node_uuid: str) -> bool:
        """Rechazo explícito de una solicitud de colaborador."""
        with self._write_lock, self.get_connection() as conn:
            with conn:
                cur = conn.execute(
                    "UPDATE project_collaborators SET status = 'rejected' WHERE project_uuid = ? AND node_uuid = ?;",
                    (project_uuid, node_uuid)
                )
                return cur.rowcount > 0

    def remove_collaborator(self, project_uuid: str, node_uuid: str) -> bool:
        """Remueve a un colaborador o nodo del proyecto."""
        with self._write_lock, self.get_connection() as conn:
            with conn:
                cur = conn.execute(
                    "DELETE FROM project_collaborators WHERE project_uuid = ? AND node_uuid = ?;",
                    (project_uuid, node_uuid)
                )
                return cur.rowcount > 0

    # ==========================
    # Configuraciones Locales (App Settings)
    # ==========================

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Obtiene una configuración local por clave."""
        with self.get_connection() as conn:
            cur = conn.execute("SELECT value FROM app_settings WHERE key = ?;", (key,))
            row = cur.fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        """Guarda o actualiza una configuración local."""
        now_str = datetime.now(timezone.utc).isoformat()
        with self._write_lock, self.get_connection() as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO app_settings (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at;
                    """,
                    (key, value, now_str)
                )

    def delete_setting(self, key: str) -> bool:
        """Elimina una configuración local."""
        with self._write_lock, self.get_connection() as conn:
            with conn:
                cur = conn.execute("DELETE FROM app_settings WHERE key = ?;", (key,))
                return cur.rowcount > 0

    # ==========================
    # Juicio de Honor y Votación Ponderada (2.0 Humano / 1.5 Agente)
    # ==========================

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
        injection_suspected: bool = False
    ) -> Dict[str, Any]:
        """Crea un nuevo ticket de arbitraje técnico con soporte para Tercera Vía (Síntesis), Matriz Neutral, Árbitro y flags de inyección."""
        now_str = datetime.now(timezone.utc).isoformat()
        esc_rec = escalation_recommendation or ""
        inj_flag = 1 if injection_suspected else 0
        with self._write_lock, self.get_connection() as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO dispute_tickets (
                        ticket_id, project_uuid, card_id, title, collision_reason,
                        contender_a_node, contender_b_node, proposal_a_ref, proposal_b_ref,
                        proposal_c_ref, jury_matrix_json, status, escalation_recommendation,
                        arbiter_node_uuid, injection_suspected, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?);
                    """,
                    (ticket_id, project_uuid, card_id, title, collision_reason,
                     contender_a_node, contender_b_node, proposal_a_ref, proposal_b_ref,
                     proposal_c_ref, jury_matrix_json, esc_rec, arbiter_node_uuid, inj_flag, now_str)
                )
        return self.get_dispute_ticket(ticket_id) or {}

    def update_dispute_synthesis_and_matrix(
        self,
        ticket_id: str,
        proposal_c_ref: Optional[str] = None,
        jury_matrix: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """Actualiza la Síntesis (Tercera Vía C) y/o la matriz de subagentes neutrales de un ticket."""
        ticket = self.get_dispute_ticket(ticket_id)
        if not ticket:
            return {}
        new_c = proposal_c_ref if proposal_c_ref is not None else ticket.get("proposal_c_ref", "")
        new_matrix = json.dumps(jury_matrix, ensure_ascii=False) if jury_matrix is not None else ticket.get("jury_matrix_json", "[]")
        with self._write_lock, self.get_connection() as conn:
            with conn:
                conn.execute(
                    "UPDATE dispute_tickets SET proposal_c_ref = ?, jury_matrix_json = ? WHERE ticket_id = ?;",
                    (new_c, new_matrix, ticket_id)
                )
        return self.get_dispute_ticket(ticket_id) or {}

    def get_dispute_ticket(self, ticket_id: str) -> Optional[Dict[str, Any]]:
        """Recupera los detalles de un ticket de disputa."""
        with self.get_connection() as conn:
            cur = conn.execute("SELECT * FROM dispute_tickets WHERE ticket_id = ?;", (ticket_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def list_dispute_tickets(
        self,
        project_uuid: str,
        status: Optional[str] = None,
        card_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Lista tickets de disputa para un proyecto, con filtros opcionales de estado o card_id."""
        query = "SELECT * FROM dispute_tickets WHERE project_uuid = ?"
        params: List[Any] = [project_uuid]
        if status and status != "all":
            query += " AND status = ?"
            params.append(status)
        if card_id:
            query += " AND card_id = ?"
            params.append(card_id)
        query += " ORDER BY created_at DESC;"

        with self.get_connection() as conn:
            cur = conn.execute(query, tuple(params))
            return [dict(row) for row in cur.fetchall()]

    def get_dispute_ticket_by_card(
        self,
        project_uuid: str,
        card_id: str
    ) -> Optional[Dict[str, Any]]:
        """Recupera el ticket de disputa más reciente asociado a una tarjeta Kanban."""
        with self.get_connection() as conn:
            cur = conn.execute(
                """
                SELECT * FROM dispute_tickets 
                WHERE project_uuid = ? AND card_id = ?
                ORDER BY created_at DESC LIMIT 1;
                """,
                (project_uuid, card_id)
            )
            row = cur.fetchone()
            return dict(row) if row else None

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
        rationale_summary: str = ""
    ) -> Dict[str, Any]:
        """Registra la evaluación Borda de un agente con ponderación por dimensiones."""
        weighted_total = (
            score_kiss * 0.25 +
            score_performance * 0.25 +
            score_security * 0.20 +
            score_ux_hicks * 0.15 +
            score_testability * 0.15
        )
        now_str = datetime.now(timezone.utc).isoformat()
        with self._write_lock, self.get_connection() as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO jury_borda_scores (
                        vote_uuid, ticket_id, juror_id, evaluated_option,
                        score_kiss, score_performance, score_security,
                        score_ux_hicks, score_testability, weighted_total,
                        rationale_summary, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        vote_uuid, ticket_id, juror_id, evaluated_option,
                        score_kiss, score_performance, score_security,
                        score_ux_hicks, score_testability, weighted_total,
                        rationale_summary, now_str
                    )
                )
        return {"vote_uuid": vote_uuid, "weighted_total": weighted_total}

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
        project_uuid: str = ""
    ) -> Dict[str, Any]:
        """Registra el voto humano directo autenticado con Ed25519."""
        now_str = datetime.now(timezone.utc).isoformat()
        s_at = signed_at or now_str
        with self._write_lock, self.get_connection() as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO human_jury_votes (
                        vote_uuid, ticket_id, node_uuid, developer_name,
                        voted_option, weight, signature_ed25519,
                        public_key, signed_at, content_sha256, project_uuid, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (vote_uuid, ticket_id, node_uuid, developer_name, voted_option,
                     weight, signature_ed25519, public_key, s_at, content_sha256, project_uuid, now_str)
                )
        return {"vote_uuid": vote_uuid, "weight": weight, "voted_option": voted_option}

    def get_jury_votes_summary(self, ticket_id: str) -> Dict[str, Any]:
        """Recupera los votos humanos y de agentes emitidos para un ticket."""
        with self.get_connection() as conn:
            agent_cur = conn.execute("SELECT * FROM jury_borda_scores WHERE ticket_id = ?;", (ticket_id,))
            agent_scores = [dict(r) for r in agent_cur.fetchall()]
            human_cur = conn.execute("SELECT * FROM human_jury_votes WHERE ticket_id = ?;", (ticket_id,))
            human_votes = [dict(r) for r in human_cur.fetchall()]
            return {"agent_scores": agent_scores, "human_votes": human_votes}

    def update_dispute_status(
        self,
        ticket_id: str,
        status: str,
        resolution_mode: str = "",
        winning_option: Optional[str] = None,
        final_adr_path: Optional[str] = None
    ) -> bool:
        """Actualiza el estado de un ticket de disputa validando el estado y guardando resolution_mode."""
        valid_statuses = {"open", "provisional_ruling", "ratified", "challenged", "resolved_hitl"}
        if status not in valid_statuses:
            raise ValueError(f"Estado de disputa inválido: {status}")

        with self._write_lock, self.get_connection() as conn:
            with conn:
                cur = conn.execute(
                    """
                    UPDATE dispute_tickets
                    SET status = ?, resolution_mode = ?,
                        winning_quadrant = COALESCE(?, winning_quadrant),
                        final_adr_path = COALESCE(?, final_adr_path)
                    WHERE ticket_id = ?;
                    """,
                    (status, resolution_mode, winning_option, final_adr_path, ticket_id)
                )
                return cur.rowcount > 0

    def resolve_dispute_ticket(
        self,
        ticket_id: str,
        winning_option: str,
        final_adr_path: Optional[str] = None,
        resolution_mode: str = "ratified_legacy"
    ) -> bool:
        """Wrapper de compatibilidad para update_dispute_status con status='ratified'."""
        return self.update_dispute_status(
            ticket_id=ticket_id,
            status="ratified",
            resolution_mode=resolution_mode,
            winning_option=winning_option,
            final_adr_path=final_adr_path
        )

    def set_project_arbiter(self, project_uuid: str, node_uuid: str) -> bool:
        """Registra o actualiza el árbitro humano externo pre-registrado para el proyecto."""
        now_str = datetime.now(timezone.utc).isoformat()
        with self._write_lock, self.get_connection() as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO project_arbiters (project_uuid, node_uuid, registered_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(project_uuid, node_uuid) DO UPDATE SET registered_at = excluded.registered_at;
                    """,
                    (project_uuid, node_uuid, now_str)
                )
                return True

    def clear_project_arbiter(self, project_uuid: str, node_uuid: str) -> bool:
        """Elimina a un árbitro registrado de un proyecto."""
        with self._write_lock, self.get_connection() as conn:
            with conn:
                cur = conn.execute(
                    "DELETE FROM project_arbiters WHERE project_uuid = ? AND node_uuid = ?;",
                    (project_uuid, node_uuid)
                )
                return cur.rowcount > 0

    def list_project_arbiters(self, project_uuid: str) -> List[Dict[str, Any]]:
        """Lista los árbitros humanos externos registrados para el proyecto."""
        with self.get_connection() as conn:
            cur = conn.execute(
                "SELECT * FROM project_arbiters WHERE project_uuid = ? ORDER BY registered_at ASC;",
                (project_uuid,)
            )
            return [dict(r) for r in cur.fetchall()]

    def record_vote_delivery(self, ticket_id: str, node_uuid: str, delivered_at: Optional[str] = None) -> bool:
        """Registra la entrega/notificación de la ventana de votación de un ticket a un nodo elegible."""
        now_str = delivered_at or datetime.now(timezone.utc).isoformat()
        with self._write_lock, self.get_connection() as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO dispute_vote_delivery (ticket_id, node_uuid, delivered_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(ticket_id, node_uuid) DO NOTHING;
                    """,
                    (ticket_id, node_uuid, now_str)
                )
                return True

    def get_vote_deliveries(self, ticket_id: str) -> List[Dict[str, Any]]:
        """Recupera los registros de entrega de un ticket."""
        with self.get_connection() as conn:
            cur = conn.execute(
                "SELECT * FROM dispute_vote_delivery WHERE ticket_id = ?;",
                (ticket_id,)
            )
            return [dict(r) for r in cur.fetchall()]

    # ==========================
    # Métodos Tipados para Eliminar SQL Crudo de ApiBridge / UI Adapters
    # ==========================

    def upsert_pending_join_stub(
        self,
        project_uuid: str,
        name: str,
        base_path: str,
        code_repo_path: str,
        gov_repo_path: str,
        code_repo_url: str = "",
        gov_repo_url: str = "",
        default_sla_hours: int = 24
    ) -> Dict[str, Any]:
        """Inserta idempotentemente un stub de proyecto en estado 'pending_approval' para join P2P."""
        validate_safe_id(project_uuid, "project_uuid")
        now_str = datetime.now(timezone.utc).isoformat()
        with self._write_lock, self.get_connection() as conn:
            with conn:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO projects (
                        project_uuid, name, base_path, code_repo_path, gov_repo_path,
                        code_repo_url, gov_repo_url, default_sla_hours, join_status,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending_approval', ?, ?);
                    """,
                    (
                        project_uuid, name, base_path, code_repo_path, gov_repo_path,
                        code_repo_url, gov_repo_url, default_sla_hours, now_str, now_str
                    )
                )
        return self.get_project_by_uuid(project_uuid) or {}

    def activate_joined_project(
        self,
        project_uuid: str,
        code_repo_path: str,
        gov_repo_path: str,
        local_node: Optional[str] = None
    ) -> Dict[str, Any]:
        """Activa un proyecto unido tras clonar sus repositorios y aprueba la fila de colaborador local."""
        now_str = datetime.now(timezone.utc).isoformat()
        target_node = local_node or local_node_uuid()
        with self._write_lock, self.get_connection() as conn:
            with conn:
                conn.execute(
                    """
                    UPDATE projects
                    SET code_repo_path = ?, gov_repo_path = ?, join_status = 'active', updated_at = ?
                    WHERE project_uuid = ?;
                    """,
                    (code_repo_path, gov_repo_path, now_str, project_uuid)
                )
                if target_node:
                    conn.execute(
                        """
                        UPDATE project_collaborators
                        SET status = 'approved', approved_at = ?,
                            rejection_count = 0, last_rejected_at = NULL,
                            request_window_start = NULL, request_window_count = 0
                        WHERE project_uuid = ? AND node_uuid = ?;
                        """,
                        (now_str, project_uuid, target_node)
                    )
        return self.get_project_by_uuid(project_uuid) or {}

    def update_project_repo_urls(
        self,
        project_uuid: str,
        code_repo_url: Optional[str] = None,
        gov_repo_url: Optional[str] = None
    ) -> bool:
        """Actualiza las URLs remotas (code_repo_url / gov_repo_url) de un proyecto."""
        if code_repo_url is None and gov_repo_url is None:
            return False
        now_str = datetime.now(timezone.utc).isoformat()
        sets: List[str] = ["updated_at = ?"]
        params: List[Any] = [now_str]
        if code_repo_url is not None:
            sets.append("code_repo_url = ?")
            params.append(code_repo_url)
        if gov_repo_url is not None:
            sets.append("gov_repo_url = ?")
            params.append(gov_repo_url)
        params.append(project_uuid)
        with self._write_lock, self.get_connection() as conn:
            with conn:
                cur = conn.execute(
                    f"UPDATE projects SET {', '.join(sets)} WHERE project_uuid = ?;",
                    tuple(params)
                )
                return cur.rowcount > 0

    def get_project_cards_and_disputes_summary(self, project_uuid: str) -> Dict[str, Any]:
        """Recupera tarjetas, mensajes A2A y tickets de disputa de un proyecto en una lectura coherente."""
        with self.get_connection() as conn:
            cards = [
                dict(r) for r in conn.execute(
                    "SELECT * FROM kanban_cards WHERE project_uuid = ? ORDER BY updated_at DESC;",
                    (project_uuid,)
                ).fetchall()
            ]
            a2a_messages = [
                dict(r) for r in conn.execute(
                    "SELECT * FROM a2a_messages WHERE project_uuid = ? ORDER BY created_at ASC, rowid ASC;",
                    (project_uuid,)
                ).fetchall()
            ]
            dispute_tickets = [
                dict(r) for r in conn.execute(
                    "SELECT * FROM dispute_tickets WHERE project_uuid = ? ORDER BY created_at DESC;",
                    (project_uuid,)
                ).fetchall()
            ]
        return {
            "cards": cards,
            "a2a_messages": a2a_messages,
            "dispute_tickets": dispute_tickets,
        }

    def get_pending_hitl_bundle(self, project_uuid: str, local_node: Optional[str] = None) -> Dict[str, Any]:
        """Recupera todos los registros SQLite necesarios para construir la bandeja HITL."""
        target_node = local_node or local_node_uuid()
        with self.get_connection() as conn:
            open_disputes = [
                dict(r) for r in conn.execute(
                    """
                    SELECT * FROM dispute_tickets
                    WHERE project_uuid = ?
                      AND (status = 'open'
                           OR (status = 'challenged' AND resolution_mode = 'awaiting_external_arbiter' AND arbiter_node_uuid = ?))
                    ORDER BY created_at DESC;
                    """,
                    (project_uuid, target_node)
                ).fetchall()
            ]
            resolved_disputes = [
                dict(r) for r in conn.execute(
                    "SELECT * FROM dispute_tickets WHERE project_uuid = ? AND status != 'open' ORDER BY created_at DESC LIMIT 30;",
                    (project_uuid,)
                ).fetchall()
            ]
            cards_in_review = [
                dict(r) for r in conn.execute(
                    "SELECT * FROM kanban_cards WHERE project_uuid = ? AND status = 'in_review' ORDER BY updated_at DESC;",
                    (project_uuid,)
                ).fetchall()
            ]
            collaborators = [
                dict(r) for r in conn.execute(
                    """
                    SELECT c.*, n.developer_name, n.status as node_status, n.reviewer_sla_hours, n.node_kind
                    FROM project_collaborators c
                    LEFT JOIN swarm_nodes n ON c.node_uuid = n.node_uuid
                    WHERE c.project_uuid = ?
                    ORDER BY c.created_at ASC;
                    """,
                    (project_uuid,)
                ).fetchall()
            ]
            swarm_nodes = [
                dict(r) for r in conn.execute(
                    "SELECT * FROM swarm_nodes ORDER BY last_heartbeat DESC;"
                ).fetchall()
            ]
        return {
            "open_disputes": open_disputes,
            "resolved_disputes": resolved_disputes,
            "cards_in_review": cards_in_review,
            "collaborators": collaborators,
            "swarm_nodes": swarm_nodes,
        }

    def get_hitl_ticket_detail(self, project_uuid: str, ticket_id: str) -> Optional[Dict[str, Any]]:
        """Recupera un ticket de disputa junto con sus votos y turnos de diálogo A2A de la tarjeta asociada."""
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM dispute_tickets WHERE project_uuid = ? AND ticket_id = ?;",
                (project_uuid, ticket_id)
            ).fetchone()
            if not row:
                return None
            ticket = dict(row)
            agent_scores = [
                dict(r) for r in conn.execute(
                    "SELECT * FROM jury_borda_scores WHERE ticket_id = ?;",
                    (ticket_id,)
                ).fetchall()
            ]
            human_votes = [
                dict(r) for r in conn.execute(
                    "SELECT * FROM human_jury_votes WHERE ticket_id = ?;",
                    (ticket_id,)
                ).fetchall()
            ]
            dialogue_rows: List[Dict[str, Any]] = []
            if ticket.get("card_id"):
                dialogue_rows = [
                    dict(r) for r in conn.execute(
                        "SELECT * FROM a2a_messages WHERE project_uuid = ? AND card_id = ? ORDER BY created_at ASC, rowid ASC;",
                        (project_uuid, ticket["card_id"])
                    ).fetchall()
                ]
        ticket["votes_summary"] = {"agent_scores": agent_scores, "human_votes": human_votes}
        ticket["a2a_messages"] = dialogue_rows
        return ticket

    def get_project_collaborators_map(self, project_uuid: str) -> Dict[str, Dict[str, Any]]:
        """Devuelve un diccionario indexado por node_uuid con los colaboradores del proyecto."""
        return {c["node_uuid"]: c for c in self.get_project_collaborators(project_uuid)}

    # ==========================
    # Identidad estable de paneles de terminal (Ola 2)
    # ==========================

    @staticmethod
    def _pane_identity_row(row: Any) -> Dict[str, Any]:
        return {
            "project_uuid": row["project_uuid"],
            "pane_slot": row["pane_slot"],
            "node_uuid": row["node_uuid"],
            "auto_relay_enabled": bool(row["auto_relay_enabled"]),
            "worker_mode": bool(row["worker_mode"]),
            "created_at": row["created_at"],
            "last_spawned_at": row["last_spawned_at"],
        }

    def get_pane_identity(self, project_uuid: str, pane_slot: str) -> Optional[Dict[str, Any]]:
        """Identidad guardada de (proyecto, ranura) o None."""
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM pane_identities WHERE project_uuid = ? AND pane_slot = ?;",
                (project_uuid or "", pane_slot),
            ).fetchone()
        return self._pane_identity_row(row) if row else None

    def get_or_create_pane_identity(
        self, project_uuid: str, pane_slot: str, *, touch: bool = False
    ) -> Dict[str, Any]:
        """Devuelve la identidad de (proyecto, ranura) creandola la primera vez (atomico bajo el cerrojo de escritura).

        `touch=True` anota `last_spawned_at` (cada vez que se abre el panel).
        """
        from src.core.domain.pane_identity import new_pane_node_uuid

        proj = project_uuid or ""
        now_str = datetime.now(timezone.utc).isoformat()
        with self._write_lock, self.get_connection() as conn:
            with conn:
                row = conn.execute(
                    "SELECT * FROM pane_identities WHERE project_uuid = ? AND pane_slot = ?;", (proj, pane_slot)
                ).fetchone()
                if row is None:
                    for _attempt in range(8):
                        candidate = new_pane_node_uuid(proj, pane_slot)
                        try:
                            conn.execute(
                                """
                                INSERT INTO pane_identities
                                    (project_uuid, pane_slot, node_uuid, auto_relay_enabled, created_at, last_spawned_at)
                                VALUES (?, ?, ?, 0, ?, ?);
                                """,
                                (proj, pane_slot, candidate, now_str, now_str if touch else None),
                            )
                            break
                        except sqlite3.IntegrityError:
                            continue  # colision del sufijo aleatorio: otro intento
                    else:  # pragma: no cover - 8 colisiones de 32 bits seguidas
                        raise RuntimeError("No se pudo generar una identidad de panel unica.")
                elif touch:
                    conn.execute(
                        "UPDATE pane_identities SET last_spawned_at = ? WHERE project_uuid = ? AND pane_slot = ?;",
                        (now_str, proj, pane_slot),
                    )
                row = conn.execute(
                    "SELECT * FROM pane_identities WHERE project_uuid = ? AND pane_slot = ?;", (proj, pane_slot)
                ).fetchone()
        return self._pane_identity_row(row)

    def set_pane_auto_relay(self, project_uuid: str, pane_slot: str, enabled: bool) -> Dict[str, Any]:
        """Persiste el interruptor Auto-Relay del panel (proyecto, ranura); crea la identidad si faltaba."""
        self.get_or_create_pane_identity(project_uuid, pane_slot)
        with self._write_lock, self.get_connection() as conn:
            with conn:
                conn.execute(
                    "UPDATE pane_identities SET auto_relay_enabled = ? WHERE project_uuid = ? AND pane_slot = ?;",
                    (1 if enabled else 0, project_uuid or "", pane_slot),
                )
        identity = self.get_pane_identity(project_uuid, pane_slot)
        assert identity is not None
        return identity

    def set_pane_worker_mode(self, project_uuid: str, pane_slot: str, enabled: bool) -> Dict[str, Any]:
        """Persiste el modo trabajador del panel (proyecto, ranura); crea la identidad si faltaba."""
        self.get_or_create_pane_identity(project_uuid, pane_slot)
        with self._write_lock, self.get_connection() as conn:
            with conn:
                conn.execute(
                    "UPDATE pane_identities SET worker_mode = ? WHERE project_uuid = ? AND pane_slot = ?;",
                    (1 if enabled else 0, project_uuid or "", pane_slot),
                )
        identity = self.get_pane_identity(project_uuid, pane_slot)
        assert identity is not None
        return identity

    def list_pane_identities(self, project_uuid: Optional[str] = None) -> List[Dict[str, Any]]:
        """Identidades guardadas (de un proyecto o de todos)."""
        with self.get_connection() as conn:
            if project_uuid is None:
                rows = conn.execute("SELECT * FROM pane_identities ORDER BY project_uuid, pane_slot;").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM pane_identities WHERE project_uuid = ? ORDER BY pane_slot;", (project_uuid,)
                ).fetchall()
        return [self._pane_identity_row(r) for r in rows]

    # ==========================
    # Extensiones, Tablas Dinámicas y Seeds P2P
    # ==========================

    def register_extension_table(self, table_name: str, ddl_sql: str) -> None:
        """Registra de forma segura una tabla de extensión para conectores en SQLite."""
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$", table_name or ""):
            raise ValueError(f"Nombre de tabla de extensión inválido: {table_name}")
        ddl_clean = (ddl_sql or "").strip()
        if not re.match(r"^CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+", ddl_clean, re.IGNORECASE):
            raise ValueError("ddl_sql debe comenzar con 'CREATE TABLE IF NOT EXISTS'")
        with self._write_lock, self.get_connection() as conn:
            with conn:
                conn.executescript(ddl_clean)

    def get_extension_setting(self, namespace: str, key: str, default: Optional[str] = None) -> Optional[str]:
        """Lee un valor de configuración de extensión aislado por namespace."""
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT value FROM extension_settings WHERE namespace = ? AND key = ?;",
                (namespace, key)
            ).fetchone()
            return row["value"] if row else default

    def set_extension_setting(self, namespace: str, key: str, value: str) -> None:
        """Guarda o actualiza una configuración de extensión."""
        now_str = datetime.now(timezone.utc).isoformat()
        with self._write_lock, self.get_connection() as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO extension_settings (namespace, key, value, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(namespace, key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at;
                    """,
                    (namespace, key, value, now_str)
                )

    def delete_extension_setting(self, namespace: str, key: str) -> bool:
        """Elimina una configuración de extensión."""
        with self._write_lock, self.get_connection() as conn:
            with conn:
                cur = conn.execute(
                    "DELETE FROM extension_settings WHERE namespace = ? AND key = ?;",
                    (namespace, key)
                )
                return cur.rowcount > 0

    def add_seed_node(self, project_uuid: Optional[str], seed_url: str, label: str = "") -> List[Dict[str, Any]]:
        """Registra un nodo semilla (seed) P2P para un proyecto o globalmente."""
        scope = (project_uuid or "__global__").strip() or "__global__"
        cleaned_url = (seed_url or "").strip()
        if not cleaned_url:
            raise ValueError("seed_url no puede estar vacío")
        now_str = datetime.now(timezone.utc).isoformat()
        with self._write_lock, self.get_connection() as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO p2p_seeds (project_uuid, seed_url, label, created_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(project_uuid, seed_url) DO UPDATE SET
                        label = COALESCE(NULLIF(excluded.label, ''), p2p_seeds.label);
                    """,
                    (scope, cleaned_url, label, now_str)
                )
        return self.list_seed_nodes(project_uuid)

    def remove_seed_node(self, project_uuid: Optional[str], seed_url: str) -> bool:
        """Elimina un nodo semilla P2P."""
        scope = (project_uuid or "__global__").strip() or "__global__"
        cleaned_url = (seed_url or "").strip()
        with self._write_lock, self.get_connection() as conn:
            with conn:
                cur = conn.execute(
                    "DELETE FROM p2p_seeds WHERE project_uuid = ? AND seed_url = ?;",
                    (scope, cleaned_url)
                )
                return cur.rowcount > 0

    def list_seed_nodes(self, project_uuid: Optional[str] = None) -> List[Dict[str, Any]]:
        """Lista los nodos semilla P2P registrados para un proyecto (o globales)."""
        scope = (project_uuid or "__global__").strip() or "__global__"
        with self.get_connection() as conn:
            cur = conn.execute(
                "SELECT project_uuid, seed_url, label, created_at FROM p2p_seeds WHERE project_uuid = ? ORDER BY created_at ASC;",
                (scope,)
            )
            return [dict(r) for r in cur.fetchall()]
