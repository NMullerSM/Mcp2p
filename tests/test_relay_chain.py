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
