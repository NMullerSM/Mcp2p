"""Pruebas unitarias de `src/adapters/terminal/hook_configs.py`."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from src.adapters.terminal.hook_configs import (
    AGY_HOOK_EVENTS,
    CLAUDE_SIMPLE_EVENTS,
    agy_settings,
    claude_settings,
    codex_notify_args,
    command_line,
    hook_command_argv,
    opencode_config_with_plugin,
    opencode_plugin_source,
)


def test_hook_command_argv_frozen_and_source(tmp_path: Path) -> None:
    frozen_argv = hook_command_argv(
        "claude.Stop",
        frozen=True,
        executable="C:\\Program Files\\Mcp2p\\Mcp2p.exe",
        repo_root=None,
    )
    assert frozen_argv == ["C:\\Program Files\\Mcp2p\\Mcp2p.exe", "--hook-post", "claude.Stop"]

    src_argv = hook_command_argv(
        "codex.notify",
        frozen=False,
        executable="/usr/bin/python3",
        repo_root=tmp_path,
    )
    assert src_argv == ["/usr/bin/python3", str(tmp_path / "src" / "app" / "hook_client.py"), "codex.notify"]

    with pytest.raises(ValueError):
        hook_command_argv("bad event!", frozen=True, executable="Mcp2p", repo_root=None)


def test_command_line_quotes_paths_with_spaces_on_windows() -> None:
    argv = [r"C:\Users\Ada Lovelace\AppData\Local\Mcp2p\Mcp2p.exe", "--hook-post", "claude.Stop"]
    win_cmd = command_line(argv, "win32")
    assert win_cmd == r'"C:\Users\Ada Lovelace\AppData\Local\Mcp2p\Mcp2p.exe" --hook-post claude.Stop'

    posix_cmd = command_line(["/opt/my app/python3", "/my repo/src/app/hook_client.py", "claude.Stop"], "linux")
    assert posix_cmd == "'/opt/my app/python3' '/my repo/src/app/hook_client.py' claude.Stop"


def test_claude_settings_structure_and_matchers() -> None:
    seen_events = []

    def fake_hook_argv(event: str):
        seen_events.append(event)
        return ["/opt/mcp2p/Mcp2p", "--hook-post", event]

    cfg = claude_settings(fake_hook_argv, "linux")
    hooks = cfg["hooks"]
    assert set(hooks) == set(CLAUDE_SIMPLE_EVENTS) | {"Notification"}

    for ev in CLAUDE_SIMPLE_EVENTS:
        entries = hooks[ev]
        assert len(entries) == 1
        cmd_hook = entries[0]["hooks"][0]
        assert cmd_hook["type"] == "command"
        assert cmd_hook["timeout"] == 5
        assert f"claude.{ev}" in cmd_hook["command"]

    notif = hooks["Notification"]
    assert [item["matcher"] for item in notif] == ["permission_prompt", "idle_prompt"]
    assert "claude.Notification.permission_prompt" in notif[0]["hooks"][0]["command"]
    assert "claude.Notification.idle_prompt" in notif[1]["hooks"][0]["command"]
    assert len(seen_events) == len(CLAUDE_SIMPLE_EVENTS) + 2


def test_codex_notify_uses_toml_literal_strings() -> None:
    raw_argv = [r"C:\Users\Bob Smith\Mcp2p.exe", "--hook-post", "codex.notify"]
    args = codex_notify_args(lambda ev: raw_argv if ev == "codex.notify" else [])
    assert len(args) == 2
    assert args[0] == "-c"
    assert args[1].startswith("notify=['")
    assert "'" in args[1]
    parsed = tomllib.loads(args[1])
    assert parsed == {"notify": raw_argv}


def test_opencode_config_keeps_mcp_and_adds_plugin(tmp_path: Path) -> None:
    mcp_cfg = {
        "$schema": "https://opencode.ai/config.json",
        "mcp": {
            "mcp2p": {
                "type": "remote",
                "url": "http://127.0.0.1:8765/mcp",
                "headers": {"Authorization": "Bearer secret"},
                "enabled": True,
            }
        },
    }
    plugin_path = tmp_path / "mcp2p_plugin.js"
    merged = opencode_config_with_plugin(mcp_cfg, plugin_path)
    assert merged["mcp"] == mcp_cfg["mcp"]
    assert merged["$schema"] == "https://opencode.ai/config.json"
    assert merged["plugin"] == [str(plugin_path)]
    assert "plugin" not in mcp_cfg  # no muta la entrada

    # Idempotente si se vuelve a combinar con el mismo plugin.
    merged2 = opencode_config_with_plugin(merged, plugin_path)
    assert merged2["plugin"] == [str(plugin_path)]


def test_agy_settings_and_opencode_plugin_source() -> None:
    cfg = agy_settings(lambda ev: ["Mcp2p", "--hook-post", ev], "linux")
    assert set(cfg["hooks"]) == set(AGY_HOOK_EVENTS)
    assert "agy.Stop" in cfg["hooks"]["Stop"][0]["hooks"][0]["command"]

    src_js = opencode_plugin_source()
    assert "session.status" in src_js
    assert "session.idle" in src_js
    assert "permission." in src_js
    assert "/hooks/v1/opencode.event" in src_js
    assert "MCP2P_HUB_URL" in src_js
    assert "MCP2P_SESSION_TOKEN" in src_js
