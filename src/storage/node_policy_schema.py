"""Esquema v2.0.2 para política de nodos y cuarentena (`trusted_nodes`, `node_chain_approvals`, `remote_message_origin`).

Dueño: WP-7. Stub vacío preparado por WP-0a.
"""

from __future__ import annotations

import sqlite3


def migrate_node_policy(conn: sqlite3.Connection) -> None:
    """Migración idempotente de tablas de política de nodos remotos (rellena WP-7)."""
    return None
