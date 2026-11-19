"""Pruebas E2E WP-6a (v2.0.2): `hook_client` en árbol real de procesos, `wire_activity` y `pty_get_matrix_status`."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest

from src.adapters.terminal.cli_catalog import CliCatalogService, _parse_entry
from src.app.api_bridge import ApiBridge
from src.core.domain.agent_activity import AgentState
from src.core.factory import ApplicationFactory
from src.core.kernel import CoreKernel
from src.mcp.pane_registry import EmbeddedMcpHub, PaneRegistry
from src.storage.db import DatabaseManager
from tests.helpers.relay_lab import PaneRootProcess, install_pty_write_spy

ROOT = Path(__file__).resolve().parents[1]
HOOK_CLIENT = ROOT / "src" / "app" / "hook_client.py"


@pytest.fixture(autouse=True)
def _no_pty_writes(monkeypatch: pytest.MonkeyPatch):
    calls = install_pty_write_spy(monkeypatch)
    yield calls
    assert calls == [], f"WP-6a no debe escribir en un PTY: {calls}"


def _verified_claude_spec():
    return _parse_entry(
        {
            "id": "claude",
            "display_name": "Claude Code",
            "icon": "claude",
            "kind": "agent_cli",
            "binary": "claude",
            "candidate_paths": {"linux": ["/usr/bin/claude"], "win32": ["C:\\bin\\claude.exe"]},
            "launch_flags": [],
            "mcp": {"method": "none", "format": "none", "verified": False},
            "resume": {"argv": [], "verified": False},
            "install": {},
            "activity": {
                "status": "verified",
                "platforms": ["linux", "darwin", "win32"],
                "hook_install": "claude_settings",
                "events": {
                    "claude.SessionStart": "session_started",
                    "claude.UserPromptSubmit": "turn_started",
                    "claude.Stop": "turn_ended",
                    "claude.Notification.permission_prompt": "permission_requested",
                },
                "session_id_field": "session_id",
                "verified_with_cli_version": "2.1.0",
                "verified_on": "2026-11-18",
                "note": "prueba e2e",
            },
        },
        source="repo",
    )


def _hooks_base_url(mcp_url: str) -> str:
    return mcp_url[:-4] if mcp_url.endswith("/mcp") else mcp_url


def test_hook_client_inside_pane_tree_sets_idle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = _verified_claude_spec()
    monkeypatch.setattr(CliCatalogService, "get_spec", lambda self, cid: spec if cid == "claude" else None)

    kernel = ApplicationFactory.create_mcp_kernel(db_path=str(tmp_path / "e2e_hooks.db"))
    activity = ApplicationFactory.agent_activity_registry(kernel)
    proj = kernel.db.create_project("E2E Hooks", str(tmp_path / "repo"))["project_uuid"]
    reg = PaneRegistry(identity_repo=kernel.pane_identity_repo)
    pane = reg.register_pane("pane_1", project_uuid=proj, cli_profile="claude")
    node_uuid = pane["node_uuid"]
    token = pane["session_token"]
    activity.reset(node_uuid, "pane_1-e2e")

    # Proceso raíz del panel que mantiene una sesión MCP viva (agent_session.py) y lanza hook_client como hijo
    pane_root = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import os, subprocess, sys; "
                "sys.stdout.write('ready\\n'); sys.stdout.flush(); "
                "line = sys.stdin.readline().strip(); "
                "env = {**os.environ, 'MCP2P_HUB_URL': sys.argv[1], 'MCP2P_SESSION_TOKEN': sys.argv[2]}; "
                "p = subprocess.run([sys.executable, '-I', sys.argv[3], 'claude.Stop'], "
                "input=line.encode('utf-8'), capture_output=True, env=env); "
                "sys.stdout.write(str(p.returncode) + '\\n'); sys.stdout.flush(); "
                "sys.stdin.readline()"
            ),
            "PLACEHOLDER",
            token,
            str(HOOK_CLIENT),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(ROOT),
    )
    # Reemplazar argv[1] con la URL real una vez arrancado el Hub: usamos un script que lea URL de stdin
    pane_root.kill()
    pane_root.wait(timeout=5)

    hub = EmbeddedMcpHub(kernel=kernel, pane_registry=reg, host="127.0.0.1", port=0)
    try:
        mcp_url = hub.start(timeout_s=15.0)
        base_url = _hooks_base_url(mcp_url)

        pane_root = subprocess.Popen(
            [
                sys.executable,
                "-c",
                (
                    "import json, os, subprocess, sys; "
                    "sys.stdout.write('ready\\n'); sys.stdout.flush(); "
                    "cmd = json.loads(sys.stdin.readline()); "
                    "env = {**os.environ, 'MCP2P_HUB_URL': cmd['url'], 'MCP2P_SESSION_TOKEN': cmd['token']}; "
                    "p = subprocess.run([sys.executable, '-I', cmd['client'], cmd['event']], "
                    "input=cmd['payload'].encode('utf-8'), capture_output=True, env=env); "
                    "sys.stdout.write(json.dumps({'rc': p.returncode, 'out': p.stdout.decode(), 'err': p.stderr.decode()}) + '\\n'); "
                    "sys.stdout.flush(); "
                    "sys.stdin.readline()"
                ),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(ROOT),
        )
        assert reg.bind_process("pane_1", pane_root.pid) is not None
        assert pane_root.stdout is not None and pane_root.stdin is not None
        assert pane_root.stdout.readline().strip() == "ready"

        # También arrancamos agent_session.py en un segundo panel para validar convivencia real
        agent_root = PaneRootProcess()
        reg.register_pane("pane_2", project_uuid=proj, cli_profile="claude")
        assert reg.bind_process("pane_2", agent_root.pid) is not None
        agent_root.session_start(mcp_url, reg.get_pane("pane_2").session_token)  # type: ignore[union-attr]

        t0 = time.monotonic()
        pane_root.stdin.write(
            json.dumps(
                {
                    "url": base_url,
                    "token": token,
                    "client": str(HOOK_CLIENT),
                    "event": "claude.Stop",
                    "payload": json.dumps({"session_id": "sess-e2e-1"}),
                }
            )
            + "\n"
        )
        pane_root.stdin.flush()
        reply = json.loads(pane_root.stdout.readline())
        elapsed_ms = (time.monotonic() - t0) * 1000.0

        assert reply["rc"] == 0
        assert reply["out"] == ""
        assert elapsed_ms < 500.0 or activity.snapshot(node_uuid).state == AgentState.IDLE
        snap = activity.snapshot(node_uuid)
        assert snap.state == AgentState.IDLE
        assert snap.source == "hook"
        assert snap.incarnation == "pane_1-e2e"
    finally:
        try:
            if pane_root.stdin:
                pane_root.stdin.write("\n")
                pane_root.stdin.flush()
            pane_root.wait(timeout=5)
        except Exception:
            pane_root.kill()
        try:
            agent_root.close()  # type: ignore[name-defined]
        except Exception:
            pass
        hub.stop(timeout_s=5.0)
        kernel.close()


def test_hook_client_outside_tree_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = _verified_claude_spec()
    monkeypatch.setattr(CliCatalogService, "get_spec", lambda self, cid: spec if cid == "claude" else None)

    kernel = ApplicationFactory.create_mcp_kernel(db_path=str(tmp_path / "e2e_foreign.db"))
    activity = ApplicationFactory.agent_activity_registry(kernel)
    proj = kernel.db.create_project("E2E Foreign", str(tmp_path / "repo"))["project_uuid"]
    reg = PaneRegistry(identity_repo=kernel.pane_identity_repo)
    pane = reg.register_pane("pane_1", project_uuid=proj, cli_profile="claude")
    node_uuid = pane["node_uuid"]
    token = pane["session_token"]

    # El panel 1 pertenece a `pane_root` (su propio árbol); llamamos desde pytest (proceso ajeno)
    pane_root = PaneRootProcess()
    assert reg.bind_process("pane_1", pane_root.pid) is not None
    hub = EmbeddedMcpHub(kernel=kernel, pane_registry=reg, host="127.0.0.1", port=0)
    try:
        mcp_url = hub.start(timeout_s=15.0)
        base_url = _hooks_base_url(mcp_url)

        env = {
            **os.environ,
            "MCP2P_HUB_URL": base_url,
            "MCP2P_SESSION_TOKEN": token,
            "MCP2P_HOOK_DEBUG": "1",
        }
        proc = subprocess.run(
            [sys.executable, "-I", str(HOOK_CLIENT), "claude.Stop"],
            input=b'{"session_id":"stolen"}',
            capture_output=True,
            env=env,
            timeout=10,
            cwd=str(ROOT),
        )
        # hook_client siempre sale 0 y sin stdout, pero el Hub lo rechazó con 403 PEER_PROCESS_MISMATCH
        assert proc.returncode == 0
        assert proc.stdout == b""
        assert b"403" in proc.stderr
        assert activity.snapshot(node_uuid).state == AgentState.UNKNOWN
    finally:
        pane_root.close()
        hub.stop(timeout_s=5.0)
        kernel.close()


def test_matrix_status_exposes_agent_state_without_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import src.app.ui_adapters.extensions_lab_ui as lab_mod

    monkeypatch.setattr(lab_mod, "is_unsupported_terminal_platform", lambda: False)

    class _FakeTerm:
        max_panes = 4

        def __init__(self) -> None:
            self.sessions: Dict[str, Dict[str, Any]] = {}
            self.launch_services = None

        def is_available(self) -> bool:
            return True

        def get_status(self) -> Dict[str, Any]:
            return {"available": True, "degraded": False, "mode": "fake"}

        def list_sessions(self) -> List[Dict[str, Any]]:
            return list(self.sessions.values())

        def spawn_session(self, **kwargs: Any) -> Dict[str, Any]:
            pane_id = kwargs["pane_id"]
            sess = {
                "pane_id": pane_id,
                "alive": True,
                "session_id": f"{pane_id}-inc1",
                "pid": os.getpid(),
                "cli_profile": kwargs.get("cli_profile", "custom_shell"),
            }
            self.sessions[pane_id] = sess
            return {"success": True, **sess}

        def close_session(self, pane_id: Any) -> Dict[str, Any]:
            closed = self.sessions.pop(str(pane_id), None) is not None
            return {"success": True, "pane_id": str(pane_id), "closed": closed}

        def close(self) -> None:
            self.sessions.clear()

    bridge = ApiBridge(db_manager=DatabaseManager(db_path=str(tmp_path / "matrix_state.db")))
    term = _FakeTerm()
    bridge.core_kernel.register_connector(CoreKernel.PORT_AGENT_TERMINAL, term)
    proj = bridge.db.create_project("Matrix Hooks", str(tmp_path / "repo"))["project_uuid"]
    try:
        assert bridge.extensions_ui.launch_services.hook_argv is not None
        assert bridge.extensions_ui.launch_services.hook_argv("claude.Stop")[-1] == "claude.Stop"

        spawn = bridge.pty_spawn_pane("pane_1", "custom_shell", start_hub=False, project_uuid=proj)
        assert spawn["success"] is True
        node_uuid = spawn["node_uuid"]

        status0 = bridge.pty_get_matrix_status()
        pane0 = next(p for p in status0["panes"] if p["pane_id"] == "pane_1")
        assert set(pane0["agent_state"].keys()) == {"state", "source", "since_ms"}
        assert pane0["agent_state"]["state"] == "unknown"
        assert pane0["agent_state"]["source"] == "initial"

        # Simula hook con datos sensibles en payload (no deben aparecer jamás en matrix_status)
        bridge.core_kernel.agent_activity.ingest(
            node_uuid, "pane_1-inc1", "turn_ended", 123456, "hook", cli_session_id="secret-sid-1"
        )
        status1 = bridge.pty_get_matrix_status()
        pane1 = next(p for p in status1["panes"] if p["pane_id"] == "pane_1")
        assert pane1["agent_state"] == {"state": "idle", "source": "hook", "since_ms": 123456}
        assert "secret-sid-1" not in json.dumps(pane1)

        # Turno humano vía registro de pulsaciones -> WORKING con fuente "human"
        bridge.extensions_ui._get_pane_registry().record_human_keystroke("pane_1", submitted=True)
        status2 = bridge.pty_get_matrix_status()
        pane2 = next(p for p in status2["panes"] if p["pane_id"] == "pane_1")
        assert pane2["agent_state"]["state"] == "working"
        assert pane2["agent_state"]["source"] == "human"
        assert set(pane2["agent_state"].keys()) == {"state", "source", "since_ms"}

        # Cerrar panel -> EXITED en el registro de actividad
        bridge.pty_close_pane("pane_1")
        assert bridge.core_kernel.agent_activity.snapshot(node_uuid).state == AgentState.EXITED
    finally:
        bridge._close_terminal_matrix()
        bridge.core_kernel.close()
