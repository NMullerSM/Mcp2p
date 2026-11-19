"""Pruebas del andamiaje v2.0.2 (WP-0a): migraciones, catálogo, aumentadores, ciclo de vida y CI."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from src.adapters.terminal import launch_augment
from src.adapters.terminal.cli_catalog import (
    CatalogValidationError,
    CliSpec,
    load_catalog,
    load_repo_catalog,
    _parse_entry,
)
from src.adapters.terminal.launch_augment import (
    LaunchAddition,
    LaunchContext,
    LaunchServices,
    run_augmenters,
)
from src.adapters.terminal.mcp_injection import McpInjectionError, PaneMcpConfigStore
from src.adapters.terminal.pty_host import PosixPtyHostConnector
from src.adapters.terminal.supervisor import SafeTerminalHostSupervisor
from src.adapters.terminal.winpty_host import WinPtyHostConnector
from src.app.api_bridge import ApiBridge
from src.app.ui_adapters.pane_lifecycle import PaneLifecycle, PaneSpawnedEvent
from src.core.domain.agent_activity import AgentState, NORMALIZED_EVENTS
from src.core.kernel import CoreKernel
from src.storage import (
    node_policy_schema,
    pane_identity_schema_v202,
    relay_schema_v202,
    terminal_session_schema,
)
from src.storage.db import DatabaseManager
from tests.helpers.legacy_db import build_legacy_db


# ---------------------------------------------------------------------------
# Helpers de prueba
# ---------------------------------------------------------------------------


def _base_catalog_entry(**overrides: Any) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "id": "demo_cli",
        "display_name": "Demo CLI",
        "icon": "demo",
        "kind": "agent_cli",
        "binary": "demo",
        "candidate_paths": {"linux": ["/usr/bin/demo"]},
        "launch_flags": [],
        "mcp": {"method": "none", "format": "none", "verified": False},
        "resume": {"argv": [], "verified": False},
        "install": {},
    }
    entry.update(overrides)
    return entry


class _CapturingTerminalConnector:
    """Conector doble mínimo que captura llamadas `spawn_session` y `close_session`."""

    def __init__(self) -> None:
        self.max_panes = 4
        self.launch_services: Optional[LaunchServices] = None
        self.spawn_calls: List[Dict[str, Any]] = []
        self.closed_calls: List[str] = []
        self._alive: set[str] = set()

    def is_available(self) -> bool:
        return True

    def list_sessions(self) -> List[Dict[str, Any]]:
        return [{"pane_id": p, "alive": True} for p in sorted(self._alive)]

    def spawn_session(self, pane_id: Any = 1, **kwargs: Any) -> Dict[str, Any]:
        norm = f"pane_{pane_id}" if isinstance(pane_id, int) else str(pane_id)
        self.spawn_calls.append({"pane_id": norm, **kwargs})
        self._alive.add(norm)
        return {
            "success": True,
            "degraded": False,
            "pane_id": norm,
            "session_id": f"{norm}-sess1",
            "pid": 43210,
            "cli_profile": kwargs.get("cli_profile", "custom_shell"),
            "role": kwargs.get("role", "implementer"),
            "node_uuid": kwargs.get("node_uuid"),
            "cols": kwargs.get("cols", 100),
            "rows": kwargs.get("rows", 28),
            "mcp_injection": {"status": "not_applicable", "method": "none", "verified": False, "reason": ""},
            "launch_meta": {"probe": "ok"},
            "launch_notes": [],
        }

    def close_session(self, pane_id: Any) -> Dict[str, Any]:
        norm = f"pane_{pane_id}" if isinstance(pane_id, int) else str(pane_id)
        self.closed_calls.append(norm)
        was_alive = norm in self._alive
        self._alive.discard(norm)
        return {"success": True, "pane_id": norm, "closed": was_alive}

    def get_pane_activity(self, pane_id: Any, *, min_silence_s: float = 1.5) -> Dict[str, Any]:
        return {"pane_id": str(pane_id), "state": "idle", "source": "silence_heuristic", "idle": True}

    def poll_output(self, pane_id: Any, since_seq: int = 0) -> Dict[str, Any]:
        return {"success": True, "pane_id": str(pane_id), "alive": str(pane_id) in self._alive, "text": ""}


class _DummyProc:
    def __init__(self, pid: int = 12345) -> None:
        self.pid = pid

    def poll(self) -> Optional[int]:
        return None

    def kill(self) -> None:
        return None

    def wait(self, timeout: Optional[float] = None) -> int:
        return 0


# ---------------------------------------------------------------------------
# 1-2. Migraciones e idempotencia sobre bases antiguas
# ---------------------------------------------------------------------------


def test_schema_v202_runs_after_existing_migrations_and_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: List[str] = []

    def _record(name: str):
        def _fn(conn: sqlite3.Connection) -> None:
            # Verifica que las migraciones previas ya dejaron sus tablas creadas.
            tables = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
            }
            assert {"projects", "pane_identities", "relay_messages", "operator_sessions"} <= tables
            calls.append(name)

        return _fn

    monkeypatch.setattr(pane_identity_schema_v202, "migrate_pane_identities_v202", _record("pane_identities_v202"))
    monkeypatch.setattr(relay_schema_v202, "migrate_relay_v202", _record("relay_v202"))
    monkeypatch.setattr(terminal_session_schema, "migrate_terminal_sessions", _record("terminal_sessions"))
    monkeypatch.setattr(node_policy_schema, "migrate_node_policy", _record("node_policy"))

    db_path = tmp_path / "v202_idem.db"
    m1 = DatabaseManager(str(db_path))
    m1.close()
    m2 = DatabaseManager(str(db_path))
    m2.close()

    expected_order = ["pane_identities_v202", "relay_v202", "terminal_sessions", "node_policy"]
    assert calls == expected_order + expected_order


@pytest.mark.parametrize("version", ["v2_0_1", "bebdcca"])
def test_legacy_databases_upgrade_without_errors(tmp_path: Path, version: str) -> None:
    db_path = tmp_path / f"legacy_{version}.db"
    seeded = build_legacy_db(db_path, version, rows=True)

    mgr = DatabaseManager(str(db_path))
    try:
        with mgr.get_connection() as conn:
            tables = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
            }
            assert {"projects", "relay_messages", "pane_identities", "operator_sessions"} <= tables
            proj = conn.execute(
                "SELECT project_uuid, name FROM projects WHERE project_uuid = ?;",
                (seeded["project_uuid"],),
            ).fetchone()
            assert proj is not None and proj["name"] == "Proyecto Legacy"

            if version == "bebdcca":
                pi = conn.execute(
                    "SELECT node_uuid FROM pane_identities WHERE project_uuid = ? AND pane_slot = ?;",
                    (seeded["project_uuid"], seeded["pane_slot"]),
                ).fetchone()
                assert pi is not None and pi["node_uuid"] == seeded["node_uuid"]

                q_row = conn.execute(
                    "SELECT state FROM relay_messages WHERE msg_id = ?;",
                    (seeded["queued_msg_id"],),
                ).fetchone()
                p_row = conn.execute(
                    "SELECT state, state_reason FROM relay_messages WHERE msg_id = ?;",
                    (seeded["parked_msg_id"],),
                ).fetchone()
                assert q_row is not None and q_row["state"] == "queued"
                assert p_row is not None and p_row["state"] == "parked" and p_row["state_reason"] == "CHAIN_DEPTH_EXCEEDED"
    finally:
        mgr.close()


# ---------------------------------------------------------------------------
# 3-5. Catálogo v2.0.2 y validación estricta
# ---------------------------------------------------------------------------


def test_catalog_new_blocks_default_unverified_for_every_repo_entry() -> None:
    cat = load_repo_catalog()
    assert len(cat.profiles) == 6
    assert AgentState.IDLE.value == "idle"
    assert "turn_ended" in NORMALIZED_EVENTS

    for spec in cat.profiles.values():
        for block_name in ("activity", "delivery", "role_launch", "session"):
            block = getattr(spec, block_name)
            assert block["status"] == "unverified", (spec.id, block_name)
            assert block["platforms"] == (), (spec.id, block_name)
            for plat in ("linux", "darwin", "win32"):
                assert spec.capability_verified(block_name, plat) is False


@pytest.mark.parametrize(
    "patch",
    [
        {"activity": {"unknown_key": 1}},
        {"delivery": {"unknown_key": 1}},
        {"role_launch": {"unknown_key": 1}},
        {"session": {"unknown_key": 1}},
        {"activity": {"platforms": ["freebsd"]}},
        {"activity": {"events": {"claude.Stop": "not_normalized"}}},
        {"activity": {"events": {"bad key!": "turn_ended"}}},
        {"activity": {"hook_install": "magic_hook"}},
        {"activity": {"session_id_field": "bad/field"}},
        {"delivery": {"paste_mode": "raw"}},
        {"delivery": {"submit_delay_ms": 10}},
        {"delivery": {"submit_delay_ms": 5000}},
        {"delivery": {"submit_delay_ms": True}},
        {"delivery": {"max_inject_bytes": 128}},
        {"delivery": {"max_inject_bytes": 16384}},
        {"delivery": {"confirm_event": "invented_event"}},
        {"role_launch": {"system_prompt_file_argv": ["--file", "{evil}"]}},
        {"role_launch": {"readonly_argv": ["--mode", "{role_file}"]}},
        {"session": {"assign_argv": ["--sid", "{evil}"]}},
        {"session": {"resume_by_id_argv": ["--resume", "{role_file}"]}},
        {"activity": {"status": "verified", "platforms": [], "verified_with_cli_version": "1.0.0", "verified_on": "2026-11-18"}},
        {"delivery": {"status": "verified", "platforms": ["linux"], "verified_with_cli_version": "", "verified_on": "2026-11-18"}},
        {"session": {"status": "verified", "platforms": ["linux"], "verified_with_cli_version": "1.0.0", "verified_on": "not-a-date"}},
    ],
)
def test_catalog_rejects_invalid_new_blocks(tmp_path: Path, patch: Dict[str, Any]) -> None:
    raw = _base_catalog_entry(**patch)
    with pytest.raises(CatalogValidationError):
        _parse_entry(raw, source="repo")

    # También una entrada de usuario con status=verified queda forzada a unverified y platforms=().
    user_path = tmp_path / "cli_catalog.user.json"
    user_payload = {
        "schema_version": 1,
        "clis": [
            _base_catalog_entry(
                id="my_user_cli",
                activity={
                    "status": "verified",
                    "platforms": ["linux"],
                    "hook_install": "claude_settings",
                    "events": {"Stop": "turn_ended"},
                    "verified_with_cli_version": "1.0.0",
                    "verified_on": "2026-11-18",
                },
                delivery={
                    "status": "verified",
                    "platforms": ["linux"],
                    "verified_with_cli_version": "1.0.0",
                    "verified_on": "2026-11-18",
                },
            )
        ],
    }
    user_path.write_text(json.dumps(user_payload), encoding="utf-8")
    cat = load_catalog(user_path=user_path, include_user=True)
    user_spec = cat.get("my_user_cli")
    assert user_spec is not None
    assert user_spec.activity["status"] == "unverified"
    assert user_spec.activity["platforms"] == ()
    assert user_spec.delivery["status"] == "unverified"
    assert user_spec.capability_verified("activity", "linux") is False


def test_capability_verified_requires_repo_status_and_platform() -> None:
    verified_block = {
        "status": "verified",
        "platforms": ["linux"],
        "verified_with_cli_version": "2.1.325",
        "verified_on": "2026-11-18",
    }
    repo_spec = _parse_entry(_base_catalog_entry(activity=verified_block), source="repo")
    assert repo_spec.capability_verified("activity", "linux") is True
    assert repo_spec.capability_verified("activity", "win32") is False
    assert repo_spec.capability_verified("delivery", "linux") is False
    assert repo_spec.capability_verified("unknown", "linux") is False

    user_spec = CliSpec(
        id="u",
        display_name="U",
        icon="u",
        kind="agent_cli",
        binary="u",
        binary_aliases=(),
        candidate_paths={},
        launch_flags=(),
        mcp={"method": "none", "format": "none", "argv": [], "env": {}},
        resume={"argv": [], "verified": False},
        install={},
        source="user",
        activity={"status": "verified", "platforms": ("linux",)},
    )
    assert user_spec.capability_verified("activity", "linux") is False


# ---------------------------------------------------------------------------
# 6-9. Aumentadores de lanzamiento y archivos privados de sesión
# ---------------------------------------------------------------------------


def test_noop_augmenters_keep_launch_argv_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cat = load_repo_catalog()
    fake_bin_dir = tmp_path / "bins"
    fake_bin_dir.mkdir()

    def _fake_which(name: str) -> Optional[str]:
        exe = fake_bin_dir / name
        if not exe.exists():
            exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            exe.chmod(0o755)
        return str(exe)

    # 1. POSIX host
    from src.adapters.terminal import cli_catalog, pty_host

    for cli_id, spec in cat.profiles.items():
        store = PaneMcpConfigStore(base_dir=tmp_path / f"posix_store_{cli_id}")
        from src.adapters.terminal.cli_catalog import CliCatalogService

        svc = CliCatalogService(
            include_user_catalog=False,
            which=_fake_which,
            platform="linux",
            home=str(tmp_path / "home"),
            environ={"SHELL": _fake_which("sh")},
        )
        host = PosixPtyHostConnector(catalog_service=svc, mcp_config_store=store)
        captured_cmds: List[List[str]] = []

        monkeypatch.setattr(pty_host.pty, "openpty", lambda: (os.open(os.devnull, os.O_RDONLY), os.open(os.devnull, os.O_RDONLY)))
        monkeypatch.setattr(host, "_set_winsize", lambda fd, c, r: None)
        monkeypatch.setattr(host, "_set_nonblocking", lambda fd: None)

        def _fake_popen(cmd: List[str], **kw: Any) -> _DummyProc:
            captured_cmds.append(list(cmd))
            return _DummyProc(1001)

        monkeypatch.setattr(pty_host.subprocess, "Popen", _fake_popen)
        monkeypatch.setattr(host, "_reader_loop", lambda sess: None)

        res = host.spawn_session(
            1,
            cli_profile=cli_id,
            hub_url="http://127.0.0.1:8765/mcp",
            session_token="tok-abc",
            node_uuid="node-1",
            use_trampoline=False,
        )
        assert res["success"] is True, res
        assert res["launch_meta"] == {}
        assert res["launch_notes"] == []

        expected_argv, _, _ = svc.build_launch(cli_id)
        assert expected_argv is not None
        # Con aumentadores no-op, el argv lanzado coincide exactamente con base + mcp_injection.
        inj_argv = [
            t.replace("{hub_url}", "http://127.0.0.1:8765/mcp")
            for t in spec.mcp.get("argv", [])
        ]
        if "{mcp_config_file}" in " ".join(inj_argv):
            assert len(captured_cmds[-1]) == len(expected_argv) + len(inj_argv)
            assert captured_cmds[-1][: len(expected_argv)] == expected_argv
        else:
            assert captured_cmds[-1] == [*expected_argv, *inj_argv]
        host.close()

    # 2. Windows host (simulado con fake winpty)
    from src.adapters.terminal import winpty_host

    for cli_id, spec in cat.profiles.items():
        store = PaneMcpConfigStore(base_dir=tmp_path / f"win_store_{cli_id}")
        from src.adapters.terminal.cli_catalog import CliCatalogService

        svc = CliCatalogService(
            include_user_catalog=False,
            which=_fake_which,
            platform="win32",
            home=str(tmp_path / "home"),
            environ={"COMSPEC": _fake_which("cmd.exe")},
        )
        class _FakeWinPty:
            def __init__(self, cols: int, rows: int, backend: Any = 0) -> None:
                self.pid = 2002

            def spawn(self, appname: str, cmdline: str = "", cwd: Any = None, env: Any = None) -> bool:
                return True

            def isalive(self) -> bool:
                return False

            def read(self, n: int = 4096) -> str:
                return ""

        win_host = WinPtyHostConnector(
            catalog_service=svc,
            mcp_config_store=store,
            winpty_module=SimpleNamespace(Backend=SimpleNamespace(ConPTY=0), PTY=_FakeWinPty),
        )
        captured_win: List[List[str]] = []
        monkeypatch.setattr(winpty_host, "is_windows_platform", lambda: True)
        monkeypatch.setattr(win_host, "_which_executable", lambda b, cwd=None: _fake_which(b))

        def _fake_win_launch(cmd: List[str], comspec: Optional[str] = None) -> SimpleNamespace:
            captured_win.append(list(cmd))
            return SimpleNamespace(appname=cmd[0], cmdline=" ".join(cmd), display=list(cmd), kind="exe")

        monkeypatch.setattr(winpty_host, "build_windows_launch", _fake_win_launch)
        monkeypatch.setattr(win_host, "_reader_loop", lambda sess: None)

        res = win_host.spawn_session(
            1,
            cli_profile=cli_id,
            hub_url="http://127.0.0.1:8765/mcp",
            session_token="tok-abc",
            node_uuid="node-1",
            use_trampoline=False,
        )
        assert res["success"] is True, res
        assert res["launch_meta"] == {}
        assert res["launch_notes"] == []
        expected_argv, _, _ = svc.build_launch(cli_id)
        assert expected_argv is not None
        assert len(captured_win[-1]) == len(expected_argv) + len(spec.mcp.get("argv", []))
        assert captured_win[-1][1 : len(expected_argv)] == expected_argv[1:]
        win_host.close()


def test_launch_addition_validation_rejects_controls_denied_env_and_bad_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = _parse_entry(_base_catalog_entry(), source="repo")
    ctx = LaunchContext(
        spec=spec,
        platform="linux",
        pane_slot="pane_1",
        session_id="pane_1-abc",
        hub_url="http://127.0.0.1:8765/mcp",
        request={},
        session_file=lambda n: tmp_path / n,
        services=None,
    )

    bad_cases = [
        lambda c: LaunchAddition(argv=("--bad\x1b",)),
        lambda c: LaunchAddition(argv=("x" * 4097,)),
        lambda c: LaunchAddition(env={"PATH": "/tmp/evil"}),
        lambda c: LaunchAddition(env={"MCP2P_SESSION_TOKEN": "override"}),
        lambda c: LaunchAddition(env={"lower_var": "1"}),
        lambda c: LaunchAddition(env={"OK_VAR": "bad\nval"}),
        lambda c: LaunchAddition(files={"../escape": b"x"}),
        lambda c: LaunchAddition(files={"..": b"x"}),
        lambda c: LaunchAddition(files={"big.txt": b"x" * (256 * 1024 + 1)}),
        lambda c: LaunchAddition(files={"bad.txt": "not-bytes"}),  # type: ignore[dict-item]
    ]

    for idx, fn in enumerate(bad_cases):
        def _good(c: LaunchContext) -> LaunchAddition:
            return LaunchAddition(argv=("--safe",), meta={"kept": "yes"})

        monkeypatch.setattr(launch_augment, "AUGMENTERS", (fn, _good))
        out = run_augmenters(ctx)
        assert out.argv == ("--safe",), f"case {idx}"
        assert out.env == {}
        assert out.files == {}
        assert out.meta == {"kept": "yes"}
        assert len(out.notes) == 1 and "descartado" in out.notes[0]


def test_failing_augmenter_is_ignored_with_note_and_pane_opens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.adapters.terminal import pty_host
    from src.adapters.terminal.cli_catalog import CliCatalogService

    def _boom(ctx: LaunchContext) -> LaunchAddition:
        raise RuntimeError("fallo simulado")

    def _ok(ctx: LaunchContext) -> LaunchAddition:
        return LaunchAddition(argv=("--extra-flag",), meta={"ok": "1"})

    monkeypatch.setattr(launch_augment, "AUGMENTERS", (_boom, _ok))

    sh_bin = tmp_path / "sh"
    sh_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    sh_bin.chmod(0o755)
    svc = CliCatalogService(
        include_user_catalog=False,
        which=lambda n: str(sh_bin),
        platform="linux",
        environ={"SHELL": str(sh_bin)},
    )
    store = PaneMcpConfigStore(base_dir=tmp_path / "store")
    host = PosixPtyHostConnector(catalog_service=svc, mcp_config_store=store)
    captured: List[List[str]] = []

    monkeypatch.setattr(pty_host.pty, "openpty", lambda: (os.open(os.devnull, os.O_RDONLY), os.open(os.devnull, os.O_RDONLY)))
    monkeypatch.setattr(host, "_set_winsize", lambda fd, c, r: None)
    monkeypatch.setattr(host, "_set_nonblocking", lambda fd: None)
    monkeypatch.setattr(pty_host.subprocess, "Popen", lambda cmd, **kw: (captured.append(list(cmd)), _DummyProc())[1])
    monkeypatch.setattr(host, "_reader_loop", lambda sess: None)

    res = host.spawn_session(1, cli_profile="custom_shell", use_trampoline=False)
    assert res["success"] is True
    assert res["launch_meta"] == {"ok": "1"}
    assert any("descartado" in n and "fallo simulado" in n for n in res["launch_notes"])
    assert "--extra-flag" in captured[0]
    host.close()


def test_session_files_are_written_0600_before_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.adapters.terminal import pty_host
    from src.adapters.terminal.cli_catalog import CliCatalogService

    store = PaneMcpConfigStore(base_dir=tmp_path / "store")

    # Rechazos directos de PaneMcpConfigStore.write_session_file
    with pytest.raises(McpInjectionError):
        store.write_session_file("pane_1-s1", "../evil", b"abc")
    with pytest.raises(McpInjectionError):
        store.write_session_file("pane_1-s1", "huge.bin", b"a" * (256 * 1024 + 1))

    written_path = store.write_session_file("pane_1-s1", "direct.txt", b"hola")
    assert written_path.is_file()
    if sys.platform != "win32":
        assert stat.S_IMODE(written_path.stat().st_mode) == 0o600
    with pytest.raises(McpInjectionError):
        # O_EXCL impide sobreescribir
        store.write_session_file("pane_1-s1", "direct.txt", b"otro")

    # Escritura antes de Popen mediante aumentador
    def _writer_aug(ctx: LaunchContext) -> LaunchAddition:
        target = ctx.session_file("role.md")
        return LaunchAddition(
            argv=("--role-file", str(target)),
            files={"role.md": b"rol de prueba"},
            meta={"role_file": "written"},
        )

    monkeypatch.setattr(launch_augment, "AUGMENTERS", (_writer_aug,))

    sh_bin = tmp_path / "sh"
    sh_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    sh_bin.chmod(0o755)
    svc = CliCatalogService(
        include_user_catalog=False,
        which=lambda n: str(sh_bin),
        platform="linux",
        environ={"SHELL": str(sh_bin)},
    )
    host = PosixPtyHostConnector(catalog_service=svc, mcp_config_store=store)
    observed_before_popen: Dict[str, Any] = {}

    monkeypatch.setattr(pty_host.pty, "openpty", lambda: (os.open(os.devnull, os.O_RDONLY), os.open(os.devnull, os.O_RDONLY)))
    monkeypatch.setattr(host, "_set_winsize", lambda fd, c, r: None)
    monkeypatch.setattr(host, "_set_nonblocking", lambda fd: None)

    def _inspect_popen(cmd: List[str], **kw: Any) -> _DummyProc:
        idx = cmd.index("--role-file")
        p = Path(cmd[idx + 1])
        observed_before_popen["exists"] = p.is_file()
        observed_before_popen["bytes"] = p.read_bytes()
        observed_before_popen["mode"] = stat.S_IMODE(p.stat().st_mode)
        observed_before_popen["dir_mode"] = stat.S_IMODE(p.parent.stat().st_mode)
        return _DummyProc()

    monkeypatch.setattr(pty_host.subprocess, "Popen", _inspect_popen)
    monkeypatch.setattr(host, "_reader_loop", lambda sess: None)

    res = host.spawn_session(1, cli_profile="custom_shell", use_trampoline=False)
    assert res["success"] is True
    assert observed_before_popen["exists"] is True
    assert observed_before_popen["bytes"] == b"rol de prueba"
    if sys.platform != "win32":
        assert observed_before_popen["mode"] == 0o600
        assert observed_before_popen["dir_mode"] == 0o700
    host.close()


# ---------------------------------------------------------------------------
# 10-12. pty_spawn_pane, PaneLifecycle y paso conpty del CI
# ---------------------------------------------------------------------------


def test_pty_spawn_pane_new_params_validated_and_positional_compat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("src.app.ui_adapters.extensions_lab_ui.is_unsupported_terminal_platform", lambda: False)
    db = DatabaseManager(str(tmp_path / "bridge.db"))
    try:
        bridge = ApiBridge(db_manager=db)
        cap = _CapturingTerminalConnector()
        sup = SafeTerminalHostSupervisor(primary=cap, fallback=bridge.extensions_ui._get_terminal_connector())  # type: ignore[arg-type]
        bridge.core_kernel.register_connector(CoreKernel.PORT_AGENT_TERMINAL, sup, replace=True)

        # Llamada posicional de 10 argumentos (contrato del frontend actual)
        res = bridge.pty_spawn_pane(
            "pane_1", "custom_shell", "implementer", None, 90, 30, False, None, False, None
        )
        assert res["success"] is True, res
        assert cap.launch_services is bridge.extensions_ui.launch_services
        assert cap.launch_services.identity_repo is bridge.core_kernel.pane_identity_repo
        assert cap.spawn_calls[-1]["resume"] is False
        assert cap.spawn_calls[-1]["launch_request"]["resume_mode"] == "none"

        # resume_mode='continue' activa resume=True
        res2 = bridge.pty_spawn_pane("pane_2", "custom_shell", resume_mode="continue", start_hub=False)
        assert res2["success"] is True
        assert cap.spawn_calls[-1]["resume"] is True
        assert cap.spawn_calls[-1]["launch_request"]["resume_mode"] == "continue"

        # Validaciones de resume_mode y cli_session_id
        bad_mode = bridge.pty_spawn_pane("pane_3", "custom_shell", resume_mode="weird", start_hub=False)
        assert bad_mode["success"] is False and bad_mode["code"] == "INVALID_ARGUMENT"

        sid_without_by_id = bridge.pty_spawn_pane(
            "pane_3", "custom_shell", resume_mode="continue", cli_session_id="abc-123", start_hub=False
        )
        assert sid_without_by_id["success"] is False and sid_without_by_id["code"] == "INVALID_ARGUMENT"

        by_id_without_sid = bridge.pty_spawn_pane("pane_3", "custom_shell", resume_mode="by_id", start_hub=False)
        assert by_id_without_sid["success"] is False and by_id_without_sid["code"] == "INVALID_ARGUMENT"

        bad_sid = bridge.pty_spawn_pane(
            "pane_3", "custom_shell", resume_mode="by_id", cli_session_id="bad/id!", start_hub=False
        )
        assert bad_sid["success"] is False and bad_sid["code"] == "INVALID_ARGUMENT"

        ok_by_id = bridge.pty_spawn_pane(
            "pane_3", "custom_shell", resume_mode="by_id", cli_session_id="sess_ABC-123", start_hub=False
        )
        assert ok_by_id["success"] is True
        assert cap.spawn_calls[-1]["resume"] is False
        assert cap.spawn_calls[-1]["launch_request"]["resume_mode"] == "by_id"
        assert cap.spawn_calls[-1]["launch_request"]["cli_session_id"] == "sess_ABC-123"
    finally:
        db.close()


def test_pane_lifecycle_events_and_isolated_listener_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("src.app.ui_adapters.extensions_lab_ui.is_unsupported_terminal_platform", lambda: False)
    db = DatabaseManager(str(tmp_path / "lifecycle.db"))
    try:
        bridge = ApiBridge(db_manager=db)
        cap = _CapturingTerminalConnector()
        bridge.core_kernel.register_connector(CoreKernel.PORT_AGENT_TERMINAL, cap, replace=True)

        events: List[tuple[str, Any]] = []
        lc: PaneLifecycle = bridge.extensions_ui.pane_lifecycle

        def _broken(*args: Any) -> None:
            raise RuntimeError("listener crash")

        lc.on_spawned(_broken)
        lc.on_spawned(lambda ev: events.append(("spawned", ev)))
        lc.on_closed_by_user(_broken)
        lc.on_closed_by_user(lambda proj, pid: events.append(("closed_by_user", (proj, pid))))
        lc.on_app_closing(_broken)
        lc.on_app_closing(lambda alive: events.append(("app_closing", (dict(alive), list(cap.closed_calls)))))

        r1 = bridge.pty_spawn_pane("pane_1", "custom_shell", start_hub=False)
        r2 = bridge.pty_spawn_pane("pane_2", "custom_shell", start_hub=False)
        assert r1["success"] is True and r2["success"] is True

        spawned_evs = [ev for kind, ev in events if kind == "spawned"]
        assert len(spawned_evs) == 2
        assert isinstance(spawned_evs[0], PaneSpawnedEvent)
        assert spawned_evs[0].pane_id == "pane_1"
        assert spawned_evs[0].launch_meta == {"probe": "ok"}

        c1 = bridge.pty_close_pane("pane_1")
        assert c1["success"] is True
        assert ("closed_by_user", (r1["project_uuid"], "pane_1")) in events

        bridge.extensions_ui.close_terminal_matrix()
        closing_evs = [payload for kind, payload in events if kind == "app_closing"]
        assert len(closing_evs) == 1
        alive_map, closed_before_emit = closing_evs[0]
        assert alive_map == {r2["project_uuid"]: ["pane_2"]}
        # emit_app_closing debe ejecutarse ANTES de cerrar las sesiones vivas.
        assert "pane_2" not in closed_before_emit
        assert "pane_2" in cap.closed_calls
    finally:
        db.close()


def test_ci_conpty_step_runs_osc_measurement_tests() -> None:
    wf_path = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "build-and-release.yml"
    content = wf_path.read_text(encoding="utf-8")
    conpty_lines = [line for line in content.splitlines() if "pytest -m conpty" in line]
    assert len(conpty_lines) == 1
    line = conpty_lines[0]
    assert "tests/test_winpty_real_windows.py" in line
    assert "tests/test_winpty_osc_real_windows.py" in line
