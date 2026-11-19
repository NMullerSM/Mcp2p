"""Esquema v2.0.2 para sesiones persistentes de terminales (`terminal_sessions`, `terminal_session_snapshots`).

Dueño: WP-4a. Prohibido almacenar salida del terminal, scrollback, buffer, pantalla o variables de entorno.
"""

from __future__ import annotations

import sqlite3
from typing import FrozenSet

TERMINAL_SESSIONS_COLUMNS: FrozenSet[str] = frozenset(
    {
        "project_uuid",
        "pane_slot",
        "cli_id",
        "cwd",
        "position",
        "cli_session_id",
        "cli_session_source",
        "state",
        "last_exit_code",
        "opened_at",
        "updated_at",
    }
)

TERMINAL_SESSION_SNAPSHOTS_COLUMNS: FrozenSet[str] = frozenset(
    {
        "project_uuid",
        "closed_cleanly",
        "saved_at",
        "restored_at",
        "dismissed_at",
        "app_version",
    }
)


def migrate_terminal_sessions(conn: sqlite3.Connection) -> None:
    """Migración idempotente de tablas de sesiones de terminal (v2.0.2)."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS terminal_sessions (
            project_uuid TEXT NOT NULL,
            pane_slot TEXT NOT NULL,
            cli_id TEXT NOT NULL,
            cwd TEXT,
            position INTEGER NOT NULL DEFAULT 0,
            cli_session_id TEXT,
            cli_session_source TEXT NOT NULL DEFAULT 'none',
            state TEXT NOT NULL DEFAULT 'open',
            last_exit_code INTEGER,
            opened_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (project_uuid, pane_slot)
        );
        CREATE INDEX IF NOT EXISTS idx_terminal_sessions_proj_state
            ON terminal_sessions(project_uuid, state, position, pane_slot);

        CREATE TABLE IF NOT EXISTS terminal_session_snapshots (
            project_uuid TEXT PRIMARY KEY,
            closed_cleanly INTEGER NOT NULL DEFAULT 0,
            saved_at TEXT NOT NULL,
            restored_at TEXT,
            dismissed_at TEXT,
            app_version TEXT NOT NULL
        );
        """
    )
