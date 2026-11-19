"""Almacén SQLite para política de nodos remotos, aprobaciones de cadena y origen remoto (v2.0.2, WP-7)."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

from src.adapters.persistence.relay_sqlite import _audit_from_message
from src.core.domain.node_policy import (
    PREVIOUS_NAME_VISIBLE_MS,
    format_fingerprint_short,
)


def _parse_previous_names(raw: Optional[str]) -> List[Dict[str, Any]]:
    if not raw:
        return []
    try:
        loaded = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(loaded, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in loaded:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            out.append({"name": item["name"], "at_ms": int(item.get("at_ms") or 0)})
        elif isinstance(item, str) and item:
            out.append({"name": item, "at_ms": 0})
    return out[-5:]


def _recent_previous_names(entries: List[Dict[str, Any]], now_ms: int) -> List[str]:
    recent: List[str] = []
    for entry in entries:
        at_ms = int(entry.get("at_ms") or 0)
        if at_ms <= 0 or (now_ms - at_ms) <= PREVIOUS_NAME_VISIBLE_MS:
            recent.append(str(entry["name"]))
    return recent


class SqliteNodePolicyStore:
    """Persistencia de `trusted_nodes`, `node_chain_approvals` y `remote_message_origin`."""

    def __init__(self, db: Any) -> None:
        self.db = db

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self.db.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.rollback()
                raise
            else:
                conn.commit()

    def _has_name_collision_in(
        self, conn: sqlite3.Connection, device_fingerprint: str, display_name: str
    ) -> bool:
        clean = str(display_name or "").strip()
        if not clean:
            return False
        row = conn.execute(
            "SELECT 1 FROM trusted_nodes WHERE device_fingerprint != ? "
            "AND LOWER(TRIM(display_name)) = LOWER(TRIM(?)) LIMIT 1;",
            (device_fingerprint, clean),
        ).fetchone()
        return row is not None

    def _hydrate_node(
        self, conn: sqlite3.Connection, raw: sqlite3.Row, *, now_ms: int
    ) -> Dict[str, Any]:
        row = dict(raw)
        entries = _parse_previous_names(row.get("previous_names"))
        recent = _recent_previous_names(entries, now_ms)
        collision = self._has_name_collision_in(
            conn, str(row["device_fingerprint"]), str(row["display_name"])
        )
        return {
            "device_fingerprint": row["device_fingerprint"],
            "fingerprint_short": format_fingerprint_short(row["device_fingerprint"]),
            "device_public_key": row["device_public_key"],
            "device_id": row["device_id"],
            "display_name": row["display_name"],
            "previous_names": recent,
            "previous_name": recent[-1] if recent else None,
            "previous_names_entries": entries,
            "relation": row["relation"],
            "human_pub": row.get("human_pub"),
            "policy": row["policy"],
            "first_seen_at_ms": int(row["first_seen_at_ms"]),
            "last_seen_at_ms": int(row["last_seen_at_ms"]),
            "policy_updated_at_ms": int(row["policy_updated_at_ms"]),
            "policy_actor": row["policy_actor"],
            "name_collision": collision,
        }

    def get_node(self, device_fingerprint: str, *, now_ms: int = 0) -> Optional[Dict[str, Any]]:
        with self.db.get_connection() as conn:
            raw = conn.execute(
                "SELECT * FROM trusted_nodes WHERE device_fingerprint = ?;",
                (device_fingerprint,),
            ).fetchone()
            if raw is None:
                return None
            ref_ms = now_ms or int(raw["last_seen_at_ms"])
            return self._hydrate_node(conn, raw, now_ms=ref_ms)

    def list_nodes(self, *, now_ms: int = 0) -> List[Dict[str, Any]]:
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM trusted_nodes ORDER BY last_seen_at_ms DESC, first_seen_at_ms ASC;"
            ).fetchall()
            out: List[Dict[str, Any]] = []
            for raw in rows:
                ref_ms = now_ms or int(raw["last_seen_at_ms"])
                out.append(self._hydrate_node(conn, raw, now_ms=ref_ms))
            return out

    def upsert_on_contact(
        self,
        *,
        device_fingerprint: str,
        device_public_key: str,
        device_id: str,
        display_name: str,
        relation: str,
        human_pub: Optional[str],
        default_policy: str,
        now_ms: int,
    ) -> Dict[str, Any]:
        """Registra un nodo en primer contacto (TOFU) o actualiza nombre/última visita sin cambiar su política."""
        with self._tx() as conn:
            existing = conn.execute(
                "SELECT * FROM trusted_nodes WHERE device_fingerprint = ?;",
                (device_fingerprint,),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO trusted_nodes (
                        device_fingerprint, device_public_key, device_id, display_name,
                        previous_names, relation, human_pub, policy,
                        first_seen_at_ms, last_seen_at_ms, policy_updated_at_ms, policy_actor
                    )
                    VALUES (?, ?, ?, ?, '[]', ?, ?, ?, ?, ?, ?, 'default');
                    """,
                    (
                        device_fingerprint,
                        device_public_key,
                        device_id,
                        display_name,
                        relation,
                        human_pub,
                        default_policy,
                        int(now_ms),
                        int(now_ms),
                        int(now_ms),
                    ),
                )
            else:
                old_name = str(existing["display_name"])
                entries = _parse_previous_names(existing["previous_names"])
                if display_name and display_name != old_name:
                    entries = (entries + [{"name": old_name, "at_ms": int(now_ms)}])[-5:]
                conn.execute(
                    """
                    UPDATE trusted_nodes
                    SET display_name = ?,
                        previous_names = ?,
                        last_seen_at_ms = ?,
                        human_pub = COALESCE(human_pub, ?)
                    WHERE device_fingerprint = ?;
                    """,
                    (
                        display_name or old_name,
                        json.dumps(entries, ensure_ascii=False),
                        int(now_ms),
                        human_pub,
                        device_fingerprint,
                    ),
                )
            updated = conn.execute(
                "SELECT * FROM trusted_nodes WHERE device_fingerprint = ?;",
                (device_fingerprint,),
            ).fetchone()
            assert updated is not None
            return self._hydrate_node(conn, updated, now_ms=int(now_ms))

    def set_policy(
        self,
        device_fingerprint: str,
        policy: str,
        *,
        actor: str,
        now_ms: int,
    ) -> Optional[Dict[str, Any]]:
        with self._tx() as conn:
            existing = conn.execute(
                "SELECT * FROM trusted_nodes WHERE device_fingerprint = ?;",
                (device_fingerprint,),
            ).fetchone()
            if existing is None:
                return None
            conn.execute(
                """
                UPDATE trusted_nodes
                SET policy = ?, policy_updated_at_ms = ?, policy_actor = ?
                WHERE device_fingerprint = ?;
                """,
                (policy, int(now_ms), actor, device_fingerprint),
            )
            updated = conn.execute(
                "SELECT * FROM trusted_nodes WHERE device_fingerprint = ?;",
                (device_fingerprint,),
            ).fetchone()
            assert updated is not None
            return self._hydrate_node(conn, updated, now_ms=int(now_ms))

    def is_chain_approved(self, chain_id: str, device_fingerprint: str, now_ms: int) -> bool:
        with self.db.get_connection() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM node_chain_approvals
                WHERE chain_id = ? AND device_fingerprint = ? AND expires_at_ms > ?
                LIMIT 1;
                """,
                (chain_id, device_fingerprint, int(now_ms)),
            ).fetchone()
        return row is not None

    def approve_chain(
        self,
        chain_id: str,
        device_fingerprint: str,
        *,
        approved_at_ms: int,
        expires_at_ms: int,
        approved_by: str,
    ) -> None:
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO node_chain_approvals (
                    chain_id, device_fingerprint, approved_at_ms, expires_at_ms, approved_by
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(chain_id) DO UPDATE SET
                    device_fingerprint = excluded.device_fingerprint,
                    approved_at_ms = excluded.approved_at_ms,
                    expires_at_ms = excluded.expires_at_ms,
                    approved_by = excluded.approved_by;
                """,
                (chain_id, device_fingerprint, int(approved_at_ms), int(expires_at_ms), approved_by),
            )

    def count_parked_for_node(self, device_fingerprint: str) -> int:
        with self.db.get_connection() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) FROM relay_messages m
                JOIN remote_message_origin o ON o.msg_id = m.msg_id
                WHERE o.device_fingerprint = ? AND m.state = 'parked';
                """,
                (device_fingerprint,),
            ).fetchone()
        return int(row[0]) if row else 0

    def record_origin(
        self,
        *,
        msg_id: str,
        device_fingerprint: str,
        transport: str,
        received_at_ms: int,
        quarantine_expires_at_ms: Optional[int],
    ) -> None:
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO remote_message_origin (
                    msg_id, device_fingerprint, transport, received_at_ms, quarantine_expires_at_ms
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(msg_id) DO NOTHING;
                """,
                (
                    msg_id,
                    device_fingerprint,
                    transport,
                    int(received_at_ms),
                    int(quarantine_expires_at_ms) if quarantine_expires_at_ms is not None else None,
                ),
            )

    def get_origin(self, msg_id: str, *, now_ms: int = 0) -> Optional[Dict[str, Any]]:
        with self.db.get_connection() as conn:
            raw = conn.execute(
                """
                SELECT o.msg_id, o.device_fingerprint, o.transport, o.received_at_ms, o.quarantine_expires_at_ms,
                       t.display_name, t.previous_names, t.relation, t.policy, t.last_seen_at_ms, t.device_id
                FROM remote_message_origin o
                JOIN trusted_nodes t ON t.device_fingerprint = o.device_fingerprint
                WHERE o.msg_id = ?;
                """,
                (msg_id,),
            ).fetchone()
            if raw is None:
                return None
            row = dict(raw)
            ref_ms = now_ms or int(row["last_seen_at_ms"])
            entries = _parse_previous_names(row.get("previous_names"))
            recent = _recent_previous_names(entries, ref_ms)
            collision = self._has_name_collision_in(
                conn, str(row["device_fingerprint"]), str(row["display_name"])
            )
            return {
                "msg_id": row["msg_id"],
                "device_fingerprint": row["device_fingerprint"],
                "fingerprint_short": format_fingerprint_short(row["device_fingerprint"]),
                "device_id": row["device_id"],
                "display_name": row["display_name"],
                "previous_names": recent,
                "previous_name": recent[-1] if recent else None,
                "relation": row["relation"],
                "policy": row["policy"],
                "transport": row["transport"],
                "received_at_ms": int(row["received_at_ms"]),
                "expires_at": (
                    int(row["quarantine_expires_at_ms"])
                    if row["quarantine_expires_at_ms"] is not None
                    else None
                ),
                "name_collision": collision,
            }

    def reveal_next_queued_for_node(self, device_fingerprint: str, *, now_ms: int) -> Optional[str]:
        """Promueve la solicitud más antigua `UNTRUSTED_NODE_QUEUED` de `device_fingerprint` a `UNTRUSTED_NODE`."""
        with self._tx() as conn:
            visible = conn.execute(
                """
                SELECT 1 FROM relay_messages m
                JOIN remote_message_origin o ON o.msg_id = m.msg_id
                WHERE o.device_fingerprint = ? AND m.state = 'parked' AND m.state_reason = 'UNTRUSTED_NODE'
                LIMIT 1;
                """,
                (device_fingerprint,),
            ).fetchone()
            if visible is not None:
                return None
            nxt = conn.execute(
                """
                SELECT m.msg_id FROM relay_messages m
                JOIN remote_message_origin o ON o.msg_id = m.msg_id
                WHERE o.device_fingerprint = ? AND m.state = 'parked' AND m.state_reason = 'UNTRUSTED_NODE_QUEUED'
                ORDER BY m.created_at_ms ASC, m.msg_id ASC
                LIMIT 1;
                """,
                (device_fingerprint,),
            ).fetchone()
            if nxt is None:
                return None
            msg_id = str(nxt[0])
            conn.execute(
                """
                UPDATE relay_messages
                SET state_reason = 'UNTRUSTED_NODE', updated_at_ms = ?
                WHERE msg_id = ? AND state = 'parked';
                """,
                (int(now_ms), msg_id),
            )
            return msg_id

    def expire_quarantine(self, relay_store: Any, now_ms: int) -> int:
        """Caduca los mensajes remotos aparcados cuya cuarentena venció en `now_ms`, con auditoría."""
        now = int(now_ms)
        deferred_audits: List[Dict[str, Any]] = []
        expired_count = 0
        with self._tx() as conn:
            rows = conn.execute(
                """
                SELECT m.* FROM relay_messages m
                JOIN remote_message_origin o ON o.msg_id = m.msg_id
                WHERE m.state = 'parked'
                  AND o.quarantine_expires_at_ms IS NOT NULL
                  AND o.quarantine_expires_at_ms <= ?
                ORDER BY m.created_at_ms ASC, m.msg_id ASC;
                """,
                (now,),
            ).fetchall()
            append_in = getattr(relay_store, "_append_audit_in", None)
            for raw in rows:
                row = dict(raw)
                conn.execute(
                    """
                    UPDATE relay_messages
                    SET state = 'expired', state_reason = 'QUARANTINE_EXPIRED', updated_at_ms = ?
                    WHERE msg_id = ? AND state = 'parked';
                    """,
                    (now, row["msg_id"]),
                )
                entry = _audit_from_message(
                    row, "expired", actor="hub", reason_code="QUARANTINE_EXPIRED", ts_ms=now
                )
                if callable(append_in):
                    append_in(conn, entry, now)
                else:
                    deferred_audits.append(entry)
                expired_count += 1
        for entry in deferred_audits:
            relay_store.append_audit(entry)
        return expired_count

    def discard_parked_for_node(
        self,
        device_fingerprint: str,
        relay_store: Any,
        *,
        now_ms: int,
        actor: str,
    ) -> int:
        """Descarta todas las solicitudes pendientes (`parked`) de un nodo bloqueado dejando auditoría."""
        with self.db.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT m.msg_id FROM relay_messages m
                JOIN remote_message_origin o ON o.msg_id = m.msg_id
                WHERE o.device_fingerprint = ? AND m.state = 'parked'
                ORDER BY m.created_at_ms ASC, m.msg_id ASC;
                """,
                (device_fingerprint,),
            ).fetchall()
        count = 0
        for raw in rows:
            res = relay_store.resolve_parked(str(raw[0]), "discard", now_ms=int(now_ms), actor=actor)
            if res is not None:
                count += 1
        return count
