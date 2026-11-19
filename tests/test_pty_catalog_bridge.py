"""
T2 (plan 2026-11-15): metodos del puente para el slider de terminales
(`pty_list_cli_catalog`, `pty_set_cli_path`, `pty_set_max_panes`, `pty_spawn_pane` con CLI + carpeta).
Cada metodo publico de ApiBridge es superficie atacable desde el webview: se validan los argumentos.
"""

from __future__ import annotations

import stat
import time
from pathlib import Path
from typing import Any, Dict

import pytest

from conftest import make_test_db
from src.adapters.terminal import CliCatalogService, PosixPtyHostConnector
from src.app.api_bridge import ApiBridge

pytestmark = pytest.mark.skipif(not PosixPtyHostConnector.is_available(), reason="Solo POSIX")


def _make_exec(path: Path, body: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture
def bridge(tmp_path: Path):
    b = ApiBridge(db_manager=make_test_db())
    primary = b.core_kernel.terminal_connector.primary
    # Aislar de los CLIs realmente instalados en la maquina que corre la suite.
    primary.catalog_service = CliCatalogService(
        b.core_kernel.extension_repo,
        user_catalog_path=tmp_path / "sin_catalogo_usuario.json",
        which=lambda name: "/usr/bin/" + name if name in {"npm", "node"} else None,
        platform="linux",
        home=str(tmp_path / "home"),
        environ={"SHELL": "/bin/sh"},
    )
    primary.min_available_mb = 0
    try:
        yield b
    finally:
        b._close_terminal_matrix()
        b.core_kernel.close()


def _wait_text(bridge: ApiBridge, pane: Any, needle: str, timeout: float = 5.0) -> str:
    cursor, acc, deadline = 0, "", time.monotonic() + timeout
    while time.monotonic() < deadline:
        res = bridge.pty_poll_output(pane, cursor)
        cursor = res.get("next_seq", cursor)
        acc += res.get("text", "")
        if needle in acc:
            return acc
        time.sleep(0.05)
    raise AssertionError(f"no aparecio {needle!r}: {acc!r}")


def test_new_methods_are_exposed_by_the_public_js_functions(bridge: ApiBridge) -> None:
    exposed = {fn.__name__ for fn in bridge.public_js_functions()}
    assert {"pty_list_cli_catalog", "pty_set_cli_path", "pty_set_max_panes", "pty_spawn_pane",
            "reset_loop_breaker"} <= exposed


def test_list_cli_catalog_reports_installed_state_without_executing(bridge: ApiBridge, tmp_path: Path) -> None:
    marker = tmp_path / "ejecutado"
    _make_exec(tmp_path / "home" / ".local" / "bin" / "agy", f"#!/bin/sh\ntouch {marker}\n")
    res = bridge.pty_list_cli_catalog()
    assert res["success"] is True and res["code"] == "OK"
    assert res["max_panes"] == 4 and res["hard_max_panes"] == 6
    assert res["terminal_platform_supported"] is True
    by_id = {c["id"]: c for c in res["clis"]}
    assert set(by_id) == {"claude", "codex", "opencode", "antigravity", "cursor", "custom_shell"}
    assert by_id["antigravity"]["installed"] is True
    assert by_id["antigravity"]["resolution_source"] == "local_bin"
    assert by_id["claude"]["installed"] is False and by_id["claude"]["resolved_path"] is None
    inst = by_id["claude"]["install"]
    assert inst["available"] is True and inst["verified"] is True
    assert inst["command"] == "npm install -g @anthropic-ai/claude-code@latest"
    assert inst["downloads_script"] is False and inst["source_url"].startswith("https://")
    assert by_id["custom_shell"]["install"]["available"] is False
    assert not marker.exists()


def test_set_cli_path_validates_and_persists(bridge: ApiBridge, tmp_path: Path) -> None:
    ok = _make_exec(tmp_path / "mi-claude")
    assert bridge.pty_set_cli_path("claude", str(ok))["success"] is True
    row = next(c for c in bridge.pty_list_cli_catalog()["clis"] if c["id"] == "claude")
    assert row["installed"] and row["resolved_path"] == str(ok) and row["resolution_source"] == "configured"
    for bad in ("relativa/claude", "/tmp/../etc/passwd", f"{ok}\nx", "/no/existe"):
        res = bridge.pty_set_cli_path("claude", bad)
        assert res["success"] is False and res["code"] in {"INVALID_PATH", "PATH_NOT_FOUND"}
    assert bridge.pty_set_cli_path("no_existe", str(ok))["code"] == "CLI_PROFILE_UNKNOWN"
    assert bridge.pty_set_cli_path({"x": 1}, str(ok))["code"] == "CLI_PROFILE_UNKNOWN"
    assert bridge.pty_set_cli_path("claude", "")["success"] is True
    row = next(c for c in bridge.pty_list_cli_catalog()["clis"] if c["id"] == "claude")
    assert row["configured_path"] is None


def test_set_max_panes_validates_applies_and_persists(bridge: ApiBridge) -> None:
    for bad in (0, 7, -1, "4", 4.5, True, None, [6]):
        assert bridge.pty_set_max_panes(bad)["code"] == "INVALID_ARGUMENT"
    res = bridge.pty_set_max_panes(6)
    assert res["success"] is True and res["max_panes"] == 6
    assert bridge.pty_get_matrix_status()["connector_status"]["max_panes"] == 6
    repo = bridge.core_kernel.extension_repo
    assert repo.get_extension_setting("terminal_matrix", "max_panes") == "6"


def test_spawn_pane_precise_error_codes(bridge: ApiBridge, tmp_path: Path) -> None:
    cwd = tmp_path / "proyecto"
    cwd.mkdir()

    miss = bridge.pty_spawn_pane(1, "claude", "implementer", None, 100, 28, False, str(cwd))
    assert miss["success"] is False and miss["code"] == "CLI_BINARY_NOT_FOUND" and miss["degraded"] is False
    assert miss["install_available"] is True and "npm install -g" in miss["install_command"]
    assert "session_token" not in miss

    unknown = bridge.pty_spawn_pane(1, "agy", "implementer", None, 100, 28, False)
    assert unknown["code"] == "CLI_PROFILE_UNKNOWN" and "antigravity" in unknown["known_profiles"]
    assert bridge.pty_spawn_pane(1, ["claude"], "implementer", None, 100, 28, False)["code"] == "CLI_PROFILE_UNKNOWN"

    assert bridge.pty_spawn_pane(7, "claude", "implementer", None, 100, 28, False)["code"] == "PANE_LIMIT_REACHED"
    assert bridge.pty_spawn_pane(5, "claude", "implementer", None, 100, 28, False)["code"] == "PANE_LIMIT_REACHED"
    assert bridge.pty_spawn_pane("pane_x", "claude", "implementer", None, 100, 28, False)["code"] == "INVALID_PANE_ID"

    # command solo se acepta en custom_shell
    cmd = bridge.pty_spawn_pane(1, "claude", "implementer", ["/bin/sh", "-c", "id"], 100, 28, False)
    assert cmd["code"] == "COMMAND_NOT_ALLOWED"
    assert bridge.pty_spawn_pane(1, "custom_shell", "implementer", ["/bin/sh\n-c"], 100, 28, False)["code"] == "INVALID_ARGUMENT"
    assert bridge.pty_spawn_pane(1, "custom_shell", "implementer", {"a": 1}, 100, 28, False)["code"] == "INVALID_ARGUMENT"

    assert bridge.pty_spawn_pane(1, "custom_shell", "bad role!", None, 100, 28, False)["code"] == "INVALID_ARGUMENT"
    assert bridge.pty_spawn_pane(1, "custom_shell", "implementer", None, 0, 28, False)["code"] == "INVALID_ARGUMENT"
    assert bridge.pty_spawn_pane(1, "custom_shell", "implementer", None, 100, 10**6, False)["code"] == "INVALID_ARGUMENT"
    assert bridge.pty_spawn_pane(1, "custom_shell", "implementer", None, True, 28, False)["code"] == "INVALID_ARGUMENT"

    afile = tmp_path / "archivo.txt"
    afile.write_text("x")
    for bad_cwd in ("relativa", str(tmp_path / "nada"), str(afile), f"{cwd}\n/etc", "", 123, ".."):
        res = bridge.pty_spawn_pane(1, "custom_shell", "implementer", None, 100, 28, False, bad_cwd)
        assert res["success"] is False and res["code"] == "INVALID_CWD", bad_cwd

    # ninguno de los rechazos dejo un panel registrado
    assert bridge.pty_get_matrix_status()["panes"] == []


def test_spawn_pane_launches_configured_cli_in_chosen_folder(bridge: ApiBridge, tmp_path: Path) -> None:
    cwd = tmp_path / "carpeta de trabajo"
    cwd.mkdir()
    script = _make_exec(tmp_path / "fake-agy", "#!/bin/sh\necho CWD=$(pwd)\nsleep 5\n")
    assert bridge.pty_set_cli_path("antigravity", str(script))["success"] is True
    res = bridge.pty_spawn_pane(2, "antigravity", "reviewer", None, 100, 28, False, str(cwd))
    assert res["success"] is True and res["code"] == "OK" and res["pane_id"] == "pane_2"
    assert res["cwd"] == str(cwd.resolve())
    assert "session_token" not in res and "session_token" not in res["session"]
    assert f"CWD={cwd.resolve()}" in _wait_text(bridge, 2, "CWD=")
    again = bridge.pty_spawn_pane(2, "antigravity", "reviewer", None, 100, 28, False, str(cwd))
    assert again["code"] == "PANE_ALREADY_ACTIVE"
    assert bridge.pty_close_pane(2)["success"] is True


def test_six_panes_when_configured(bridge: ApiBridge, tmp_path: Path) -> None:
    assert bridge.pty_set_max_panes(6)["success"] is True
    script = _make_exec(tmp_path / "fake-codex", "#!/bin/sh\nsleep 10\n")
    assert bridge.pty_set_cli_path("codex", str(script))["success"] is True
    for pane in range(1, 7):
        res = bridge.pty_spawn_pane(pane, "codex", "implementer", None, 80, 24, False)
        assert res["success"] is True, (pane, res)
    assert bridge.pty_spawn_pane(7, "codex", "implementer", None, 80, 24, False)["code"] == "PANE_LIMIT_REACHED"
