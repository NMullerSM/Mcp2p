"""F0 «desarmar» del relevo (dossier 37 §8): el relevo no escribe en ningun PTY y el mensaje es un dato.

Pruebas adversarias con paneles `/bin/sh` reales: un mensaje hostil (control, ESC, Ctrl-U, CR, OSC) enviado
entre paneles con Auto-Relay ACTIVADO no debe escribir un solo byte en el PTY destino.
"""

from __future__ import annotations

import ast
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pytest

from src.a2a.bus import A2ABus
from src.adapters.terminal.pty_host import PosixPtyHostConnector
from src.core.events import DomainEventBus
from src.core.factory import ApplicationFactory
from src.core.kernel import CoreKernel
from src.core.services import SwarmRelayOrchestrator
from src.core.services.swarm_relay_service import MAX_DIRECT_MESSAGE_BYTES
from src.mcp.pane_registry import PaneRegistry
from src.mcp.tools import MCPToolsHandler
from src.storage.db import DatabaseManager

ROOT = Path(__file__).resolve().parents[1]
posix_only = pytest.mark.skipif(
    sys.platform == "win32" or not PosixPtyHostConnector.is_available(),
    reason="Requiere PTY POSIX con /bin/sh para probar secuencias de control en shell real",
)

SENTINEL = "SENTINEL_PWN_F0"


def _hostile(marker: Path) -> str:
    """Control C/D, ESC, Ctrl-U, CR y secuencias OSC (titulo, estado 7501, portapapeles) + un comando."""
    return (
        "\x03\x04\x1b[2K\x1b\x15\r"
        f"touch {marker}\r"
        f"echo {SENTINEL}\r\n"
        "\x1b]0;pwn\x07\x1b]7501;idle\x07\x1b]52;c;aGk=\x07\x1b]7501;working\x1b\\"
    )


class _SpyConnector:
    """Envuelve un conector real y anota toda escritura en cualquier PTY."""

    def __init__(self, inner: PosixPtyHostConnector) -> None:
        self._inner = inner
        self.writes: List[Any] = []

    def send_input(self, pane_id: Any, data: Any) -> Dict[str, Any]:
        self.writes.append((str(pane_id), data))
        return self._inner.send_input(pane_id, data)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class Swarm:
    def __init__(self, tmp_path: Path, *, with_ptys: bool) -> None:
        self.tmp_path = tmp_path
        self.db = DatabaseManager(db_path=str(tmp_path / "f0.db"))
        self.proj = self.db.create_project("Proyecto F0", str(tmp_path / "repo"))["project_uuid"]
        self.event_bus = DomainEventBus()
        self.bus = A2ABus(self.db)
        self.registry = PaneRegistry()
        # Ola 2: los paneles pertenecen al proyecto y el Auto-Relay es por panel (activo en ambos aqui).
        self.tok1 = self.registry.register_pane(
            "pane_1", node_uuid="node-1", session_token="tok-1", project_uuid=self.proj, auto_relay_enabled=True
        )["session_token"]
        self.tok2 = self.registry.register_pane(
            "pane_2", node_uuid="node-2", session_token="tok-2", pid=4242, project_uuid=self.proj,
            auto_relay_enabled=True,
        )["session_token"]
        self.pty = PosixPtyHostConnector(max_panes=2) if with_ptys else None
        self.spy = _SpyConnector(self.pty) if self.pty is not None else None
        self.os_writes: List[Any] = []
        self._os_writes_lock = threading.Lock()
        self._real_os_write: Optional[Callable[[int, Any], int]] = None
        self.master_fds: set[int] = set()
        if self.pty is not None:
            for pane in ("pane_1", "pane_2"):
                res = self.pty.spawn_session(pane, command=["/bin/sh"], cwd=tmp_path, use_trampoline=False)
                assert res["success"] is True
            self.master_fds = {
                s.master_fd
                for s in self.pty._sessions.values()
                if getattr(s, "master_fd", None) is not None
            }
            assert len(self.master_fds) == 2
            real_os_write = os.write
            master_fds = self.master_fds
            os_writes = self.os_writes
            lock = self._os_writes_lock

            def _spy_os_write(fd: int, data: Any) -> int:
                if fd in master_fds:
                    with lock:
                        os_writes.append((fd, bytes(data)))
                return real_os_write(fd, data)

            self._real_os_write = real_os_write
            os.write = _spy_os_write  # type: ignore[assignment]
        try:
            self.orch = SwarmRelayOrchestrator(event_bus=self.event_bus, bus=self.bus, pane_registry=self.registry)
            self.kernel = ApplicationFactory.create_mcp_kernel(
                db_manager=self.db, bus=self.bus, event_bus=self.event_bus
            )
            if self.spy is not None:
                self.kernel.register_connector(CoreKernel.PORT_AGENT_TERMINAL, self.spy)
            self.handler = MCPToolsHandler(
                kernel=self.kernel, owns_kernel=False, pane_registry=self.registry, relay_orchestrator=self.orch
            )
        except Exception:
            if self._real_os_write is not None:
                os.write = self._real_os_write  # type: ignore[assignment]
                self._real_os_write = None
            raise

    def send(self, message: str, *, token: str = "tok-1", target: str = "node-2", **extra: Any) -> Dict[str, Any]:
        return json.loads(
            self.handler.send_pane_message(
                project_uuid=self.proj, target_node_uuid=target, message=message, session_token=token, **extra
            )
        )

    def pull(self, *, token: str = "tok-2", timeout_ms: int = 0) -> Dict[str, Any]:
        return json.loads(
            self.handler.await_next_assignment(project_uuid=self.proj, session_token=token, timeout_ms=timeout_ms)
        )

    def outbox_count(self) -> int:
        with self.db.get_connection() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM outbox_events").fetchone()[0])

    def poll_all(self, pane: str) -> str:
        assert self.pty is not None
        res = self.pty.poll_output(pane, 0)
        return str(res.get("text") or "")

    def close(self) -> None:
        try:
            self.handler.close()
            self.orch.close()
            self.kernel.close()
            if self.pty is not None:
                shutdown = getattr(self.pty, "shutdown_all", None) or getattr(self.pty, "close_all", None)
                if callable(shutdown):
                    shutdown()
                else:
                    for s in list(getattr(self.pty, "_sessions", {}).values()):
                        self.pty._shutdown_session(s)
        finally:
            if self._real_os_write is not None:
                os.write = self._real_os_write  # type: ignore[assignment]
                self._real_os_write = None


@pytest.fixture
def swarm(tmp_path: Path):
    s = Swarm(tmp_path, with_ptys=True)
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def swarm_no_pty(tmp_path: Path):
    s = Swarm(tmp_path, with_ptys=False)
    try:
        yield s
    finally:
        s.close()


# ----------------------------------------------------------------------------------------------------------
# F0.1 Nunca escribir en el PTY desde el relevo
# ----------------------------------------------------------------------------------------------------------


@posix_only
def test_hostile_message_between_real_shell_panes_writes_nothing_to_the_target_pty(swarm: Swarm, tmp_path: Path) -> None:
    marker = tmp_path / "pwned"
    time.sleep(0.4)  # deja que /bin/sh imprima su prompt inicial
    baseline = swarm.poll_all("pane_2")

    out = swarm.send(_hostile(marker), card_id="CARD-F0")
    assert out["success"] is True, out
    # y un aviso del Auto-Relay (evento de dominio dirigido a pane_2) tampoco debe tocar la terminal
    from src.core.domain.context import DomainEvent

    swarm.event_bus.publish(
        DomainEvent(
            event_type="kanban.card_assigned",
            project_uuid=swarm.proj,
            payload={"card_id": "CARD-F0", "target_node_uuid": "node-2", "sender_node_uuid": "node-1"},
        )
    )

    time.sleep(0.8)
    after = swarm.poll_all("pane_2")
    assert swarm.spy.writes == [], "el relevo escribio en un PTY"
    assert swarm.os_writes == [], "el relevo escribio directo por os.write en master_fd"
    assert SENTINEL not in after and "pwned" not in after and "7501" not in after
    assert not marker.exists(), "el mensaje hostil se ejecuto en la shell destino"
    assert after.startswith(baseline[:20])  # no se reescribio la pantalla

    # control: el PTY destino sigue vivo y un humano SI puede escribir (la prueba no esta ciega)
    assert swarm.pty.send_input("pane_2", "echo CONTROL_OK_F0\n")["success"] is True
    deadline, seen = time.monotonic() + 5, ""
    while "CONTROL_OK_F0" not in seen and time.monotonic() < deadline:
        seen = swarm.poll_all("pane_2")
        time.sleep(0.05)
    assert "CONTROL_OK_F0" in seen
    assert any(b"CONTROL_OK_F0" in payload for _, payload in swarm.os_writes)


@posix_only
def test_hostile_message_arrives_whole_as_data_with_separate_provenance(swarm_no_pty: Swarm, tmp_path: Path) -> None:
    swarm = swarm_no_pty
    hostile = _hostile(tmp_path / "pwned")
    sent = swarm.send(hostile, card_id="CARD-F0")
    assert sent["success"] is True and sent["message_uuid"]

    got = swarm.pull()
    assert got["success"] is True and got["has_messages"] is True
    (msg,) = got["messages"]
    assert msg["text"] == hostile  # integro: ni filtrado ni prefijado
    assert msg["id"] == sent["message_uuid"]
    assert msg["kind"] == "direct_message"
    assert msg["from"]["node_uuid"] == "node-1" and msg["from"]["pane_id"] == "pane_1"
    assert msg["from"]["verified_by"] == "hub_pane_session"
    assert msg["to"]["node_uuid"] == "node-2"
    assert "MCP2P" not in msg["text"] and isinstance(msg["timestamp"], float)
    # se entrega una sola vez
    assert swarm.pull()["messages"] == []
    # y el remitente no puede dictar su procedencia: el node_uuid declarado distinto se rechaza
    spoof = json.loads(
        swarm.handler.send_pane_message(
            project_uuid=swarm.proj, target_node_uuid="node-2", message="x", node_uuid="node-2", session_token="tok-1"
        )
    )
    assert spoof["code"] == "NODE_UUID_SPOOFING_FORBIDDEN"


# ----------------------------------------------------------------------------------------------------------
# F0.3 Limites: 8 KiB y sin cola P2P
# ----------------------------------------------------------------------------------------------------------


def test_message_size_limit_is_8_kib_in_bytes(swarm_no_pty: Swarm) -> None:
    swarm = swarm_no_pty
    assert MAX_DIRECT_MESSAGE_BYTES == 8192
    assert swarm.send("a" * 8192)["success"] is True
    too_big = swarm.send("a" * 8193)
    assert too_big["success"] is False and too_big["code"] == "MESSAGE_TOO_LARGE"
    multibyte = swarm.send("é" * 4097)  # 8194 bytes en UTF-8 aunque solo 4097 caracteres
    assert multibyte["code"] == "MESSAGE_TOO_LARGE"
    msgs = swarm.pull()["messages"]
    assert [len(m["text"]) for m in msgs] == [8192]  # lo rechazado no queda en la bandeja


def test_local_direct_messages_never_enter_the_p2p_outbox(swarm_no_pty: Swarm) -> None:
    swarm = swarm_no_pty
    before = swarm.outbox_count()
    for i in range(3):
        assert swarm.send(f"mensaje local {i}")["success"] is True
    assert swarm.outbox_count() == before
    # la auditoria local SI conserva la fila (solo el outbox queda excluido)
    rows = swarm.db.get_recent_a2a_messages(swarm.proj, limit=10)
    assert sum(1 for r in rows if r.get("sender_node_uuid") == "node-1") == 3
    # y el bus rechaza meter esta clase de señal en el outbox aunque se llame directamente
    swarm.bus.send_informative(swarm.proj, "node-1", "DIRECT_MESSAGE", {"message": "x"})
    assert swarm.outbox_count() == before
    swarm.bus.send_informative(swarm.proj, "node-1", "LEASE_RENEWED", {"k": 1})
    assert swarm.outbox_count() == before + 1  # el resto de señales informativas sigue sincronizandose


def test_inbox_is_bounded_and_rejects_instead_of_dropping(swarm_no_pty: Swarm) -> None:
    swarm = swarm_no_pty
    from src.core.services.swarm_relay_service import MAX_INBOX_DEPTH

    codes = {swarm.send(f"m{i}")["success"] for i in range(MAX_INBOX_DEPTH)}
    assert codes == {True}
    full = swarm.send("uno mas")
    assert full["success"] is False and full["code"] == "INBOX_FULL"


# ----------------------------------------------------------------------------------------------------------
# Invariante estructural: swarm_relay_service.py nunca vuelve a escribir en un PTY
# ----------------------------------------------------------------------------------------------------------

_FORBIDDEN_CALLS = {"send_input", "write", "os.write", "writelines", "send_keys", "pty_write_input"}


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        base = func.value
        if isinstance(base, ast.Name):
            return f"{base.id}.{func.attr}"
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def test_ast_swarm_relay_service_never_writes_to_a_pty() -> None:
    path = ROOT / "src" / "core" / "services" / "swarm_relay_service.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name in _FORBIDDEN_CALLS or name.split(".")[-1] in {"send_input", "write", "writelines"}:
                offenders.append((node.lineno, name))
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = [a.name for a in node.names] + [getattr(node, "module", "") or ""]
            if any(m.startswith(("pty", "termios", "fcntl")) for m in mods):
                offenders.append((node.lineno, "import " + ",".join(mods)))
        if isinstance(node, ast.Attribute) and node.attr in {"terminal_connector", "send_input"}:
            offenders.append((node.lineno, node.attr))
    assert offenders == [], f"swarm_relay_service.py vuelve a tocar un PTY: {offenders}"


def test_ast_check_actually_detects_a_pty_write() -> None:
    """El detector no esta ciego: sobre un modulo con `connector.send_input(...)` si falla."""
    tree = ast.parse("def f(connector):\n    connector.send_input(1, 'x')\n    os.write(3, b'x')\n")
    names = [_call_name(n) for n in ast.walk(tree) if isinstance(n, ast.Call)]
    assert "connector.send_input" in names and "os.write" in names


# ----------------------------------------------------------------------------------------------------------
# F0.2 Fallo cerrado fuera del Hub
# ----------------------------------------------------------------------------------------------------------


def _clean_env() -> Dict[str, str]:
    import os

    return {k: v for k, v in os.environ.items() if not k.startswith("MCP2P_HUB") and k != "MCP2P_SESSION_TOKEN"}


def test_standalone_server_answers_hub_required_and_persists_no_forged_sender(tmp_path: Path, monkeypatch) -> None:
    from src.mcp.server import create_mcp_server

    monkeypatch.delenv("MCP2P_SESSION_TOKEN", raising=False)
    srv = create_mcp_server(db_path=str(tmp_path / "standalone.db"))
    handler = srv.mcp2p_handler
    try:
        proj = handler.db.create_project("P", str(tmp_path / "repo"))["project_uuid"]
        tools = srv._tool_manager._tools
        assert {"send_pane_message", "list_swarm_panes", "await_next_assignment"} <= set(tools)
        outs = [
            tools["send_pane_message"].fn(
                project_uuid=proj, target_node_uuid="pane-x", message="hola", node_uuid="human:owner"
            ),
            tools["list_swarm_panes"].fn(project_uuid=proj),
            tools["await_next_assignment"].fn(node_uuid="human:owner", timeout_ms=0),
        ]
        for raw in outs:
            parsed = json.loads(raw)
            assert parsed["success"] is False and parsed["code"] == "HUB_REQUIRED", parsed
        # EGM-106: el remitente autodeclarado no se persiste en a2a_messages
        assert handler.db.get_recent_a2a_messages(proj, limit=10) == []
    finally:
        handler.close()


def test_registry_without_a_pane_token_is_also_hub_required(swarm_no_pty: Swarm, monkeypatch) -> None:
    monkeypatch.delenv("MCP2P_SESSION_TOKEN", raising=False)
    h = swarm_no_pty.handler
    for raw in (
        h.send_pane_message(project_uuid=swarm_no_pty.proj, target_node_uuid="node-2", message="x", node_uuid="node-1"),
        h.await_next_assignment(node_uuid="node-2"),
        h.list_swarm_panes(project_uuid=swarm_no_pty.proj),
    ):
        assert json.loads(raw)["code"] == "HUB_REQUIRED"
    # con un token valido las tres funcionan
    assert json.loads(h.list_swarm_panes(project_uuid=swarm_no_pty.proj, session_token="tok-1"))["success"] is True


@pytest.mark.parametrize(
    "env,expect_error",
    [
        ({}, False),  # modo independiente explicito
        ({"MCP2P_HUB_URL": "http://127.0.0.1:9", "MCP2P_SESSION_TOKEN": "t"}, False),  # proxy valido
        ({"MCP2P_SESSION_TOKEN": "t"}, True),  # falta la URL
        ({"MCP2P_HUB_URL": "http://127.0.0.1:9"}, True),  # falta el token
        ({"MCP2P_HUB_URL": "http://example.com:9", "MCP2P_SESSION_TOKEN": "t"}, True),  # no loopback
        ({"MCP2P_HUB_URL": "https://127.0.0.1:9", "MCP2P_SESSION_TOKEN": "t"}, True),  # no http
    ],
)
def test_hub_proxy_misconfiguration_only_when_proxy_was_intended(env: Dict[str, str], expect_error: bool) -> None:
    from src.mcp.stdio_proxy import hub_proxy_misconfiguration

    assert (hub_proxy_misconfiguration(env) is not None) is expect_error


def test_stdio_server_fails_closed_when_proxy_intended_but_hub_url_invalid() -> None:
    import subprocess

    for extra in (
        {"MCP2P_SESSION_TOKEN": "tok"},
        {"MCP2P_HUB_URL": "https://evil.example:1/mcp", "MCP2P_SESSION_TOKEN": "tok"},
    ):
        proc = subprocess.run(
            [sys.executable, "-m", "src.mcp.server", "--transport", "stdio"],
            env={**_clean_env(), **extra},
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=30,
            input="",
        )
        assert proc.returncode == 2, (proc.returncode, proc.stderr[-400:])
        assert "No se arranca un servidor independiente" in proc.stderr
        assert proc.stdout == ""


def test_explicit_standalone_stdio_server_still_starts_without_hub_variables(tmp_path: Path) -> None:
    import subprocess

    proc = subprocess.Popen(
        [sys.executable, "-m", "src.mcp.server", "--transport", "stdio", "--db-path", str(tmp_path / "s.db")],
        env=_clean_env(),
        cwd=str(ROOT),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        time.sleep(2.0)
        assert proc.poll() is None, "el modo independiente explicito no debe romperse"
    finally:
        proc.kill()
        proc.wait(timeout=10)


# ----------------------------------------------------------------------------------------------------------
# F0.4 Ocultar lo que no debe verse
# ----------------------------------------------------------------------------------------------------------


def test_list_swarm_panes_hides_pid_keystroke_and_tokens(swarm_no_pty: Swarm) -> None:
    swarm = swarm_no_pty
    swarm.registry.record_human_keystroke("pane_2")
    raw = swarm.handler.list_swarm_panes(project_uuid=swarm.proj, session_token="tok-1")
    out = json.loads(raw)
    assert out["success"] is True and out["count"] == 2
    for pane in out["panes"]:
        assert not {"pid", "last_human_keystroke_at", "session_token"} & set(pane)
    # ninguna respuesta del flujo de relevo filtra pid, tokens ni telemetria de tecleo
    responses = [
        raw,
        swarm.handler.send_pane_message(
            project_uuid=swarm.proj, target_node_uuid="node-2", message="hola", session_token="tok-1"
        ),
        swarm.handler.await_next_assignment(project_uuid=swarm.proj, session_token="tok-2"),
    ]
    for text in responses:
        for secret in ("tok-1", "tok-2", "4242", "pid", "keystroke"):
            assert secret not in text, f"{secret!r} aparece en una respuesta MCP"


# ----------------------------------------------------------------------------------------------------------
# F0.5 Activar Auto-Relay exige confirmacion NATIVA (patron EGM-74); apagarlo no pregunta
# ----------------------------------------------------------------------------------------------------------


@pytest.fixture
def bridge(monkeypatch: pytest.MonkeyPatch):
    from conftest import make_test_db
    from src.app.api_bridge import ApiBridge

    monkeypatch.delenv("MCP2P_TEST_AUTO_CONFIRM", raising=False)
    b = ApiBridge(db_manager=make_test_db())
    try:
        yield b
    finally:
        b._close_terminal_matrix()
        b.core_kernel.close()


class _Dialog:
    def __init__(self, answers: List[Any]) -> None:
        self.answers = list(answers)
        self.calls: List[Any] = []

    def __call__(self, title: str, message: str) -> Any:
        self.calls.append((title, message))
        return self.answers.pop(0) if self.answers else False


def test_enabling_auto_relay_needs_app_confirmation_and_disabling_does_not(bridge) -> None:
    # D2: el Auto-Relay es por panel; la firma global antigua (un solo booleano) se rechaza.
    registry = bridge.extensions_ui._get_pane_registry()
    registry.register_pane("pane_1", cli_profile="custom_shell")
    assert bridge.pty_set_auto_relay(True)["code"] == "INVALID_ARGUMENT"
    assert bridge.pty_set_auto_relay("pane_5", True)["code"] == "PANE_NOT_FOUND"

    # WP-9: activar Auto-Relay usa dialogo propio recordable (CONFIRMATION_REQUIRED -> ui_confirm_action)
    dialog = _Dialog([])
    bridge.confirmations.dialog = dialog
    req1 = bridge.pty_set_auto_relay("pane_1", True)
    assert req1["success"] is False and req1["code"] == "CONFIRMATION_REQUIRED"
    assert req1["confirmation"]["action_id"] == "auto_relay.enable"
    assert req1["confirmation"]["rememberable"] is True
    assert dialog.calls == []
    assert registry.get_pane("pane_1").auto_relay_enabled is False

    declined = bridge.ui_confirm_action(req1["confirmation"]["token"], False, False)
    assert declined["success"] is False and declined["code"] == "CONFIRMATION_DECLINED"
    assert registry.get_pane("pane_1").auto_relay_enabled is False

    req2 = bridge.pty_set_auto_relay("pane_1", True)
    assert req2["code"] == "CONFIRMATION_REQUIRED"
    message = req2["confirmation"]["message"]
    assert "bandeja" in message and "ninguna terminal" in message and "pane_1" in message

    accepted = bridge.ui_confirm_action(req2["confirmation"]["token"], True, False)
    assert accepted["success"] is True and accepted["auto_relay_enabled"] is True
    assert accepted["pane_id"] == "pane_1"
    assert "key_encrypted" not in accepted and "deferred_count" not in accepted  # Gate 0c ya no aplica

    off = bridge.pty_set_auto_relay("pane_1", False)
    assert off["success"] is True and off["auto_relay_enabled"] is False
    assert dialog.calls == []  # ni activar ni apagar abren dialogo nativo
