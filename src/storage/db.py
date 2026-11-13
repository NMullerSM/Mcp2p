"""
MCP2P Local Storage Engine
Persistencia Local-First con SQLite en modo WAL (Write-Ahead Logging).
Subsistema Multi-Proyecto con Aislamiento Estricto de Doble Repositorio (app y governance).
"""

from __future__ import annotations

import os
import json
import logging
import platform
import sqlite3
import subprocess
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional


from src.app.paths import get_default_db_path
from src.storage.validators import validate_safe_id

logger = logging.getLogger("mcp2p.storage.db")


def _run_git_logged(args: List[str], cwd: Path, creationflags: int = 0) -> subprocess.CompletedProcess:
    """Ejecuta git y registra un warning con stderr si el código de salida no es 0 (no lanza)."""
    res = subprocess.run(args, cwd=str(cwd), capture_output=True, creationflags=creationflags)
    if res.returncode != 0:
        err = (res.stderr or b"").decode("utf-8", errors="replace").strip()
        logger.warning("git %s falló en %s (código %s): %s", " ".join(args[1:3]), cwd, res.returncode, err[:300])
    return res


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB_PATH = os.environ.get("MCP2P_DB_PATH", get_default_db_path())

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
    """Gestor de base de datos local SQLite con soporte WAL y multi-proyecto."""

    _instance: Optional[DatabaseManager] = None
    _lock = threading.Lock()

    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()
        self._initialize_schema()
        self._ensure_default_project()

    @classmethod
    def get_instance(cls, db_path: str = DEFAULT_DB_PATH) -> DatabaseManager:
        """
        Retorna la instancia singleton de DatabaseManager.
        Si se especifica explícitamente una ruta distinta (p. ej. en suites de tests automatizados
        con bases de datos temporales), se re-instancia el singleton para garantizar aislamiento.
        """
        with cls._lock:
            resolved = Path(db_path).resolve() if db_path else None
            if cls._instance is None or (resolved and cls._instance.db_path != resolved):
                cls._instance = cls(str(resolved or db_path))
            return cls._instance

    @contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Provee una conexión con configuración optimizada para alta velocidad."""
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

                    # Migración para a2a_messages (soporte para DIALOGUE y CHECKPOINT_COMPACT)
                    a2a_info = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='a2a_messages';").fetchone()
                    if a2a_info and "'DIALOGUE'" not in a2a_info[0]:
                        conn.executescript("""
                        CREATE TABLE IF NOT EXISTS a2a_messages_dg_tmp (
                            message_uuid TEXT PRIMARY KEY,
                            project_uuid TEXT NOT NULL,
                            sender_node_uuid TEXT NOT NULL,
                            intent TEXT CHECK(intent IN ('INFORMATIVE', 'COORDINATION', 'SEMANTIC_COLLISION', 'SUMMARY_SYNTHESIS', 'DIALOGUE', 'CHECKPOINT_COMPACT')) NOT NULL,
                            card_id TEXT,
                            payload_rfc_yaml TEXT NOT NULL,
                            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                            FOREIGN KEY (project_uuid) REFERENCES projects(project_uuid) ON DELETE CASCADE
                        );
                        INSERT INTO a2a_messages_dg_tmp (message_uuid, project_uuid, sender_node_uuid, intent, card_id, payload_rfc_yaml, created_at)
                        SELECT message_uuid, project_uuid, sender_node_uuid, intent, card_id, payload_rfc_yaml, created_at FROM a2a_messages;
                        DROP TABLE a2a_messages;
                        ALTER TABLE a2a_messages_dg_tmp RENAME TO a2a_messages;
                        """)

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

                -- Índices de ultra alta velocidad (<1ms)
                CREATE INDEX IF NOT EXISTS idx_cards_project_status ON kanban_cards(project_uuid, status);
                CREATE INDEX IF NOT EXISTS idx_a2a_project ON a2a_messages(project_uuid);
                CREATE INDEX IF NOT EXISTS idx_outbox_pending ON outbox_events(project_uuid, sync_status, created_at);
                CREATE INDEX IF NOT EXISTS idx_collab_project ON project_collaborators(project_uuid, status);
                """)

    def _ensure_default_project(self) -> None:
        """Asegura la existencia de un proyecto por defecto si la base está vacía."""
        with self.get_connection() as conn:
            cur = conn.execute("SELECT COUNT(*) as cnt FROM projects;")
            if cur.fetchone()["cnt"] == 0:
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

        # Inicializar OKF estándar en el repositorio de gobernanza
        from src.storage.okf_adapter import OKFAdapter
        okf_adapter = OKFAdapter()
        okf_adapter.initialize_project_okf(gov_dir, name, p_uuid)

        if init_git:
            # Configurar flags de subprocess para evitar CMD flashing en Windows
            creationflags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0

            # 1. Inicializar repo de código si no existe .git
            if not (app_dir / ".git").exists():
                _run_git_logged(["git", "init", "-b", "main"], app_dir, creationflags)
                _run_git_logged(["git", "config", "core.autocrlf", "false"], app_dir, creationflags)
                readme = app_dir / "README.md"
                if not readme.exists():
                    readme.write_text(f"# {name}\n\nEspacio de desarrollo local-first gestionado por MCP2P.\nUUID: `{p_uuid}`\n", encoding="utf-8")
                _run_git_logged(["git", "add", "."], app_dir, creationflags)
                _run_git_logged(["git", "commit", "-m", f"chore(init): initialize {clean_name} development workspace"], app_dir, creationflags)

            # 2. Inicializar repo de gobernanza con rama huérfana aislada
            if not (gov_dir / ".git").exists():
                _run_git_logged(["git", "init", "-b", "main"], gov_dir, creationflags)
                _run_git_logged(["git", "config", "core.autocrlf", "false"], gov_dir, creationflags)

                # Commit inicial de OKF en main
                marker = gov_dir / ".okf_project.json"
                marker.write_text(f'{{"project_uuid": "{p_uuid}", "name": "{name}"}}\n', encoding="utf-8")
                _run_git_logged(["git", "add", "."], gov_dir, creationflags)
                _run_git_logged(["git", "commit", "-m", f"init(okf): initialize governance master branch for {p_uuid}"], gov_dir, creationflags)

                # Desacoplar la rama huérfana en un worktree dentro de .git para no ensuciar main
                orphan_branch = f"orphan-conversa-memory-{p_uuid}"
                orphan_wt_path = gov_dir / ".git" / "orphan_worktree"
                _run_git_logged(
                    ["git", "worktree", "add", "--orphan", "-b", orphan_branch, str(orphan_wt_path)],
                    gov_dir, creationflags
                )
                if orphan_wt_path.exists():
                    conversa_readme = orphan_wt_path / "README_CONVERSA.md"
                    conversa_readme.write_text(
                        f"# Memoria Conversacional Desacoplada\n\nProyecto: {name} ({p_uuid})\nRama: {orphan_branch}\n",
                        encoding="utf-8"
                    )
                    _run_git_logged(["git", "add", "."], orphan_wt_path, creationflags)
                    _run_git_logged(
                        ["git", "commit", "-m", f"init(orphan): initialize conversation memory branch for {p_uuid}"],
                        orphan_wt_path, creationflags
                    )

            # 3. Instalar hooks de Git para sincronización en tiempo real
            for d in (app_dir, gov_dir):
                hooks_dir = d / ".git" / "hooks"
                if hooks_dir.is_dir():
                    post_commit = hooks_dir / "post-commit"
                    post_commit.write_text("#!/bin/sh\ntouch .git_pulse 2>/dev/null || true\n", encoding="utf-8")
                    try:
                        post_commit.chmod(0o755)
                    except Exception:
                        pass

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

        local_node = os.environ.get("MCP2P_NODE_UUID", f"node_{platform.node().lower()[:12]}")
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

    def get_project_sync_fingerprint(self, project_uuid: str) -> Dict[str, Any]:
        """Calcula una huella de sincronización atómica para detección reactiva de cambios en tiempo real."""
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

        cards_count, cards_max, a2a_count, a2a_max, tickets_count, tickets_max = row if row else (0, "", 0, "", 0, "")

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
        """Sincroniza atómicamente la tarjeta modificada al archivo YAML en governance/kanban/cards/
        y comitea el cambio a git con un mensaje que lleva el título y la descripción de la
        tarjeta, para que el historial de gobernanza quede versionado y no huérfano."""
        try:
            proj = self.get_project_by_uuid(project_uuid)
            if not proj or not proj.get("gov_repo_path"):
                return
            gov_dir = Path(proj["gov_repo_path"])
            if not gov_dir.exists():
                return
            from src.storage.okf_adapter import OKFAdapter
            okf = OKFAdapter()
            yaml_path = okf.write_card_yaml(gov_dir, card_data)

            card_id = card_data.get("card_id") or card_data.get("id") or "UNKNOWN"
            status = card_data.get("status", "")
            title = (card_data.get("title") or "").strip()
            description = (card_data.get("description") or "").strip()
            commit_message = f"sync(kanban): {card_id} [{status}] {title}"
            if description:
                commit_message += f"\n\n{description}"
            should_push = status != "in_review"
            okf.commit_gov_file(gov_dir, yaml_path, commit_message, push_now=should_push)
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
        """Actualiza los detalles completos de una tarjeta y sincroniza a YAML.
        assignee_node_uuid: None = no se toca la asignación actual; '' (string vacío) =
        desasignar explícitamente; cualquier otro valor debe ser un colaborador aprobado
        real del proyecto (se verifica antes de escribir, nunca un subagente de jurado)."""
        card = self.get_card_by_id(project_uuid, card_id)
        if not card:
            raise ValueError(f"Tarjeta no encontrada: {card_id}")

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
            self._validate_assignable_node(project_uuid, assignee_node_uuid)
            new_assignee = assignee_node_uuid

        now_str = datetime.now(timezone.utc).isoformat()
        with self._write_lock, self.get_connection() as conn:
            with conn:
                conn.execute(
                    """
                    UPDATE kanban_cards
                    SET title = ?, description = ?, acceptance_criteria = ?, status = ?, branch_ref = ?, assignee_node_uuid = ?, updated_at = ?
                    WHERE project_uuid = ? AND card_id = ?;
                    """,
                    (new_title, new_desc, new_crit, new_status, new_branch, new_assignee, now_str, project_uuid, card_id)
                )
        updated = self.get_card_by_id(project_uuid, card_id) or {}
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
        card_id: Optional[str] = None
    ) -> None:
        """Registra un mensaje o evento del canal Agente a Agente dentro de un proyecto."""
        with self._write_lock, self.get_connection() as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO a2a_messages (message_uuid, project_uuid, sender_node_uuid, intent, card_id, payload_rfc_yaml)
                    VALUES (?, ?, ?, ?, ?, ?);
                    """,
                    (message_uuid, project_uuid, sender_node_uuid, intent, card_id, payload_rfc_yaml)
                )

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

    def get_project_collaborators(self, project_uuid: str) -> List[Dict[str, Any]]:
        """Lista todos los colaboradores y solicitudes pendientes de un proyecto."""
        with self.get_connection() as conn:
            cur = conn.execute(
                """
                SELECT c.*, n.developer_name, n.status as node_status, n.reviewer_sla_hours, n.node_kind
                FROM project_collaborators c
                LEFT JOIN swarm_nodes n ON c.node_uuid = n.node_uuid
                WHERE c.project_uuid = ?
                ORDER BY c.created_at ASC;
                """,
                (project_uuid,)
            )
            return [dict(row) for row in cur.fetchall()]

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
