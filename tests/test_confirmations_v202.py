"""Pruebas de confirmaciones recordables, diálogo propio y superficie de seguridad (WP-9)."""

from __future__ import annotations

import ast
import json
import stat
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import anyio
import pytest

from conftest import make_test_db
from src.adapters.outbound.extension_gate import NullAgentTerminalConnector
from src.adapters.terminal import CliCatalogService
from src.adapters.terminal.mcp_injection import PaneMcpConfigStore, build_agy_entry
from src.app.api_bridge import ApiBridge
from src.app.app_confirmations import AppConfirmationBroker, ConfirmationGate
from src.app.native_confirm import NativeConfirmationService
from src.core.domain.confirmations import CONFIRMATION_TABLE, REMEMBERABLE, ConfirmationSpec
from src.mcp.server import create_mcp_server


class _Clock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


class _SpyDialog:
    def __init__(self, answers: List[Any]) -> None:
        self.answers = list(answers)
        self.calls: List[Tuple[str, str]] = []

    def __call__(self, title: str, message: str) -> Any:
        self.calls.append((title, message))
        return self.answers.pop(0) if self.answers else False


@pytest.fixture(autouse=True)
def _no_auto_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP2P_TEST_AUTO_CONFIRM", raising=False)


# ---------------------------------------------------------------------------
# 1. Invariantes de la tabla cerrada de confirmaciones
# ---------------------------------------------------------------------------


def test_table_invariants_no_native_rememberable_and_high_risk_is_native() -> None:
    expected_ids = {
        "auto_relay.enable",
        "worker_mode.enable",
        "delivery.enable_pane",
        "cli.install.package_manager",
        "cli.install.script",
        "relay.discard_parked",
        "cli.set_binary_path",
        "pane.custom_command",
        "agy.register_global",
        "relay.send_to_elevated",
        "relay.release_parked",
        "relay.global_resume",
        "relay.resume_pane",
        "loop_breaker.reset",
        "operator.pair",
        "node.trust_direct",
        "external_url.open",
        "human_key.unlock",
    }
    assert set(CONFIRMATION_TABLE.keys()) == expected_ids

    for key, spec in CONFIRMATION_TABLE.items():
        assert isinstance(spec, ConfirmationSpec)
        assert spec.action_id == key
        assert spec.label_es.strip()
        assert spec.surface in {"app", "native", "password"}
        if spec.surface != "app":
            assert spec.rememberable is False, key
        if spec.risk == "high":
            assert spec.surface != "app" and spec.rememberable is False, key
            if key != "human_key.unlock":
                assert spec.surface == "native", key

    assert REMEMBERABLE == frozenset(
        {"auto_relay.enable", "worker_mode.enable", "delivery.enable_pane", "cli.install.package_manager"}
    )
    assert "cli.install.script" not in REMEMBERABLE
    assert "relay.discard_parked" not in REMEMBERABLE
    assert "external_url.open" not in REMEMBERABLE


# ---------------------------------------------------------------------------
# 2. Broker, TTL, un solo uso y vinculación a acción y argumentos
# ---------------------------------------------------------------------------


def test_app_confirmation_flow_token_single_use_and_ttl() -> None:
    db = make_test_db()
    try:
        clock = _Clock()
        broker = AppConfirmationBroker(ttl_s=120.0, clock=clock)
        gate = ConfirmationGate(NativeConfirmationService(lambda: None), db, broker=broker)
        calls: List[str] = []

        req = gate.require(
            "relay.discard_parked",
            "Descartar mensaje",
            "Texto exacto",
            lambda: calls.append("ran") or {"success": True, "state": "discarded"},
            args_digest="discard:m-1",
        )
        assert req["success"] is False
        assert req["code"] == "CONFIRMATION_REQUIRED"
        conf = req["confirmation"]
        assert conf["action_id"] == "relay.discard_parked"
        assert conf["title"] == "Descartar mensaje"
        assert conf["message"] == "Texto exacto"
        assert conf["rememberable"] is False
        assert conf["expires_in_s"] == 120.0
        tok = conf["token"]
        assert isinstance(tok, str) and len(tok) >= 32
        assert calls == []

        # Confirmar ejecuta una vez y quema el token
        res = gate.confirm(tok, True, False)
        assert res == {"success": True, "state": "discarded"}
        assert calls == ["ran"]
        assert gate.confirm(tok, True, False)["code"] == "CONFIRMATION_TOKEN_INVALID"
        assert calls == ["ran"]

        # Token caducado tras 120 s -> CONFIRMATION_TOKEN_EXPIRED y no ejecuta
        req2 = gate.require("relay.discard_parked", "T", "M", lambda: calls.append("late") or {"success": True})
        clock.now += 121.0
        exp = gate.confirm(req2["confirmation"]["token"], True, False)
        assert exp["success"] is False and exp["code"] == "CONFIRMATION_TOKEN_EXPIRED"
        assert calls == ["ran"]
        # Tras devolver expirado, ya no existe
        assert gate.confirm(req2["confirmation"]["token"], True, False)["code"] == "CONFIRMATION_TOKEN_INVALID"

        # Rechazar (o valor distinto de True) quema el token y no ejecuta
        req3 = gate.require("relay.discard_parked", "T", "M", lambda: calls.append("nope") or {"success": True})
        dec = gate.confirm(req3["confirmation"]["token"], "yes", False)
        assert dec["success"] is False and dec["code"] == "CONFIRMATION_DECLINED"
        assert gate.confirm(req3["confirmation"]["token"], True, False)["code"] == "CONFIRMATION_TOKEN_INVALID"
        assert calls == ["ran"]
    finally:
        db.close()


def test_token_bound_to_action_and_args() -> None:
    bridge = ApiBridge(db_manager=make_test_db())
    try:
        reg = bridge.extensions_ui._get_pane_registry()
        reg.register_pane("pane_1", project_uuid="proj_a", cli_profile="custom_shell")
        reg.register_pane("pane_2", project_uuid="proj_a", cli_profile="custom_shell")

        r1 = bridge.pty_set_worker_mode("pane_1", True)
        assert r1["code"] == "CONFIRMATION_REQUIRED"
        tok1 = r1["confirmation"]["token"]

        # Emitir otro token para pane_2 no invalida el de pane_1, y confirmar tok1 solo cambia pane_1.
        r2 = bridge.pty_set_worker_mode("pane_2", True)
        assert r2["code"] == "CONFIRMATION_REQUIRED"
        tok2 = r2["confirmation"]["token"]
        assert tok1 != tok2

        res = bridge.ui_confirm_action(tok1, True, False)
        assert res["success"] is True and res["pane_id"] == "pane_1"
        assert reg.get_pane("pane_1").worker_mode is True
        assert reg.get_pane("pane_2").worker_mode is False

        # Y el broker valida expected_action_id / expected_args_digest si se comprueba explícitamente.
        broker = AppConfirmationBroker()
        t_p1, _ = broker.issue("worker_mode.enable", lambda: {"pane": "pane_1"}, args_digest="pane_1")
        assert broker.consume(t_p1, expected_action_id="worker_mode.enable", expected_args_digest="pane_2") is None
        assert broker.consume(t_p1) is None  # quedó quemado al fallar
    finally:
        bridge._close_terminal_matrix()
        bridge.core_kernel.close()


# ---------------------------------------------------------------------------
# 3. Preferencias recordadas, falsificación en BD y olvido
# ---------------------------------------------------------------------------


def test_remember_only_for_rememberable_actions() -> None:
    bridge = ApiBridge(db_manager=make_test_db())
    try:
        reg = bridge.extensions_ui._get_pane_registry()
        reg.register_pane("pane_1", project_uuid="proj_a", cli_profile="custom_shell")
        reg.register_pane("pane_2", project_uuid="proj_a", cli_profile="custom_shell")

        # 1) Acción NO recordable (relay.discard_parked): incluso si el cliente envía remember=True, NO se guarda.
        non_rem = bridge.confirm_gate.require(
            "relay.discard_parked", "Descartar", "Cuerpo", lambda: {"success": True, "discarded": True}
        )
        assert non_rem["confirmation"]["rememberable"] is False
        assert bridge.ui_confirm_action(non_rem["confirmation"]["token"], True, True)["discarded"] is True
        assert bridge.db.get_setting("confirm.remember.relay.discard_parked") is None
        assert bridge.ui_list_confirmation_prefs()["prefs"] == []

        # cli.install.script tampoco es recordable
        scr = bridge.confirm_gate.require(
            "cli.install.script", "Instalar script", "curl | sh", lambda: {"success": True}
        )
        assert scr["confirmation"]["rememberable"] is False
        assert bridge.ui_confirm_action(scr["confirmation"]["token"], True, True)["success"] is True
        assert bridge.db.get_setting("confirm.remember.cli.install.script") is None

        # 2) Acción SÍ recordable (worker_mode.enable): remember=True guarda preferencia y omite siguientes avisos.
        req = bridge.pty_set_worker_mode("pane_1", True)
        assert req["code"] == "CONFIRMATION_REQUIRED" and req["confirmation"]["rememberable"] is True
        ok = bridge.ui_confirm_action(req["confirmation"]["token"], True, True)
        assert ok["success"] is True and ok["worker_mode"] is True

        raw = bridge.db.get_setting("confirm.remember.worker_mode.enable")
        assert raw is not None
        parsed = json.loads(raw)
        assert parsed["v"] == 1 and parsed["remember"] is True and parsed["set_at"] and parsed["app_version"]

        # Siguiente activación en otro panel ya no pide confirmación.
        ok2 = bridge.pty_set_worker_mode("pane_2", True)
        assert ok2["success"] is True and ok2["worker_mode"] is True
    finally:
        bridge._close_terminal_matrix()
        bridge.core_kernel.close()


def test_forged_db_pref_never_skips_native_dialog(tmp_path: Path) -> None:
    bridge = ApiBridge(db_manager=make_test_db())
    try:
        spy = _SpyDialog([False, False])
        bridge.confirmations.dialog = spy

        forged = json.dumps({"v": 1, "remember": True, "set_at": "2026-11-19T00:00:00Z", "app_version": "2.0.2"})
        bridge.db.set_setting("confirm.remember.agy.register_global", forged)
        bridge.db.set_setting("confirm.remember.cli.set_binary_path", forged)

        # Ni list_prefs ni forget exponen filas no recordables falsificadas
        assert bridge.ui_list_confirmation_prefs()["prefs"] == []
        assert bridge.ui_forget_confirmation("agy.register_global")["code"] == "INVALID_ARGUMENT"

        # Gate directo sobre acciones nativas sigue pidiendo el diálogo nativo
        r_agy = bridge.confirm_gate.require("agy.register_global", "Registrar agy", "Cuerpo", lambda: {"success": True})
        assert r_agy["code"] == "CONFIRMATION_DECLINED"

        exe = tmp_path / "fake-claude"
        exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
        r_path = bridge.pty_set_cli_path("claude", str(exe))
        assert r_path["code"] == "CONFIRMATION_DECLINED"

        assert len(spy.calls) == 2
        assert bridge.db.get_extension_setting("terminal_cli", "binary_path.claude") is None
    finally:
        bridge._close_terminal_matrix()
        bridge.core_kernel.close()


@pytest.mark.parametrize(
    "bad_value",
    [
        "",
        "not-json",
        "[]",
        "123",
        json.dumps({"v": 2, "remember": True}),
        json.dumps({"v": True, "remember": True}),
        json.dumps({"v": 1, "remember": "true"}),
        json.dumps({"v": 1, "remember": False}),
        json.dumps({"remember": True}),
    ],
)
def test_malformed_pref_is_ignored(bad_value: str) -> None:
    db = make_test_db()
    try:
        db.set_setting("confirm.remember.auto_relay.enable", bad_value)
        gate = ConfirmationGate(NativeConfirmationService(lambda: None), db)
        res = gate.require("auto_relay.enable", "T", "M", lambda: {"success": True})
        assert res["code"] == "CONFIRMATION_REQUIRED"
        assert gate.list_prefs() == []
    finally:
        db.close()


def test_forget_and_forget_all() -> None:
    bridge = ApiBridge(db_manager=make_test_db())
    try:
        reg = bridge.extensions_ui._get_pane_registry()
        reg.register_pane("pane_1", project_uuid="proj_a", cli_profile="custom_shell")

        r_auto = bridge.pty_set_auto_relay("pane_1", True)
        assert bridge.ui_confirm_action(r_auto["confirmation"]["token"], True, True)["success"] is True

        r_work = bridge.pty_set_worker_mode("pane_1", True)
        assert bridge.ui_confirm_action(r_work["confirmation"]["token"], True, True)["success"] is True

        listed = bridge.ui_list_confirmation_prefs()
        assert listed["success"] is True
        ids = {p["action_id"] for p in listed["prefs"]}
        assert ids == {"auto_relay.enable", "worker_mode.enable"}

        fg1 = bridge.ui_forget_confirmation("auto_relay.enable")
        assert fg1["success"] is True and fg1["removed"] is True
        assert bridge.ui_forget_confirmation("auto_relay.enable")["removed"] is False

        # Al olvidar auto_relay.enable, vuelve a pedir confirmación
        bridge.pty_set_auto_relay("pane_1", False)
        assert bridge.pty_set_auto_relay("pane_1", True)["code"] == "CONFIRMATION_REQUIRED"

        # worker_mode.enable sigue recordado hasta forget_all
        assert [p["action_id"] for p in bridge.ui_list_confirmation_prefs()["prefs"]] == ["worker_mode.enable"]
        fg_all = bridge.ui_forget_all_confirmations()
        assert fg_all["success"] is True and fg_all["removed_count"] == 1
        assert bridge.ui_list_confirmation_prefs()["prefs"] == []

        bridge.pty_set_worker_mode("pane_1", False)
        assert bridge.pty_set_worker_mode("pane_1", True)["code"] == "CONFIRMATION_REQUIRED"
    finally:
        bridge._close_terminal_matrix()
        bridge.core_kernel.close()


# ---------------------------------------------------------------------------
# 4. Superficie MCP y puente JS
# ---------------------------------------------------------------------------


def test_no_mcp_surface_for_confirmations(tmp_path: Path) -> None:
    mcp_dir = Path(__file__).resolve().parents[1] / "src" / "mcp"
    forbidden = {"app_confirmations", "confirmations", "confirmations_ui"}
    for py_file in sorted(mcp_dir.rglob("*.py")):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    parts = set(alias.name.split("."))
                    assert not (parts & forbidden), f"{py_file} importa {alias.name}"
            elif isinstance(node, ast.ImportFrom) and node.module:
                parts = set(node.module.split("."))
                assert not (parts & forbidden), f"{py_file} importa desde {node.module}"
                for alias in node.names:
                    assert alias.name not in forbidden, f"{py_file} importa {alias.name} desde {node.module}"

    server = create_mcp_server(db_path=str(tmp_path / "mcp.db"))

    async def _check() -> None:
        tools = await server.list_tools()
        names = [t.name for t in tools]
        for name in names:
            low = name.lower()
            assert "confirm" not in low and "remember" not in low and "pref" not in low, name
        with pytest.raises(Exception):
            await server.call_tool("ui_confirm_action", {"token": "x", "accepted": True, "remember": True})

    anyio.run(_check)


def test_bridge_exposes_confirmation_methods_only_to_js() -> None:
    bridge = ApiBridge(db_manager=make_test_db())
    try:
        names = {fn.__name__ for fn in bridge.public_js_functions()}
        expected = {
            "ui_confirm_action",
            "ui_list_confirmation_prefs",
            "ui_forget_confirmation",
            "ui_forget_all_confirmations",
        }
        assert expected <= names
        assert "confirm_gate" not in names
        assert "confirmations_ui" not in names
        assert "confirmations" not in names
    finally:
        bridge._close_terminal_matrix()
        bridge.core_kernel.close()


# ---------------------------------------------------------------------------
# 5. Endurecimiento agy (X9) y rama discard vs release en relevo
# ---------------------------------------------------------------------------


def test_agy_remembered_grant_does_not_rewrite_global_file_without_dialog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr("src.app.ui_adapters.extensions_lab_ui.is_unsupported_terminal_platform", lambda: False)

    fake_agy = home / ".local" / "bin" / ("agy.cmd" if sys.platform == "win32" else "agy")
    fake_agy.parent.mkdir(parents=True, exist_ok=True)
    fake_agy.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    if sys.platform != "win32":
        fake_agy.chmod(fake_agy.stat().st_mode | stat.S_IXUSR)

    bridge = ApiBridge(db_manager=make_test_db())
    try:
        terminal = bridge.extensions_ui._get_terminal_connector()
        primary = getattr(terminal, "primary", terminal)
        plat = "win32" if sys.platform == "win32" else "linux"
        catalog = CliCatalogService(
            bridge.core_kernel.extension_repo,
            user_catalog_path=tmp_path / "sin_usuario.json",
            which=lambda name: str(fake_agy) if name in {"agy", "antigravity"} else None,
            platform=plat,
            home=str(home),
            environ={"SHELL": "/bin/sh"} if plat != "win32" else {"COMSPEC": "cmd.exe"},
        )
        store = PaneMcpConfigStore(tmp_path / "mcp-store")
        if isinstance(primary, NullAgentTerminalConnector) or not hasattr(primary, "_mcp_store"):
            monkeypatch.setattr(bridge.extensions_ui, "_catalog_service", lambda: catalog)
            monkeypatch.setattr(
                terminal,
                "spawn_session",
                lambda **kw: {
                    "success": True,
                    "degraded": False,
                    "pane_id": kw["pane_id"],
                    "session_id": f"{kw['pane_id']}-fake",
                    "mcp_injection": store.prepare(
                        catalog.get_spec(kw.get("cli_profile")),
                        session_id=f"{kw['pane_id']}-fake",
                        hub_url=kw.get("hub_url"),
                        token=kw.get("session_token"),
                    ).public(),
                },
            )
        else:
            primary.catalog_service = catalog
            primary.min_available_mb = 0
            primary._mcp_store = store

        cfg = home / ".gemini" / "config" / "mcp_config.json"
        dialog = _SpyDialog([True])
        bridge.confirmations.dialog = dialog

        # 1) Primer spawn: acepta -> recuerda "granted" y crea el archivo global.
        res1 = bridge.pty_spawn_pane(1, "antigravity", "implementer", None, 100, 28, True, str(tmp_path))
        assert res1["success"] is True and res1["mcp_injection"]["status"] == "injected"
        assert len(dialog.calls) == 1 and cfg.is_file()

        # 2) Mientras el archivo coincide con build_agy_entry(), otro spawn NO pregunta.
        res2 = bridge.pty_spawn_pane(2, "antigravity", "implementer", None, 100, 28, True, str(tmp_path))
        assert res2["success"] is True and res2["mcp_injection"]["status"] == "injected"
        assert len(dialog.calls) == 1

        # 3) Si el archivo global falta aunque consent == "granted", vuelve a exigir diálogo nativo; si rechaza, NO escribe.
        cfg.unlink()
        deny_spy = _SpyDialog([False])
        bridge.confirmations.dialog = deny_spy
        res3 = bridge.pty_spawn_pane(3, "antigravity", "implementer", None, 100, 28, True, str(tmp_path))
        assert res3["success"] is True and res3["mcp_injection"]["status"] == "needs_consent"
        assert len(deny_spy.calls) == 1 and not cfg.exists()

        # 4) Si la entrada existe pero difiere de build_agy_entry(), también exige diálogo nativo y no reescribe si se rechaza.
        tampered = {"mcpServers": {"mcp2p": {**build_agy_entry(), "args": ["-m", "evil.mod", "--hub-proxy-only"]}}}
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(json.dumps(tampered), encoding="utf-8")
        snapshot = cfg.read_bytes()
        deny_spy2 = _SpyDialog([False])
        bridge.confirmations.dialog = deny_spy2
        res4 = bridge.pty_spawn_pane(4, "antigravity", "implementer", None, 100, 28, True, str(tmp_path))
        assert res4["success"] is True and res4["mcp_injection"]["status"] == "needs_consent"
        assert len(deny_spy2.calls) == 1 and cfg.read_bytes() == snapshot
    finally:
        bridge._close_terminal_matrix()
        bridge.core_kernel.close()


def test_agy_install_cli_with_remembered_grant_asks_native_dialog_when_file_missing_or_differs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr("src.app.ui_adapters.extensions_lab_ui.is_unsupported_terminal_platform", lambda: False)

    bridge = ApiBridge(db_manager=make_test_db())
    try:
        terminal = bridge.extensions_ui._get_terminal_connector()
        plat = "win32" if sys.platform == "win32" else "linux"
        catalog = CliCatalogService(
            bridge.core_kernel.extension_repo,
            user_catalog_path=tmp_path / "sin_usuario.json",
            which=lambda name: f"/usr/bin/{name}" if name in {"curl", "bash", "powershell"} else None,
            platform=plat,
            home=str(home),
            environ={"SHELL": "/bin/sh"} if plat != "win32" else {"COMSPEC": "cmd.exe"},
        )
        monkeypatch.setattr(bridge.extensions_ui, "_catalog_service", lambda: catalog)
        monkeypatch.setattr(
            terminal,
            "spawn_session",
            lambda **kw: {"success": True, "pane_id": kw["pane_id"], "session_id": "inst-1", "pid": 99},
        )
        bridge.extensions_ui._set_agy_consent_state("granted")

        cfg = home / ".gemini" / "config" / "mcp_config.json"
        deny_spy = _SpyDialog([False])
        bridge.confirmations.dialog = deny_spy

        prep = bridge.pty_prepare_install("antigravity")
        req = bridge.pty_install_cli("antigravity", prep["confirmation_token"])
        assert req["code"] == "CONFIRMATION_REQUIRED"
        res = bridge.ui_confirm_action(req["confirmation"]["token"], True, False)
        assert res["success"] is True and res["code"] == "INSTALL_STARTED"
        # Archivo global faltante -> pidió diálogo nativo y, al rechazar, no escribió nada.
        assert len(deny_spy.calls) == 1 and not cfg.exists()
    finally:
        bridge._close_terminal_matrix()
        bridge.core_kernel.close()


def test_relay_discard_uses_app_confirmation_and_release_stays_native(tmp_path: Path) -> None:
    from src.mcp.tools import MCPToolsHandler

    bridge = ApiBridge(db_manager=make_test_db())
    handler = None
    try:
        proj = bridge.db.create_project("P", str(tmp_path / "p"))["project_uuid"]
        reg = bridge.extensions_ui._get_pane_registry()
        p1 = reg.register_pane("pane_1", project_uuid=proj, cli_profile="custom_shell")
        reg.register_pane("pane_2", project_uuid=proj, cli_profile="custom_shell")
        handler = MCPToolsHandler(kernel=bridge.core_kernel, owns_kernel=False, pane_registry=reg)
        tok1 = p1["session_token"]

        out1 = json.loads(
            handler.send_message(
                session_token=tok1,
                target_pane_id="pane_2",
                text="mensaje 1 para descartar",
                confirm_with_human=True,
            )
        )
        out2 = json.loads(
            handler.send_message(
                session_token=tok1,
                target_pane_id="pane_2",
                text="mensaje 2 para liberar",
                confirm_with_human=True,
            )
        )

        spy = _SpyDialog([False, True])
        bridge.confirmations.dialog = spy

        # Descartar: superficie propia, no recordable, nunca llama al diálogo nativo
        req_disc = bridge.relay_resolve_parked(out1["msg_id"], "discard")
        assert req_disc["code"] == "CONFIRMATION_REQUIRED"
        assert req_disc["confirmation"]["action_id"] == "relay.discard_parked"
        assert req_disc["confirmation"]["rememberable"] is False
        assert spy.calls == []
        res_disc = bridge.ui_confirm_action(req_disc["confirmation"]["token"], True, True)
        assert res_disc["success"] is True and res_disc["state"] == "discarded"
        assert bridge.db.get_setting("confirm.remember.relay.discard_parked") is None

        # Liberar: sigue usando el diálogo nativo con el texto exacto
        declined_rel = bridge.relay_resolve_parked(out2["msg_id"], "release")
        assert declined_rel["code"] == "CONFIRMATION_DECLINED"
        assert len(spy.calls) == 1 and "mensaje 2 para liberar" in spy.calls[0][1]
        accepted_rel = bridge.relay_resolve_parked(out2["msg_id"], "release")
        assert accepted_rel["success"] is True and accepted_rel["state"] == "queued"
        assert len(spy.calls) == 2
    finally:
        if handler is not None:
            handler.close()
        bridge._close_terminal_matrix()
        bridge.core_kernel.close()
