"""Registro global de `mcp2p` para Antigravity (`agy`): atomico, con copia de seguridad, sin enlaces y con consentimiento."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from src.adapters.terminal import mcp_injection
from src.adapters.terminal.cli_catalog import CatalogValidationError, load_repo_catalog
from src.adapters.terminal.mcp_injection import (
    AGY_MANAGED_KEY,
    PaneMcpConfigStore,
    agy_registration_state,
    build_agy_entry,
    ensure_agy_mcp_registered,
    unregister_agy_mcp,
)

POSIX = sys.platform != "win32"


def _cfg(tmp_path: Path) -> Path:
    return tmp_path / ".gemini" / "config" / "mcp_config.json"


def _register(cfg_path: Path, tmp_path: Path, **kw):
    return ensure_agy_mcp_registered(cfg_path, python_executable="/custom/venv/bin/python", repo_root=tmp_path, **kw)


def _leftovers(cfg_path: Path):
    return [p.name for p in cfg_path.parent.iterdir() if p.name.endswith(".tmp")]


# -- creacion y forma de la entrada --------------------------------------------------------------------------------


def test_creates_hub_proxy_only_entry_from_sources_with_managed_marker(tmp_path: Path) -> None:
    cfg_path = _cfg(tmp_path)
    res = _register(cfg_path, tmp_path, frozen=False)

    assert res == {"registered": True, "already_present": False, "managed": True, "path": str(cfg_path)}
    if POSIX:  # Windows no tiene modos POSIX
        assert stat.S_IMODE(cfg_path.stat().st_mode) == 0o600
    raw = cfg_path.read_text(encoding="utf-8")
    assert "MCP2P_SESSION_TOKEN" not in raw and "127.0.0.1:" not in raw

    entry = json.loads(raw)["mcpServers"]["mcp2p"]
    assert entry["command"] == "/custom/venv/bin/python"
    assert entry["args"] == ["-m", "src.mcp.server", "--hub-proxy-only"]
    assert entry["cwd"] == str(tmp_path)
    assert entry["env"] == {"PYTHONPATH": str(tmp_path)}
    marker = entry[AGY_MANAGED_KEY]
    assert marker["installed_by"] == "mcp2p" and marker["version"]
    assert not _leftovers(cfg_path)


def test_frozen_entry_uses_the_executable_with_mcp_server_and_no_cwd(tmp_path: Path) -> None:
    entry = build_agy_entry(frozen=True, executable="/opt/mcp2p/mcp2p", repo_root=tmp_path)
    assert entry["command"] == "/opt/mcp2p/mcp2p"
    assert entry["args"] == ["--mcp-server", "--hub-proxy-only"]
    assert "cwd" not in entry and "env" not in entry
    assert entry[AGY_MANAGED_KEY]["installed_by"] == "mcp2p"


def test_preserves_other_servers_and_is_idempotent(tmp_path: Path) -> None:
    cfg_path = _cfg(tmp_path)
    cfg_path.parent.mkdir(parents=True)
    initial = {"theme": "dark", "mcpServers": {"github": {"command": "npx", "args": ["-y", "server-github"], "env": {"TOKEN": "s3cr3t"}}}}
    cfg_path.write_text(json.dumps(initial, indent=4), encoding="utf-8")

    res1 = _register(cfg_path, tmp_path, frozen=False)
    assert res1["registered"] is True and res1["already_present"] is False
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert data["theme"] == "dark"
    assert data["mcpServers"]["github"] == initial["mcpServers"]["github"]
    assert cfg_path.read_text(encoding="utf-8").startswith('{\n    "theme"')  # conserva la sangria del usuario

    snapshot = cfg_path.read_bytes()
    res2 = _register(cfg_path, tmp_path, frozen=False)
    assert res2 == {"registered": True, "already_present": True, "managed": True, "path": str(cfg_path)}
    assert cfg_path.read_bytes() == snapshot


def test_invalid_json_is_never_overwritten(tmp_path: Path) -> None:
    cfg_path = tmp_path / "mcp_config.json"
    cfg_path.write_text("{not valid json", encoding="utf-8")
    res = ensure_agy_mcp_registered(cfg_path)
    assert res["registered"] is False and res["reason"] == "invalid_existing_config"
    assert cfg_path.read_text(encoding="utf-8") == "{not valid json"
    assert not (tmp_path / "mcp_config.json.mcp2p.bak").exists()


# -- entradas propias y manuales -----------------------------------------------------------------------------------


def test_managed_entry_with_dead_or_old_path_is_updated(tmp_path: Path) -> None:
    cfg_path = _cfg(tmp_path)
    _register(cfg_path, tmp_path / "old_repo_that_moved", frozen=False)  # repo_root que luego "se mueve"
    before = json.loads(cfg_path.read_text(encoding="utf-8"))["mcpServers"]["mcp2p"]

    new_root = tmp_path / "new_repo"
    new_root.mkdir()
    res = ensure_agy_mcp_registered(cfg_path, python_executable="/new/python", repo_root=new_root, frozen=False)
    assert res["registered"] is True and res["updated"] is True and res["already_present"] is False
    entry = json.loads(cfg_path.read_text(encoding="utf-8"))["mcpServers"]["mcp2p"]
    assert entry["command"] == "/new/python" and entry["cwd"] == str(new_root)
    assert before["command"] != entry["command"]


def test_manual_entry_without_marker_is_respected_unless_explicitly_replaced(tmp_path: Path) -> None:
    cfg_path = _cfg(tmp_path)
    cfg_path.parent.mkdir(parents=True)
    manual = {"command": "/usr/bin/python3", "args": ["-m", "src.mcp.server"], "env": {"MCP2P_DB_PATH": "/home/u/real.db"}}
    cfg_path.write_text(json.dumps({"mcpServers": {"mcp2p": manual}}), encoding="utf-8")
    snapshot = cfg_path.read_bytes()

    res = _register(cfg_path, tmp_path, frozen=False)
    assert res["registered"] is True and res["already_present"] is True and res["managed"] is False
    assert res["hub_proxy_only"] is False  # la UI puede ofrecer migrarla
    assert cfg_path.read_bytes() == snapshot
    assert not cfg_path.with_name(cfg_path.name + ".mcp2p.bak").exists()
    assert agy_registration_state(cfg_path)["state"] == "manual"

    res = _register(cfg_path, tmp_path, frozen=False, replace_unmanaged=True)
    assert res["registered"] is True and res["managed"] is True
    entry = json.loads(cfg_path.read_text(encoding="utf-8"))["mcpServers"]["mcp2p"]
    assert "--hub-proxy-only" in entry["args"] and "MCP2P_DB_PATH" not in entry.get("env", {})
    assert cfg_path.with_name(cfg_path.name + ".mcp2p.bak").read_bytes() == snapshot  # la manual queda en la copia


# -- copia de seguridad ---------------------------------------------------------------------------------------------


def test_backup_is_made_once_with_the_original_bytes(tmp_path: Path) -> None:
    cfg_path = _cfg(tmp_path)
    cfg_path.parent.mkdir(parents=True)
    original = b'{\n\t"mcpServers": {"github": {"command": "npx"}}\n}\n'
    cfg_path.write_bytes(original)
    backup = cfg_path.with_name("mcp_config.json.mcp2p.bak")

    _register(cfg_path, tmp_path, frozen=False)
    assert backup.read_bytes() == original
    if POSIX:
        assert stat.S_IMODE(backup.stat().st_mode) == 0o600

    # Una segunda modificacion (actualizacion de entrada propia) NO pisa la copia original.
    ensure_agy_mcp_registered(cfg_path, python_executable="/other/python", repo_root=tmp_path, frozen=False)
    assert backup.read_bytes() == original


def test_no_backup_when_file_did_not_exist(tmp_path: Path) -> None:
    cfg_path = _cfg(tmp_path)
    _register(cfg_path, tmp_path, frozen=False)
    assert not cfg_path.with_name("mcp_config.json.mcp2p.bak").exists()


# -- atomicidad y enlaces -------------------------------------------------------------------------------------------


def test_crash_during_replace_leaves_original_intact_and_no_temp_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_path = _cfg(tmp_path)
    cfg_path.parent.mkdir(parents=True)
    original = json.dumps({"mcpServers": {"github": {"command": "npx"}}}, indent=2).encode()
    cfg_path.write_bytes(original)

    def _boom(src, dst):
        raise OSError("disco lleno / proceso muerto")

    monkeypatch.setattr(mcp_injection.os, "replace", _boom)
    res = _register(cfg_path, tmp_path, frozen=False)
    assert res["registered"] is False and res["reason"] == "write_failed"
    assert cfg_path.read_bytes() == original  # nunca truncado
    assert not _leftovers(cfg_path)


def test_crash_while_writing_the_temp_file_leaves_original_intact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_path = _cfg(tmp_path)
    cfg_path.parent.mkdir(parents=True)
    original = b'{"mcpServers": {}}\n'
    cfg_path.write_bytes(original)

    def _boom(fd):
        raise OSError("fsync fallo")

    monkeypatch.setattr(mcp_injection.os, "fsync", _boom)
    res = _register(cfg_path, tmp_path, frozen=False)
    assert res["registered"] is False and res["reason"] == "write_failed"
    assert cfg_path.read_bytes() == original
    assert not _leftovers(cfg_path)


def _symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("sin privilegio para crear enlaces simbolicos (Windows sin modo desarrollador)")


def test_symlinked_config_file_is_rejected_and_target_untouched(tmp_path: Path) -> None:
    real = tmp_path / "dotfiles" / "mcp_config.json"
    real.parent.mkdir()
    real.write_text('{"mcpServers": {}}', encoding="utf-8")
    cfg_path = _cfg(tmp_path)
    cfg_path.parent.mkdir(parents=True)
    _symlink_or_skip(cfg_path, real)

    res = _register(cfg_path, tmp_path, frozen=False)
    assert res["registered"] is False and res["reason"] == "symlink_rejected"
    assert real.read_text(encoding="utf-8") == '{"mcpServers": {}}'
    assert cfg_path.is_symlink()
    assert unregister_agy_mcp(cfg_path)["removed"] is False
    assert agy_registration_state(cfg_path)["state"] == "error"


def test_dangling_symlink_is_rejected_too(tmp_path: Path) -> None:
    cfg_path = _cfg(tmp_path)
    cfg_path.parent.mkdir(parents=True)
    _symlink_or_skip(cfg_path, tmp_path / "no_existe.json")
    res = _register(cfg_path, tmp_path, frozen=False)
    assert res["registered"] is False and res["reason"] == "symlink_rejected"
    assert not (tmp_path / "no_existe.json").exists()


# -- desregistro -----------------------------------------------------------------------------------------------------


def test_unregister_removes_only_managed_entry(tmp_path: Path) -> None:
    cfg_path = _cfg(tmp_path)
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text(json.dumps({"mcpServers": {"github": {"command": "npx"}}}), encoding="utf-8")
    _register(cfg_path, tmp_path, frozen=False)

    res = unregister_agy_mcp(cfg_path)
    assert res == {"removed": True, "path": str(cfg_path)}
    assert json.loads(cfg_path.read_text(encoding="utf-8")) == {"mcpServers": {"github": {"command": "npx"}}}
    assert unregister_agy_mcp(cfg_path)["reason"] == "not_registered"
    assert agy_registration_state(cfg_path)["state"] == "not_registered"


def test_unregister_respects_entries_without_marker(tmp_path: Path) -> None:
    cfg_path = _cfg(tmp_path)
    cfg_path.parent.mkdir(parents=True)
    manual = {"command": "python", "args": ["-m", "src.mcp.server"], "x-mcp2p-managed": {"installed_by": "someone-else"}}
    cfg_path.write_text(json.dumps({"mcpServers": {"mcp2p": manual}}), encoding="utf-8")
    snapshot = cfg_path.read_bytes()
    assert unregister_agy_mcp(cfg_path) == {"removed": False, "reason": "not_managed", "path": str(cfg_path)}
    assert cfg_path.read_bytes() == snapshot

    cfg_path.write_text(json.dumps({"mcpServers": {"mcp2p": {"command": "python"}}}), encoding="utf-8")
    assert unregister_agy_mcp(cfg_path)["reason"] == "not_managed"
    assert unregister_agy_mcp(tmp_path / "nope.json")["reason"] == "not_registered"


# -- catalogo y almacen del panel (consentimiento) --------------------------------------------------------------------


def test_catalog_splits_verification_between_sources_and_frozen_binary() -> None:
    spec = load_repo_catalog().get("antigravity")
    assert spec is not None
    mcp = spec.mcp
    assert mcp["method"] == "stdio_env_proxy"
    assert mcp["verified_dev"] is True and mcp["verified_frozen"] is False
    assert mcp["verified_with_cli_version"] == "1.5.9" and mcp["verified_on"] == "2026-11-16"
    assert spec.mcp_verified(frozen=False) is True
    assert spec.mcp_verified(frozen=True) is False  # el binario empaquetado no se anuncia como verificado


def test_catalog_schema_rejects_ambiguous_or_undated_verification(tmp_path: Path) -> None:
    from src.adapters.terminal.cli_catalog import load_catalog

    base = json.loads((Path(mcp_injection.__file__).parent / "cli_catalog.json").read_text(encoding="utf-8"))

    def _with(mcp_patch):
        data = json.loads(json.dumps(base))
        entry = next(e for e in data["clis"] if e["id"] == "antigravity")
        entry["mcp"] = {**entry["mcp"], **mcp_patch}
        path = tmp_path / "catalog.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    with pytest.raises(CatalogValidationError):
        load_catalog(repo_path=_with({"verified": True}))  # verified junto a verified_dev
    with pytest.raises(CatalogValidationError):
        load_catalog(repo_path=_with({"verified_on": "ayer"}))
    with pytest.raises(CatalogValidationError):
        load_catalog(repo_path=_with({"verified_frozen": "si"}))


def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Path.home() usa USERPROFILE en Windows
    return home


def test_pane_store_without_consent_writes_nothing_outside_the_panel_profile(tmp_path: Path, monkeypatch) -> None:
    home = _isolate_home(tmp_path, monkeypatch)
    spec = load_repo_catalog().get("antigravity")
    store = PaneMcpConfigStore(tmp_path / "pane-store")
    inj = store.prepare(spec, session_id="pane_1-test", hub_url="http://127.0.0.1:43210/mcp", token="TOK-SECRET")

    assert inj.status == "needs_consent"
    assert inj.method == "stdio_env_proxy" and inj.reason == "needs_consent"
    assert inj.argv == [] and inj.env == {} and inj.session_dir is None
    assert not home.exists()  # ni ~/.gemini ni nada
    assert inj.public()["status"] == "needs_consent"


def test_pane_store_with_consent_registers_without_leaking_the_token(tmp_path: Path, monkeypatch) -> None:
    home = _isolate_home(tmp_path, monkeypatch)
    spec = load_repo_catalog().get("antigravity")
    store = PaneMcpConfigStore(tmp_path / "pane-store")
    inj = store.prepare(
        spec, session_id="pane_1-test", hub_url="http://127.0.0.1:43210/mcp", token="TOK-SECRET",
        global_registration_consent=True,
    )
    assert inj.status == "injected" and inj.method == "stdio_env_proxy" and inj.reason == "stdio_hub_proxy"
    assert inj.verified is spec.mcp_verified()
    assert inj.argv == [] and inj.env == {} and inj.session_dir is None
    assert not store.base_dir.exists()

    agy_cfg = home / ".gemini" / "config" / "mcp_config.json"
    assert agy_cfg.is_file()
    text = agy_cfg.read_text(encoding="utf-8")
    assert "TOK-SECRET" not in text and "--hub-proxy-only" in text


def test_pane_store_reports_skipped_when_registration_fails(tmp_path: Path, monkeypatch) -> None:
    _isolate_home(tmp_path, monkeypatch)
    spec = load_repo_catalog().get("antigravity")

    def _raise(*args, **kwargs):
        raise PermissionError("read-only home")

    monkeypatch.setattr("src.adapters.terminal.mcp_injection.ensure_agy_mcp_registered", _raise)
    inj = PaneMcpConfigStore(tmp_path / "pane-store").prepare(
        spec, session_id="pane_1-ro", hub_url="http://127.0.0.1:43210/mcp", token="TOK-SECRET",
        global_registration_consent=True,
    )
    assert inj.status == "skipped" and inj.reason == "registration_failed"
