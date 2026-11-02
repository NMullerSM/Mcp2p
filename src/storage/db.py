"""
MCP2P Local Storage Engine
Persistencia Local-First con SQLite en modo WAL (Write-Ahead Logging).
Subsistema Multi-Proyecto con Aislamiento Estricto de Doble Repositorio (app y governance).
"""

from __future__ import annotations

import os
import platform
import sqlite3
import subprocess
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional


DEFAULT_DB_PATH = os.environ.get("MCP2P_DB_PATH", "mcp2p_local.db")


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
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(db_path)
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
                        if "'discarded'" not in sql_schema:
                            conn.executescript("""
                            CREATE TABLE IF NOT EXISTS kanban_cards_dg_tmp (
                                card_id TEXT NOT NULL,
                                project_uuid TEXT NOT NULL,
                                title TEXT NOT NULL,
                                description TEXT DEFAULT '',
                                acceptance_criteria TEXT DEFAULT '',
                                status TEXT CHECK(status IN ('backlog', 'in_progress', 'in_review', 'disputed', 'merged', 'consolidated', 'discarded')) NOT NULL DEFAULT 'backlog',
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

                    # Migración para dispute_tickets (Síntesis Tercera Vía y Matriz Neutral Anti-Sycophancy)
                    dt_info = conn.execute("PRAGMA table_info(dispute_tickets);").fetchall()
                    dt_cols = [c["name"] for c in dt_info]
                    if dt_cols:
                        if "proposal_c_ref" not in dt_cols:
                            conn.execute("ALTER TABLE dispute_tickets ADD COLUMN proposal_c_ref TEXT DEFAULT '';")
                        if "jury_matrix_json" not in dt_cols:
                            conn.execute("ALTER TABLE dispute_tickets ADD COLUMN jury_matrix_json TEXT DEFAULT '[]';")

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
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                -- 2. Nodos del Enjambre P2P (Adaptable a N participantes)
                CREATE TABLE IF NOT EXISTS swarm_nodes (
                    node_uuid TEXT PRIMARY KEY,
                    developer_name TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
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
                    status TEXT CHECK(status IN ('backlog', 'in_progress', 'in_review', 'disputed', 'merged', 'consolidated', 'discarded')) NOT NULL DEFAULT 'backlog',
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
                    jury_matrix_json TEXT DEFAULT '[]',
                    status TEXT CHECK(status IN ('open', 'provisional_ruling', 'ratified', 'challenged', 'resolved_hitl')) NOT NULL DEFAULT 'open',
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
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    approved_at DATETIME,
                    PRIMARY KEY (project_uuid, node_uuid),
                    FOREIGN KEY (project_uuid) REFERENCES projects(project_uuid) ON DELETE CASCADE
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
        alignment_dossier_markdown: str = ""
    ) -> Dict[str, Any]:
        """
        Crea un nuevo proyecto en el subsistema.
        Genera UUIDv4 único y estructura de doble repositorio (app_{name} y okf_{name}_{uuid}).
        """
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
                subprocess.run(["git", "init", "-b", "main"], cwd=str(app_dir), capture_output=True, creationflags=creationflags)
                subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=str(app_dir), capture_output=True, creationflags=creationflags)
                readme = app_dir / "README.md"
                if not readme.exists():
                    readme.write_text(f"# {name}\n\nEspacio de desarrollo local-first gestionado por MCP2P.\nUUID: `{p_uuid}`\n", encoding="utf-8")
                subprocess.run(["git", "add", "."], cwd=str(app_dir), capture_output=True, creationflags=creationflags)
                subprocess.run(["git", "commit", "-m", f"chore(init): initialize {clean_name} development workspace"], cwd=str(app_dir), capture_output=True, creationflags=creationflags)

            # 2. Inicializar repo de gobernanza con rama huérfana aislada
            if not (gov_dir / ".git").exists():
                subprocess.run(["git", "init", "-b", "main"], cwd=str(gov_dir), capture_output=True, creationflags=creationflags)
                subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=str(gov_dir), capture_output=True, creationflags=creationflags)
                
                # Commit inicial de OKF en main
                marker = gov_dir / ".okf_project.json"
                marker.write_text(f'{{"project_uuid": "{p_uuid}", "name": "{name}"}}\n', encoding="utf-8")
                subprocess.run(["git", "add", "."], cwd=str(gov_dir), capture_output=True, creationflags=creationflags)
                subprocess.run(["git", "commit", "-m", f"init(okf): initialize governance master branch for {p_uuid}"], cwd=str(gov_dir), capture_output=True, creationflags=creationflags)

                # Desacoplar la rama huérfana en un worktree dentro de .git para no ensuciar main
                orphan_branch = f"orphan-conversa-memory-{p_uuid}"
                orphan_wt_path = gov_dir / ".git" / "orphan_worktree"
                subprocess.run(
                    ["git", "worktree", "add", "--orphan", "-b", orphan_branch, str(orphan_wt_path)],
                    cwd=str(gov_dir),
                    capture_output=True,
                    creationflags=creationflags
                )
                if orphan_wt_path.exists():
                    conversa_readme = orphan_wt_path / "README_CONVERSA.md"
                    conversa_readme.write_text(
                        f"# Memoria Conversacional Desacoplada\n\nProyecto: {name} ({p_uuid})\nRama: {orphan_branch}\n",
                        encoding="utf-8"
                    )
                    subprocess.run(["git", "add", "."], cwd=str(orphan_wt_path), capture_output=True, creationflags=creationflags)
                    subprocess.run(
                        ["git", "commit", "-m", f"init(orphan): initialize conversation memory branch for {p_uuid}"],
                        cwd=str(orphan_wt_path),
                        capture_output=True,
                        creationflags=creationflags
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
                return cur.rowcount > 0 or {}

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
        """Sincroniza atómicamente la tarjeta modificada al archivo YAML en governance/kanban/cards/."""
        try:
            proj = self.get_project_by_uuid(project_uuid)
            if not proj or not proj.get("gov_repo_path"):
                return
            gov_dir = Path(proj["gov_repo_path"])
            if not gov_dir.exists():
                return
            from src.storage.okf_adapter import OKFAdapter
            okf = OKFAdapter()
            okf.write_card_yaml(gov_dir, card_data)
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

    def create_card(
        self,
        project_uuid: str,
        card_id: str,
        title: str,
        description: str = "",
        acceptance_criteria: str = ""
    ) -> Dict[str, Any]:
        """Crea una nueva tarjeta en el backlog asociada a un proyecto y sincroniza a YAML."""
        now = datetime.now(timezone.utc).isoformat()
        with self._write_lock, self.get_connection() as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO kanban_cards (card_id, project_uuid, title, description, acceptance_criteria, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 'backlog', ?, ?);
                    """,
                    (card_id, project_uuid, title, description, acceptance_criteria, now, now)
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
        branch_ref: Optional[str] = None
    ) -> Dict[str, Any]:
        """Actualiza los detalles completos de una tarjeta y sincroniza a YAML."""
        card = self.get_card_by_id(project_uuid, card_id)
        if not card:
            raise ValueError(f"Tarjeta no encontrada: {card_id}")

        new_title = title if title is not None else card["title"]
        new_desc = description if description is not None else card.get("description", "")
        new_crit = acceptance_criteria if acceptance_criteria is not None else card.get("acceptance_criteria", "")
        new_status = status if status is not None else card["status"]
        new_branch = branch_ref if branch_ref is not None else card.get("branch_ref")

        now_str = datetime.now(timezone.utc).isoformat()
        with self._write_lock, self.get_connection() as conn:
            with conn:
                conn.execute(
                    """
                    UPDATE kanban_cards
                    SET title = ?, description = ?, acceptance_criteria = ?, status = ?, branch_ref = ?, updated_at = ?
                    WHERE project_uuid = ? AND card_id = ?;
                    """,
                    (new_title, new_desc, new_crit, new_status, new_branch, now_str, project_uuid, card_id)
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
            row = conn.execute("SELECT * FROM swarm_nodes WHERE node_uuid = ?;", (node_uuid,))
            r = row.fetchone()
            return dict(r) if r else {}

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

    def get_collaborator(self, project_uuid: str, node_uuid: str) -> Optional[Dict[str, Any]]:
        """Recupera la información de un colaborador en un proyecto."""
        with self.get_connection() as conn:
            cur = conn.execute(
                "SELECT * FROM project_collaborators WHERE project_uuid = ? AND node_uuid = ?;",
                (project_uuid, node_uuid)
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def get_project_collaborators(self, project_uuid: str) -> List[Dict[str, Any]]:
        """Lista todos los colaboradores y solicitudes pendientes de un proyecto."""
        with self.get_connection() as conn:
            cur = conn.execute(
                """
                SELECT c.*, n.developer_name, n.status as node_status, n.reviewer_sla_hours
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
                    "UPDATE project_collaborators SET status = 'approved', approved_at = ? WHERE project_uuid = ? AND node_uuid = ?;",
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
        jury_matrix_json: str = "[]"
    ) -> Dict[str, Any]:
        """Crea un nuevo ticket de arbitraje técnico con soporte para Tercera Vía (Síntesis) y Matriz Neutral."""
        now_str = datetime.now(timezone.utc).isoformat()
        with self._write_lock, self.get_connection() as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO dispute_tickets (
                        ticket_id, project_uuid, card_id, title, collision_reason,
                        contender_a_node, contender_b_node, proposal_a_ref, proposal_b_ref,
                        proposal_c_ref, jury_matrix_json, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?);
                    """,
                    (ticket_id, project_uuid, card_id, title, collision_reason,
                     contender_a_node, contender_b_node, proposal_a_ref, proposal_b_ref,
                     proposal_c_ref, jury_matrix_json, now_str)
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
        signature_ed25519: str
    ) -> Dict[str, Any]:
        """Registra el voto humano directo de un desarrollador (Peso fijo 2.0)."""
        now_str = datetime.now(timezone.utc).isoformat()
        with self._write_lock, self.get_connection() as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO human_jury_votes (
                        vote_uuid, ticket_id, node_uuid, developer_name,
                        voted_option, weight, signature_ed25519, created_at
                    ) VALUES (?, ?, ?, ?, ?, 2.0, ?, ?);
                    """,
                    (vote_uuid, ticket_id, node_uuid, developer_name, voted_option, signature_ed25519, now_str)
                )
        return {"vote_uuid": vote_uuid, "weight": 2.0, "voted_option": voted_option}

    def get_jury_votes_summary(self, ticket_id: str) -> Dict[str, Any]:
        """Recupera los votos humanos y de agentes emitidos para un ticket."""
        with self.get_connection() as conn:
            agent_cur = conn.execute("SELECT * FROM jury_borda_scores WHERE ticket_id = ?;", (ticket_id,))
            agent_scores = [dict(r) for r in agent_cur.fetchall()]
            human_cur = conn.execute("SELECT * FROM human_jury_votes WHERE ticket_id = ?;", (ticket_id,))
            human_votes = [dict(r) for r in human_cur.fetchall()]
            return {"agent_scores": agent_scores, "human_votes": human_votes}

    def resolve_dispute_ticket(
        self,
        ticket_id: str,
        winning_option: str,
        final_adr_path: Optional[str] = None
    ) -> bool:
        """Actualiza el ticket a ratificado y registra el ganador final."""
        with self._write_lock, self.get_connection() as conn:
            with conn:
                cur = conn.execute(
                    """
                    UPDATE dispute_tickets
                    SET status = 'ratified', winning_quadrant = ?, final_adr_path = ?
                    WHERE ticket_id = ?;
                    """,
                    (winning_option, final_adr_path, ticket_id)
                )
                return cur.rowcount > 0
