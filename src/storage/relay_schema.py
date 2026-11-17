"""Esquema SQLite del relevo persistente (Ola 3, F1.4). Migracion idempotente, mismo patron que `pane_identities`.

Crea las tablas si faltan y añade las columnas que una version anterior no tuviera. No toca filas.
"""

from __future__ import annotations

import sqlite3

_RELAY_MESSAGES_COLUMNS = {
    # columna: definicion para ALTER TABLE (solo las añadibles con DEFAULT/NULL)
    "state_reason": "TEXT",
    "card_id": "TEXT",
    "delivered_at_ms": "INTEGER",
    "acked_at_ms": "INTEGER",
    "purged_at_ms": "INTEGER",
    # Reserva del puntero constante (Ola 4, dossier 44 §3.1-3.3): RESERVED/WRITE_ATTEMPTED/ENTER_ATTEMPTED/SETTLED
    # y la encarnacion del PTY al reservar. SIN USO en esta version: ningun codigo las escribe.
    "pointer_state": "TEXT",
    "pointer_pty_incarnation": "TEXT",
}


def migrate_relay_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS relay_messages (
            msg_id TEXT PRIMARY KEY,
            project_uuid TEXT NOT NULL,
            from_principal TEXT NOT NULL,
            to_principal TEXT NOT NULL,
            kind TEXT NOT NULL,
            chain_id TEXT NOT NULL,
            hop INTEGER NOT NULL,
            parent_msg_id TEXT,
            trust TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            content TEXT,
            envelope_json TEXT NOT NULL,
            state TEXT NOT NULL,
            state_reason TEXT,
            card_id TEXT,
            created_at_ms INTEGER NOT NULL,
            expires_at_ms INTEGER NOT NULL,
            updated_at_ms INTEGER NOT NULL,
            delivered_at_ms INTEGER,
            acked_at_ms INTEGER,
            purged_at_ms INTEGER,
            pointer_state TEXT,
            pointer_pty_incarnation TEXT
        );
        """
    )
    cols = {row[1] for row in conn.execute("PRAGMA table_info(relay_messages);").fetchall()}
    for name, ddl in _RELAY_MESSAGES_COLUMNS.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE relay_messages ADD COLUMN {name} {ddl};")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_relay_messages_inbox ON relay_messages(to_principal, state, created_at_ms);"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_relay_messages_project ON relay_messages(project_uuid, state);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_relay_messages_chain ON relay_messages(chain_id);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_relay_messages_created ON relay_messages(created_at_ms);")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS relay_audit (
            seq INTEGER PRIMARY KEY,
            ts_ms INTEGER NOT NULL,
            event TEXT NOT NULL,
            msg_id TEXT,
            chain_id TEXT,
            hop INTEGER,
            project_uuid TEXT,
            from_principal TEXT,
            to_principal TEXT,
            trust TEXT,
            content_hash TEXT,
            decision TEXT,
            reason_code TEXT,
            actor TEXT,
            prev_hash TEXT NOT NULL,
            row_hash TEXT NOT NULL
        );
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_relay_audit_msg ON relay_audit(msg_id);")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS relay_pause (
            scope TEXT PRIMARY KEY,
            paused INTEGER NOT NULL DEFAULT 0,
            actor TEXT,
            reason TEXT,
            updated_at_ms INTEGER NOT NULL
        );
        """
    )
