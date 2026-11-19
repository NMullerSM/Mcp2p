"""Construcción de bases de datos SQLite legadas para pruebas de migración v2.0.2."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional, Union

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
_VERSIONS = {
    "v2_0_1": _FIXTURES_DIR / "db_schema_v2_0_1.sql",
    "bebdcca": _FIXTURES_DIR / "db_schema_bebdcca.sql",
}


def build_legacy_db(path: Union[str, Path], version: str, rows: bool = True) -> Dict[str, Any]:
    """Inicializa una BD SQLite desde el volcado de `version` (`v2_0_1` | `bebdcca`) e inserta filas mínimas."""
    sql_file = _VERSIONS.get(version)
    if sql_file is None:
        raise ValueError(f"Versión legada desconocida: {version!r}. Permitidas: {sorted(_VERSIONS)}.")

    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    ddl = sql_file.read_text(encoding="utf-8")

    project_uuid = "proj-legacy-1"
    pane_slot = "pane_1"
    node_uuid: Optional[str] = None
    queued_msg_id: Optional[str] = None
    parked_msg_id: Optional[str] = None

    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(ddl)
        if rows:
            with conn:
                conn.execute(
                    """
                    INSERT INTO projects (project_uuid, name, base_path)
                    VALUES (?, ?, ?);
                    """,
                    (project_uuid, "Proyecto Legacy", "/tmp/legacy-project"),
                )
                if version == "bebdcca":
                    node_uuid = "pane-node-legacy-1"
                    queued_msg_id = "msg-legacy-queued-1"
                    parked_msg_id = "msg-legacy-parked-1"
                    now_iso = "2026-11-18T00:00:00+00:00"
                    now_ms = 1_763_424_000_000
                    expires_ms = now_ms + 3_600_000
                    conn.execute(
                        """
                        INSERT INTO pane_identities (
                            project_uuid, pane_slot, node_uuid,
                            auto_relay_enabled, created_at, last_spawned_at, worker_mode
                        )
                        VALUES (?, ?, ?, 0, ?, ?, 0);
                        """,
                        (project_uuid, pane_slot, node_uuid, now_iso, now_iso),
                    )
                    base_env = json.dumps({"v": 1, "from": "human:local", "to": f"pane:{node_uuid}"})
                    conn.execute(
                        """
                        INSERT INTO relay_messages (
                            msg_id, project_uuid, from_principal, to_principal, kind,
                            chain_id, hop, parent_msg_id, trust, content_hash, content,
                            envelope_json, state, state_reason, card_id,
                            created_at_ms, expires_at_ms, updated_at_ms
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            queued_msg_id, project_uuid, "human:local", f"pane:{node_uuid}", "note",
                            "chain-legacy-1", 0, None, "human_confirmed", "h-queued", "mensaje en cola",
                            base_env, "queued", None, None,
                            now_ms, expires_ms, now_ms,
                        ),
                    )
                    conn.execute(
                        """
                        INSERT INTO relay_messages (
                            msg_id, project_uuid, from_principal, to_principal, kind,
                            chain_id, hop, parent_msg_id, trust, content_hash, content,
                            envelope_json, state, state_reason, card_id,
                            created_at_ms, expires_at_ms, updated_at_ms
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            parked_msg_id, project_uuid, f"pane:{node_uuid}", "human:local", "note",
                            "chain-legacy-2", 2, None, "pane_authenticated", "h-parked", "mensaje retenido",
                            base_env, "parked", "CHAIN_DEPTH_EXCEEDED", None,
                            now_ms + 1, expires_ms, now_ms + 1,
                        ),
                    )
    finally:
        conn.close()

    return {
        "path": db_path,
        "version": version,
        "project_uuid": project_uuid,
        "pane_slot": pane_slot if node_uuid else None,
        "node_uuid": node_uuid,
        "queued_msg_id": queued_msg_id,
        "parked_msg_id": parked_msg_id,
    }
