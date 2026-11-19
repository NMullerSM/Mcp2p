"""Integración I3 (v2.0.2): `/hooks/v1/claude.SessionStart` -> `AgentActivityRegistry` -> `on_cli_session_id`."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

from src.adapters.terminal.cli_catalog import CliCatalogService, _parse_entry
from src.app.api_bridge import ApiBridge
from src.core.domain.agent_activity import AgentState
from src.core.kernel import CoreKernel
from src.storage.db import DatabaseManager
from tests.helpers.relay_lab import install_pty_write_spy

ROOT = Path(__file__).resolve().parents[1]
HOOK_CLIENT = ROOT / "src" / "app" / "hook_client.py"


@pytest.fixture(autouse=True)
def _no_pty_writes(monkeypatch: pytest.MonkeyPatch):
    calls = install_pty_write_spy(monkeypatch)
    yield calls
    assert calls == [], f"I3 no debe escribir en un PTY: {calls}"


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
                "events": {"claude.SessionStart": "session_started"},
                "session_id_field": "session_id",
                "verified_with_cli_version": "2.1.0",
                "verified_on": "2026-11-18",
                "note": "I3",
            },
        },
        source="repo",
    )


class _CompatibleSessionReceiver:
    """Receptor compatible con `TerminalSessionService.record_cli_session_id` mientras WP-4a no esté fusionado."""

    def __init__(self) -> None:
        self.recorded: List[Dict[str, Any]] = []

    def record_cli_session_id(
        self,
        *,
        project_uuid: str,
        pane_slot: str,
        cli_session_id: str,
        source: str = "hook",
    ) -> bool:
        self.recorded.append(
            {
                "project_uuid": project_uuid,
                "pane_slot": pane_slot,
                "cli_session_id": cli_session_id,
                "source": source,
            }
        )
        return True


def test_i3_session_start_hook_forwards_cli_session_id_to_session_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import src.app.ui_adapters.extensions_lab_ui as lab_mod

    monkeypatch.setattr(lab_mod, "is_unsupported_terminal_platform", lambda: False)
    spec = _verified_claude_spec()
    monkeypatch.setattr(CliCatalogService, "get_spec", lambda self, cid: spec if cid == "claude" else None)

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
                "sys.stdout.write(json.dumps({'rc': p.returncode}) + '\\n'); sys.stdout.flush(); "
                "sys.stdin.readline()"
            ),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(ROOT),
    )

    class _FakeTerm:
        max_panes = 4

        def __init__(self) -> None:
            self.sessions: Dict[str, Dict[str, Any]] = {}

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
                "session_id": "pane_1-i3",
                "pid": pane_root.pid,
                "cli_profile": kwargs.get("cli_profile", "claude"),
            }
            self.sessions[pane_id] = sess
            return {"success": True, **sess}

        def close_session(self, pane_id: Any) -> Dict[str, Any]:
            closed = self.sessions.pop(str(pane_id), None) is not None
            return {"success": True, "pane_id": str(pane_id), "closed": closed}

        def close(self) -> None:
            self.sessions.clear()

    bridge = ApiBridge(db_manager=DatabaseManager(db_path=str(tmp_path / "i3_sessions_hooks.db")))
    bridge.core_kernel.register_connector(CoreKernel.PORT_AGENT_TERMINAL, _FakeTerm())
    proj = bridge.db.create_project("I3 Repo", str(tmp_path / "repo"))["project_uuid"]

    # Si WP-4a ya está presente, usa TerminalSessionService real; si no, usa el receptor compatible
    real_session_service = getattr(bridge.core_kernel, "terminal_session_service", None)
    receiver = None
    if real_session_service is None:
        try:
            from src.core.services.terminal_session_service import TerminalSessionService  # type: ignore

            real_session_service = TerminalSessionService(bridge.db)
            bridge.core_kernel.terminal_session_service = real_session_service
        except ImportError:
            receiver = _CompatibleSessionReceiver()
            bridge.core_kernel.terminal_session_service = receiver

    try:
        assert pane_root.stdout is not None and pane_root.stdin is not None
        assert pane_root.stdout.readline().strip() == "ready"

        spawn = bridge.pty_spawn_pane("pane_1", "claude", start_hub=True, project_uuid=proj)
        assert spawn["success"] is True
        hub_url = spawn["hub_url"]
        assert isinstance(hub_url, str) and hub_url.endswith("/mcp")
        base_url = hub_url[:-4]
        token = bridge.extensions_ui._get_pane_registry().get_pane("pane_1").session_token  # type: ignore[union-attr]

        if real_session_service is not None:
            real_session_service.record_spawn(
                project_uuid=proj,
                pane_slot="pane_1",
                cli_id="claude",
                cwd=str(tmp_path / "repo"),
                cli_session_id=None,
                cli_session_source="none",
            )

        pane_root.stdin.write(
            json.dumps(
                {
                    "url": base_url,
                    "token": token,
                    "client": str(HOOK_CLIENT),
                    "event": "claude.SessionStart",
                    "payload": json.dumps({"session_id": "sess-claude-xyz"}),
                }
            )
            + "\n"
        )
        pane_root.stdin.flush()
        reply = json.loads(pane_root.stdout.readline())
        assert reply["rc"] == 0

        assert bridge.core_kernel.agent_activity.snapshot(spawn["node_uuid"]).state == AgentState.IDLE

        if real_session_service is not None:
            plan = real_session_service.restorable(proj, max_panes=4)
            item = next(it for it in plan.items if it.pane_slot == "pane_1")
            assert item.cli_session_id == "sess-claude-xyz"
            assert item.resume_mode == "by_id"
        else:
            assert receiver is not None
            assert receiver.recorded == [
                {
                    "project_uuid": proj,
                    "pane_slot": "pane_1",
                    "cli_session_id": "sess-claude-xyz",
                    "source": "hook",
                }
            ]
    finally:
        try:
            if pane_root.stdin:
                pane_root.stdin.write("\n")
                pane_root.stdin.flush()
            pane_root.wait(timeout=5)
        except Exception:
            pane_root.kill()
        bridge._close_terminal_matrix()
        bridge.core_kernel.close()
