"""R1.4 (D3): el registro global de `agy` es opt-in con dialogo nativo, se recuerda y se puede revocar."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

import pytest

from test_egm70_cli_hub_connection import _install_fake, lab, posix_only  # noqa: F401  (fixture `lab`)


class Dialog:
    """Dialogo nativo falso: registra los textos mostrados y responde segun `answers`."""

    def __init__(self, *answers: Optional[bool]) -> None:
        self.answers = list(answers)
        self.shown: List[str] = []

    def __call__(self, title: str, message: str):
        self.shown.append(message)
        return self.answers.pop(0) if self.answers else False


@pytest.fixture
def agy(lab, monkeypatch: pytest.MonkeyPatch):
    home: Path = lab["home"]
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("MCP2P_TEST_AUTO_CONFIRM", raising=False)  # estos tests SI pasan por el dialogo
    _install_fake(home, "agy")
    lab["cfg"] = home / ".gemini" / "config" / "mcp_config.json"
    return lab


def _spawn(lab, pane: int):
    res = lab["bridge"].pty_spawn_pane(pane, "antigravity", "implementer", None, 100, 28, True, str(lab["tmp"]))
    assert res["success"] is True, res
    return res["mcp_injection"]


@posix_only
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


@posix_only
def test_spawn_without_window_keeps_needs_consent_and_asks_again_next_time(agy) -> None:
    # Sin ventana ni dialogo inyectado: CONFIRMATION_UNAVAILABLE. No se registra ni se recuerda nada.
    agy["bridge"].confirmations.dialog = None
    inj = _spawn(agy, 1)
    assert inj["status"] == "needs_consent"
    assert not (agy["home"] / ".gemini").exists()
    assert agy["bridge"].pty_get_agy_mcp_status()["consent"] == "unset"


@posix_only
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


@posix_only
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


@posix_only
def test_register_button_declined_writes_nothing(agy) -> None:
    agy["bridge"].confirmations.dialog = Dialog(False)
    res = agy["bridge"].pty_register_agy_mcp()
    assert res["success"] is False and res["code"] == "CONFIRMATION_DECLINED"
    assert not (agy["home"] / ".gemini").exists()


@posix_only
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


@posix_only
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


@posix_only
def test_revoke_leaves_a_manual_entry_alone(agy) -> None:
    cfg: Path = agy["cfg"]
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"mcpServers": {"mcp2p": {"command": "python"}}}), encoding="utf-8")
    snapshot = cfg.read_bytes()
    res = agy["bridge"].pty_unregister_agy_mcp()
    assert res["success"] is True and res["removed"] is False and res["reason"] == "not_managed"
    assert cfg.read_bytes() == snapshot
