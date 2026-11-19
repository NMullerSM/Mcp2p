"""Esquema v2.0.2 para `pane_identities` (roles, mandato y entrega por panel).

Dueño: WP-3a.
"""

from __future__ import annotations

import sqlite3
from typing import Tuple

_V202_COLUMNS: Tuple[Tuple[str, str], ...] = (
    ("role", "TEXT NOT NULL DEFAULT 'peer'"),
    ("mandate", "TEXT NOT NULL DEFAULT ''"),
    ("role_updated_at", "TEXT"),
    ("delivery_mode", "TEXT NOT NULL DEFAULT 'auto'"),
    ("delivery_batch", "INTEGER NOT NULL DEFAULT 0"),
)


def migrate_pane_identities_v202(conn: sqlite3.Connection) -> None:
    """Migración idempotente de columnas 2.0.2 en `pane_identities` (roles, mandato y entrega)."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(pane_identities);").fetchall()}
    if not cols:
        return
    for name, ddl in _V202_COLUMNS:
        if name not in cols:
            conn.execute(f"ALTER TABLE pane_identities ADD COLUMN {name} {ddl};")
