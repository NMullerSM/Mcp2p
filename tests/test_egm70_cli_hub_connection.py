"""EGM-70 / EGM-75: los CLI de los paneles se conectan al Hub embebido con el token de SU panel.

Un CLI falso (script) lee la configuracion MCP que el host le inyecta -segun el campo `mcp` del catalogo-
y hace llamadas MCP reales contra el `EmbeddedMcpHub`: autenticada funciona, sin token da 401 y con el
`node_uuid` de otro panel no puede suplantar (EGM-22/EGM-66).
"""

from __future__ import annotations

import json
import os
import stat
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

import psutil
import pytest

from conftest import make_test_db
from src.adapters.terminal import CliCatalogService, PosixPtyHostConnector
from src.adapters.terminal.cli_catalog import CatalogValidationError, load_repo_catalog
from src.adapters.terminal.mcp_injection import McpInjectionError, PaneMcpConfigStore
from src.app.api_bridge import ApiBridge

posix_only = pytest.mark.skipif(not PosixPtyHostConnector.is_available(), reason="Solo POSIX")

FAKE_CLI = '''#!{python}
import asyncio, json, os, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = Path(__file__).name
expect = json.loads((HERE / "expect.json").read_text())
out = {{"cli": NAME, "argv": sys.argv[1:], "cmdline": open("/proc/self/cmdline").read().replace("\\0", " ")}}

url = token = None
cfg_path = None
if "--mcp-config" in sys.argv:
    cfg_path = sys.argv[sys.argv.index("--mcp-config") + 1]
    srv = json.load(open(cfg_path))["mcpServers"]["mcp2p"]
    url, token, out["mode"] = srv["url"], srv["headers"]["Authorization"].split(" ", 1)[1], "claude_flag"
elif os.environ.get("OPENCODE_CONFIG"):
    cfg_path = os.environ["OPENCODE_CONFIG"]
    srv = json.load(open(cfg_path))["mcp"]["mcp2p"]
    url, token, out["mode"] = srv["url"], srv["headers"]["Authorization"].split(" ", 1)[1], "opencode_env"
elif os.environ.get("GEMINI_CLI_SYSTEM_SETTINGS_PATH"):
    cfg_path = os.environ["GEMINI_CLI_SYSTEM_SETTINGS_PATH"]
    srv = json.load(open(cfg_path))["mcpServers"]["mcp2p"]
    url, token, out["mode"] = srv["httpUrl"], srv["headers"]["Authorization"].split(" ", 1)[1], "gemini_env"
elif "-c" in sys.argv:
    kv = dict(a.split("=", 1) for a in sys.argv if a.startswith("mcp_servers.mcp2p."))
    url = json.loads(kv["mcp_servers.mcp2p.url"])
    token = os.environ[json.loads(kv["mcp_servers.mcp2p.bearer_token_env_var"])]
    out["mode"] = "codex_override"
if cfg_path:
    st = os.stat(cfg_path)
    out["cfg_mode"] = oct(st.st_mode & 0o777)
    out["cfg_dir_mode"] = oct(os.stat(os.path.dirname(cfg_path)).st_mode & 0o777)
    out["cfg_path"] = cfg_path


async def main():
    import httpx2
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    init = {{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {{"protocolVersion": "2025-06-18",
            "capabilities": {{}}, "clientInfo": {{"name": "fake", "version": "0"}}}}}}
    accept = {{"Accept": "application/json, text/event-stream"}}
    async with httpx2.AsyncClient() as http:
        out["unauth_status"] = (await http.post(url, headers=accept, json=init)).status_code
        out["badtoken_status"] = (await http.post(url, headers={{**accept, "Authorization": "Bearer nope"}}, json=init)).status_code
    async with httpx2.AsyncClient(headers={{"Authorization": "Bearer " + token}}) as http_client:
        async with streamable_http_client(url, http_client=http_client) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                res = await session.call_tool("list_projects", {{}})
                out["authed_ok"] = "project" in res.content[0].text.lower()
                spoof = await session.call_tool("claim_kanban_task", {{"project_uuid": expect["project_uuid"],
                    "card_id": "NOPE", "node_uuid": expect["other_node"]}})
                out["spoof_code"] = json.loads(spoof.content[0].text).get("code")


if url:
    try:
        asyncio.run(main())
    except Exception as exc:  # noqa
        out["error"] = repr(exc)
(HERE / ("result-" + NAME + ".json")).write_text(json.dumps(out))
print("FAKE-CLI-DONE", flush=True)
'''


def _install_fake(home: Path, name: str) -> Path:
    path = home / ".local" / "bin" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(FAKE_CLI.format(python=sys.executable))
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture
def lab(tmp_path: Path):
    home = tmp_path / "home"
    b = ApiBridge(db_manager=make_test_db())
    primary = b.core_kernel.terminal_connector.primary
    primary.catalog_service = CliCatalogService(
        b.core_kernel.extension_repo,
        user_catalog_path=tmp_path / "sin_usuario.json",
        which=lambda name: None,
        platform="linux",
        home=str(home),
        environ={"SHELL": "/bin/sh"},
    )
    primary.min_available_mb = 0
    store = PaneMcpConfigStore(tmp_path / "mcp-store")
    primary._mcp_store = store
    registry = b.extensions_ui._get_pane_registry()
    other = registry.register_pane(4)  # panel ajeno: su node_uuid es el que se intenta suplantar
    project_uuid = b.core_kernel.db.list_projects()[0]["project_uuid"]
    (home / ".local" / "bin").mkdir(parents=True)
    (home / ".local" / "bin" / "expect.json").write_text(
        json.dumps({"project_uuid": project_uuid, "other_node": other["node_uuid"]})
    )
    try:
        yield {"bridge": b, "home": home, "store": store, "registry": registry, "tmp": tmp_path, "primary": primary}
    finally:
        b._close_terminal_matrix()
        b.core_kernel.close()


def _wait_result(home: Path, name: str, timeout: float = 40.0) -> Dict[str, Any]:
    target = home / ".local" / "bin" / f"result-{name}.json"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if target.exists():
            try:
                return json.loads(target.read_text())
            except ValueError:
                pass
        time.sleep(0.1)
    raise AssertionError(f"el CLI falso {name} no dejo resultado")


def _all_text(bridge: ApiBridge, pane: Any) -> str:
    time.sleep(0.3)
    return json.dumps(bridge.pty_poll_output(pane, 0))


@posix_only
@pytest.mark.parametrize(
    "cli,mode,pane",
    [("claude", "claude_flag", 1), ("opencode", "opencode_env", 2), ("gemini", "gemini_env", 3)],
)
def test_cli_connects_to_embedded_hub_with_its_panel_token(lab, cli: str, mode: str, pane: int) -> None:
    bridge, home, store, registry = lab["bridge"], lab["home"], lab["store"], lab["registry"]
    _install_fake(home, cli)
    res = bridge.pty_spawn_pane(pane, cli, "implementer", None, 100, 28, True, str(lab["tmp"]))
    assert res["success"] is True, res
    token = registry.get_pane(pane).session_token
    inj = res["mcp_injection"]
    assert inj["status"] == "injected" and "path" not in json.dumps(inj)

    out = _wait_result(home, cli)
    assert out["mode"] == mode and "error" not in out, out
    # llamada real autenticada, rechazo sin token y con token ajeno, sin suplantacion de otro panel
    assert out["authed_ok"] is True
    assert out["unauth_status"] == 401 and out["badtoken_status"] == 401
    assert out["spoof_code"] == "NODE_UUID_SPOOFING_FORBIDDEN"
    # permisos: archivo 0600 dentro de un directorio 0700 propio
    assert out["cfg_mode"] == "0o600" and out["cfg_dir_mode"] == "0o700"
    assert str(store.base_dir) in out["cfg_path"]
    # el token nunca va en argv ni se filtra al puente
    assert token not in out["cmdline"] and token not in json.dumps(out["argv"])
    blob = json.dumps(res) + _all_text(bridge, pane) + json.dumps(bridge.pty_get_matrix_status())
    assert token not in blob
    assert "Bearer" not in blob

    # el proceso termino solo: su configuracion (con el token) se borra del disco
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and any(store.base_dir.iterdir()):
        time.sleep(0.1)
    assert list(store.base_dir.iterdir()) == []


@posix_only
def test_codex_receives_only_the_env_var_name_in_argv(lab) -> None:
    bridge, home, registry = lab["bridge"], lab["home"], lab["registry"]
    _install_fake(home, "codex")
    res = bridge.pty_spawn_pane(1, "codex", "implementer", None, 100, 28, True, str(lab["tmp"]))
    assert res["success"] is True and res["mcp_injection"]["status"] == "injected"
    assert res["mcp_injection"]["verified"] is False  # solo documentacion, sin binario
    out = _wait_result(home, "codex")
    assert out["mode"] == "codex_override" and "error" not in out, out
    assert out["authed_ok"] is True and out["unauth_status"] == 401
    assert out["spoof_code"] == "NODE_UUID_SPOOFING_FORBIDDEN"
    assert registry.get_pane(1).session_token not in out["cmdline"]
    assert "MCP2P_SESSION_TOKEN" in out["cmdline"] and "mcp_servers.mcp2p.url" in out["cmdline"]


@posix_only
def test_agy_is_reported_unsupported_and_nothing_is_written(lab) -> None:
    bridge, home, store = lab["bridge"], lab["home"], lab["store"]
    _install_fake(home, "agy")
    res = bridge.pty_spawn_pane(1, "antigravity", "implementer", None, 100, 28, True, str(lab["tmp"]))
    assert res["success"] is True
    assert res["mcp_injection"]["status"] == "unsupported"
    assert res["mcp_injection"]["verified"] is True
    out = _wait_result(home, "agy")
    assert "mode" not in out and out["argv"] == []
    assert not store.base_dir.exists() or list(store.base_dir.iterdir()) == []


@posix_only
def test_config_is_deleted_when_the_panel_is_closed(lab) -> None:
    bridge, home, store = lab["bridge"], lab["home"], lab["store"]
    path = home / ".local" / "bin" / "claude"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nsleep 60\n")
    path.chmod(0o755)
    res = bridge.pty_spawn_pane(1, "claude", "implementer", None, 100, 28, True, str(lab["tmp"]))
    assert res["success"] is True and res["mcp_injection"]["status"] == "injected"
    dirs = list(store.base_dir.iterdir())
    assert len(dirs) == 1 and (dirs[0] / "mcp-config.json").is_file()
    assert bridge.pty_close_pane(1)["success"] is True
    assert list(store.base_dir.iterdir()) == []


@posix_only
def test_resume_uses_only_verified_catalog_argv(lab) -> None:
    bridge, home = lab["bridge"], lab["home"]
    _install_fake(home, "claude")
    _install_fake(home, "gemini")
    res = bridge.pty_spawn_pane(1, "claude", "implementer", None, 100, 28, True, str(lab["tmp"]), True)
    assert res["success"] is True
    out = _wait_result(home, "claude")
    assert out["argv"][0] == "--continue" and "--mcp-config" in out["argv"]
    # gemini: resume sin verificar -> se rechaza en vez de adivinar el flag
    bad = bridge.pty_spawn_pane(2, "gemini", "implementer", None, 100, 28, True, str(lab["tmp"]), True)
    assert bad["success"] is False and bad["code"] == "RESUME_NOT_AVAILABLE"
    assert bridge.pty_spawn_pane(2, "gemini", "implementer", None, 100, 28, True, str(lab["tmp"]), "si")["code"] == "INVALID_ARGUMENT"


@posix_only
def test_egm75_concurrent_spawns_keep_the_registered_token_of_the_live_process(lab, monkeypatch) -> None:
    import subprocess

    from src.adapters.terminal import pty_host

    bridge, home, registry = lab["bridge"], lab["home"], lab["registry"]
    _install_fake(home, "claude")
    real_popen = subprocess.Popen

    class _SlowPopen(real_popen):
        def __init__(self, *a, **k):
            time.sleep(0.15)
            super().__init__(*a, **k)

    monkeypatch.setattr(pty_host.subprocess, "Popen", _SlowPopen)
    results: List[Dict[str, Any]] = []
    barrier = threading.Barrier(4)

    def _go() -> None:
        barrier.wait()
        results.append(bridge.pty_spawn_pane(1, "claude", "implementer", None, 100, 28, True, str(lab["tmp"])))

    threads = [threading.Thread(target=_go) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert [r["success"] for r in results].count(True) == 1, [r.get("code") for r in results]
    # el token registrado es el del proceso vivo: su llamada autenticada funciona (no 401)
    out = _wait_result(home, "claude")
    assert out["authed_ok"] is True and "error" not in out, out
    assert out["unauth_status"] == 401


# ---------------------------------------------------------------------------
# Unidad: almacen de configuracion y esquema del catalogo
# ---------------------------------------------------------------------------


def test_store_writes_private_files_and_cleans_up(tmp_path: Path) -> None:
    spec = load_repo_catalog().get("claude")
    store = PaneMcpConfigStore(tmp_path / "base")
    inj = store.prepare(spec, session_id="pane_1-abc", hub_url="http://127.0.0.1:1234/mcp", token="TOK-SECRET")
    assert inj.status == "injected" and inj.argv[0] == "--mcp-config"
    cfg = Path(inj.argv[1])
    assert stat.S_IMODE(cfg.stat().st_mode) == 0o600 and stat.S_IMODE(cfg.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(store.base_dir.stat().st_mode) == 0o700
    assert json.loads(cfg.read_text())["mcpServers"]["mcp2p"]["headers"]["Authorization"] == "Bearer TOK-SECRET"
    assert "TOK-SECRET" not in json.dumps(inj.argv) and "TOK-SECRET" not in json.dumps(inj.public())
    assert store.cleanup("pane_1-abc") is True and not cfg.parent.exists()
    assert store.cleanup("pane_1-abc") is False


def test_store_rejects_symlinked_base_and_bad_inputs(tmp_path: Path) -> None:
    spec = load_repo_catalog().get("claude")
    target = tmp_path / "ajeno"
    target.mkdir()
    link = tmp_path / "base"
    link.symlink_to(target)
    with pytest.raises(McpInjectionError):
        PaneMcpConfigStore(link).prepare(spec, session_id="p-1", hub_url="http://127.0.0.1:1/mcp", token="t")
    assert list(target.iterdir()) == []
    store = PaneMcpConfigStore(tmp_path / "ok")
    with pytest.raises(McpInjectionError):
        store.prepare(spec, session_id="../x", hub_url="http://127.0.0.1:1/mcp", token="t")
    with pytest.raises(McpInjectionError):
        store.prepare(spec, session_id="p-2", hub_url='http://x/"; rm -rf /', token="t")
    assert store.prepare(spec, session_id="p-3", hub_url=None, token="t").status == "skipped"
    assert store.prepare(None, session_id="p-4", hub_url="http://127.0.0.1:1/mcp", token="t").status == "skipped"


def test_sweep_orphans_removes_only_dead_process_dirs(tmp_path: Path) -> None:
    base = tmp_path / "base"
    base.mkdir(mode=0o700)
    dead = psutil.Process(os.getpid()).pid + 4_000_000
    while psutil.pid_exists(dead):
        dead += 1
    (base / f"{dead}-pane_1-aaa").mkdir()
    (base / f"{dead}-pane_1-aaa" / "mcp-config.json").write_text("{}")
    live = base / f"{os.getppid()}-pane_2-bbb"
    live.mkdir()
    own = base / f"{os.getpid()}-pane_3-ccc"
    own.mkdir()
    (base / "ajeno.txt").write_text("x")
    link_target = tmp_path / "victima"
    link_target.mkdir()
    (base / f"{dead + 1}-link").symlink_to(link_target)
    store = PaneMcpConfigStore(base)
    assert store.sweep_orphans() == 1
    assert not (base / f"{dead}-pane_1-aaa").exists()
    assert live.exists() and own.exists() and (base / "ajeno.txt").exists() and link_target.exists()


def test_catalog_schema_blocks_tokens_in_argv_and_unsafe_env(tmp_path: Path) -> None:
    from src.adapters.terminal.cli_catalog import _parse_entry

    base = {
        "id": "x", "display_name": "X", "binary": "x", "kind": "agent_cli",
        "mcp": {"method": "flag", "format": "claude_mcp_json", "argv": ["--c", "{mcp_config_file}"], "verified": True},
    }
    assert _parse_entry(base, source="repo").mcp["method"] == "flag"
    for mcp in (
        {"method": "flag", "format": "none", "argv": ["--t", "{token}"]},
        {"method": "flag", "format": "none", "argv": ["--t", "{session_token}"]},
        {"method": "flag", "format": "claude_mcp_json", "argv": ["--c", "x"]},
        {"method": "env", "format": "opencode_json", "env": {"PATH": "{mcp_config_file}"}},
        {"method": "env", "format": "opencode_json", "env": {"MCP2P_SESSION_TOKEN": "{mcp_config_file}"}},
        {"method": "env", "format": "opencode_json", "env": {"LD_PRELOAD": "{mcp_config_file}"}},
        {"method": "env", "format": "otro", "env": {"X_CFG": "{mcp_config_file}"}},
        {"method": "unsupported", "format": "none", "argv": ["--x"]},
    ):
        with pytest.raises(CatalogValidationError):
            _parse_entry({**base, "mcp": mcp}, source="repo")


def test_user_catalog_clis_are_never_injected(tmp_path: Path) -> None:
    from src.adapters.terminal.cli_catalog import _parse_entry

    spec = _parse_entry(
        {"id": "mio", "display_name": "Mio", "binary": "mio", "kind": "agent_cli",
         "mcp": {"method": "flag", "format": "claude_mcp_json", "argv": ["--c", "{mcp_config_file}"], "verified": True}},
        source="user",
    )
    inj = PaneMcpConfigStore(tmp_path / "b").prepare(spec, session_id="p-1", hub_url="http://127.0.0.1:1/mcp", token="t")
    assert inj.status == "not_applicable" and inj.argv == [] and not (tmp_path / "b").exists()
