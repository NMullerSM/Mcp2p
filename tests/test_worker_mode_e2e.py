"""Modo trabajador extremo a extremo (F2.4): dos «agentes» con procesos reales, cada uno con una sesion MCP persistente
dentro del arbol de procesos de su panel, conversan por la bandeja SIN que el sistema escriba en ninguna terminal.

A (en inbox_wait) y B (en inbox_wait): A envia -> B despierta al instante y responde con inbox_reply -> A despierta.
Mide la latencia con reloj de pared (tiempo desde que A empieza `send_message` hasta que la espera de B termina).
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Dict, List

import pytest

from tests.helpers.relay_lab import RelayLab, install_pty_write_spy

# La espera dura mucho mas que cualquier latencia esperada: si la entrega dependiera de un sondeo (la
# reevaluacion de seguridad del Hub es cada 5 s; en local la espera real es de decenas de ms), la medida lo delataria.
WAIT_MS = 30_000
MAX_WAKE_S = 4.0  # holgura para CI lento (Windows); un sondeo de 5 s no pasaria de forma fiable


@pytest.fixture
def lab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    calls = install_pty_write_spy(monkeypatch)
    lab = RelayLab(tmp_path, pty_calls=calls)
    try:
        yield lab, calls
    finally:
        lab.close()


class _Waiter(threading.Thread):
    """Un agente bloqueado en una llamada larga (como un CLI esperando el resultado de una herramienta)."""

    def __init__(self, lab: RelayLab, idx: int, tool: str, args: Dict[str, Any]) -> None:
        super().__init__(daemon=True)
        self.lab, self.idx, self.tool, self.args = lab, idx, tool, args
        self.reply: Dict[str, Any] = {}
        self.error: BaseException | None = None

    def run(self) -> None:
        try:
            self.reply = self.lab.root(self.idx).session_call(self.tool, self.args)
        except BaseException as exc:  # pragma: no cover - lo reporta la prueba
            self.error = exc


def _join(waiter: _Waiter, timeout: float = 60.0) -> Dict[str, Any]:
    waiter.join(timeout)
    assert not waiter.is_alive(), "la espera no termino"
    assert waiter.error is None, waiter.error
    return waiter.reply


def test_two_worker_agents_converse_through_the_inbox_without_touching_any_terminal(lab, capsys) -> None:
    lab, pty_calls = lab
    lab.registry.set_worker_mode("pane_1", True)
    lab.registry.set_worker_mode("pane_2", True)
    for idx in (1, 2):
        lab.root(idx).session_start(lab.url, lab.token(idx))

    # B se queda esperando (modo trabajador).
    b_wait = _Waiter(lab, 2, "inbox_wait", {"timeout_ms": WAIT_MS})
    b_wait.start()
    import time

    time.sleep(1.0)  # B ya esta dentro de inbox_wait; si no lo estuviera, el mensaje esperaria igual en su bandeja
    assert b_wait.is_alive(), "inbox_wait debe seguir abierta mientras no haya mensajes"

    sent = lab.root(1).session_call(
        "send_message", {"target_pane_id": "pane_2", "text": "Suma 2 + 2 y respondeme.", "kind": "task.request"}
    )
    assert sent["result"]["success"] is True and sent["result"]["state"] == "queued"
    got_b = _join(b_wait)
    wake_ab = got_b["t1"] - sent["t0"]

    result_b = got_b["result"]
    assert result_b["worker_mode"] is True and result_b["timed_out"] is False
    assert "inbox_wait" in result_b["hint"] and result_b["timeout_ms_recommended"] > 0
    (message,) = result_b["messages"]
    env = message["mcp2p_envelope"]
    assert env["verified"] is True and env["from"]["node_uuid"] == lab.node(1)
    assert env["kind"] == "task.request" and env["chain"]["hop"] == 0
    assert message["payload_untrusted_text"] == "Suma 2 + 2 y respondeme."

    # A se queda esperando la respuesta y B responde con inbox_reply.
    a_wait = _Waiter(lab, 1, "inbox_wait", {"timeout_ms": WAIT_MS})
    a_wait.start()
    time.sleep(1.0)
    assert a_wait.is_alive()
    ack = lab.root(2).session_call("inbox_ack", {"msg_ids": [env["msg_id"]]})
    assert ack["result"]["acked"] == [env["msg_id"]]
    reply = lab.root(2).session_call("inbox_reply", {"msg_id": env["msg_id"], "text": "4"})
    assert reply["result"]["success"] is True
    got_a = _join(a_wait)
    wake_ba = got_a["t1"] - reply["t0"]

    (answer,) = got_a["result"]["messages"]
    aenv = answer["mcp2p_envelope"]
    assert aenv["verified"] is True and aenv["kind"] == "task.reply" and aenv["from"]["node_uuid"] == lab.node(2)
    assert aenv["parent_msg_id"] == env["msg_id"] and aenv["chain"]["hop"] == 1
    assert aenv["chain"]["chain_id"] == env["chain"]["chain_id"]
    assert answer["payload_untrusted_text"] == "4"
    assert got_a["result"]["worker_mode"] is True

    # Latencia: despierta al instante (no por sondeo) y NADIE escribio en un PTY.
    with capsys.disabled():
        print(f"\n[latencia modo trabajador] A->B {wake_ab * 1000:.0f} ms | B->A {wake_ba * 1000:.0f} ms")
    assert wake_ab < MAX_WAKE_S, wake_ab
    assert wake_ba < MAX_WAKE_S, wake_ba
    assert pty_calls == [], f"el relevo escribio en un PTY: {pty_calls}"


def test_worker_wait_that_times_out_keeps_the_loop_alive_and_a_late_message_is_not_lost(lab) -> None:
    lab, pty_calls = lab
    lab.registry.set_worker_mode("pane_2", True)
    for idx in (1, 2):
        lab.root(idx).session_start(lab.url, lab.token(idx))

    quiet = lab.root(2).session_call("inbox_wait", {"timeout_ms": 1_000})["result"]
    assert quiet["timed_out"] is True and quiet["messages"] == []
    assert quiet["worker_mode"] is True and "inbox_wait" in quiet["hint"]  # el hint repite el bucle

    lab.root(1).session_call("send_message", {"target_pane_id": "pane_2", "text": "tarde"})
    late = lab.root(2).session_call("inbox_wait", {"timeout_ms": 1_000})["result"]
    assert [m["payload_untrusted_text"] for m in late["messages"]] == ["tarde"]
    assert "inbox_ack" in late["hint"] and "inbox_reply" in late["hint"]
    assert pty_calls == []


def test_a_third_automatic_message_in_the_chain_waits_for_the_human_even_between_workers(lab) -> None:
    """La cadena sellada (EGM-93) tambien frena a dos trabajadores: A->B, B->A y el siguiente A->B queda aparcado."""
    lab, pty_calls = lab
    for idx in (1, 2):
        lab.registry.set_worker_mode(f"pane_{idx}", True)
        lab.root(idx).session_start(lab.url, lab.token(idx))

    lab.root(1).session_call("send_message", {"target_pane_id": "pane_2", "text": "uno"})
    first = lab.root(2).session_call("inbox_wait", {"timeout_ms": 5_000})["result"]["messages"][0]
    lab.root(2).session_call("inbox_reply", {"msg_id": first["mcp2p_envelope"]["msg_id"], "text": "dos"})
    second = lab.root(1).session_call("inbox_wait", {"timeout_ms": 5_000})["result"]["messages"][0]
    third = lab.root(1).session_call(
        "inbox_reply", {"msg_id": second["mcp2p_envelope"]["msg_id"], "text": "tres"}
    )["result"]
    assert third["success"] is False and third["code"] == "CHAIN_DEPTH_EXCEEDED" and third["parked"] is True
    parked: List[Dict[str, Any]] = lab.kernel.relay_store.list_parked(lab.proj)
    assert [p["content"] for p in parked] == ["tres"]
    assert pty_calls == []
