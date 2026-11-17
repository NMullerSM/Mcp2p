"""Bandeja persistente, auditoria con cadena de hashes y parada del relevo sobre SQLite (Ola 3, F1.4).

Cada escritura que cambia el estado de un mensaje anota su evento de auditoria en la MISMA transaccion
(`BEGIN IMMEDIATE`), asi que no hay mensaje sin rastro ni rastro sin mensaje.

Cadena de auditoria: `row_hash = sha256(prev_hash + "\\n" + json_ordenado(campos de la fila incluido seq))`, con
`prev_hash` de la primera fila = 64 ceros. `verify_chain()` detecta la edicion, el borrado o la insercion de una fila
intermedia. Limite honesto: quien tenga el mismo usuario puede reescribir toda la tabla y recalcular los hashes; esto
detecta errores y manipulaciones casuales, no a un atacante local decidido (anclaje externo: Ola 5/6).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, Iterable, Iterator, List, Optional

GENESIS_HASH = "0" * 64
_AUDIT_FIELDS = (
    "seq",
    "ts_ms",
    "event",
    "msg_id",
    "chain_id",
    "hop",
    "project_uuid",
    "from_principal",
    "to_principal",
    "trust",
    "content_hash",
    "decision",
    "reason_code",
    "actor",
)
_MESSAGE_COLUMNS = (
    "msg_id",
    "project_uuid",
    "from_principal",
    "to_principal",
    "kind",
    "chain_id",
    "hop",
    "parent_msg_id",
    "trust",
    "content_hash",
    "content",
    "envelope_json",
    "state",
    "state_reason",
    "card_id",
    "created_at_ms",
    "expires_at_ms",
    "updated_at_ms",
)


def audit_row_hash(prev_hash: str, fields: Dict[str, Any]) -> str:
    body = json.dumps({k: fields.get(k) for k in _AUDIT_FIELDS}, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256((prev_hash + "\n" + body).encode("utf-8")).hexdigest()


def _audit_from_message(row: Dict[str, Any], event: str, **extra: Any) -> Dict[str, Any]:
    entry = {
        "event": event,
        "msg_id": row.get("msg_id"),
        "chain_id": row.get("chain_id"),
        "hop": row.get("hop"),
        "project_uuid": row.get("project_uuid"),
        "from_principal": row.get("from_principal"),
        "to_principal": row.get("to_principal"),
        "trust": row.get("trust"),
        "content_hash": row.get("content_hash"),
    }
    entry.update(extra)
    return entry


class SqliteRelayStore:
    """`MessageStorePort` + `RelayAuditPort` + `RelayPausePort` sobre la BD de MCP2P."""

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

    # -- auditoria -------------------------------------------------------------------------------------------------

    def _append_audit_in(self, conn: sqlite3.Connection, entry: Dict[str, Any], ts_ms: int) -> Dict[str, Any]:
        last = conn.execute("SELECT seq, row_hash FROM relay_audit ORDER BY seq DESC LIMIT 1;").fetchone()
        seq = int(last[0]) + 1 if last else 1
        prev_hash = str(last[1]) if last else GENESIS_HASH
        fields = {k: entry.get(k) for k in _AUDIT_FIELDS}
        fields["seq"] = seq
        fields["ts_ms"] = int(entry.get("ts_ms") or ts_ms)
        fields["hop"] = None if fields.get("hop") is None else int(fields["hop"])
        row_hash = audit_row_hash(prev_hash, fields)
        conn.execute(
            f"INSERT INTO relay_audit ({', '.join(_AUDIT_FIELDS)}, prev_hash, row_hash) "
            f"VALUES ({', '.join('?' for _ in _AUDIT_FIELDS)}, ?, ?);",
            (*[fields[k] for k in _AUDIT_FIELDS], prev_hash, row_hash),
        )
        return {**fields, "prev_hash": prev_hash, "row_hash": row_hash}

    def append_audit(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        with self._tx() as conn:
            return self._append_audit_in(conn, entry, int(entry.get("ts_ms") or 0))

    def verify_chain(self) -> Dict[str, Any]:
        with self.db.get_connection() as conn:
            rows = conn.execute(f"SELECT {', '.join(_AUDIT_FIELDS)}, prev_hash, row_hash FROM relay_audit ORDER BY seq;").fetchall()
        expected_prev = GENESIS_HASH
        expected_seq = 1
        for raw in rows:
            row = dict(raw)
            if int(row["seq"]) != expected_seq:
                return {"ok": False, "rows": len(rows), "first_bad_seq": int(row["seq"]), "reason": "SEQ_GAP"}
            if row["prev_hash"] != expected_prev:
                return {"ok": False, "rows": len(rows), "first_bad_seq": int(row["seq"]), "reason": "PREV_HASH_MISMATCH"}
            if audit_row_hash(row["prev_hash"], row) != row["row_hash"]:
                return {"ok": False, "rows": len(rows), "first_bad_seq": int(row["seq"]), "reason": "ROW_HASH_MISMATCH"}
            expected_prev = row["row_hash"]
            expected_seq += 1
        return {"ok": True, "rows": len(rows), "head_hash": expected_prev, "first_bad_seq": None, "reason": None}

    def audit_counts(self) -> Dict[str, int]:
        with self.db.get_connection() as conn:
            rows = conn.execute("SELECT event, COUNT(*) FROM relay_audit GROUP BY event;").fetchall()
        return {str(r[0]): int(r[1]) for r in rows}

    def audit_for(self, msg_id: str) -> List[Dict[str, Any]]:
        with self.db.get_connection() as conn:
            rows = conn.execute("SELECT * FROM relay_audit WHERE msg_id = ? ORDER BY seq;", (msg_id,)).fetchall()
        return [dict(r) for r in rows]

    # -- mensajes --------------------------------------------------------------------------------------------------

    def insert_message(self, row: Dict[str, Any], audit: Iterable[Dict[str, Any]]) -> bool:
        """Inserta el sobre y sus eventos en una transaccion. False si el `msg_id` ya existia (repeticion)."""
        ts = int(row["created_at_ms"])
        try:
            with self._tx() as conn:
                conn.execute(
                    f"INSERT INTO relay_messages ({', '.join(_MESSAGE_COLUMNS)}) "
                    f"VALUES ({', '.join('?' for _ in _MESSAGE_COLUMNS)});",
                    tuple(row.get(c) for c in _MESSAGE_COLUMNS),
                )
                for entry in audit:
                    self._append_audit_in(conn, entry, ts)
        except sqlite3.IntegrityError:
            return False
        return True

    def get_message(self, msg_id: str) -> Optional[Dict[str, Any]]:
        with self.db.get_connection() as conn:
            row = conn.execute("SELECT * FROM relay_messages WHERE msg_id = ?;", (msg_id,)).fetchone()
        return dict(row) if row else None

    def count_pending(self, to_principal: str) -> int:
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM relay_messages WHERE to_principal = ? AND state IN ('queued', 'delivered');",
                (to_principal,),
            ).fetchone()
        return int(row[0])

    def has_deliverable(self, to_principal: str, project_uuid: str, now_ms: int) -> bool:
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM relay_messages WHERE to_principal = ? AND project_uuid = ? AND state = 'queued' "
                "AND expires_at_ms > ? LIMIT 1;",
                (to_principal, project_uuid, int(now_ms)),
            ).fetchone()
        return row is not None

    def _expire_in(self, conn: sqlite3.Connection, now_ms: int, where: str = "", params: tuple = ()) -> int:
        rows = conn.execute(
            "SELECT * FROM relay_messages WHERE state IN ('queued', 'delivered', 'parked') AND expires_at_ms <= ?"
            + where + ";",
            (int(now_ms), *params),
        ).fetchall()
        for raw in rows:
            row = dict(raw)
            conn.execute(
                "UPDATE relay_messages SET state = 'expired', state_reason = 'EXPIRED', updated_at_ms = ? WHERE msg_id = ?;",
                (int(now_ms), row["msg_id"]),
            )
            self._append_audit_in(conn, _audit_from_message(row, "expired", actor="hub", reason_code="EXPIRED"), now_ms)
        return len(rows)

    def expire_due(self, now_ms: int) -> int:
        with self._tx() as conn:
            return self._expire_in(conn, now_ms)

    def take_for_delivery(
        self, to_principal: str, project_uuid: str, *, limit: int, include_unacked: bool, now_ms: int, actor: str
    ) -> List[Dict[str, Any]]:
        states = ("queued", "delivered") if include_unacked else ("queued",)
        with self._tx() as conn:
            self._expire_in(conn, now_ms, " AND to_principal = ?", (to_principal,))
            rows = conn.execute(
                f"SELECT * FROM relay_messages WHERE to_principal = ? AND project_uuid = ? AND state IN "
                f"({', '.join('?' for _ in states)}) ORDER BY created_at_ms, msg_id LIMIT ?;",
                (to_principal, project_uuid, *states, max(1, int(limit))),
            ).fetchall()
            taken: List[Dict[str, Any]] = []
            for raw in rows:
                row = dict(raw)
                redelivery = row["state"] == "delivered"
                conn.execute(
                    "UPDATE relay_messages SET state = 'delivered', delivered_at_ms = ?, updated_at_ms = ? WHERE msg_id = ?;",
                    (int(now_ms), int(now_ms), row["msg_id"]),
                )
                self._append_audit_in(
                    conn,
                    _audit_from_message(row, "delivered", actor=actor, decision="redelivered" if redelivery else "delivered"),
                    now_ms,
                )
                row["redelivered"] = redelivery
                row["state"] = "delivered"
                row["delivered_at_ms"] = int(now_ms)
                taken.append(row)
        return taken

    def mark_acked(self, msg_ids: Iterable[str], to_principal: str, *, now_ms: int, actor: str) -> List[str]:
        acked: List[str] = []
        with self._tx() as conn:
            for msg_id in msg_ids:
                raw = conn.execute(
                    "SELECT * FROM relay_messages WHERE msg_id = ? AND to_principal = ? AND state = 'delivered';",
                    (msg_id, to_principal),
                ).fetchone()
                if raw is None:
                    continue
                row = dict(raw)
                conn.execute(
                    "UPDATE relay_messages SET state = 'acked', acked_at_ms = ?, updated_at_ms = ? WHERE msg_id = ?;",
                    (int(now_ms), int(now_ms), msg_id),
                )
                self._append_audit_in(conn, _audit_from_message(row, "acked", actor=actor), now_ms)
                acked.append(msg_id)
        return acked

    def replies_to(self, parent_msg_id: str, to_principal: str) -> List[Dict[str, Any]]:
        """Respuestas (`parent_msg_id`) dirigidas a `to_principal` que siguen vivas (canal del operador, Ola 4)."""
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM relay_messages WHERE parent_msg_id = ? AND to_principal = ? "
                "AND state IN ('queued', 'delivered', 'acked') ORDER BY created_at_ms, msg_id;",
                (parent_msg_id, to_principal),
            ).fetchall()
        return [dict(r) for r in rows]

    def take_replies(self, parent_msg_id: str, to_principal: str, *, now_ms: int, actor: str) -> List[Dict[str, Any]]:
        """Entrega Y confirma las respuestas a `parent_msg_id` para `to_principal` (el operador las recoge con
        `operator_await_reply`); devuelve tambien las ya entregadas antes (lectura idempotente)."""
        with self._tx() as conn:
            self._expire_in(conn, now_ms, " AND to_principal = ?", (to_principal,))
            rows = conn.execute(
                "SELECT * FROM relay_messages WHERE parent_msg_id = ? AND to_principal = ? "
                "AND state IN ('queued', 'delivered', 'acked') ORDER BY created_at_ms, msg_id;",
                (parent_msg_id, to_principal),
            ).fetchall()
            out: List[Dict[str, Any]] = []
            for raw in rows:
                row = dict(raw)
                if row["state"] == "queued":
                    self._append_audit_in(conn, _audit_from_message(row, "delivered", actor=actor, decision="delivered"), now_ms)
                if row["state"] in ("queued", "delivered"):
                    conn.execute(
                        "UPDATE relay_messages SET state = 'acked', delivered_at_ms = COALESCE(delivered_at_ms, ?), "
                        "acked_at_ms = ?, updated_at_ms = ? WHERE msg_id = ?;",
                        (int(now_ms), int(now_ms), int(now_ms), row["msg_id"]),
                    )
                    self._append_audit_in(conn, _audit_from_message(row, "acked", actor=actor), now_ms)
                    row["state"] = "acked"
                out.append(row)
        return out

    def list_parked(self, project_uuid: Optional[str]) -> List[Dict[str, Any]]:
        with self.db.get_connection() as conn:
            if project_uuid is None:
                rows = conn.execute("SELECT * FROM relay_messages WHERE state = 'parked' ORDER BY created_at_ms;").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM relay_messages WHERE state = 'parked' AND project_uuid = ? ORDER BY created_at_ms;",
                    (project_uuid,),
                ).fetchall()
        return [dict(r) for r in rows]

    def resolve_parked(self, msg_id: str, decision: str, *, now_ms: int, actor: str) -> Optional[Dict[str, Any]]:
        if decision not in ("release", "discard"):
            raise ValueError("decision debe ser release o discard")
        with self._tx() as conn:
            self._expire_in(conn, now_ms, " AND msg_id = ?", (msg_id,))
            raw = conn.execute("SELECT * FROM relay_messages WHERE msg_id = ? AND state = 'parked';", (msg_id,)).fetchone()
            if raw is None:
                return None
            row = dict(raw)
            new_state, event, reason = (
                ("queued", "released", "HUMAN_RELEASED") if decision == "release" else ("discarded", "discarded", "HUMAN_DISCARDED")
            )
            conn.execute(
                "UPDATE relay_messages SET state = ?, state_reason = ?, updated_at_ms = ? WHERE msg_id = ?;",
                (new_state, reason, int(now_ms), msg_id),
            )
            self._append_audit_in(
                conn, _audit_from_message(row, event, actor=actor, decision=decision, reason_code=reason), now_ms
            )
            row.update(state=new_state, state_reason=reason)
            return row

    def purge_content(self, older_than_ms: int, *, now_ms: int) -> int:
        """Retencion (D19): borra el TEXTO de los mensajes anteriores a `older_than_ms`; el sobre (con su hash) y la
        auditoria se conservan siempre."""
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT * FROM relay_messages WHERE content IS NOT NULL AND created_at_ms < ?;", (int(older_than_ms),)
            ).fetchall()
            for raw in rows:
                row = dict(raw)
                conn.execute(
                    "UPDATE relay_messages SET content = NULL, purged_at_ms = ?, updated_at_ms = ? WHERE msg_id = ?;",
                    (int(now_ms), int(now_ms), row["msg_id"]),
                )
                self._append_audit_in(
                    conn, _audit_from_message(row, "purged", actor="hub", reason_code="RETENTION"), now_ms
                )
        return len(rows)

    def message_counts(self) -> Dict[str, int]:
        with self.db.get_connection() as conn:
            rows = conn.execute("SELECT state, COUNT(*) FROM relay_messages GROUP BY state;").fetchall()
        return {str(r[0]): int(r[1]) for r in rows}

    # -- parada ------------------------------------------------------------------------------------------------------

    def set_pause(self, scope: str, paused: bool, *, actor: str, reason: str, now_ms: int) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO relay_pause (scope, paused, actor, reason, updated_at_ms) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(scope) DO UPDATE SET paused = excluded.paused, actor = excluded.actor, "
                "reason = excluded.reason, updated_at_ms = excluded.updated_at_ms;",
                (scope, 1 if paused else 0, actor, reason, int(now_ms)),
            )
            self._append_audit_in(
                conn,
                {"event": "paused" if paused else "resumed", "to_principal": scope, "actor": actor,
                 "reason_code": reason or None},
                now_ms,
            )

    def is_paused(self, scope: str) -> bool:
        with self.db.get_connection() as conn:
            row = conn.execute("SELECT paused FROM relay_pause WHERE scope = ?;", (scope,)).fetchone()
        return bool(row and row[0])

    def list_pauses(self) -> List[Dict[str, Any]]:
        with self.db.get_connection() as conn:
            rows = conn.execute("SELECT * FROM relay_pause WHERE paused = 1 ORDER BY scope;").fetchall()
        return [dict(r) for r in rows]
