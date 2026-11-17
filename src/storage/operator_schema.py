"""Esquema SQLite de las sesiones del operador (Ola 4, F2.1 del dossier 37). Migracion idempotente, mismo patron que
`relay_schema.py`: crea la tabla si falta y añade las columnas que una version anterior no tuviera. No toca filas.

Una sesion de operador NO guarda ningun secreto: queda ligada al PROCESO del *harness* (pid + `create_time` + ruta del
ejecutable) que el humano aprobo en un dialogo nativo (D11). Si ese proceso muere, si el pid se reutiliza, si el
humano la revoca o si caduca por inactividad, la sesion deja de valer.
"""

from __future__ import annotations

import sqlite3

_OPERATOR_SESSION_COLUMNS = {
    # columna: definicion para ALTER TABLE (solo las añadibles con DEFAULT/NULL)
    "state_reason": "TEXT",
    "harness_name": "TEXT",
    "harness_user": "TEXT",
    "harness_cwd": "TEXT",
    "ended_at_ms": "INTEGER",
    "ended_by": "TEXT",
}


def migrate_operator_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS operator_sessions (
            session_id TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            state_reason TEXT,
            harness_pid INTEGER NOT NULL,
            harness_create_time REAL NOT NULL,
            harness_exe TEXT NOT NULL,
            harness_name TEXT,
            harness_user TEXT,
            harness_cwd TEXT,
            ancestors_json TEXT NOT NULL,
            projects_json TEXT NOT NULL,
            autonomy_level TEXT NOT NULL,
            transport TEXT NOT NULL,
            created_at_ms INTEGER NOT NULL,
            last_seen_at_ms INTEGER NOT NULL,
            idle_timeout_ms INTEGER NOT NULL,
            ended_at_ms INTEGER,
            ended_by TEXT
        );
        """
    )
    cols = {row[1] for row in conn.execute("PRAGMA table_info(operator_sessions);").fetchall()}
    for name, ddl in _OPERATOR_SESSION_COLUMNS.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE operator_sessions ADD COLUMN {name} {ddl};")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_operator_sessions_harness ON operator_sessions(harness_pid, state);"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_operator_sessions_state ON operator_sessions(state);")
