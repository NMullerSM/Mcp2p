"""Esquema v2.0.2 para política de nodos y cuarentena (`trusted_nodes`, `node_chain_approvals`, `remote_message_origin`).

Dueño: WP-7.
"""

from __future__ import annotations

import sqlite3


def migrate_node_policy(conn: sqlite3.Connection) -> None:
    """Migración idempotente de tablas de política de nodos remotos (WP-7)."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS trusted_nodes (
            device_fingerprint TEXT PRIMARY KEY,
            device_public_key TEXT NOT NULL,
            device_id TEXT NOT NULL,
            display_name TEXT NOT NULL,
            previous_names TEXT NOT NULL DEFAULT '[]',
            relation TEXT NOT NULL,
            human_pub TEXT,
            policy TEXT NOT NULL,
            first_seen_at_ms INTEGER NOT NULL,
            last_seen_at_ms INTEGER NOT NULL,
            policy_updated_at_ms INTEGER NOT NULL,
            policy_actor TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS node_chain_approvals (
            chain_id TEXT PRIMARY KEY,
            device_fingerprint TEXT NOT NULL,
            approved_at_ms INTEGER NOT NULL,
            expires_at_ms INTEGER NOT NULL,
            approved_by TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS remote_message_origin (
            msg_id TEXT PRIMARY KEY,
            device_fingerprint TEXT NOT NULL,
            transport TEXT NOT NULL,
            received_at_ms INTEGER NOT NULL,
            quarantine_expires_at_ms INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_remote_origin_fp ON remote_message_origin(device_fingerprint, received_at_ms);
        """
    )
