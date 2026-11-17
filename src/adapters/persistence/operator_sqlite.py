"""Sesiones del operador sobre SQLite (Ola 4, F2.1 del dossier 37).

Una fila = un *harness* emparejado (pid + `create_time` + ejecutable) aprobado por el humano. Sin secretos: la prueba
de identidad es el proceso, no un token. Estados: ``active`` -> ``revoked`` (humano) | ``expired`` (inactividad) |
``dead`` (el proceso del harness murio o su pid se reutilizo). Una sesion que deja de estar ``active`` no vuelve.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional

_COLUMNS = (
    "session_id",
    "state",
    "state_reason",
    "harness_pid",
    "harness_create_time",
    "harness_exe",
    "harness_name",
    "harness_user",
    "harness_cwd",
    "ancestors_json",
    "projects_json",
    "autonomy_level",
    "transport",
    "created_at_ms",
    "last_seen_at_ms",
    "idle_timeout_ms",
    "ended_at_ms",
    "ended_by",
)

ACTIVE = "active"
ENDED_STATES = frozenset({"revoked", "expired", "dead"})


def _decode(row: Any) -> Dict[str, Any]:
    out = dict(row)
    for key, target in (("ancestors_json", "ancestors"), ("projects_json", "projects")):
        try:
            out[target] = json.loads(out.get(key) or "[]")
        except (TypeError, ValueError):
            out[target] = []
    return out


class SqliteOperatorSessionStore:
    def __init__(self, db: Any) -> None:
        self.db = db

    def create(self, row: Dict[str, Any]) -> Dict[str, Any]:
        data = dict(row)
        data["ancestors_json"] = json.dumps(list(data.pop("ancestors", []) or []), ensure_ascii=False)
        data["projects_json"] = json.dumps(list(data.pop("projects", []) or []), ensure_ascii=False)
        with self.db.get_connection() as conn:
            conn.execute(
                f"INSERT INTO operator_sessions ({', '.join(_COLUMNS)}) VALUES ({', '.join('?' for _ in _COLUMNS)});",
                tuple(data.get(c) for c in _COLUMNS),
            )
            conn.commit()
        return self.get(str(data["session_id"])) or {}

    def get(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self.db.get_connection() as conn:
            row = conn.execute("SELECT * FROM operator_sessions WHERE session_id = ?;", (session_id,)).fetchone()
        return _decode(row) if row else None

    def find_for_harness(self, pid: int, create_time: float) -> Optional[Dict[str, Any]]:
        """Sesion MAS RECIENTE (en cualquier estado) de ESE proceso (pid + `create_time`): una revocada impide volver
        a emparejar el mismo proceso; una caducada permite pedirlo de nuevo."""
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM operator_sessions WHERE harness_pid = ? ORDER BY created_at_ms DESC, rowid DESC;",
                (int(pid),),
            ).fetchall()
        for row in rows:
            if abs(float(row["harness_create_time"]) - float(create_time)) < 0.01:
                return _decode(row)
        return None

    def list(self, states: Optional[Iterable[str]] = None, limit: int = 200) -> List[Dict[str, Any]]:
        wanted = tuple(states or ())
        with self.db.get_connection() as conn:
            if wanted:
                rows = conn.execute(
                    f"SELECT * FROM operator_sessions WHERE state IN ({', '.join('?' for _ in wanted)}) "
                    "ORDER BY created_at_ms DESC LIMIT ?;",
                    (*wanted, int(limit)),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM operator_sessions ORDER BY created_at_ms DESC LIMIT ?;", (int(limit),)
                ).fetchall()
        return [_decode(r) for r in rows]

    def touch(self, session_id: str, now_ms: int) -> None:
        with self.db.get_connection() as conn:
            conn.execute(
                "UPDATE operator_sessions SET last_seen_at_ms = ? WHERE session_id = ? AND state = 'active';",
                (int(now_ms), session_id),
            )
            conn.commit()

    def end(self, session_id: str, state: str, *, reason: str, now_ms: int, actor: str) -> bool:
        """Termina una sesion ACTIVA (`revoked`/`expired`/`dead`). False si no existia o ya habia terminado."""
        if state not in ENDED_STATES:
            raise ValueError("estado final invalido")
        with self.db.get_connection() as conn:
            cur = conn.execute(
                "UPDATE operator_sessions SET state = ?, state_reason = ?, ended_at_ms = ?, ended_by = ? "
                "WHERE session_id = ? AND state = 'active';",
                (state, reason, int(now_ms), actor, session_id),
            )
            conn.commit()
            return cur.rowcount > 0
