"""Esquema v2.0.2 para sesiones persistentes de terminales (`terminal_sessions`, `terminal_session_snapshots`).

Dueño: WP-4a. Stub vacío preparado por WP-0a.
"""

from __future__ import annotations

import sqlite3


def migrate_terminal_sessions(conn: sqlite3.Connection) -> None:
    """Migración idempotente de tablas de sesiones de terminal (rellena WP-4a)."""
    return None
