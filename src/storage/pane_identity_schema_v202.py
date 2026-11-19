"""Esquema v2.0.2 para `pane_identities` (roles, mandato y entrega por panel).

Dueño: WP-3a. Stub vacío preparado por WP-0a.
"""

from __future__ import annotations

import sqlite3


def migrate_pane_identities_v202(conn: sqlite3.Connection) -> None:
    """Migración idempotente de columnas 2.0.2 en `pane_identities` (rellena WP-3a)."""
    return None
