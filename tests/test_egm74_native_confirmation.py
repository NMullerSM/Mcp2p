"""EGM-74: confirmacion NATIVA (dialogo del sistema, del lado Python) antes de instalar, persistir una ruta
de binario o abrir un panel con un comando propio. El JS puede llamar al puente, pero no pulsar el dialogo."""

from __future__ import annotations

import stat
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

import pytest

from conftest import make_test_db
from src.adapters.terminal import CliCatalogService, PosixPtyHostConnector
from src.adapters.terminal.mcp_injection import PaneMcpConfigStore
from src.app.api_bridge import ApiBridge
from src.app.native_confirm import NativeConfirmationService

pytestmark = pytest.mark.skipif(not PosixPtyHostConnector.is_available(), reason="Solo POSIX")


@pytest.fixture(autouse=True)
def _real_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP2P_TEST_AUTO_CONFIRM", raising=False)


@pytest.fixture
def bridge(tmp_path: Path):
    b = ApiBridge(db_manager=make_test_db())
    primary = b.core_kernel.terminal_connector.primary
    primary.catalog_service = CliCatalogService(
        b.core_kernel.extension_repo,
        user_catalog_path=tmp_path / "sin_usuario.json",
        which=lambda name: "/usr/bin/" + name if name in {"npm", "node"} else None,
        platform="linux",
        home=str(tmp_path / "home"),
        environ={"SHELL": "/bin/sh"},
    )
    primary.min_available_mb = 0
    primary._mcp_store = PaneMcpConfigStore(tmp_path / "mcp")
    try:
        yield b
    finally:
        b._close_terminal_matrix()
        b.core_kernel.close()


class _Dialog:
    def __init__(self, answers: List[Any]) -> None:
        self.answers = list(answers)
        self.calls: List[Tuple[str, str]] = []

    def __call__(self, title: str, message: str) -> Any:
        self.calls.append((title, message))
        return self.answers.pop(0) if self.answers else False


def _exec(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_without_window_actions_are_rejected_with_a_clear_code(bridge: ApiBridge, tmp_path: Path) -> None:
    exe = _exec(tmp_path / "mi-claude")
    res = bridge.pty_set_cli_path("claude", str(exe))
    assert res["success"] is False and res["code"] == "CONFIRMATION_UNAVAILABLE"
    assert bridge.core_kernel.extension_repo.get_extension_setting("terminal_cli", "binary_path.claude") is None

    spawn = bridge.pty_spawn_pane(1, "custom_shell", "implementer", ["touch", str(tmp_path / "pwned")], 100, 28, False, None)
    assert spawn["success"] is False and spawn["code"] == "CONFIRMATION_UNAVAILABLE"
    assert not (tmp_path / "pwned").exists()
    assert bridge.extensions_ui._get_pane_registry().list_panes() == []
    assert [s for s in bridge.core_kernel.terminal_connector.list_sessions() if s["alive"]] == []


def test_set_cli_path_shows_the_exact_path_and_only_persists_when_accepted(bridge: ApiBridge, tmp_path: Path) -> None:
    exe = _exec(tmp_path / "mi-claude")
    dialog = _Dialog([False, True])
    bridge.confirmations.dialog = dialog

    declined = bridge.pty_set_cli_path("claude", str(exe))
    assert declined["code"] == "CONFIRMATION_DECLINED" and declined["success"] is False
    assert bridge.core_kernel.extension_repo.get_extension_setting("terminal_cli", "binary_path.claude") is None

    accepted = bridge.pty_set_cli_path("claude", str(exe))
    assert accepted["success"] is True and accepted["configured_path"] == str(exe)
    assert all(str(exe) in message for _, message in dialog.calls) and len(dialog.calls) == 2

    # Invalida o borrar no abren dialogo
    dialog.calls.clear()
    assert bridge.pty_set_cli_path("claude", "relativa/x")["code"] == "INVALID_PATH"
    assert bridge.pty_set_cli_path("claude", "")["success"] is True
    assert dialog.calls == []


def test_custom_shell_with_command_shows_the_exact_argv_and_runs_only_when_accepted(bridge: ApiBridge, tmp_path: Path) -> None:
    marker = tmp_path / "ejecutado"
    dialog = _Dialog([False, True])
    bridge.confirmations.dialog = dialog

    no = bridge.pty_spawn_pane(1, "custom_shell", "implementer", ["touch", str(marker)], 100, 28, False, str(tmp_path))
    assert no["success"] is False and no["code"] == "CONFIRMATION_DECLINED"
    assert bridge.extensions_ui._get_pane_registry().list_panes() == []

    yes = bridge.pty_spawn_pane(1, "custom_shell", "implementer", "touch '%s'" % marker, 100, 28, False, str(tmp_path))
    assert yes["success"] is True
    assert f"touch {marker}" in dialog.calls[0][1] and f"touch {marker}" in dialog.calls[1][1]
    assert str(tmp_path) in dialog.calls[1][1]
    import time

    deadline = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert marker.exists()


def test_plain_shell_and_catalog_clis_need_no_dialog(bridge: ApiBridge, tmp_path: Path) -> None:
    dialog = _Dialog([])
    bridge.confirmations.dialog = dialog
    res = bridge.pty_spawn_pane(1, "custom_shell", "implementer", None, 100, 28, False, str(tmp_path))
    assert res["success"] is True
    missing = bridge.pty_spawn_pane(2, "claude", "implementer", None, 100, 28, False, str(tmp_path))
    assert missing["code"] == "CLI_BINARY_NOT_FOUND"
    assert dialog.calls == []


def test_install_needs_token_then_native_dialog_with_the_exact_command(bridge: ApiBridge, monkeypatch: pytest.MonkeyPatch) -> None:
    spawned: List[Dict[str, Any]] = []
    terminal = bridge.extensions_ui._get_terminal_connector()
    monkeypatch.setattr(
        terminal, "spawn_session", lambda *a, **k: spawned.append(k) or {"success": True, "session_id": "s", "pid": 1}
    )
    dialog = _Dialog([False, True])
    bridge.confirmations.dialog = dialog

    # Token invalido: ni siquiera se abre el dialogo (no se puede inundar de dialogos sin token)
    bad = bridge.pty_install_cli("claude", "token-falso")
    assert bad["code"] == "INSTALL_TOKEN_INVALID" and dialog.calls == []

    prep = bridge.pty_prepare_install("claude")
    declined = bridge.pty_install_cli("claude", prep["confirmation_token"])
    assert declined["code"] == "CONFIRMATION_DECLINED" and spawned == []
    assert prep["command"] in dialog.calls[0][1]
    # el token se gasto: hay que preparar de nuevo
    assert bridge.pty_install_cli("claude", prep["confirmation_token"])["code"] == "INSTALL_TOKEN_INVALID"

    prep2 = bridge.pty_prepare_install("claude")
    ok = bridge.pty_install_cli("claude", prep2["confirmation_token"])
    assert ok["success"] is True and ok["code"] == "INSTALL_STARTED"
    assert len(spawned) == 1 and prep2["command"] in spawned[0]["command"][-1]


def test_dialog_failure_and_repeated_declines_are_handled(tmp_path: Path) -> None:
    def boom(title: str, message: str) -> bool:
        raise RuntimeError("sin pantalla")

    now = [0.0]
    svc = NativeConfirmationService(lambda: None, dialog=boom, clock=lambda: now[0])
    assert svc.confirm("t", "m")["code"] == "CONFIRMATION_UNAVAILABLE"

    answers = _Dialog([False, False, False, True])
    svc = NativeConfirmationService(lambda: None, dialog=answers, clock=lambda: now[0])
    for _ in range(3):
        assert svc.confirm("t", "m")["code"] == "CONFIRMATION_DECLINED"
    assert len(answers.calls) == 3
    # tras tres rechazos seguidos se corta el goteo de dialogos durante un minuto
    assert svc.confirm("t", "m")["code"] == "CONFIRMATION_DECLINED" and len(answers.calls) == 3
    now[0] += 61
    assert svc.confirm("t", "m") is None and len(answers.calls) == 4
    # Solo `True` acepta (el SDK devuelve -1 si la ventana ya no existe)
    assert NativeConfirmationService(lambda: None, dialog=lambda t, m: -1).confirm("t", "m")["code"] == "CONFIRMATION_DECLINED"


def test_default_dialog_is_the_pywebview_window_native_dialog() -> None:
    shown: List[Tuple[str, str]] = []

    class _Window:
        def create_confirmation_dialog(self, title: str, message: str) -> bool:
            shown.append((title, message))
            return True

    window: Any = None
    svc = NativeConfirmationService(lambda: window)
    assert svc.confirm("Titulo", "cuerpo")["code"] == "CONFIRMATION_UNAVAILABLE"
    window = _Window()
    assert svc.confirm("Titulo", "cuerpo") is None
    assert "cuerpo" in shown[0][1] and shown[0][1].startswith("Titulo")  # el cuerpo viaja entero (GTK solo muestra `message`)


def test_test_env_var_is_the_only_bypass(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = NativeConfirmationService(lambda: None)
    assert svc.confirm("t", "m")["code"] == "CONFIRMATION_UNAVAILABLE"
    monkeypatch.setenv("MCP2P_TEST_AUTO_CONFIRM", "1")
    assert svc.confirm("t", "m") is None
    monkeypatch.setenv("MCP2P_TEST_AUTO_CONFIRM", "yes")
    assert svc.confirm("t", "m")["code"] == "CONFIRMATION_UNAVAILABLE"
