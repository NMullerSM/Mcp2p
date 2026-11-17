"""EGM-105 (Ola 3, F1.2): el token de un panel solo vale desde el arbol de procesos de ese panel.

Pruebas con procesos REALES que hablan HTTP con un Hub real (Linux y Windows). Antes de la Ola 3 el Hub solo comprobaba
el *bearer*: un proceso del mismo usuario que leia `MCP2P_SESSION_TOKEN` de `/proc/<pid>/environ` actuaba como el panel
desde cualquier sitio (M8 del dossier 37).
"""

from __future__ import annotations

import asyncio
import os
import socket
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

from helpers.relay_lab import RelayLab, call_from_foreign_process, install_pty_write_spy
from src.adapters.identity.peer_process import PsutilPeerProcessResolver, is_loopback_endpoint
from src.core.ports.identity import PEER_MISMATCH, PEER_VERIFIED, ProcessIdentity
from src.mcp.pane_registry import PaneRegistry
from src.mcp.peer_binding import (
    PEER_PROCESS_MISMATCH,
    PeerBinding,
    PeerBindingVerifier,
    get_current_peer_binding,
    peer_binding_context,
    relay_peer_allowed,
)

MISMATCH = PEER_PROCESS_MISMATCH


@pytest.fixture(autouse=True)
def _no_system_pty_writes(monkeypatch):
    """Invariante F0/D12 en toda la suite nueva: ninguna escritura del sistema en un PTY."""
    calls = install_pty_write_spy(monkeypatch)
    yield calls
    assert calls == [], f"el relevo escribio en un PTY: {calls}"


@pytest.fixture
def lab(tmp_path: Path, _no_system_pty_writes):
    lab = RelayLab(tmp_path, pty_calls=_no_system_pty_writes)
    try:
        yield lab
    finally:
        lab.close()


def _ok(res: Dict[str, Any]) -> bool:
    return res.get("success") is True


def test_token_used_from_its_own_pane_tree_is_accepted(lab: RelayLab) -> None:
    res = lab.root(1).call(lab.url, lab.token(1), "list_swarm_panes")
    assert _ok(res), res
    assert {p["node_uuid"] for p in res["panes"]} == {lab.node(1), lab.node(2)}


def test_stolen_token_from_another_pane_tree_is_rejected(lab: RelayLab) -> None:
    # M8: la shell del panel 2 lee el token del panel 1 y lo usa desde SU arbol
    res = lab.root(2).call(lab.url, lab.token(1), "list_swarm_panes")
    assert res.get("code") == MISMATCH and res.get("success") is False
    # la respuesta no da pistas (ni pids, ni motivo, ni paneles)
    assert set(res) == {"success", "code", "error"}
    # y el panel 2 con SU token sigue funcionando
    assert _ok(lab.root(2).call(lab.url, lab.token(2), "list_swarm_panes"))


def test_stolen_token_from_a_foreign_process_is_rejected(lab: RelayLab) -> None:
    res = call_from_foreign_process(lab.url, lab.token(1), "list_swarm_panes")
    assert res.get("code") == MISMATCH


def test_relay_tools_enforce_while_legacy_tools_only_warn(lab: RelayLab) -> None:
    for tool, args in (
        ("send_pane_message", {"target_pane_id": "pane_2", "message": "hola"}),
        ("await_next_assignment", {"timeout_ms": 0}),
    ):
        res = call_from_foreign_process(lab.url, lab.token(1), tool, args)
        assert res.get("code") == MISMATCH, (tool, res)
    legacy = call_from_foreign_process(lab.url, lab.token(1), "get_kanban_board", {"project_uuid": lab.proj})
    assert legacy.get("success") is True
    assert legacy["peer_warning"] == {"code": MISMATCH, "mode": "warn"}
    inside = lab.root(1).call(lab.url, lab.token(1), "get_kanban_board", {"project_uuid": lab.proj})
    assert "peer_warning" not in inside


def test_orphaned_descendant_is_rejected(lab: RelayLab, tmp_path: Path) -> None:
    """Un descendiente cuyo padre termina queda fuera del arbol (POSIX: lo adopta init/subreaper; Windows: su ppid
    apunta a un proceso muerto). Equivale a escapar del arbol sin `setsid`."""
    res = lab.root(1).call_from_orphan(
        lab.url, lab.token(1), "list_swarm_panes", {}, tmp_path / "orphan.json", new_session=False
    )
    assert res.get("code") == MISMATCH


@pytest.mark.skipif(sys.platform == "win32", reason="setsid es POSIX; en Windows el equivalente es el huerfano")
def test_daemonized_with_setsid_is_rejected(lab: RelayLab, tmp_path: Path) -> None:
    res = lab.root(1).call_from_orphan(
        lab.url, lab.token(1), "list_swarm_panes", {}, tmp_path / "daemon.json", new_session=True
    )
    assert res.get("code") == MISMATCH


def test_reused_pid_with_another_create_time_is_rejected(lab: RelayLab) -> None:
    ident = lab.registry.process_resolver.process_identity(lab.root(1).pid)
    assert ident is not None
    # la raiz «murio» y su pid lo tiene ahora otro proceso: el create_time registrado ya no coincide
    lab.registry.bind_process("pane_1", lab.root(1).pid, create_time=ident.create_time - 1000.0)
    res = lab.root(1).call(lab.url, lab.token(1), "list_swarm_panes")
    assert res.get("code") == MISMATCH


def test_pane_without_bound_process_fails_closed(lab: RelayLab) -> None:
    pane = lab.registry.register_pane("pane_3", project_uuid=lab.proj)
    res = lab.root(1).call(lab.url, pane["session_token"], "list_swarm_panes")
    assert res.get("code") == MISMATCH


def _ipv6_loopback_available() -> bool:
    if not socket.has_ipv6:
        return False
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as s:
            s.bind(("::1", 0))
        return True
    except OSError:
        return False


@pytest.mark.skipif(not _ipv6_loopback_available(), reason="sin loopback IPv6")
def test_ipv6_loopback_hub_binds_tokens_to_the_pane_tree(tmp_path: Path) -> None:
    lab = RelayLab(tmp_path, host="::1")
    try:
        assert lab.url.startswith("http://[::1]:")
        assert _ok(lab.root(1).call(lab.url, lab.token(1), "list_swarm_panes"))
        assert lab.root(2).call(lab.url, lab.token(1), "list_swarm_panes").get("code") == MISMATCH
    finally:
        lab.close()


# -- unidades: resolvedor, verificador y contexto ---------------------------------------------------------------


def test_non_loopback_client_is_rejected_without_inspecting_processes() -> None:
    class _NoPsutil:
        def __getattr__(self, name: str) -> Any:  # si se consultara psutil, la prueba fallaria
            raise AssertionError(f"no debe consultar psutil ({name})")

    resolver = PsutilPeerProcessResolver(psutil_module=_NoPsutil())
    root = ProcessIdentity(os.getpid(), 0.0)
    for client in (("10.0.0.5", 4242), ("192.168.1.20", 50000), ("::ffff:10.1.2.3", 1), ("2001:db8::1", 2)):
        check = resolver.check(client, ("127.0.0.1", 8765), root)
        assert check.status == PEER_MISMATCH and check.reason == "non_loopback_client"
    assert is_loopback_endpoint(("::ffff:127.0.0.1", 1)) and is_loopback_endpoint(("::1", 1))
    assert not is_loopback_endpoint(None)


def test_in_process_http_client_from_the_hub_itself_is_not_the_pane(lab: RelayLab) -> None:
    """El propio proceso del Hub (pytest aqui) tampoco es el arbol del panel."""
    import httpx2
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    import json

    async def _go() -> Dict[str, Any]:
        async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {lab.token(1)}"}) as http_client:
            async with streamable_http_client(lab.url, http_client=http_client) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    out = await session.call_tool("list_swarm_panes", {})
                    return json.loads(out.content[0].text)

    assert asyncio.run(_go()).get("code") == MISMATCH


def test_verifier_caches_only_successes_and_revalidates() -> None:
    calls: List[str] = []

    class _Resolver:
        def __init__(self) -> None:
            self.verdict = PEER_VERIFIED
            self.still = True

        def check(self, client, server, root):
            from src.core.ports.identity import PeerCheck

            calls.append("check")
            return PeerCheck(self.verdict, peer=ProcessIdentity(1, 1.0) if self.verdict == PEER_VERIFIED else None)

        def still_bound(self, peer, client, server, root):
            calls.append("still")
            return self.still

    resolver = _Resolver()
    verifier = PeerBindingVerifier(resolver)
    reg = PaneRegistry(process_resolver=resolver)
    rec_dict = reg.register_pane("pane_1", node_uuid="n1")
    rec = reg.get_pane("pane_1")
    rec.pid, rec.pid_create_time = 123, 5.0
    ends = (("127.0.0.1", 5000), ("127.0.0.1", 8765))
    assert verifier.verify(rec, *ends).verified and calls == ["check"]
    assert verifier.verify(rec, *ends).verified and calls == ["check", "still"]
    resolver.still = False  # el proceso dejo de ser dueño de la conexion: se recalcula
    resolver.verdict = PEER_MISMATCH
    assert verifier.verify(rec, *ends).status == PEER_MISMATCH
    assert verifier.verify(rec, *ends).status == PEER_MISMATCH and calls[-1] == "check"
    assert rec_dict["node_uuid"] == "n1"


def test_relay_allowance_follows_the_binding_context() -> None:
    assert get_current_peer_binding() is None and relay_peer_allowed() is True  # en proceso
    with peer_binding_context(PeerBinding(PEER_VERIFIED, "pane_1")):
        assert relay_peer_allowed() is True
    for status in (PEER_MISMATCH, "unbound", "unavailable"):
        with peer_binding_context(PeerBinding(status, "pane_1")):
            assert relay_peer_allowed() is False
    assert get_current_peer_binding() is None


def test_call_helper_accepts_tokens_that_start_with_a_dash() -> None:
    # Los tokens urlsafe pueden empezar por "-" (1 de cada 64): sin "--" argparse los tomaba por una opcion y
    # las pruebas con procesos reales fallaban de forma intermitente con "arguments are required: args".
    import json as _json
    import subprocess as _sp
    import sys as _sys
    from tests.helpers.relay_lab import CALL

    proc = _sp.run([_sys.executable, str(CALL), "--", "http://127.0.0.1:1/mcp", "-token-con-guion", "list_swarm_panes", "{}"],
                   capture_output=True, text=True, timeout=60)
    payload = _json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["ok"] is False and "usage" not in payload["error"].lower()
