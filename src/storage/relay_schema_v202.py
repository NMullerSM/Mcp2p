"""Esquema v2.0.2 para el relevo (`recipient_seq` y tabla `relay_delivery`).

Dueño: WP-6b. Stub vacío preparado por WP-0a.
"""

from __future__ import annotations

import sqlite3


def migrate_relay_v202(conn: sqlite3.Connection) -> None:
    """Migración idempotente de `relay_messages` y `relay_delivery` (rellena WP-6b)."""
    return None
