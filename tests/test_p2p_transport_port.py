"""
Pruebas del puerto hexagonal P2PTransport y su adaptador nulo NullTransportAdapter.
"""

import pytest

from src.p2p.transport import NullTransportAdapter, P2PTransport


def test_null_adapter_never_connects():
    t = NullTransportAdapter()
    assert t.try_connect("proj_x_nonce123") is False


def test_null_adapter_send_returns_false():
    t = NullTransportAdapter()
    assert t.send({"type": "join_request"}) is False


def test_null_adapter_on_message_and_close_do_not_raise():
    t = NullTransportAdapter()
    t.on_message(lambda msg: None)
    t.close()  # must not raise


def test_cannot_instantiate_abstract_port_directly():
    with pytest.raises(TypeError):
        P2PTransport()
