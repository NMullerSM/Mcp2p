"""Cadena causal sellada por el Hub (F1.1 + F1.4, Ola 3): sustituye al Loop Breaker por `card_id` (EGM-93)."""

from __future__ import annotations

import pytest

from src.core.domain.identity import TrustLevel
from src.core.domain.relay_chain import ChainTracker


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000_000

    def __call__(self) -> int:
        return self.now


def _tracker(**kw):
    clock = _Clock()
    ids = iter(f"chain-{i}" for i in range(1000))
    return ChainTracker(clock_ms=clock, new_chain_id=lambda: next(ids), **kw), clock


def _deliver(tr: ChainTracker, to: str, link) -> None:
    tr.observe_delivery(to, chain_id=link.chain_id, hop=link.hop, trust=TrustLevel.PANE_AUTHENTICATED)


def test_a_b_a_allowed_and_a_b_a_b_exceeds_depth_one() -> None:
    tr, _ = _tracker()
    l1 = tr.next_link("A")  # A -> B
    assert (l1.hop, l1.exceeded, l1.inherited) == (0, False, False)
    _deliver(tr, "B", l1)
    l2 = tr.next_link("B")  # B -> A (respuesta automatica)
    assert (l2.chain_id, l2.hop, l2.exceeded) == (l1.chain_id, 1, False)
    _deliver(tr, "A", l2)
    l3 = tr.next_link("A")  # A -> B otra vez
    assert (l3.chain_id, l3.hop, l3.exceeded) == (l1.chain_id, 2, True)


def test_depth_is_configurable() -> None:
    tr, _ = _tracker(max_depth=3)
    link = tr.next_link("A")
    sender, receiver = "A", "B"
    for expected in range(1, 5):
        _deliver(tr, receiver, link)
        sender, receiver = receiver, sender
        link = tr.next_link(sender)
        assert link.hop == expected and link.exceeded is (expected > 3)
    with pytest.raises(ValueError):
        ChainTracker(max_depth=-1)


def test_replying_to_an_older_message_cannot_lower_the_hop() -> None:
    tr, _ = _tracker()
    first = tr.next_link("A")
    _deliver(tr, "B", first)  # B ve hop 0
    other = tr.next_link("C")
    _deliver(tr, "C2", other)
    deep = tr.next_link("C2")  # hop 1 en otra cadena
    _deliver(tr, "B", deep)  # B tambien ve hop 1
    reply = tr.next_link("B", parent_chain_id=first.chain_id, parent_hop=first.hop)
    assert reply.hop == 2 and reply.exceeded  # el contexto de B es el de MAYOR salto


def test_human_turn_opens_a_new_chain() -> None:
    tr, clock = _tracker()
    l1 = tr.next_link("A")
    _deliver(tr, "B", l1)
    clock.now += 10
    # un turno humano (Enter en la GUI) en B despues de la entrega abre una cadena nueva
    fresh = tr.next_link("B", human_turn_at_ms=clock.now)
    assert fresh.hop == 0 and fresh.chain_id != l1.chain_id and fresh.inherited is False
    # un turno humano ANTERIOR a la entrega no limpia nada
    _deliver(tr, "B", l1)
    still = tr.next_link("B", human_turn_at_ms=clock.now - 5)
    assert still.hop == 1 and still.chain_id == l1.chain_id


def test_chain_context_expires_after_ttl() -> None:
    tr, clock = _tracker(ttl_ms=1000)
    l1 = tr.next_link("A")
    _deliver(tr, "B", l1)
    clock.now += 1001
    assert tr.next_link("B").hop == 0


def test_origin_trust_is_the_minimum_of_the_chain() -> None:
    tr, _ = _tracker()
    tr.observe_delivery("B", chain_id="c", hop=0, trust="system")
    tr.observe_delivery("B", chain_id="c", hop=0, trust="unverified")
    assert tr.context_for("B").origin_trust is TrustLevel.UNVERIFIED
    assert tr.next_link("B").origin_trust is TrustLevel.UNVERIFIED


# ----------------------------------------------------------------------------------------------------------------
# Extremo a extremo con las herramientas MCP (en proceso): A->B->A->B, card_id rotado, turno humano, respuesta
# ----------------------------------------------------------------------------------------------------------------

import json  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, Dict  # noqa: E402

from helpers.relay_lab import SpyTerminal, install_pty_write_spy  # noqa: E402


@pytest.fixture(autouse=True)
def _no_system_pty_writes(monkeypatch):
    calls = install_pty_write_spy(monkeypatch)
    yield calls
    assert calls == [], f"el relevo escribio en un PTY: {calls}"


@pytest.fixture
def tools(tmp_path: Path, _no_system_pty_writes):
    from src.core.factory import ApplicationFactory
    from src.core.kernel import CoreKernel
    from src.mcp.pane_registry import PaneRegistry
    from src.mcp.tools import MCPToolsHandler

    kernel = ApplicationFactory.create_mcp_kernel(db_path=str(tmp_path / "chain.db"))
    kernel.register_connector(CoreKernel.PORT_AGENT_TERMINAL, SpyTerminal(_no_system_pty_writes))
    proj = kernel.db.create_project("Cadena", str(tmp_path / "repo"))["project_uuid"]
    reg = PaneRegistry()
    reg.register_pane("pane_1", node_uuid="agent-a", project_uuid=proj, session_token="tok-a")
    reg.register_pane("pane_2", node_uuid="agent-b", project_uuid=proj, session_token="tok-b")
    handler = MCPToolsHandler(kernel=kernel, owns_kernel=False, pane_registry=reg)

    class _T:
        registry = reg
        project = proj

        @staticmethod
        def call(tool: str, token: str, **kw: Any) -> Dict[str, Any]:
            return json.loads(getattr(handler, tool)(session_token=token, **kw))

        service = property(lambda self: handler._get_message_relay())

    try:
        yield _T()
    finally:
        handler.close()
        kernel.close()


def _ping(t, sender_tok: str, target: str, text: str, card: str) -> Dict[str, Any]:
    return t.call("send_message", sender_tok, target_pane_id=target, text=text, card_id=card)


def _pull(t, tok: str) -> list:
    return t.call("inbox_next", tok)["messages"]


def test_ping_pong_parks_the_third_message_even_rotating_card_id(tools) -> None:
    first = _ping(tools, "tok-a", "pane_2", "A->B", "K-1")
    assert first["success"] and first["chain"]["hop"] == 0
    assert len(_pull(tools, "tok-b")) == 1
    second = _ping(tools, "tok-b", "pane_1", "B->A", "K-2")  # otra tarjeta: no cambia nada
    assert second["success"] and second["chain"] == {**first["chain"], "hop": 1}
    assert len(_pull(tools, "tok-a")) == 1
    third = _ping(tools, "tok-a", "pane_2", "A->B otra vez", "K-3")
    assert third["success"] is False and third["code"] == "CHAIN_DEPTH_EXCEEDED"
    assert third["delivered"] is False and third["parked"] is True and third["chain"]["hop"] == 2
    assert _pull(tools, "tok-b") == []  # no llega al agente...
    (card,) = tools.service.list_human_inbox(tools.project)  # ...queda para el humano
    assert card["text"] == "A->B otra vez" and card["reason"] == "CHAIN_DEPTH_EXCEEDED"
    events = [e["event"] for e in tools.service.store.audit_for(third["msg_id"])]
    assert events == ["submitted", "parked"]


def test_reply_inherits_the_chain_and_counts_hops(tools) -> None:
    first = _ping(tools, "tok-a", "pane_2", "pregunta", "K-1")
    (item,) = _pull(tools, "tok-b")
    reply = tools.call("inbox_reply", "tok-b", msg_id=item["mcp2p_envelope"]["msg_id"], text="respuesta")
    assert reply["success"] and reply["chain"]["chain_id"] == first["chain"]["chain_id"] and reply["chain"]["hop"] == 1
    (back,) = _pull(tools, "tok-a")
    assert back["mcp2p_envelope"]["parent_msg_id"] == first["msg_id"] and back["mcp2p_envelope"]["kind"] == "task.reply"
    again = tools.call("inbox_reply", "tok-a", msg_id=back["mcp2p_envelope"]["msg_id"], text="y otra")
    assert again["code"] == "CHAIN_DEPTH_EXCEEDED"
    # no se puede responder a un mensaje que no recibiste
    assert tools.call("inbox_reply", "tok-b", msg_id=first["msg_id"] + "x", text="?")["code"] == "MESSAGE_NOT_FOUND"
    assert tools.call("inbox_reply", "tok-a", msg_id=first["msg_id"], text="?")["code"] == "MESSAGE_NOT_FOUND"


def test_human_enter_in_the_pane_opens_a_new_chain(tools) -> None:
    _ping(tools, "tok-a", "pane_2", "1", "K")
    _pull(tools, "tok-b")
    _ping(tools, "tok-b", "pane_1", "2", "K")
    _pull(tools, "tok-a")
    tools.registry.record_human_keystroke("pane_1")  # teclear sin Enter no es un turno humano
    assert _ping(tools, "tok-a", "pane_2", "3", "K")["code"] == "CHAIN_DEPTH_EXCEEDED"
    tools.registry.record_human_keystroke("pane_1", timestamp=time_now() + 1, submitted=True)
    fresh = _ping(tools, "tok-a", "pane_2", "nuevo turno humano", "K")
    assert fresh["success"] is True and fresh["chain"]["hop"] == 0


def test_human_release_of_a_parked_message_does_not_reset_the_chain(tools) -> None:
    _ping(tools, "tok-a", "pane_2", "1", "K")
    _pull(tools, "tok-b")
    _ping(tools, "tok-b", "pane_1", "2", "K")
    _pull(tools, "tok-a")
    parked = _ping(tools, "tok-a", "pane_2", "3", "K")
    assert tools.service.resolve_parked(parked["msg_id"], "release")["state"] == "queued"
    (item,) = _pull(tools, "tok-b")
    assert item["mcp2p_envelope"]["chain"]["hop"] == 2 and item["mcp2p_envelope"]["released_by_human"] is True
    # B ya arrastra el salto 2: lo siguiente que emita vuelve a esperar al humano
    assert _ping(tools, "tok-b", "pane_1", "4", "K")["code"] == "CHAIN_DEPTH_EXCEEDED"


def time_now() -> float:
    import time as _time

    return _time.time()
