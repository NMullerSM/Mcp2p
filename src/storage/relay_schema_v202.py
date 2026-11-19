"""Esquema v2.0.2 para el relevo (`recipient_seq` y tabla `relay_delivery`).

Dueño: WP-6b. Migración idempotente, sobre `relay_schema.migrate_relay_tables`.
"""

from __future__ import annotations

import sqlite3


def migrate_relay_v202(conn: sqlite3.Connection) -> None:
    """Añade `recipient_seq` (orden FIFO por destinatario) y la tabla `relay_delivery` (detalle de la entrega directa)."""
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table';").fetchall()}
    if "relay_messages" in tables:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(relay_messages);").fetchall()}
        if "recipient_seq" not in cols:
            conn.execute("ALTER TABLE relay_messages ADD COLUMN recipient_seq INTEGER;")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_relay_messages_recipient_seq "
            "ON relay_messages(to_principal, recipient_seq) WHERE recipient_seq IS NOT NULL;"
        )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS relay_delivery (
            msg_id TEXT PRIMARY KEY,
            to_principal TEXT NOT NULL,
            project_uuid TEXT NOT NULL,
            state TEXT NOT NULL,
            reason TEXT,
            batch_id TEXT,
            pty_incarnation TEXT NOT NULL,
            sanitized_controls INTEGER NOT NULL DEFAULT 0,
            bytes_written INTEGER NOT NULL DEFAULT 0,
            reserved_at_ms INTEGER NOT NULL,
            updated_at_ms INTEGER NOT NULL,
            confirmed_at_ms INTEGER
        );
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_relay_delivery_open ON relay_delivery(state, to_principal);")
