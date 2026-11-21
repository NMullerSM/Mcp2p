"""Pruebas WP-4a (v2.0.2): persistencia de sesiones, captura de identificador, relanzamiento y restauración."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from src.adapters.persistence.terminal_session_sqlite import TerminalSessionSqliteRepo
from src.adapters.terminal import launch_augment, session_launch
from src.adapters.terminal.cli_catalog import CliCatalogService, _parse_entry
from src.adapters.terminal.launch_augment import LaunchContext, run_augmenters
from src.app.api_bridge import ApiBridge
from src.app.ui_adapters import sessions_ui as sessions_ui_mod
from src.app.ui_adapters.pane_lifecycle import PaneSpawnedEvent
from src.core.factory import ApplicationFactory
from src.core.kernel import CoreKernel
from src.core.services.terminal_session_service import (
    RestoreItem,
    RestorePlan,
    TerminalSessionService,
    format_warning,
)
from src.storage.db import DatabaseManager
from src.storage.terminal_session_schema import (
    TERMINAL_SESSION_SNAPSHOTS_COLUMNS,
    TERMINAL_SESSIONS_COLUMNS,
)
from tests.helpers.legacy_db import build_legacy_db
from tests.helpers.relay_lab import install_pty_write_spy


@pytest.fixture(autouse=True)
def _no_system_pty_writes(monkeypatch: pytest.MonkeyPatch):
    calls = install_pty_write_spy(monkeypatch)
    yield calls
    assert calls == [], f"WP-4a no debe escribir en un PTY: {calls}"


class _FakeClock:
    def __init__(self, start_s: float = 1000.0) -> None:
        self.now_s = float(start_s)
        self.dt = datetime(2026, 11, 19, 12, 0, 0, tzinfo=timezone.utc)
        self.sleeps: List[float] = []

    def __call__(self) -> datetime:
        self.dt = self.dt + timedelta(seconds=1)
        return self.dt

    def monotonic(self) -> float:
        return self.now_s

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(float(seconds))
        self.now_s += float(seconds)


class _FakeTerminal:
    max_panes = 6

    def __init__(self) -> None:
        self.pid = os.getpid()
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self.spawn_calls: List[Dict[str, Any]] = []
        self.close_calls: List[str] = []
        self.exit_on_spawn: Dict[str, Optional[int]] = {}
        self.exit_after_polls: Dict[str, tuple[int, Optional[int]]] = {}
        self._poll_counts: Dict[str, int] = {}

    def is_available(self) -> bool:
        return True

    def get_status(self) -> Dict[str, Any]:
        return {"available": True, "degraded": False, "mode": "fake"}

    def list_sessions(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for pane_id, sess in self.sessions.items():
            copy = dict(sess)
            if pane_id in self.exit_after_polls:
                self._poll_counts[pane_id] = self._poll_counts.get(pane_id, 0) + 1
                threshold, code = self.exit_after_polls[pane_id]
                if self._poll_counts[pane_id] >= threshold:
                    copy["alive"] = False
                    copy["exit_code"] = code
                    sess["alive"] = False
                    sess["exit_code"] = code
            out.append(copy)
        return out

    def spawn_session(self, pane_id: Any = 1, **kwargs: Any) -> Dict[str, Any]:
        norm = f"pane_{pane_id}" if isinstance(pane_id, int) else str(pane_id)
        self.spawn_calls.append({"pane_id": norm, **kwargs})
        alive = norm not in self.exit_on_spawn
        exit_code = self.exit_on_spawn.get(norm)
        sess = {
            "pane_id": norm,
            "alive": alive,
            "exit_code": exit_code,
            "session_id": f"sess-{norm}-{len(self.spawn_calls)}",
            "pid": self.pid,
            "cli_profile": kwargs.get("cli_profile"),
        }
        self.sessions[norm] = sess
        launch_meta: Dict[str, Any] = {}
        req = kwargs.get("launch_request") or {}
        if req.get("resume_mode") == "by_id" and req.get("cli_session_id"):
            launch_meta["cli_session_id"] = req["cli_session_id"]
        elif req.get("resume_mode") in (None, "none", "fresh") and kwargs.get("cli_profile") == "claude":
            launch_meta["cli_session_id"] = str(uuid.uuid4())
            launch_meta["cli_session_source"] = "assigned"
        return {
            "success": True,
            "degraded": False,
            **sess,
            "launch_meta": launch_meta,
            "launch_notes": [],
        }

    def close_session(self, pane_id: Any) -> Dict[str, Any]:
        norm = f"pane_{pane_id}" if isinstance(pane_id, int) else str(pane_id)
        self.close_calls.append(norm)
        existed = self.sessions.pop(norm, None) is not None
        return {"success": True, "pane_id": norm, "closed": existed}

    def poll_output(self, pane_id: Any, since_seq: int = 0) -> Dict[str, Any]:
        norm = str(pane_id)
        sess = self.sessions.get(norm)
        if sess is None:
            return {"success": True, "pane_id": norm, "alive": False, "exit_code": None, "text": ""}
        return {
            "success": True,
            "pane_id": norm,
            "alive": bool(sess.get("alive", True)),
            "exit_code": sess.get("exit_code"),
            "text": "",
        }


def _make_catalog_file(tmp_path: Path, clis: List[Dict[str, Any]]) -> Path:
    path = tmp_path / "cli_catalog.json"
    path.write_text(
        json.dumps({"schema_version": 1, "catalog_version": "2026.11.19", "clis": clis}),
        encoding="utf-8",
    )
    return path


def _make_exec(tmp_path: Path, name: str) -> str:
    exe = tmp_path / "bin" / name
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    exe.chmod(0o755)
    return str(exe)


def _cli_entry(
    cid: str,
    binary: str,
    *,
    kind: str = "agent_cli",
    resume_argv: Optional[List[str]] = None,
    resume_verified: bool = False,
    session_verified: bool = False,
    assign_argv: Optional[List[str]] = None,
    resume_by_id_argv: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return {
        "id": cid,
        "display_name": cid.capitalize(),
        "icon": cid,
        "kind": kind,
        "binary": os.path.basename(binary) or cid,
        "candidate_paths": {"linux": [binary]},
        "launch_flags": [],
        "mcp": {"method": "none", "format": "none", "verified": True},
        "resume": {"argv": resume_argv or [], "verified": resume_verified},
        "session": {
            "status": "verified" if session_verified else "unverified",
            "platforms": ["linux"] if session_verified else [],
            "assign_argv": assign_argv or [],
            "resume_by_id_argv": resume_by_id_argv or [],
            "verified_with_cli_version": "2.1.325" if session_verified else "",
            "verified_on": "2026-11-19" if session_verified else "",
        },
        "install": {},
    }


def _build_bridge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    db_name: str = "sessions.db",
    catalog_service: Optional[CliCatalogService] = None,
) -> tuple[ApiBridge, _FakeTerminal, str]:
    import src.app.ui_adapters.extensions_lab_ui as lab_mod

    monkeypatch.setattr(lab_mod, "is_unsupported_terminal_platform", lambda: False)
    monkeypatch.delenv("MCP2P_TEST_AUTO_CONFIRM", raising=False)
    db = DatabaseManager(db_path=str(tmp_path / db_name))
    bridge = ApiBridge(db_manager=db)
    monkeypatch.setattr(bridge.extensions_ui, "_ensure_hub_started", lambda: None)
    term = _FakeTerminal()
    bridge.core_kernel.register_connector(CoreKernel.PORT_AGENT_TERMINAL, term)
    if catalog_service is not None:
        monkeypatch.setattr(bridge.extensions_ui, "_catalog_service", lambda: catalog_service)
        svc = ApplicationFactory.terminal_session_service(bridge.core_kernel)
        svc._catalog = catalog_service
    proj = bridge.db.create_project("Proyecto Sesiones", str(tmp_path / "repo"))["project_uuid"]
    (tmp_path / "repo").mkdir(parents=True, exist_ok=True)
    return bridge, term, proj


# ---------------------------------------------------------------------------
# 1. Esquema: nunca guarda salida, scrollback, buffer, screen ni env
# ---------------------------------------------------------------------------


def test_terminal_sessions_schema_never_stores_terminal_output(tmp_path: Path) -> None:
    db = DatabaseManager(db_path=str(tmp_path / "schema.db"))
    try:
        with db.get_connection() as conn:
            sess_cols = {row[1] for row in conn.execute("PRAGMA table_info(terminal_sessions);").fetchall()}
            snap_cols = {
                row[1] for row in conn.execute("PRAGMA table_info(terminal_session_snapshots);").fetchall()
            }
        assert sess_cols == TERMINAL_SESSIONS_COLUMNS
        assert snap_cols == TERMINAL_SESSION_SNAPSHOTS_COLUMNS

        forbidden = ("output", "scrollback", "buffer", "screen", "env")
        for col in sess_cols | snap_cols:
            lower = col.lower()
            for bad in forbidden:
                assert bad not in lower, f"Columna prohibida encontrada: {col}"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 2. Estados: open, closed_by_user, closed_by_app, exited
# ---------------------------------------------------------------------------


def test_states_open_closed_by_user_closed_by_app_exited(tmp_path: Path) -> None:
    db = DatabaseManager(db_path=str(tmp_path / "states.db"))
    try:
        repo = TerminalSessionSqliteRepo(db)
        cat = CliCatalogService(include_user_catalog=False, platform="linux")
        svc = TerminalSessionService(repo=repo, catalog=cat, identity_repo=db, settings=db, app_version="2.0.2")
        proj = db.create_project("Estados", str(tmp_path / "proj"))["project_uuid"]

        svc.record_spawn(
            project_uuid=proj,
            pane_slot="pane_1",
            cli_id="custom_shell",
            cwd=str(tmp_path),
            cli_session_id=None,
            cli_session_source="none",
        )
        svc.record_spawn(
            project_uuid=proj,
            pane_slot="pane_2",
            cli_id="custom_shell",
            cwd=str(tmp_path),
            cli_session_id=None,
            cli_session_source="none",
        )
        svc.record_spawn(
            project_uuid=proj,
            pane_slot="pane_3",
            cli_id="custom_shell",
            cwd=str(tmp_path),
            cli_session_id=None,
            cli_session_source="none",
        )
        assert {r["pane_slot"]: r["state"] for r in repo.list(proj)} == {
            "pane_1": "open",
            "pane_2": "open",
            "pane_3": "open",
        }

        svc.record_closed_by_user(proj, "pane_1")
        assert repo.get(proj, "pane_1")["state"] == "closed_by_user"

        # pane_2 sigue viva; pane_3 murió antes de cerrar la app -> exited
        closed_count = svc.mark_app_closing({proj: ["pane_2"]})
        assert closed_count == 1
        assert repo.get(proj, "pane_1")["state"] == "closed_by_user"
        assert repo.get(proj, "pane_2")["state"] == "closed_by_app"
        assert repo.get(proj, "pane_3")["state"] == "exited"

        snap = repo.snapshot_get(proj)
        assert snap is not None and snap["closed_cleanly"] is True

        restorable_rows = repo.list(proj, states=("open", "closed_by_app"))
        assert [r["pane_slot"] for r in restorable_rows] == ["pane_2"]
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 3. Modos de reanudación y ambigüedad
# ---------------------------------------------------------------------------


def test_restore_plan_resume_modes_and_ambiguity(tmp_path: Path) -> None:
    claude_bin = _make_exec(tmp_path, "claude")
    codex_bin = _make_exec(tmp_path, "codex")
    opencode_bin = _make_exec(tmp_path, "opencode")
    cat_path = _make_catalog_file(
        tmp_path,
        [
            _cli_entry(
                "claude",
                claude_bin,
                resume_argv=["--continue"],
                resume_verified=True,
                session_verified=True,
                assign_argv=["--session-id", "{cli_session_id}"],
                resume_by_id_argv=["--resume", "{cli_session_id}"],
            ),
            _cli_entry("codex", codex_bin, resume_argv=["resume"], resume_verified=True, session_verified=False),
            _cli_entry("opencode", opencode_bin, resume_argv=[], resume_verified=False, session_verified=False),
        ],
    )
    cat = CliCatalogService(
        repo_catalog_path=cat_path,
        include_user_catalog=False,
        which=lambda name: {"claude": claude_bin, "codex": codex_bin, "opencode": opencode_bin}.get(name),
        platform="linux",
    )

    dir_a = tmp_path / "dir_a"
    dir_b = tmp_path / "dir_b"
    dir_a.mkdir()
    dir_b.mkdir()
    dir_a_link = tmp_path / "dir_a_link"
    dir_a_link.symlink_to(dir_a)

    db = DatabaseManager(db_path=str(tmp_path / "modes.db"))
    try:
        repo = TerminalSessionSqliteRepo(db)
        svc = TerminalSessionService(repo=repo, catalog=cat, identity_repo=db, settings=db, app_version="2.0.2")
        proj = db.create_project("Modos", str(dir_a))["project_uuid"]

        # Dos Claude con session_id conocido en la MISMA carpeta -> ambos by_id (sin ambigüedad)
        svc.record_spawn(
            project_uuid=proj, pane_slot="pane_1", cli_id="claude", cwd=str(dir_a),
            cli_session_id="sid-claude-1", cli_session_source="assigned",
        )
        svc.record_spawn(
            project_uuid=proj, pane_slot="pane_2", cli_id="claude", cwd=str(dir_a),
            cli_session_id="sid-claude-2", cli_session_source="hook",
        )
        # Un codex único en dir_b -> continue
        svc.record_spawn(
            project_uuid=proj, pane_slot="pane_3", cli_id="codex", cwd=str(dir_b),
            cli_session_id=None, cli_session_source="none",
        )
        # Dos codex sin session_id en dir_a (uno vía symlink) -> fresh + AMBIGUOUS_CONTINUE
        svc.record_spawn(
            project_uuid=proj, pane_slot="pane_4", cli_id="codex", cwd=str(dir_a),
            cli_session_id=None, cli_session_source="none",
        )
        svc.record_spawn(
            project_uuid=proj, pane_slot="pane_5", cli_id="codex", cwd=str(dir_a_link),
            cli_session_id=None, cli_session_source="none",
        )
        # Un opencode sin resume verificado -> fresh + RESUME_NOT_VERIFIED
        svc.record_spawn(
            project_uuid=proj, pane_slot="pane_6", cli_id="opencode", cwd=str(dir_b),
            cli_session_id=None, cli_session_source="none",
        )
        svc.mark_app_closing({proj: ["pane_1", "pane_2", "pane_3", "pane_4", "pane_5", "pane_6"]})

        plan = svc.restorable(proj, max_panes=6)
        assert plan.closed_cleanly is True
        by_slot = {item.pane_slot: item for item in plan.items}
        assert by_slot["pane_1"].resume_mode == "by_id" and by_slot["pane_1"].cli_session_id == "sid-claude-1"
        assert by_slot["pane_2"].resume_mode == "by_id" and by_slot["pane_2"].cli_session_id == "sid-claude-2"
        assert by_slot["pane_3"].resume_mode == "continue" and by_slot["pane_3"].warnings == ()
        assert by_slot["pane_4"].resume_mode == "fresh" and "AMBIGUOUS_CONTINUE" in by_slot["pane_4"].warnings
        assert by_slot["pane_5"].resume_mode == "fresh" and "AMBIGUOUS_CONTINUE" in by_slot["pane_5"].warnings
        assert by_slot["pane_6"].resume_mode == "fresh" and "RESUME_NOT_VERIFIED" in by_slot["pane_6"].warnings
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 4. Los 8 avisos explícitos (código, mensaje en español y acción)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "warning_code",
    [
        "CWD_MISSING",
        "CLI_NOT_INSTALLED",
        "CLI_REMOVED",
        "AMBIGUOUS_CONTINUE",
        "RESUME_NOT_VERIFIED",
        "PANE_LIMIT",
        "SLOT_BUSY",
        "UNCLEAN_SHUTDOWN",
    ],
)
def test_restore_plan_explicit_warnings(tmp_path: Path, warning_code: str) -> None:
    installed_bin = _make_exec(tmp_path, "claude")
    codex_bin = _make_exec(tmp_path, "codex")
    cat_path = _make_catalog_file(
        tmp_path,
        [
            _cli_entry("claude", installed_bin, resume_argv=["--continue"], resume_verified=True),
            _cli_entry("codex", codex_bin, resume_argv=[], resume_verified=False),
            _cli_entry("opencode", str(tmp_path / "missing_opencode"), resume_argv=[], resume_verified=False),
        ],
    )
    cat = CliCatalogService(
        repo_catalog_path=cat_path,
        include_user_catalog=False,
        which=lambda name: {"claude": installed_bin, "codex": codex_bin}.get(name),
        platform="linux",
        home=str(tmp_path / "empty_home"),
        environ={},
    )
    existing_dir = tmp_path / "exists"
    existing_dir.mkdir()
    missing_dir = tmp_path / "does_not_exist"

    db = DatabaseManager(db_path=str(tmp_path / f"warn_{warning_code}.db"))
    try:
        repo = TerminalSessionSqliteRepo(db)
        svc = TerminalSessionService(repo=repo, catalog=cat, identity_repo=db, settings=db, app_version="2.0.2")
        proj = db.create_project("Avisos", str(existing_dir))["project_uuid"]

        max_panes = 4
        busy: tuple[str, ...] = ()

        if warning_code == "CWD_MISSING":
            svc.record_spawn(
                project_uuid=proj, pane_slot="pane_1", cli_id="claude", cwd=str(missing_dir),
                cli_session_id=None, cli_session_source="none",
            )
            svc.mark_app_closing({proj: ["pane_1"]})
        elif warning_code == "CLI_NOT_INSTALLED":
            svc.record_spawn(
                project_uuid=proj, pane_slot="pane_1", cli_id="opencode", cwd=str(existing_dir),
                cli_session_id=None, cli_session_source="none",
            )
            svc.mark_app_closing({proj: ["pane_1"]})
        elif warning_code == "CLI_REMOVED":
            svc.record_spawn(
                project_uuid=proj, pane_slot="pane_1", cli_id="legacy_cli", cwd=str(existing_dir),
                cli_session_id=None, cli_session_source="none",
            )
            svc.mark_app_closing({proj: ["pane_1"]})
        elif warning_code == "AMBIGUOUS_CONTINUE":
            svc.record_spawn(
                project_uuid=proj, pane_slot="pane_1", cli_id="claude", cwd=str(existing_dir),
                cli_session_id=None, cli_session_source="none",
            )
            svc.record_spawn(
                project_uuid=proj, pane_slot="pane_2", cli_id="claude", cwd=str(existing_dir),
                cli_session_id=None, cli_session_source="none",
            )
            svc.mark_app_closing({proj: ["pane_1", "pane_2"]})
        elif warning_code == "RESUME_NOT_VERIFIED":
            svc.record_spawn(
                project_uuid=proj, pane_slot="pane_1", cli_id="codex", cwd=str(existing_dir),
                cli_session_id=None, cli_session_source="none",
            )
            svc.mark_app_closing({proj: ["pane_1"]})
        elif warning_code == "PANE_LIMIT":
            max_panes = 1
            svc.record_spawn(
                project_uuid=proj, pane_slot="pane_1", cli_id="claude", cwd=str(existing_dir),
                cli_session_id=None, cli_session_source="none",
            )
            svc.record_spawn(
                project_uuid=proj, pane_slot="pane_2", cli_id="codex", cwd=str(existing_dir),
                cli_session_id=None, cli_session_source="none",
            )
            svc.mark_app_closing({proj: ["pane_1", "pane_2"]})
        elif warning_code == "SLOT_BUSY":
            busy = ("pane_1",)
            svc.record_spawn(
                project_uuid=proj, pane_slot="pane_1", cli_id="claude", cwd=str(existing_dir),
                cli_session_id=None, cli_session_source="none",
            )
            svc.mark_app_closing({proj: ["pane_1"]})
        elif warning_code == "UNCLEAN_SHUTDOWN":
            # Sin mark_app_closing -> closed_cleanly == 0
            svc.record_spawn(
                project_uuid=proj, pane_slot="pane_1", cli_id="claude", cwd=str(existing_dir),
                cli_session_id=None, cli_session_source="none",
            )

        plan = svc.restorable(proj, max_panes=max_panes, busy_slots=busy)
        if warning_code == "UNCLEAN_SHUTDOWN":
            assert plan.closed_cleanly is False
            assert "UNCLEAN_SHUTDOWN" in plan.warnings
        else:
            all_item_warnings = [w for it in plan.items for w in it.warnings]
            assert warning_code in all_item_warnings
            if warning_code in {"CWD_MISSING", "CLI_NOT_INSTALLED", "CLI_REMOVED", "SLOT_BUSY"}:
                assert plan.items[0].skip is True
            if warning_code == "PANE_LIMIT":
                assert plan.items[1].skip is True

        formatted = format_warning(warning_code, cli_name="Claude Code", cwd=str(missing_dir), pane_slot="pane_1")
        assert formatted["code"] == warning_code
        assert len(formatted["message"]) > 10
        if warning_code == "CLI_NOT_INSTALLED":
            assert formatted["action"] == "install"
        elif warning_code == "CWD_MISSING":
            assert formatted["action"] == "choose_cwd"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 5. Restauración en orden por posición con escalonado de 750 ms (reloj/sleep falsos)
# ---------------------------------------------------------------------------


def test_restore_spawns_in_order_with_stagger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sh_bin = _make_exec(tmp_path, "sh")
    claude_bin = _make_exec(tmp_path, "claude")
    cat_path = _make_catalog_file(
        tmp_path,
        [
            _cli_entry("custom_shell", sh_bin, kind="shell", resume_argv=[], resume_verified=True),
            _cli_entry(
                "claude",
                claude_bin,
                resume_argv=["--continue"],
                resume_verified=True,
                session_verified=True,
                assign_argv=["--session-id", "{cli_session_id}"],
                resume_by_id_argv=["--resume", "{cli_session_id}"],
            ),
        ],
    )
    cat = CliCatalogService(
        repo_catalog_path=cat_path,
        include_user_catalog=False,
        which=lambda name: {"sh": sh_bin, "claude": claude_bin}.get(name),
        platform="linux",
        environ={"SHELL": sh_bin},
    )
    bridge, term, proj = _build_bridge(tmp_path, monkeypatch, catalog_service=cat)
    try:
        repo = ApplicationFactory.terminal_session_service(bridge.core_kernel)._repo
        # Insertar fuera de orden de ranura con posiciones explícitas
        repo.upsert(
            project_uuid=proj, pane_slot="pane_3", cli_id="custom_shell", cwd=str(tmp_path / "repo"),
            position=10, state="closed_by_app",
        )
        repo.upsert(
            project_uuid=proj, pane_slot="pane_1", cli_id="claude", cwd=str(tmp_path / "repo"),
            position=30, cli_session_id="sid-last", cli_session_source="assigned", state="closed_by_app",
        )
        repo.upsert(
            project_uuid=proj, pane_slot="pane_2", cli_id="custom_shell", cwd=str(tmp_path / "repo"),
            position=20, state="closed_by_app",
        )
        repo.snapshot_set(proj, closed_cleanly=True, app_version="2.0.2")

        fake_clock = _FakeClock()
        spawn_times: List[tuple[str, float]] = []
        orig_spawn = term.spawn_session

        def _timed_spawn(*args: Any, **kwargs: Any) -> Dict[str, Any]:
            res = orig_spawn(*args, **kwargs)
            spawn_times.append((res["pane_id"], fake_clock.monotonic()))
            return res

        monkeypatch.setattr(term, "spawn_session", _timed_spawn)
        monkeypatch.setattr(sessions_ui_mod.time, "sleep", fake_clock.sleep)
        monkeypatch.setattr(sessions_ui_mod.time, "monotonic", fake_clock.monotonic)

        res = bridge.pty_restore_session(proj, ["pane_1", "pane_2", "pane_3"])
        assert res["success"] is True
        assert [r["pane_slot"] for r in res["results"]] == ["pane_3", "pane_2", "pane_1"]
        assert all(r["ok"] is True for r in res["results"])
        assert [slot for slot, _ in spawn_times] == ["pane_3", "pane_2", "pane_1"]
        assert spawn_times[1][1] - spawn_times[0][1] == pytest.approx(0.75)
        assert spawn_times[2][1] - spawn_times[1][1] == pytest.approx(0.75)
        assert fake_clock.sleeps[:2] == [0.75, 0.75]
        # pane_1 se lanzó con by_id y su cli_session_id
        last_call = term.spawn_calls[-1]
        assert last_call["pane_id"] == "pane_1"
        assert last_call["launch_request"]["resume_mode"] == "by_id"
        assert last_call["launch_request"]["cli_session_id"] == "sid-last"
    finally:
        bridge._close_terminal_matrix()
        bridge.db.close()


# ---------------------------------------------------------------------------
# 6. Fallo temprano de CLI reanudado -> RESUME_FAILED_EARLY_EXIT + exit_code
# ---------------------------------------------------------------------------


def test_restore_reports_early_exit_of_resumed_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    claude_bin = _make_exec(tmp_path, "claude")
    cat_path = _make_catalog_file(
        tmp_path,
        [
            _cli_entry(
                "claude",
                claude_bin,
                resume_argv=["--continue"],
                resume_verified=True,
                session_verified=True,
                assign_argv=["--session-id", "{cli_session_id}"],
                resume_by_id_argv=["--resume", "{cli_session_id}"],
            ),
        ],
    )
    cat = CliCatalogService(
        repo_catalog_path=cat_path,
        include_user_catalog=False,
        which=lambda name: claude_bin if name == "claude" else None,
        platform="linux",
    )
    bridge, term, proj = _build_bridge(tmp_path, monkeypatch, catalog_service=cat)
    try:
        repo = ApplicationFactory.terminal_session_service(bridge.core_kernel)._repo
        repo.upsert(
            project_uuid=proj, pane_slot="pane_1", cli_id="claude", cwd=str(tmp_path / "repo"),
            cli_session_id="expired-sid-99", cli_session_source="assigned", state="closed_by_app",
        )
        repo.snapshot_set(proj, closed_cleanly=True, app_version="2.0.2")

        # Simula que el CLI reanudado muere tras 2 chequeos con código 2
        term.exit_after_polls["pane_1"] = (2, 2)
        fake_clock = _FakeClock()
        monkeypatch.setattr(sessions_ui_mod.time, "sleep", fake_clock.sleep)
        monkeypatch.setattr(sessions_ui_mod.time, "monotonic", fake_clock.monotonic)

        res = bridge.pty_restore_session(proj, ["pane_1"])
        assert res["success"] is True
        assert len(res["results"]) == 1
        item_res = res["results"][0]
        assert item_res["ok"] is False
        assert item_res["code"] == "RESUME_FAILED_EARLY_EXIT"
        assert item_res["exit_code"] == 2
        assert "2" in item_res["message"]
        assert repo.get(proj, "pane_1")["state"] == "exited"
        assert repo.get(proj, "pane_1")["last_exit_code"] == 2
    finally:
        bridge._close_terminal_matrix()
        bridge.db.close()


# ---------------------------------------------------------------------------
# 7. Asignación de session_id de Claude solo cuando session está verificado
# ---------------------------------------------------------------------------


def test_claude_session_id_assigned_only_when_verified(tmp_path: Path) -> None:
    unverified_spec = _parse_entry(
        _cli_entry("claude", "/usr/bin/claude", session_verified=False, assign_argv=["--session-id", "{cli_session_id}"]),
        source="repo",
    )
    verified_spec = _parse_entry(
        _cli_entry(
            "claude",
            "/usr/bin/claude",
            session_verified=True,
            assign_argv=["--session-id", "{cli_session_id}"],
            resume_by_id_argv=["--resume", "{cli_session_id}"],
        ),
        source="repo",
    )
    user_spec = _parse_entry(
        _cli_entry("claude_user", "/usr/bin/claude", session_verified=True, assign_argv=["--session-id", "{cli_session_id}"]),
        source="user",
    )

    def _ctx(spec: Any, req: Dict[str, Any], platform: str = "linux") -> LaunchContext:
        return LaunchContext(
            spec=spec,
            platform=platform,
            pane_slot="pane_1",
            session_id="pane_1-s1",
            hub_url=None,
            request=req,
            session_file=lambda n: tmp_path / n,
        )

    # No verificado (ni en otra plataforma ni fuente user) -> no asigna nada
    add_unver = session_launch.augment(_ctx(unverified_spec, {"resume_mode": "none"}))
    assert add_unver.argv == ()
    assert add_unver.meta == {}

    add_win = session_launch.augment(_ctx(verified_spec, {"resume_mode": "none"}, platform="win32"))
    assert add_win.argv == () and add_win.meta == {}

    add_user = session_launch.augment(_ctx(user_spec, {"resume_mode": "none"}))
    assert add_user.argv == () and add_user.meta == {}

    # Verificado en lanzamiento fresco -> uuid4 + meta assigned
    for req in ({}, {"resume_mode": "none"}, {"resume_mode": "fresh"}, {"resume": False}):
        add_ok = session_launch.augment(_ctx(verified_spec, req))
        assert len(add_ok.argv) == 2 and add_ok.argv[0] == "--session-id"
        assigned_id = add_ok.meta["cli_session_id"]
        assert str(uuid.UUID(assigned_id)) == assigned_id
        assert add_ok.argv[1] == assigned_id
        assert add_ok.meta["cli_session_source"] == "assigned"

    # Si está reanudando (continue o resume=True) -> NO asigna nuevo id
    assert session_launch.augment(_ctx(verified_spec, {"resume_mode": "continue"})).argv == ()
    assert session_launch.augment(_ctx(verified_spec, {"resume": True})).argv == ()

    # Reanudación by_id -> usa resume_by_id_argv con el id pedido
    add_by_id = session_launch.augment(
        _ctx(verified_spec, {"resume_mode": "by_id", "cli_session_id": "sess_ABC-123"})
    )
    assert add_by_id.argv == ("--resume", "sess_ABC-123")
    assert add_by_id.meta["cli_session_id"] == "sess_ABC-123"


# ---------------------------------------------------------------------------
# 8. Rechazo de cli_session_id inválidos en todas las entradas
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_id",
    [
        "--evil",
        "id con espacios",
        "a" * 129,
        "",
        "../etc/passwd",
        "bad\nnewline",
        "bad;cmd",
    ],
)
def test_resume_by_id_rejects_invalid_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_id: str
) -> None:
    verified_spec = _parse_entry(
        _cli_entry(
            "claude",
            "/usr/bin/claude",
            session_verified=True,
            assign_argv=["--session-id", "{cli_session_id}"],
            resume_by_id_argv=["--resume", "{cli_session_id}"],
        ),
        source="repo",
    )
    ctx = LaunchContext(
        spec=verified_spec,
        platform="linux",
        pane_slot="pane_1",
        session_id="pane_1-s1",
        hub_url=None,
        request={"resume_mode": "by_id", "cli_session_id": bad_id},
        session_file=lambda n: tmp_path / n,
    )
    with pytest.raises(ValueError):
        session_launch.augment(ctx)

    # run_augmenters descarta al aumentador y nunca inyecta bad_id en argv
    monkeypatch.setattr(launch_augment, "AUGMENTERS", (session_launch.augment,))
    merged = run_augmenters(ctx)
    assert merged.argv == ()
    assert bad_id not in merged.meta.values()

    db = DatabaseManager(db_path=str(tmp_path / "invalid_ids.db"))
    try:
        repo = TerminalSessionSqliteRepo(db)
        cat = CliCatalogService(include_user_catalog=False, platform="linux")
        svc = TerminalSessionService(repo=repo, catalog=cat, identity_repo=db, settings=db, app_version="2.0.2")
        proj = db.create_project("Inv", str(tmp_path))["project_uuid"]
        with pytest.raises(ValueError):
            svc.record_spawn(
                project_uuid=proj,
                pane_slot="pane_1",
                cli_id="claude",
                cwd=str(tmp_path),
                cli_session_id=bad_id,
                cli_session_source="assigned",
            )
        svc.record_spawn(
            project_uuid=proj,
            pane_slot="pane_1",
            cli_id="claude",
            cwd=str(tmp_path),
            cli_session_id=None,
            cli_session_source="none",
        )
        with pytest.raises(ValueError):
            svc.record_cli_session_id(project_uuid=proj, pane_slot="pane_1", cli_session_id=bad_id)
        with pytest.raises(ValueError):
            repo.set_cli_session_id(proj, "pane_1", bad_id)
    finally:
        db.close()

    bridge, term, _ = _build_bridge(tmp_path, monkeypatch, db_name="invalid_bridge.db")
    try:
        res = bridge.pty_spawn_pane(
            "pane_1", "custom_shell", resume_mode="by_id", cli_session_id=bad_id, start_hub=False
        )
        if not bad_id.startswith("-"):
            assert res["success"] is False
            assert res["code"] == "INVALID_ARGUMENT"
        else:
            # Incluso si la capa de entrada previa permitiera '-', session_launch lo descarta y no lo añade al comando.
            assert term.spawn_calls == [] or bad_id not in (term.spawn_calls[-1].get("command") or [])
    finally:
        bridge._close_terminal_matrix()
        bridge.db.close()


# ---------------------------------------------------------------------------
# 9. Política de restauración: por defecto 'ask' y valores válidos
# ---------------------------------------------------------------------------


def test_restore_policy_default_ask_and_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bridge, _, _ = _build_bridge(tmp_path, monkeypatch)
    db_path = str(bridge.db.db_path)
    try:
        assert bridge.pty_get_restore_policy()["policy"] == "ask"
        for val in ("auto", "never", "ask"):
            res = bridge.pty_set_restore_policy(val)
            assert res["success"] is True and res["policy"] == val
            assert bridge.pty_get_restore_policy()["policy"] == val

        assert bridge.pty_set_restore_policy("always")["success"] is False
        assert bridge.pty_set_restore_policy("always")["code"] == "INVALID_ARGUMENT"
        assert bridge.pty_set_restore_policy(None)["code"] == "INVALID_ARGUMENT"
        assert bridge.pty_set_restore_policy("auto")["policy"] == "auto"
    finally:
        bridge._close_terminal_matrix()
        bridge.db.close()

    # Persistencia tras reiniciar puente
    db2 = DatabaseManager(db_path=db_path)
    bridge2 = ApiBridge(db_manager=db2)
    try:
        assert bridge2.pty_get_restore_policy()["policy"] == "auto"
    finally:
        bridge2._close_terminal_matrix()
        db2.close()


# ---------------------------------------------------------------------------
# 10. Descartar ("Ahora no") persiste hasta la siguiente instantánea
# ---------------------------------------------------------------------------


def test_dismiss_until_next_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bridge, _, proj = _build_bridge(tmp_path, monkeypatch)
    db_path = str(bridge.db.db_path)
    try:
        assert bridge.pty_spawn_pane("pane_1", "custom_shell", start_hub=False, project_uuid=proj)["success"] is True
        bridge.extensions_ui.close_terminal_matrix()
    finally:
        bridge.db.close()

    # Nuevo arranque: ofrece pane_1; dismiss lo oculta
    db2 = DatabaseManager(db_path=db_path)
    bridge2, _, _ = _build_bridge(tmp_path, monkeypatch, db_name="sessions.db")
    try:
        got = bridge2.pty_get_restorable_session(proj)
        assert len(got["plan"]["items"]) == 1
        assert bridge2.pty_dismiss_restore(proj)["success"] is True
        got_after = bridge2.pty_get_restorable_session(proj)
        assert got_after["plan"]["items"] == []
    finally:
        bridge2._close_terminal_matrix()
        bridge2.db.close()
        db2.close()

    # Nuevo arranque tras dismiss: sigue oculto
    bridge3, _, _ = _build_bridge(tmp_path, monkeypatch, db_name="sessions.db")
    try:
        assert bridge3.pty_get_restorable_session(proj)["plan"]["items"] == []
        # Nueva sesión -> nueva instantánea al cerrar -> vuelve a ofrecerse
        assert bridge3.pty_spawn_pane("pane_2", "custom_shell", start_hub=False, project_uuid=proj)["success"] is True
        bridge3.extensions_ui.close_terminal_matrix()
    finally:
        bridge3.db.close()

    bridge4, _, _ = _build_bridge(tmp_path, monkeypatch, db_name="sessions.db")
    try:
        got_next = bridge4.pty_get_restorable_session(proj)
        slots = [it["pane_slot"] for it in got_next["plan"]["items"]]
        assert slots == ["pane_1", "pane_2"]
    finally:
        bridge4._close_terminal_matrix()
        bridge4.db.close()


def test_include_handled_lists_the_last_session_even_after_dismiss(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bridge, _, proj = _build_bridge(tmp_path, monkeypatch)
    db_path = str(bridge.db.db_path)
    try:
        assert bridge.pty_spawn_pane("pane_1", "custom_shell", start_hub=False, project_uuid=proj)["success"] is True
        bridge.extensions_ui.close_terminal_matrix()
    finally:
        bridge.db.close()

    db2 = DatabaseManager(db_path=db_path)
    bridge2, _, _ = _build_bridge(tmp_path, monkeypatch, db_name="sessions.db")
    try:
        assert bridge2.pty_dismiss_restore(proj)["success"] is True
        # Sin la bandera, "Ahora no" oculta la oferta; con ella la última sesión sigue disponible a mano.
        assert bridge2.pty_get_restorable_session(proj)["plan"]["items"] == []
        forced = bridge2.pty_get_restorable_session(proj, include_handled=True)
        assert [it["pane_slot"] for it in forced["plan"]["items"]] == ["pane_1"]
        # Un valor no booleano nunca activa el modo manual (el puente lo recibe de JavaScript).
        assert bridge2.pty_get_restorable_session(proj, include_handled="true")["plan"]["items"] == []
    finally:
        bridge2._close_terminal_matrix()
        bridge2.db.close()
        db2.close()


# ---------------------------------------------------------------------------
# 11. La ranura restaurada recupera node_uuid, banderas, rol y mandato
# ---------------------------------------------------------------------------


def test_restored_slot_recovers_node_uuid_flags_and_role(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bridge1, _, proj = _build_bridge(tmp_path, monkeypatch)
    try:
        r1 = bridge1.pty_spawn_pane("pane_2", "custom_shell", start_hub=False, project_uuid=proj)
        assert r1["success"] is True
        orig_node_uuid = r1["node_uuid"]

        bridge1.db.set_pane_auto_relay(proj, "pane_2", True)
        with bridge1.db.get_connection() as conn:
            with conn:
                conn.execute(
                    "UPDATE pane_identities SET delivery_mode = ? WHERE project_uuid = ? AND pane_slot = ?;",
                    ("inbox", proj, "pane_2"),
                )
        assert bridge1.pty_set_pane_role("pane_2", "auditor", "Solo lectura")["success"] is True
        bridge1.extensions_ui.close_terminal_matrix()
    finally:
        bridge1.db.close()

    bridge2, _, _ = _build_bridge(tmp_path, monkeypatch, db_name="sessions.db")
    try:
        plan = bridge2.pty_get_restorable_session(proj)["plan"]
        assert len(plan["items"]) == 1
        item = plan["items"][0]
        assert item["role"] == "auditor"
        assert item["mandate"] == "Solo lectura"
        assert item["auto_relay"] is True
        assert "worker_mode" not in item
        assert item["delivery_mode"] == "inbox"

        restored = bridge2.pty_restore_session(proj, ["pane_2"])
        assert restored["success"] is True and restored["results"][0]["ok"] is True

        reg = bridge2.extensions_ui._get_pane_registry()
        rec = reg.get_pane("pane_2")
        assert rec is not None
        assert rec.node_uuid == orig_node_uuid
        assert rec.role == "auditor"
        assert rec.mandate == "Solo lectura"
        assert rec.auto_relay_enabled is True
        assert not hasattr(rec, "worker_mode")
        assert rec.delivery_mode == "inbox"
    finally:
        bridge2._close_terminal_matrix()
        bridge2.db.close()


# ---------------------------------------------------------------------------
# 12. Actualización de cli_session_id desde callback de hook
# ---------------------------------------------------------------------------


def test_cli_session_id_update_from_hook_callback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bridge, _, proj = _build_bridge(tmp_path, monkeypatch)
    try:
        svc = ApplicationFactory.terminal_session_service(bridge.core_kernel)
        assert bridge.pty_spawn_pane("pane_1", "custom_shell", start_hub=False, project_uuid=proj)["success"] is True
        row_before = svc.get_session(proj, "pane_1")
        assert row_before is not None and row_before["cli_session_id"] is None

        updated = svc.record_cli_session_id(
            project_uuid=proj,
            pane_slot="pane_1",
            cli_session_id="hook-sess-42",
            source="hook",
        )
        assert updated is True
        row_after = svc.get_session(proj, "pane_1")
        assert row_after is not None
        assert row_after["cli_session_id"] == "hook-sess-42"
        assert row_after["cli_session_source"] == "hook"

        # Panel inexistente o cerrado por usuario -> False sin modificar BD
        assert svc.record_cli_session_id(project_uuid=proj, pane_slot="pane_4", cli_session_id="x-1") is False
        assert bridge.pty_close_pane("pane_1")["success"] is True
        assert svc.record_cli_session_id(project_uuid=proj, pane_slot="pane_1", cli_session_id="late-99") is False
        assert svc.get_session(proj, "pane_1")["cli_session_id"] == "hook-sess-42"
    finally:
        bridge._close_terminal_matrix()
        bridge.db.close()


# ---------------------------------------------------------------------------
# 13. Relanzar panel conserva ranura, identidad, rol y usa el mejor modo
# ---------------------------------------------------------------------------


def test_relaunch_pane_keeps_slot_and_resumes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    claude_bin = _make_exec(tmp_path, "claude")
    codex_bin = _make_exec(tmp_path, "codex")
    cat_path = _make_catalog_file(
        tmp_path,
        [
            _cli_entry(
                "claude",
                claude_bin,
                resume_argv=["--continue"],
                resume_verified=True,
                session_verified=True,
                assign_argv=["--session-id", "{cli_session_id}"],
                resume_by_id_argv=["--resume", "{cli_session_id}"],
            ),
            _cli_entry("codex", codex_bin, resume_argv=["resume"], resume_verified=True, session_verified=False),
        ],
    )
    cat = CliCatalogService(
        repo_catalog_path=cat_path,
        include_user_catalog=False,
        which=lambda name: {"claude": claude_bin, "codex": codex_bin}.get(name),
        platform="linux",
    )
    bridge, term, proj = _build_bridge(tmp_path, monkeypatch, catalog_service=cat)
    try:
        # Panel no activo -> PANE_NOT_FOUND
        missing = bridge.pty_relaunch_pane("pane_1")
        assert missing["success"] is False and missing["code"] == "PANE_NOT_FOUND"

        # Caso 1: Claude con session_id (vía hook/asignación) -> relanza con by_id
        s1 = bridge.pty_spawn_pane("pane_2", "claude", start_hub=False, project_uuid=proj)
        assert s1["success"] is True
        orig_node = s1["node_uuid"]
        assert bridge.pty_set_pane_role("pane_2", "orchestrator", "Coordina equipo")["success"] is True
        svc = ApplicationFactory.terminal_session_service(bridge.core_kernel)
        assert svc.record_cli_session_id(project_uuid=proj, pane_slot="pane_2", cli_session_id="claude-sid-77") is True

        rel = bridge.pty_relaunch_pane("pane_2")
        assert rel["success"] is True
        assert rel["pane_id"] == "pane_2"
        assert rel["node_uuid"] == orig_node
        assert rel["resume_mode"] == "by_id"
        assert "pane_2" in term.close_calls
        assert term.spawn_calls[-1]["launch_request"]["resume_mode"] == "by_id"
        assert term.spawn_calls[-1]["launch_request"]["cli_session_id"] == "claude-sid-77"
        assert bridge.pty_get_pane_role("pane_2")["role"] == "orchestrator"
        assert bridge.pty_get_pane_role("pane_2")["mandate"] == "Coordina equipo"

        # Caso 2: Codex sin cli_session_id y único en la carpeta -> relanza con continue
        s2 = bridge.pty_spawn_pane("pane_3", "codex", start_hub=False, project_uuid=proj)
        assert s2["success"] is True
        rel2 = bridge.pty_relaunch_pane("pane_3")
        assert rel2["success"] is True
        assert rel2["resume_mode"] == "continue"
        assert term.spawn_calls[-1]["resume"] is True

        # Caso 3: Dos Codex en la misma carpeta -> ambigüedad impide continue -> relanza fresh
        s3 = bridge.pty_spawn_pane("pane_4", "codex", start_hub=False, project_uuid=proj)
        assert s3["success"] is True
        rel3 = bridge.pty_relaunch_pane("pane_4")
        assert rel3["success"] is True
        assert rel3["resume_mode"] == "fresh"
        assert term.spawn_calls[-1]["resume"] is False
    finally:
        bridge._close_terminal_matrix()
        bridge.db.close()


# ---------------------------------------------------------------------------
# 14. Migración sobre BD legadas v2.0.1 y bebdcca
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("version", ["v2_0_1", "bebdcca"])
def test_session_migration_on_legacy_dbs(tmp_path: Path, version: str) -> None:
    info = build_legacy_db(tmp_path / f"{version}.db", version)
    for _ in range(2):
        db = DatabaseManager(db_path=str(info["path"]))
        db.close()

    with sqlite3.connect(str(info["path"])) as conn:
        sess_cols = {row[1] for row in conn.execute("PRAGMA table_info(terminal_sessions);").fetchall()}
        snap_cols = {row[1] for row in conn.execute("PRAGMA table_info(terminal_session_snapshots);").fetchall()}
    assert sess_cols == TERMINAL_SESSIONS_COLUMNS
    assert snap_cols == TERMINAL_SESSION_SNAPSHOTS_COLUMNS

    db = DatabaseManager(db_path=str(info["path"]))
    try:
        repo = TerminalSessionSqliteRepo(db)
        cat = CliCatalogService(include_user_catalog=False, platform="linux")
        svc = TerminalSessionService(repo=repo, catalog=cat, identity_repo=db, settings=db, app_version="2.0.2")
        proj = info["project_uuid"]
        assert db.get_project_by_uuid(proj) is not None

        svc.record_spawn(
            project_uuid=proj,
            pane_slot="pane_1",
            cli_id="custom_shell",
            cwd=None,
            cli_session_id=None,
            cli_session_source="none",
        )
        svc.mark_app_closing({proj: ["pane_1"]})
        plan = svc.restorable(proj, max_panes=4)
        assert len(plan.items) == 1
        assert plan.items[0].pane_slot == "pane_1"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Adicionales: role == 'installer' y fábrica idempotente / puente público
# ---------------------------------------------------------------------------


def test_wire_sessions_ignores_installer_role_and_factory_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge, _, proj = _build_bridge(tmp_path, monkeypatch)
    try:
        s1 = ApplicationFactory.terminal_session_service(bridge.core_kernel)
        s2 = ApplicationFactory.terminal_session_service(bridge.core_kernel)
        assert s1 is s2
        assert bridge.core_kernel.terminal_session_service is s1

        bridge.extensions_ui.pane_lifecycle.emit_spawned(
            PaneSpawnedEvent(
                pane_id="pane_1",
                project_uuid=proj,
                node_uuid="n-inst",
                cli_profile="custom_shell",
                cwd=None,
                session_id="s-inst",
                launch_meta={"role": "installer"},
                request={"role": "installer"},
            )
        )
        assert s1.get_session(proj, "pane_1") is None

        exposed = {fn.__name__ for fn in bridge.public_js_functions()}
        assert {
            "pty_relaunch_pane",
            "pty_get_restorable_session",
            "pty_restore_session",
            "pty_dismiss_restore",
            "pty_get_restore_policy",
            "pty_set_restore_policy",
        } <= exposed
    finally:
        bridge._close_terminal_matrix()
        bridge.db.close()


@pytest.mark.skipif(os.name != "posix", reason="PosixPtyHostConnector solo corre en POSIX.")
def test_posix_pty_host_wires_session_launch_assign_and_resume_by_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace
    from src.adapters.terminal import pty_host
    from src.adapters.terminal.mcp_injection import PaneMcpConfigStore
    from src.adapters.terminal.pty_host import PosixPtyHostConnector

    claude_bin = _make_exec(tmp_path, "claude")
    cat_path = _make_catalog_file(
        tmp_path,
        [
            _cli_entry(
                "claude",
                claude_bin,
                resume_argv=["--continue"],
                resume_verified=True,
                session_verified=True,
                assign_argv=["--session-id", "{cli_session_id}"],
                resume_by_id_argv=["--resume", "{cli_session_id}"],
            ),
        ],
    )
    svc = CliCatalogService(
        repo_catalog_path=cat_path,
        include_user_catalog=False,
        which=lambda n: claude_bin if n == "claude" else None,
        platform="linux",
    )
    store = PaneMcpConfigStore(base_dir=tmp_path / "mcp_store")
    host = PosixPtyHostConnector(catalog_service=svc, mcp_config_store=store)
    captured: List[List[str]] = []
    monkeypatch.setattr(
        pty_host.pty,
        "openpty",
        lambda: (os.open(os.devnull, os.O_RDONLY), os.open(os.devnull, os.O_RDONLY)),
    )
    monkeypatch.setattr(host, "_set_winsize", lambda fd, c, r: None)
    monkeypatch.setattr(host, "_set_nonblocking", lambda fd: None)
    monkeypatch.setattr(
        pty_host.subprocess,
        "Popen",
        lambda cmd, **kw: (captured.append(list(cmd)), SimpleNamespace(pid=9999, poll=lambda: None))[1],
    )
    monkeypatch.setattr(host, "_reader_loop", lambda sess: None)
    try:
        res1 = host.spawn_session(
            1,
            cli_profile="claude",
            use_trampoline=False,
            launch_request={"resume_mode": "none"},
        )
        assert res1["success"] is True
        assigned = res1["launch_meta"]["cli_session_id"]
        assert res1["launch_meta"]["cli_session_source"] == "assigned"
        assert "--session-id" in captured[-1] and assigned in captured[-1]

        res2 = host.spawn_session(
            2,
            cli_profile="claude",
            use_trampoline=False,
            launch_request={"resume_mode": "by_id", "cli_session_id": assigned},
        )
        assert res2["success"] is True
        assert "--resume" in captured[-1] and assigned in captured[-1]
        assert "--session-id" not in captured[-1]
    finally:
        host.close()
