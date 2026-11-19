"""Orquestador de migraciones SQLite de MCP2P 2.0.2 (cada módulo gestiona su parte)."""

from __future__ import annotations

import sqlite3

from src.storage import (
    node_policy_schema,
    pane_identity_schema_v202,
    relay_schema_v202,
    terminal_session_schema,
)


def migrate_v202(conn: sqlite3.Connection) -> None:
    """Ejecuta en orden las migraciones idempotentes de la versión 2.0.2 sobre `conn`."""
    pane_identity_schema_v202.migrate_pane_identities_v202(conn)
    relay_schema_v202.migrate_relay_v202(conn)
    terminal_session_schema.migrate_terminal_sessions(conn)
    node_policy_schema.migrate_node_policy(conn)
