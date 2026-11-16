"""Pruebas para el registro automatico idempotente de `mcp2p` para Antigravity (`agy`)."""

from __future__ import annotations

import json
import stat
from pathlib import Path

from src.adapters.terminal.cli_catalog import load_repo_catalog
from src.adapters.terminal.mcp_injection import PaneMcpConfigStore, ensure_agy_mcp_registered


def test_ensure_agy_mcp_registered_creates_static_stdio_entry(tmp_path: Path) -> None:
    cfg_path = tmp_path / ".gemini" / "config" / "mcp_config.json"

    res = ensure_agy_mcp_registered(
        cfg_path,
        python_executable="/custom/venv/bin/python",
        repo_root=tmp_path,
    )

    assert res == {"registered": True, "already_present": False, "path": str(cfg_path)}
    assert cfg_path.is_file()
    assert stat.S_IMODE(cfg_path.stat().st_mode) == 0o600

    raw = cfg_path.read_text(encoding="utf-8")
    assert "MCP2P_SESSION_TOKEN" not in raw
    assert "127.0.0.1:" not in raw

    data = json.loads(raw)
    entry = data["mcpServers"]["mcp2p"]
    assert entry["command"] == "/custom/venv/bin/python"
    assert entry["args"] == ["-m", "src.mcp.server"]
    assert entry["cwd"] == str(tmp_path)
    assert entry["env"] == {"PYTHONPATH": str(tmp_path)}
    assert "MCP2P_SESSION_TOKEN" not in entry.get("env", {})


def test_ensure_agy_mcp_registered_preserves_existing_servers_and_is_idempotent(tmp_path: Path) -> None:
    cfg_path = tmp_path / "mcp_config.json"
    initial = {
        "theme": "dark",
        "mcpServers": {
            "github": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"]},
        },
    }
    cfg_path.write_text(json.dumps(initial), encoding="utf-8")

    res1 = ensure_agy_mcp_registered(cfg_path, python_executable="/usr/bin/python3", repo_root=tmp_path)
    assert res1["registered"] is True
    assert res1["already_present"] is False

    data1 = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert data1["theme"] == "dark"
    assert data1["mcpServers"]["github"] == initial["mcpServers"]["github"]
    assert data1["mcpServers"]["mcp2p"]["command"] == "/usr/bin/python3"

    # Modificar manualmente mcp2p para verificar que no se sobreescribe en la segunda llamada
    data1["mcpServers"]["mcp2p"]["custom_flag"] = True
    cfg_path.write_text(json.dumps(data1), encoding="utf-8")

    res2 = ensure_agy_mcp_registered(cfg_path, python_executable="/other/python", repo_root=tmp_path)
    assert res2 == {"registered": True, "already_present": True, "path": str(cfg_path)}
    data2 = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert data2["mcpServers"]["mcp2p"]["command"] == "/usr/bin/python3"
    assert data2["mcpServers"]["mcp2p"]["custom_flag"] is True


def test_ensure_agy_mcp_registered_does_not_overwrite_invalid_json(tmp_path: Path) -> None:
    cfg_path = tmp_path / "mcp_config.json"
    cfg_path.write_text("{not valid json", encoding="utf-8")

    res = ensure_agy_mcp_registered(cfg_path)
    assert res["registered"] is False
    assert res["reason"] == "invalid_existing_config"
    assert cfg_path.read_text(encoding="utf-8") == "{not valid json"


def test_cli_catalog_and_pane_store_support_antigravity_stdio_env_proxy(tmp_path: Path, monkeypatch) -> None:
    catalog = load_repo_catalog()
    spec = catalog.get("antigravity")
    assert spec is not None
    assert spec.mcp["method"] == "stdio_env_proxy"
    assert spec.mcp["verified"] is True

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    store = PaneMcpConfigStore(tmp_path / "pane-store")
    inj = store.prepare(
        spec,
        session_id="pane_1-test",
        hub_url="http://127.0.0.1:43210/mcp",
        token="TOK-SECRET",
    )

    assert inj.status == "injected"
    assert inj.method == "stdio_env_proxy"
    assert inj.verified is True
    assert inj.reason == "stdio_hub_proxy"
    assert inj.argv == []
    assert inj.env == {}
    assert inj.session_dir is None
    assert not store.base_dir.exists()

    agy_cfg = tmp_path / "home" / ".gemini" / "config" / "mcp_config.json"
    assert agy_cfg.is_file()
    assert "TOK-SECRET" not in agy_cfg.read_text(encoding="utf-8")


def test_pane_store_tolerates_unwritable_home_for_stdio_env_proxy(tmp_path: Path, monkeypatch) -> None:
    spec = load_repo_catalog().get("antigravity")
    assert spec is not None

    def _raise(*args, **kwargs):
        raise PermissionError("read-only home")

    monkeypatch.setattr("src.adapters.terminal.mcp_injection.ensure_agy_mcp_registered", _raise)
    store = PaneMcpConfigStore(tmp_path / "pane-store")
    inj = store.prepare(
        spec,
        session_id="pane_1-ro",
        hub_url="http://127.0.0.1:43210/mcp",
        token="TOK-SECRET",
    )
    assert inj.status == "injected"
    assert inj.method == "stdio_env_proxy"
    assert inj.verified is True
    assert inj.reason == "stdio_hub_proxy"
