"""Repositorio SQLite para sesiones persistentes de terminales e instantáneas por proyecto (v2.0.2, WP-4a)."""

from __future__ import annotations

import re
import sqlite3
from contextlib import nullcontext
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

_CLI_SESSION_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")
_PANE_SLOT_NUM_RE = re.compile(r"pane_(\d+)$")

VALID_SESSION_STATES = frozenset({"open", "closed_by_user", "closed_by_app", "exited"})
VALID_SESSION_SOURCES = frozenset({"assigned", "hook", "none"})

_UNSET = object()


def validate_cli_session_id(value: Any) -> str:
    """Valida que `cli_session_id` cumpla `^[A-Za-z0-9_-]{1,128}$` y no empiece por `-`."""
    if not isinstance(value, str) or value.startswith("-") or _CLI_SESSION_ID_RE.fullmatch(value) is None:
        raise ValueError("cli_session_id inválido: debe cumplir ^[A-Za-z0-9_-]{1,128}$ sin '-' inicial.")
    return value


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_position_for_slot(pane_slot: str) -> int:
    match = _PANE_SLOT_NUM_RE.match(pane_slot)
    return int(match.group(1)) if match else 0


def _decode_session_row(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "project_uuid": row["project_uuid"],
        "pane_slot": row["pane_slot"],
        "cli_id": row["cli_id"],
        "cwd": row["cwd"],
        "position": int(row["position"]),
        "cli_session_id": row["cli_session_id"],
        "cli_session_source": row["cli_session_source"],
        "state": row["state"],
        "last_exit_code": row["last_exit_code"],
        "opened_at": row["opened_at"],
        "updated_at": row["updated_at"],
    }


def _decode_snapshot_row(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "project_uuid": row["project_uuid"],
        "closed_cleanly": bool(row["closed_cleanly"]),
        "saved_at": row["saved_at"],
        "restored_at": row["restored_at"],
        "dismissed_at": row["dismissed_at"],
        "app_version": row["app_version"],
    }


class TerminalSessionSqliteRepo:
    """Persistencia SQLite de `terminal_sessions` y `terminal_session_snapshots`."""

    def __init__(self, db: Any) -> None:
        self.db = db

    def _write_lock(self) -> Any:
        return getattr(self.db, "_write_lock", None) or nullcontext()

    def get(self, project_uuid: str, pane_slot: str) -> Optional[Dict[str, Any]]:
        """Devuelve la fila de `(project_uuid, pane_slot)` o `None`."""
        proj = project_uuid or ""
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM terminal_sessions WHERE project_uuid = ? AND pane_slot = ?;",
                (proj, pane_slot),
            ).fetchone()
        return _decode_session_row(row) if row else None

    def upsert(
        self,
        *,
        project_uuid: str,
        pane_slot: str,
        cli_id: str,
        cwd: Optional[str] = None,
        position: Optional[int] = None,
        cli_session_id: Optional[str] = None,
        cli_session_source: str = "none",
        state: str = "open",
        last_exit_code: Optional[int] = None,
        now_iso: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Inserta o actualiza una sesión de panel por `(project_uuid, pane_slot)`."""
        if not isinstance(pane_slot, str) or not pane_slot.strip():
            raise ValueError("pane_slot es obligatorio.")
        if not isinstance(cli_id, str) or not cli_id.strip():
            raise ValueError("cli_id es obligatorio.")
        if state not in VALID_SESSION_STATES:
            raise ValueError(f"Estado de sesión inválido: {state!r}.")
        if cli_session_source not in VALID_SESSION_SOURCES:
            raise ValueError(f"Origen de cli_session_id inválido: {cli_session_source!r}.")

        validated_sid: Optional[str] = None
        if cli_session_id is not None:
            validated_sid = validate_cli_session_id(cli_session_id)

        proj = project_uuid or ""
        slot = pane_slot.strip()
        pos = _default_position_for_slot(slot) if position is None else int(position)
        ts = now_iso or _utc_now_iso()

        with self._write_lock(), self.db.get_connection() as conn:
            with conn:
                effective_source = cli_session_source
                if validated_sid is None:
                    effective_source = "none"
                elif effective_source == "none":
                    existing = conn.execute(
                        "SELECT cli_session_id, cli_session_source FROM terminal_sessions "
                        "WHERE project_uuid = ? AND pane_slot = ?;",
                        (proj, slot),
                    ).fetchone()
                    if (
                        existing is not None
                        and existing["cli_session_id"] == validated_sid
                        and existing["cli_session_source"] in ("assigned", "hook")
                    ):
                        effective_source = str(existing["cli_session_source"])
                    else:
                        effective_source = "assigned"

                conn.execute(
                    """
                    INSERT INTO terminal_sessions (
                        project_uuid, pane_slot, cli_id, cwd, position,
                        cli_session_id, cli_session_source, state, last_exit_code,
                        opened_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(project_uuid, pane_slot) DO UPDATE SET
                        cli_id = excluded.cli_id,
                        cwd = excluded.cwd,
                        position = excluded.position,
                        cli_session_id = excluded.cli_session_id,
                        cli_session_source = excluded.cli_session_source,
                        state = excluded.state,
                        last_exit_code = excluded.last_exit_code,
                        opened_at = excluded.opened_at,
                        updated_at = excluded.updated_at;
                    """,
                    (
                        proj,
                        slot,
                        cli_id.strip(),
                        cwd,
                        pos,
                        validated_sid,
                        effective_source,
                        state,
                        last_exit_code,
                        ts,
                        ts,
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM terminal_sessions WHERE project_uuid = ? AND pane_slot = ?;",
                    (proj, slot),
                ).fetchone()
        assert row is not None
        return _decode_session_row(row)

    def set_state(
        self,
        project_uuid: str,
        pane_slot: str,
        state: str,
        *,
        last_exit_code: Optional[int] = None,
        now_iso: Optional[str] = None,
    ) -> bool:
        """Actualiza el estado de una fila existente; devuelve True si existía."""
        if state not in VALID_SESSION_STATES:
            raise ValueError(f"Estado de sesión inválido: {state!r}.")
        proj = project_uuid or ""
        ts = now_iso or _utc_now_iso()
        with self._write_lock(), self.db.get_connection() as conn:
            with conn:
                if last_exit_code is not None:
                    cur = conn.execute(
                        "UPDATE terminal_sessions SET state = ?, last_exit_code = ?, updated_at = ? "
                        "WHERE project_uuid = ? AND pane_slot = ?;",
                        (state, int(last_exit_code), ts, proj, pane_slot),
                    )
                else:
                    cur = conn.execute(
                        "UPDATE terminal_sessions SET state = ?, updated_at = ? "
                        "WHERE project_uuid = ? AND pane_slot = ?;",
                        (state, ts, proj, pane_slot),
                    )
                return cur.rowcount > 0

    def set_cli_session_id(
        self,
        project_uuid: str,
        pane_slot: str,
        cli_session_id: str,
        *,
        source: str = "hook",
        only_open: bool = False,
        now_iso: Optional[str] = None,
    ) -> bool:
        """Registra un `cli_session_id` validado sobre un panel existente."""
        validated_sid = validate_cli_session_id(cli_session_id)
        if source not in VALID_SESSION_SOURCES:
            raise ValueError(f"Origen de cli_session_id inválido: {source!r}.")
        proj = project_uuid or ""
        ts = now_iso or _utc_now_iso()
        with self._write_lock(), self.db.get_connection() as conn:
            with conn:
                if only_open:
                    cur = conn.execute(
                        "UPDATE terminal_sessions "
                        "SET cli_session_id = ?, cli_session_source = ?, updated_at = ? "
                        "WHERE project_uuid = ? AND pane_slot = ? AND state = 'open';",
                        (validated_sid, source, ts, proj, pane_slot),
                    )
                else:
                    cur = conn.execute(
                        "UPDATE terminal_sessions "
                        "SET cli_session_id = ?, cli_session_source = ?, updated_at = ? "
                        "WHERE project_uuid = ? AND pane_slot = ?;",
                        (validated_sid, source, ts, proj, pane_slot),
                    )
                return cur.rowcount > 0

    def list(
        self,
        project_uuid: str,
        states: Optional[Iterable[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Lista sesiones de un proyecto ordenadas por `(position, pane_slot)`."""
        proj = project_uuid or ""
        if states is not None:
            state_tuple = tuple(states)
            if not state_tuple:
                return []
            for st in state_tuple:
                if st not in VALID_SESSION_STATES:
                    raise ValueError(f"Estado de sesión inválido: {st!r}.")
            placeholders = ", ".join("?" for _ in state_tuple)
            sql = (
                f"SELECT * FROM terminal_sessions "
                f"WHERE project_uuid = ? AND state IN ({placeholders}) "
                f"ORDER BY position ASC, pane_slot ASC;"
            )
            params: tuple[Any, ...] = (proj, *state_tuple)
        else:
            sql = (
                "SELECT * FROM terminal_sessions "
                "WHERE project_uuid = ? "
                "ORDER BY position ASC, pane_slot ASC;"
            )
            params = (proj,)
        with self.db.get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_decode_session_row(r) for r in rows]

    def mark_app_closing(
        self,
        alive_slots: Mapping[str, Sequence[str]],
        *,
        app_version: str,
        now_iso: Optional[str] = None,
    ) -> int:
        """Transiciona sesiones `open` -> `closed_by_app` (vivas) o `exited` (muertas) y marca `closed_cleanly=1`."""
        ts = now_iso or _utc_now_iso()
        alive_by_proj: Dict[str, set[str]] = {
            (proj or ""): {str(slot) for slot in slots}
            for proj, slots in alive_slots.items()
        }
        closed_count = 0
        with self._write_lock(), self.db.get_connection() as conn:
            with conn:
                open_rows = conn.execute(
                    "SELECT project_uuid, pane_slot FROM terminal_sessions WHERE state = 'open';"
                ).fetchall()
                touched_projects: set[str] = set(alive_by_proj.keys())
                for row in open_rows:
                    proj = str(row["project_uuid"])
                    slot = str(row["pane_slot"])
                    touched_projects.add(proj)
                    if slot in alive_by_proj.get(proj, set()):
                        conn.execute(
                            "UPDATE terminal_sessions SET state = 'closed_by_app', updated_at = ? "
                            "WHERE project_uuid = ? AND pane_slot = ? AND state = 'open';",
                            (ts, proj, slot),
                        )
                        closed_count += 1
                    else:
                        conn.execute(
                            "UPDATE terminal_sessions SET state = 'exited', updated_at = ? "
                            "WHERE project_uuid = ? AND pane_slot = ? AND state = 'open';",
                            (ts, proj, slot),
                        )
                for proj in touched_projects:
                    conn.execute(
                        """
                        INSERT INTO terminal_session_snapshots (
                            project_uuid, closed_cleanly, saved_at, restored_at, dismissed_at, app_version
                        )
                        VALUES (?, 1, ?, NULL, NULL, ?)
                        ON CONFLICT(project_uuid) DO UPDATE SET
                            closed_cleanly = 1,
                            saved_at = excluded.saved_at,
                            restored_at = NULL,
                            dismissed_at = NULL,
                            app_version = excluded.app_version;
                        """,
                        (proj, ts, app_version),
                    )
        return closed_count

    def snapshot_get(self, project_uuid: str) -> Optional[Dict[str, Any]]:
        """Devuelve la instantánea de cierre/restauración de `project_uuid` o `None`."""
        proj = project_uuid or ""
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM terminal_session_snapshots WHERE project_uuid = ?;",
                (proj,),
            ).fetchone()
        return _decode_snapshot_row(row) if row else None

    def snapshot_set(
        self,
        project_uuid: str,
        *,
        closed_cleanly: bool = False,
        saved_at: Optional[str] = None,
        restored_at: Any = _UNSET,
        dismissed_at: Any = _UNSET,
        app_version: str = "0.0.0",
    ) -> Dict[str, Any]:
        """Crea o actualiza la instantánea de sesión de un proyecto."""
        proj = project_uuid or ""
        ts = saved_at or _utc_now_iso()
        with self._write_lock(), self.db.get_connection() as conn:
            with conn:
                existing = conn.execute(
                    "SELECT * FROM terminal_session_snapshots WHERE project_uuid = ?;",
                    (proj,),
                ).fetchone()
                resolved_restored = (
                    (existing["restored_at"] if existing is not None else None)
                    if restored_at is _UNSET
                    else restored_at
                )
                resolved_dismissed = (
                    (existing["dismissed_at"] if existing is not None else None)
                    if dismissed_at is _UNSET
                    else dismissed_at
                )
                conn.execute(
                    """
                    INSERT INTO terminal_session_snapshots (
                        project_uuid, closed_cleanly, saved_at, restored_at, dismissed_at, app_version
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(project_uuid) DO UPDATE SET
                        closed_cleanly = excluded.closed_cleanly,
                        saved_at = excluded.saved_at,
                        restored_at = excluded.restored_at,
                        dismissed_at = excluded.dismissed_at,
                        app_version = excluded.app_version;
                    """,
                    (
                        proj,
                        1 if closed_cleanly else 0,
                        ts,
                        resolved_restored,
                        resolved_dismissed,
                        app_version,
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM terminal_session_snapshots WHERE project_uuid = ?;",
                    (proj,),
                ).fetchone()
        assert row is not None
        return _decode_snapshot_row(row)
