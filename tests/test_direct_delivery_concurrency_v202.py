"""Concurrencia de la entrega directa (WP-6b): hilos reales y SQLite real en `tmp_path`.

El agente simulado confirma (`turn_started`) y queda libre (`turn_ended`) justo después de cada Enter, como hacen los
hooks del CLI, y los hilos productores insertan mientras el motor drena.
"""

from __future__ import annotations

import re
import threading

import pytest

from tests.helpers.direct_delivery_lab import NODE_A, NODE_B, NODE_C, PROJECT, Lab, wait_until

MSG_RE = re.compile(r"msg=([0-9a-f-]{36})")


@pytest.fixture
def lab(tmp_path):
    lab = Lab(tmp_path, async_mode=True)
    lab.add_pane(NODE_B, "pane_2")
    lab.add_pane(NODE_C, "pane_3")
    lab.add_pane(NODE_A, "pane_1", role="orchestrator")
    for node in (NODE_A, NODE_B, NODE_C):
        lab.make_idle(node)

    def agent(pane_id: str, data: str) -> None:
        if data == "\r":
            node = {"pane_2": NODE_B, "pane_3": NODE_C}.get(pane_id)
            if node:
                lab.hook(node, "turn_started")
                lab.make_idle(node)

    lab.input.on_write = agent
    yield lab
    lab.service.stop()


def _delivered_ids(lab, pane_id="pane_2"):
    ids = []
    for text in lab.input.texts(pane_id):
        found = MSG_RE.search(text)
        if found:
            ids.append(found.group(1))
    return ids


def test_two_producers_fifo_by_recipient_seq_no_loss_no_dupes(lab):
    def producer(tag: str) -> None:
        for i in range(50):
            lab.insert(NODE_B, f"{tag}-{i}", chain_id=f"c-{tag}")
            lab.service.on_wake(NODE_B)

    threads = [threading.Thread(target=producer, args=(t,)) for t in ("p1", "p2")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    lab.service.on_wake(NODE_B)
    assert wait_until(lambda: len(_delivered_ids(lab)) == 100, timeout_s=60), len(_delivered_ids(lab))
    delivered = _delivered_ids(lab)
    assert len(set(delivered)) == 100, "sin duplicados"
    with lab.db.get_connection() as conn:
        order = [r[0] for r in conn.execute(
            "SELECT msg_id FROM relay_messages WHERE to_principal = ? ORDER BY recipient_seq;", (f"pane:{NODE_B}",))]
    assert delivered == order, "orden FIFO por recipient_seq"
    assert lab.input.max_concurrent == 1, "máximo una escritura en vuelo por destinatario"
    assert all(lab.state_of(m) == "delivered" for m in delivered)


def test_wake_storm_keeps_single_inflight(lab):
    msg = lab.insert(NODE_B, "uno")
    threads = [threading.Thread(target=lab.service.on_wake, args=(NODE_B,)) for _ in range(200)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert wait_until(lambda: lab.state_of(msg) == "delivered")
    assert wait_until(lambda: lab.delivery_of(msg) == "injected")
    assert _delivered_ids(lab) == [msg], "una sola inyección"
    assert lab.input.max_concurrent == 1
    assert len(lab.input.texts("pane_2")) == 2


def test_slow_recipient_does_not_delay_others(lab):
    gate = threading.Event()
    lab.input.block["pane_2"] = gate
    slow = lab.insert(NODE_B, "lento")
    fast = lab.insert(NODE_C, "rápido")
    lab.service.on_wake(NODE_B)
    import time

    start = time.monotonic()
    lab.service.on_wake(NODE_C)
    assert wait_until(lambda: lab.delivery_of(fast) in ("submitted", "injected"), timeout_s=5)
    assert time.monotonic() - start < 0.5
    assert lab.state_of(slow) == "delivered" or lab.state_of(slow) == "queued"
    gate.set()
    assert wait_until(lambda: lab.delivery_of(slow) in ("submitted", "injected"), timeout_s=10)


def test_inbox_next_race_exactly_one_consumer(lab):
    ids = [lab.insert(NODE_B, f"m{i}") for i in range(60)]
    inbox_got = []
    stop = threading.Event()

    def reader() -> None:
        while not stop.is_set():
            rows = lab.store.take_for_delivery(f"pane:{NODE_B}", PROJECT, limit=1, include_unacked=False,
                                               now_ms=lab.clock(), actor="reader")
            inbox_got.extend(r["msg_id"] for r in rows)

    t = threading.Thread(target=reader)
    t.start()
    lab.service.on_wake(NODE_B)
    assert wait_until(lambda: all(lab.state_of(m) == "delivered" for m in ids), timeout_s=60)
    stop.set()
    t.join()
    injected = set(_delivered_ids(lab))
    assert injected.isdisjoint(inbox_got), "ningún mensaje llega por los dos caminos"
    assert injected | set(inbox_got) == set(ids), "ninguno se pierde"
    assert len(inbox_got) == len(set(inbox_got))


def test_recipient_seq_monotonic_under_concurrent_inserts(lab):
    def producer(tag: str) -> None:
        for i in range(100):
            lab.insert(NODE_C, f"{tag}{i}", chain_id=f"c-{tag}")

    threads = [threading.Thread(target=producer, args=(f"t{n}",)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    with lab.db.get_connection() as conn:
        seqs = sorted(r[0] for r in conn.execute(
            "SELECT recipient_seq FROM relay_messages WHERE to_principal = ?;", (f"pane:{NODE_C}",)))
    assert seqs == list(range(1, 401))
