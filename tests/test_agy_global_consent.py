"""R1.4 (D3): el registro global de `agy` es opt-in con dialogo nativo, se recuerda y se puede revocar."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from conftest import make_test_db
from src.adapters.outbound.extension_gate import NullAgentTerminalConnector
from src.adapters.terminal import CliCatalogService
from src.adapters.terminal.mcp_injection import PaneMcpConfigStore
from src.app.api_bridge import ApiBridge


class Dialog:
    """Dialogo nativo falso: registra los textos mostrados y responde segun `answers`."""

    def __init__(self, *answers: Optional[bool]) -> None:
        self.answers = list(answers)
        self.shown: List[str] = []

    def __call__(self, title: str, message: str):
        self.shown.append(message)
        return self.answers.pop(0) if self.answers else False


def _install_fake_agy(home: Path) -> Path:
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        exe = bin_dir / "agy.cmd"
        exe.write_text("@echo off\r\nexit /b 0\r\n", encoding="utf-8")
        return exe
    exe = bin_dir / "agy"
    exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return exe


@pytest.fixture
def agy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("MCP2P_MEASUREMENTS_FILE", str(tmp_path / "measurements.json"))
    monkeypatch.delenv("MCP2P_TEST_AUTO_CONFIRM", raising=False)  # estos tests SI pasan por el dialogo
    monkeypatch.setattr("src.app.ui_adapters.extensions_lab_ui.is_unsupported_terminal_platform", lambda: False)
    fake_agy = _install_fake_agy(home)

    b = ApiBridge(db_manager=make_test_db())
    terminal = b.extensions_ui._get_terminal_connector()
    primary = getattr(terminal, "primary", terminal)
    plat = "win32" if sys.platform == "win32" else "linux"
    catalog_svc = CliCatalogService(
        b.core_kernel.extension_repo,
        user_catalog_path=tmp_path / "sin_usuario.json",
        which=lambda name: str(fake_agy) if name in {"agy", "antigravity"} else None,
        platform=plat,
        home=str(home),
        environ=(
            {"COMSPEC": os.environ.get("COMSPEC", "C:\\Windows\\System32\\cmd.exe")}
            if plat == "win32"
            else {"SHELL": "/bin/sh"}
        ),
    )
    store = PaneMcpConfigStore(tmp_path / "mcp-store")

    if isinstance(primary, NullAgentTerminalConnector) or not hasattr(primary, "_mcp_store"):
        monkeypatch.setattr(b.extensions_ui, "_catalog_service", lambda: catalog_svc)

        def _fake_spawn_session(**kw: Any) -> Dict[str, Any]:
            spec = catalog_svc.get_spec(kw.get("cli_profile"))
            injection = store.prepare(
                spec,
                session_id=f"{kw['pane_id']}-fake",
                hub_url=kw.get("hub_url"),
                token=kw.get("session_token"),
            )
            return {
                "success": True,
                "degraded": False,
                "pane_id": kw["pane_id"],
                "session_id": f"{kw['pane_id']}-fake",
                "mcp_injection": injection.public(),
            }

        monkeypatch.setattr(terminal, "spawn_session", _fake_spawn_session)
    else:
        primary.catalog_service = catalog_svc
        primary.min_available_mb = 0
        primary._mcp_store = store

    try:
        yield {
            "bridge": b,
            "home": home,
            "cfg": home / ".gemini" / "config" / "mcp_config.json",
            "tmp": tmp_path,
        }
    finally:
        b._close_terminal_matrix()
        b.core_kernel.close()


def _spawn(lab, pane: int):
    res = lab["bridge"].pty_spawn_pane(pane, "antigravity", "implementer", None, 100, 28, True, str(lab["tmp"]))
    assert res["success"] is True, res
    return res["mcp_injection"]


def test_spawn_with_declined_dialog_opens_panel_but_writes_nothing_and_does_not_ask_again(agy) -> None:
    dialog = Dialog(False)
    agy["bridge"].confirmations.dialog = dialog

    inj = _spawn(agy, 1)
    assert inj["status"] == "needs_consent" and inj["method"] == "stdio_env_proxy"
    assert not (agy["home"] / ".gemini").exists()  # nada fuera del perfil del panel
    assert len(dialog.shown) == 1

    inj = _spawn(agy, 2)  # el rechazo se recuerda: no se pregunta en cada spawn
    assert inj["status"] == "needs_consent"
    assert len(dialog.shown) == 1
    assert not (agy["home"] / ".gemini").exists()
    assert agy["bridge"].pty_get_agy_mcp_status()["consent"] == "declined"


def test_spawn_without_window_keeps_needs_consent_and_asks_again_next_time(agy) -> None:
    # Sin ventana ni dialogo inyectado: CONFIRMATION_UNAVAILABLE. No se registra ni se recuerda nada.
    agy["bridge"].confirmations.dialog = None
    inj = _spawn(agy, 1)
    assert inj["status"] == "needs_consent"
    assert not (agy["home"] / ".gemini").exists()
    assert agy["bridge"].pty_get_agy_mcp_status()["consent"] == "unset"


def test_accepted_dialog_registers_shows_exact_entry_and_is_remembered(agy) -> None:
    dialog = Dialog(True)
    agy["bridge"].confirmations.dialog = dialog

    inj = _spawn(agy, 1)
    assert inj["status"] == "injected" and inj["reason"] == "stdio_hub_proxy"
    assert len(dialog.shown) == 1
    text = dialog.shown[0]
    assert str(agy["cfg"]) in text and "--hub-proxy-only" in text and "x-mcp2p-managed" in text
    assert "MCP2P_SESSION_TOKEN" not in text

    data = json.loads(agy["cfg"].read_text(encoding="utf-8"))
    assert data["mcpServers"]["mcp2p"]["args"][-1] == "--hub-proxy-only"
    assert agy["bridge"].pty_get_agy_mcp_status()["consent"] == "granted"

    # Consentimiento ya concedido en esta maquina: el siguiente panel no pregunta y mantiene la entrada.
    inj = _spawn(agy, 2)
    assert inj["status"] == "injected"
    assert len(dialog.shown) == 1


def test_register_button_retries_with_dialog_after_a_decline(agy) -> None:
    agy["bridge"].confirmations.dialog = Dialog(False)
    assert _spawn(agy, 1)["status"] == "needs_consent"

    dialog = Dialog(True)
    agy["bridge"].confirmations.dialog = dialog
    res = agy["bridge"].pty_register_agy_mcp()
    assert res["success"] is True and res["managed"] is True
    assert "MCP2P_SESSION_TOKEN" not in json.dumps(res)
    assert len(dialog.shown) == 1 and str(agy["cfg"]) in dialog.shown[0]
    assert agy["cfg"].is_file()
    assert agy["bridge"].pty_get_agy_mcp_status()["consent"] == "granted"
    assert agy["bridge"].pty_get_agy_mcp_status()["registration"] == "managed"


def test_register_button_declined_writes_nothing(agy) -> None:
    agy["bridge"].confirmations.dialog = Dialog(False)
    res = agy["bridge"].pty_register_agy_mcp()
    assert res["success"] is False and res["code"] == "CONFIRMATION_DECLINED"
    assert not (agy["home"] / ".gemini").exists()


def test_dialog_warns_when_a_manual_entry_will_be_replaced_and_keeps_it_in_the_backup(agy) -> None:
    cfg: Path = agy["cfg"]
    cfg.parent.mkdir(parents=True)
    manual = {"mcpServers": {"mcp2p": {"command": "python", "args": ["-m", "src.mcp.server"],
                                         "env": {"MCP2P_DB_PATH": "/home/u/real.db"}}}}
    cfg.write_text(json.dumps(manual), encoding="utf-8")
    original = cfg.read_bytes()

    dialog = Dialog(True)
    agy["bridge"].confirmations.dialog = dialog
    res = agy["bridge"].pty_register_agy_mcp()
    assert res["success"] is True
    assert "reemplazara" in dialog.shown[0] and "NO usa --hub-proxy-only" in dialog.shown[0]
    assert "--hub-proxy-only" in json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"]["mcp2p"]["args"]
    assert cfg.with_name(cfg.name + ".mcp2p.bak").read_bytes() == original


def test_revoke_removes_only_our_entry_and_stops_future_registration(agy) -> None:
    cfg: Path = agy["cfg"]
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"mcpServers": {"github": {"command": "npx"}}}), encoding="utf-8")
    agy["bridge"].confirmations.dialog = Dialog(True)
    assert _spawn(agy, 1)["status"] == "injected"

    res = agy["bridge"].pty_unregister_agy_mcp()
    assert res["success"] is True and res["removed"] is True
    assert json.loads(cfg.read_text(encoding="utf-8")) == {"mcpServers": {"github": {"command": "npx"}}}
    assert agy["bridge"].pty_get_agy_mcp_status()["consent"] == "declined"

    dialog = Dialog(True)
    agy["bridge"].confirmations.dialog = dialog
    assert _spawn(agy, 2)["status"] == "needs_consent"  # revocado: ni pregunta ni escribe
    assert dialog.shown == []
    assert "mcp2p" not in json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"]


def test_revoke_leaves_a_manual_entry_alone(agy) -> None:
    cfg: Path = agy["cfg"]
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"mcpServers": {"mcp2p": {"command": "python"}}}), encoding="utf-8")
    snapshot = cfg.read_bytes()
    res = agy["bridge"].pty_unregister_agy_mcp()
    assert res["success"] is True and res["removed"] is False and res["reason"] == "not_managed"
    assert cfg.read_bytes() == snapshot


def test_verified_agy_registers_and_revokes_hooks_json_alongside_mcp_config(agy) -> None:
    from src.adapters.terminal.cli_measurement_store import MeasurementStore
    from src.core.domain.cli_measurement import Attempt, HookEvent, build_record, evaluate_attempt

    plat = "win32" if sys.platform == "win32" else "linux"
    fake_agy = agy["home"] / ".local" / "bin" / ("agy.cmd" if plat == "win32" else "agy")
    st = os.stat(fake_agy)
    ev = evaluate_attempt(
        Attempt(
            "bracketed",
            True,
            events=(
                HookEvent("agy.SessionStart", 10, session_fields=("conversationId",)),
                HookEvent("agy.PreInvocation", 1100, session_fields=("conversationId",)),
                HookEvent("agy.Stop", 2000, session_fields=("conversationId",)),
            ),
            enter_at_ms=1000,
            screen_after_paste="Prueba LISTO",
        )
    )
    assert ev.ok
    rec = build_record(
        cli_id="antigravity",
        platform=plat,
        today="2026-11-21",
        cli_version="1.5.14",
        binary_path=str(fake_agy),
        binary_mtime_ns=st.st_mtime_ns,
        binary_size=st.st_size,
        result=ev,
        paste_mode="bracketed",
    )
    store = agy["bridge"].extensions_ui._catalog_service().measurement_store()
    store.put(rec)
    hooks_cfg: Path = agy["home"] / ".gemini" / "config" / "hooks.json"

    dialog = Dialog(True)
    agy["bridge"].confirmations.dialog = dialog
    res = agy["bridge"].pty_register_agy_mcp()
    assert res["success"] is True
    assert str(hooks_cfg) in dialog.shown[0]
    assert agy["cfg"].is_file() and hooks_cfg.is_file()
    hooks_data = json.loads(hooks_cfg.read_text(encoding="utf-8"))
    assert "mcp2p" in hooks_data and "Stop" in hooks_data["mcp2p"]

    unreg = agy["bridge"].pty_unregister_agy_mcp()
    assert unreg["success"] is True and unreg["removed"] is True
    assert "mcp2p" not in json.loads(agy["cfg"].read_text(encoding="utf-8")).get("mcpServers", {})
    assert "mcp2p" not in json.loads(hooks_cfg.read_text(encoding="utf-8"))
