"""
Pruebas del puerto único de red `PeerNetworkPort` (EGM-48) y de las correcciones EGM-47
cableadas en `ApiBridge`/`CollabMeshUIAdapter`.

Además verifican (sin modificar código) el estado de EGM-46 en `WebRTCTransportAdapter`:
rol Answerer real y cierre del par anterior en cada `try_connect`.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest

from conftest import make_test_db
from src.adapters.network import (
    DirectPeerNetworkConnector,
    LanWebhookNetworkConnector,
    NullNetworkConnector,
    SafePeerNetworkSupervisor,
    build_p2p_transport,
)
from src.app.api_bridge import ApiBridge
from src.core.factory import ApplicationFactory
from src.core.kernel import CoreKernel
from src.core.ports.connectors import PeerNetworkPort
from src.p2p import webrtc_adapter as webrtc_mod
from src.p2p.crypto import NodeCryptoManager
from src.p2p.transport import NullTransportAdapter
from src.p2p.webrtc_adapter import WebRTCTransportAdapter, _SignalingClient


# ---------------------------------------------------------------------------
# Dobles
# ---------------------------------------------------------------------------


class RecordingTransport:
    """Doble de P2PTransport que registra todo lo que se le hace."""

    def __init__(self, url: str = "", connected: bool = True, connect_results: List[bool] | None = None):
        self.url = url
        self.connected = connected
        self.sent: List[Dict[str, Any]] = []
        self.registered_callback = None
        self.closed = False
        self.connect_calls = 0
        self._connect_results = list(connect_results or [])

    def try_connect(self, rid, timeout_s=8.0):
        self.connect_calls += 1
        if self._connect_results:
            return self._connect_results.pop(0)
        return self.connected

    def send(self, message):
        if not self.connected:
            return False
        self.sent.append(message)
        return True

    def on_message(self, callback):
        self.registered_callback = callback

    def close(self):
        self.closed = True


class ExplodingConnector(NullNetworkConnector):
    connector_mode = "exploding"

    def __init__(self):
        self.calls = 0

    def send_to(self, target_node_uuid, envelope):
        self.calls += 1
        raise OSError("socket roto")


class RecordingConnector(NullNetworkConnector):
    connector_mode = "recording"

    def __init__(self, deliver: bool = True):
        self.deliver = deliver
        self.sent: List[tuple] = []
        self.listening_with = None

    def start_listening(self, local_node_uuid, on_envelope):
        self.listening_with = on_envelope
        return True

    def send_to(self, target_node_uuid, envelope):
        self.sent.append((target_node_uuid, envelope))
        return self.deliver


# ---------------------------------------------------------------------------
# Contrato del puerto
# ---------------------------------------------------------------------------


def test_all_connectors_satisfy_peer_network_port() -> None:
    direct = DirectPeerNetworkConnector(lambda url: RecordingTransport(url))
    lan = LanWebhookNetworkConnector(node_uuid="n1", service=_FakeLanService())
    sup = SafePeerNetworkSupervisor({"direct": direct, "lan": lan})
    for conn in (NullNetworkConnector(), direct, lan, sup):
        assert isinstance(conn, PeerNetworkPort), type(conn).__name__


def test_build_p2p_transport_single_factory_rules() -> None:
    assert isinstance(build_p2p_transport("", object()), NullTransportAdapter)

    def _boom(*_a, **_k):
        raise ImportError("sin aiortc")

    assert isinstance(build_p2p_transport("wss://x", object(), adapter_cls=_boom), NullTransportAdapter)
    built = build_p2p_transport("wss://x", "crypto", adapter_cls=lambda url, c: RecordingTransport(url))
    assert isinstance(built, RecordingTransport) and built.url == "wss://x"


# ---------------------------------------------------------------------------
# Supervisor: aislamiento de fallos y circuit breaker
# ---------------------------------------------------------------------------


def test_supervisor_isolates_exceptions_and_falls_back() -> None:
    boom = ExplodingConnector()
    rec = RecordingConnector(deliver=True)
    sup = SafePeerNetworkSupervisor({"direct": boom, "lan": rec})
    assert sup.send_to("node_b", {"type": "join_decision"}) is True
    assert rec.sent == [("node_b", {"type": "join_decision"})]
    status = sup.get_status()["connectors"]
    assert status["direct"]["consecutive_failures"] == 1
    assert "socket roto" in status["direct"]["last_error"]


def test_supervisor_circuit_opens_after_threshold_and_half_opens_after_cooldown() -> None:
    now = [100.0]
    boom = ExplodingConnector()
    sup = SafePeerNetworkSupervisor({"direct": boom}, failure_threshold=3, cooldown_s=10.0, clock=lambda: now[0])
    for _ in range(3):
        assert sup.send_to("x", {}) is False
    assert sup.get_circuit_state() == {"direct": "open"}
    calls_when_open = boom.calls
    assert sup.send_to("x", {}) is False
    assert boom.calls == calls_when_open  # circuito abierto: ni siquiera se invoca
    now[0] += 11.0
    assert sup.get_circuit_state() == {"direct": "half_open"}
    assert sup.send_to("x", {}) is False  # un fallo en half_open reabre de inmediato
    assert sup.get_circuit_state() == {"direct": "open"}


def test_supervisor_start_listening_per_connector_handlers_and_safe_wrapper() -> None:
    a, b = RecordingConnector(), RecordingConnector()
    seen: List[str] = []

    def default_handler(env):
        seen.append("default")

    def bad_handler(env):
        raise RuntimeError("handler roto")

    sup = SafePeerNetworkSupervisor({"a": a, "b": b})
    assert sup.start_listening("me", default_handler, handlers={"b": bad_handler}) is True
    a.listening_with({"type": "x"})
    b.listening_with({"type": "x"})  # no debe propagar la excepción
    assert seen == ["default"]


# ---------------------------------------------------------------------------
# Conector 1:1 directo
# ---------------------------------------------------------------------------


def test_direct_reconfigure_closes_old_and_rebinds_listener() -> None:
    built: List[RecordingTransport] = []

    def factory(url):
        t = RecordingTransport(url)
        built.append(t)
        return t

    direct = DirectPeerNetworkConnector(factory, signaling_url="wss://a")
    handler = lambda env: None  # noqa: E731
    direct.start_listening("me", handler)
    first = direct.transport
    assert first.registered_callback is handler

    res = direct.reconfigure({"signaling_url": "wss://b"})
    assert res["success"] is True and res["applied"] == ["signaling_url"]
    assert first.closed is True
    assert direct.transport is built[-1] and direct.transport.url == "wss://b"
    assert direct.transport.registered_callback is handler
    assert direct.get_status()["signaling_url_configured"] is True


def test_direct_connect_uses_bounded_exponential_backoff() -> None:
    sleeps: List[float] = []
    t = RecordingTransport(connect_results=[False, False, False, False])
    direct = DirectPeerNetworkConnector(
        lambda url: t, max_attempts=4, backoff_base_s=0.5, backoff_max_s=1.5, sleep=sleeps.append
    )
    assert direct.connect("rid") is False
    assert t.connect_calls == 4
    assert sleeps == [0.5, 1.0, 1.5]  # acotado por backoff_max_s, sin dormir tras el último


def test_direct_send_to_enforces_bound_peer() -> None:
    t = RecordingTransport(connect_results=[True])
    direct = DirectPeerNetworkConnector(lambda url: t)
    assert direct.connect("rid", target_node_uuid="owner_node") is True
    assert direct.send_to("intruso", {"type": "x"}) is False
    assert direct.send_to("owner_node", {"type": "x"}) is True
    assert direct.get_presence() == [{"node_uuid": "owner_node", "connector_mode": "direct_peer", "bound": True}]


# ---------------------------------------------------------------------------
# Conector LAN real (envuelve P2PNotificationService sobre loopback)
# ---------------------------------------------------------------------------


class _FakeLanService:
    def __init__(self):
        self.on_event = None
        self.lan_port = 0
        self._running = False

    def start_lan_server(self):
        self._running = True
        return True

    def stop_lan_server(self):
        self._running = False

    def broadcast_lan_notification(self, endpoints, payload):
        return {"delivered": len(endpoints), "failed": 0, "failures": []}


def test_lan_connector_end_to_end_signed_delivery_and_trust(tmp_path: Path) -> None:
    crypto_a = NodeCryptoManager(key_path=tmp_path / "a.key")
    crypto_b = NodeCryptoManager(key_path=tmp_path / "b.key")
    received: List[Dict[str, Any]] = []
    trusted = {crypto_a.public_key_base64}

    lan_b = LanWebhookNetworkConnector(
        node_uuid="node_b", crypto_manager=crypto_b, is_trusted_pubkey=lambda pk: pk in trusted, lan_port=0
    )
    assert lan_b.start_listening("node_b", received.append) is True
    endpoints = {"node_b": f"127.0.0.1:{lan_b.lan_port}"}
    lan_a = LanWebhookNetworkConnector(
        node_uuid="node_a",
        crypto_manager=crypto_a,
        endpoint_resolver=endpoints.get,
        project_peers_resolver=lambda p: ["node_a", "node_b", "node_sin_endpoint"],
        lan_port=0,
    )
    try:
        assert lan_a.send_to("node_b", {"type": "sync_pulse", "project_uuid": "p1"}) is True
        deadline = time.time() + 2.0
        while not received and time.time() < deadline:
            time.sleep(0.02)
        assert received and received[0]["type"] == "sync_pulse"
        assert received[0]["target_node_uuid"] == "node_b" and received[0]["from_node"] == "node_a"

        res = lan_a.broadcast("p1", {"type": "sync_pulse"})
        assert res["delivered"] == 1 and res["skipped_without_endpoint"] == ["node_sin_endpoint"]

        assert lan_a.send_to("desconocido", {"type": "x"}) is False  # sin endpoint

        trusted.clear()  # node_a deja de ser colaborador aprobado -> 403, no entrega
        assert lan_a.send_to("node_b", {"type": "sync_pulse"}) is False
        assert lan_b.get_status()["listening"] is True
    finally:
        lan_a.close()
        lan_b.close()
    assert lan_b.get_status()["listening"] is False


def test_factory_composes_production_network(tmp_path: Path) -> None:
    db = make_test_db()
    crypto = NodeCryptoManager(key_path=tmp_path / "k.key")
    net = ApplicationFactory.create_peer_network(node_uuid="me", crypto=crypto, db=db, signaling_url="", lan_port=0)
    try:
        assert set(net.connector_names()) == {"direct", "lan"}
        assert isinstance(net.get_connector("direct").transport, NullTransportAdapter)
        kernel = ApplicationFactory.create_test_kernel(db_manager=db)
        kernel.register_connector(CoreKernel.PORT_NETWORK, net)
        status = {c["name"]: c for c in kernel.list_connectors_status()}
        assert status["peer_network"]["plugged"] is True
        assert set(status["peer_network"]["details"]["network"]["connectors"]) == {"direct", "lan"}
    finally:
        net.close()


# ---------------------------------------------------------------------------
# EGM-47 en ApiBridge / CollabMeshUIAdapter
# ---------------------------------------------------------------------------


def test_egm47_set_signaling_url_hot_swaps_without_network(monkeypatch) -> None:
    import src.adapters.network.direct_peer as direct_mod

    built: List[RecordingTransport] = []
    monkeypatch.setattr(
        direct_mod, "build_p2p_transport", lambda url, c, adapter_cls=None: built.append(RecordingTransport(url)) or built[-1]
    )
    old = RecordingTransport()
    bridge = ApiBridge(db_manager=make_test_db(), transport=old)
    res = bridge.set_signaling_server_url("wss://relay.example/ws")
    assert res["success"] is True and res["transport"] == "RecordingTransport"
    assert old.closed is True
    assert bridge.transport is built[-1] and bridge.transport.url == "wss://relay.example/ws"
    assert bridge.transport.registered_callback == bridge._handle_transport_message


def test_egm47_set_signaling_url_reconfigures_network_connector_in_place() -> None:
    db = make_test_db()
    built: List[RecordingTransport] = []

    def factory(url):
        built.append(RecordingTransport(url))
        return built[-1]

    bridge = ApiBridge(db_manager=db)
    direct = DirectPeerNetworkConnector(factory, signaling_url="")
    net = SafePeerNetworkSupervisor({"direct": direct, "lan": RecordingConnector(deliver=False)})
    bridge.core_kernel.register_connector(CoreKernel.PORT_NETWORK, net)
    bridge.set_transport(direct.transport)
    first = direct.transport

    res = bridge.set_signaling_server_url("wss://nuevo.example/ws")
    assert res["success"] is True
    assert first.closed is True
    assert bridge.transport is direct.transport is built[-1]
    assert direct.signaling_url == "wss://nuevo.example/ws"
    assert bridge.transport.registered_callback == bridge._handle_transport_message
    assert bridge.get_signaling_server_url() == "wss://nuevo.example/ws"


def _seed_pending(bridge: ApiBridge, p_uuid: str, node: str) -> None:
    bridge.db.create_project(name="p", project_uuid=p_uuid)
    bridge.db.add_or_request_collaborator(
        project_uuid=p_uuid, node_uuid=node, public_key="PK", role="collaborator", status="pending_approval"
    )


def test_egm47_reject_collaborator_notifies_live_over_transport() -> None:
    t = RecordingTransport(connected=True)
    bridge = ApiBridge(db_manager=make_test_db(), transport=t)
    _seed_pending(bridge, "proj_rej", "node_req")
    res = bridge.reject_collaborator("proj_rej", "node_req")
    assert res["success"] is True
    assert t.sent[-1] == {
        "type": "join_decision", "decision": "rejected", "project_uuid": "proj_rej", "node_uuid": "node_req"
    }


def test_egm47_reject_falls_back_to_lan_when_direct_channel_is_down() -> None:
    bridge = ApiBridge(db_manager=make_test_db())
    direct = DirectPeerNetworkConnector(lambda url: RecordingTransport(connected=False))
    lan = RecordingConnector(deliver=True)
    bridge.core_kernel.register_connector(
        CoreKernel.PORT_NETWORK, SafePeerNetworkSupervisor({"direct": direct, "lan": lan})
    )
    _seed_pending(bridge, "proj_rej2", "node_req")
    bridge.reject_collaborator("proj_rej2", "node_req")
    assert lan.sent and lan.sent[-1][0] == "node_req"
    assert lan.sent[-1][1]["decision"] == "rejected"


def test_egm47_requester_processes_live_rejection(monkeypatch) -> None:
    monkeypatch.setenv("MCP2P_NODE_UUID", "node_requester")
    bridge = ApiBridge(db_manager=make_test_db())
    _seed_pending(bridge, "proj_live", "node_requester")
    retries: List[str] = []
    monkeypatch.setattr(bridge, "retry_p2p_join", lambda project_uuid: retries.append(project_uuid))

    # Rechazo dirigido a OTRO nodo: se ignora
    bridge._handle_transport_message(
        {"type": "join_decision", "decision": "rejected", "project_uuid": "proj_live", "node_uuid": "otro"}
    )
    row = next(c for c in bridge.db.get_project_collaborators("proj_live") if c["node_uuid"] == "node_requester")
    assert row["status"] == "pending_approval"

    # Rechazo dirigido a este nodo: se registra localmente y NO dispara retry
    bridge._handle_transport_message(
        {"type": "join_decision", "decision": "rejected", "project_uuid": "proj_live", "node_uuid": "node_requester"}
    )
    row = next(c for c in bridge.db.get_project_collaborators("proj_live") if c["node_uuid"] == "node_requester")
    assert row["status"] == "rejected" and row["rejection_count"] == 1
    assert retries == []


def test_lan_envelope_router_dispatches_join_and_sync(monkeypatch) -> None:
    bridge = ApiBridge(db_manager=make_test_db())
    seen: List[Any] = []
    monkeypatch.setattr(bridge, "retry_p2p_join", lambda project_uuid: seen.append(("retry", project_uuid)))
    monkeypatch.setattr(bridge, "sync_three_tiers", lambda project_uuid=None: seen.append(("sync", project_uuid)))
    bridge._handle_peer_envelope({"type": "join_decision", "decision": "approved", "project_uuid": "p9"})
    bridge._handle_peer_envelope({"action": "card_updated"})  # payload LAN legado
    bridge._handle_peer_envelope("basura")  # ignorado sin lanzar
    assert seen == [("retry", "p9"), ("sync", None)]


def test_main_builds_single_network_composition(tmp_path: Path) -> None:
    import src.app.main as main_mod

    class _Api:
        crypto = NodeCryptoManager(key_path=tmp_path / "m.key")

        @staticmethod
        def get_signaling_server_url():
            return ""

    net = main_mod._build_peer_network(_Api(), make_test_db(), "node_main")
    try:
        assert isinstance(net, SafePeerNetworkSupervisor)
        assert isinstance(net.get_connector("lan"), LanWebhookNetworkConnector)
        assert isinstance(net.get_connector("direct"), DirectPeerNetworkConnector)
    finally:
        net.close()


# ---------------------------------------------------------------------------
# Verificación de EGM-46 (sin cambios de código)
# ---------------------------------------------------------------------------


class _CountingSignaling:
    instances: List["_CountingSignaling"] = []

    def __init__(self, url):
        self.url = url
        self.closed = False
        _CountingSignaling.instances.append(self)

    def connect(self, rid, timeout_s):
        return True

    def close(self):
        self.closed = True


def test_egm46_try_connect_releases_previous_pair_every_time() -> None:
    _CountingSignaling.instances = []
    adapter = WebRTCTransportAdapter(
        "wss://x", object(), pc_factory=lambda: object(), signaling_factory=_CountingSignaling
    )
    for _ in range(10):
        assert adapter.try_connect("rid") is True
    assert len(_CountingSignaling.instances) == 10
    assert all(s.closed for s in _CountingSignaling.instances[:-1])
    assert _CountingSignaling.instances[-1].closed is False
    adapter.close()
    assert _CountingSignaling.instances[-1].closed is True


class _FakeChannel:
    def __init__(self):
        self.readyState = "open"
        self.handlers: Dict[str, Any] = {}

    def on(self, name):
        def deco(fn):
            self.handlers[name] = fn
            return fn
        return deco


class _FakeDesc:
    def __init__(self, sdp="local-sdp", type="answer"):
        self.sdp, self.type = sdp, type


class _FakeAnswererPC:
    def __init__(self):
        self.iceGatheringState = "complete"
        self.localDescription = _FakeDesc()
        self._handlers: Dict[str, Any] = {}
        self.remote = None
        self.channel = _FakeChannel()

    def on(self, name):
        def deco(fn):
            self._handlers[name] = fn
            return fn
        return deco

    async def setRemoteDescription(self, desc):
        self.remote = desc
        self._handlers["datachannel"](self.channel)  # el offerer abre el canal

    async def createAnswer(self):
        return _FakeDesc(sdp="answer-sdp")

    async def setLocalDescription(self, desc):
        self.localDescription = desc

    async def close(self):
        return None


class _FakeWS:
    def __init__(self):
        self.sent: List[Dict[str, Any]] = []

    async def send(self, data):
        self.sent.append(json.loads(data))

    async def recv(self):
        return json.dumps({"type": "offer", "sdp": "offer-sdp"})


class _FakeCtx:
    def __init__(self, ws):
        self.ws = ws

    async def __aenter__(self):
        return self.ws

    async def __aexit__(self, *a):
        return False


def test_egm46_answerer_role_negotiates_and_receives_messages(monkeypatch) -> None:
    ws = _FakeWS()

    class _WsMod:
        @staticmethod
        def connect(url, open_timeout=None):
            return _FakeCtx(ws)

    monkeypatch.setattr(webrtc_mod, "websockets", _WsMod)
    monkeypatch.setattr(webrtc_mod, "RTCSessionDescription", _FakeDesc)

    client = _SignalingClient("wss://x", role="answerer")
    client.pc = _FakeAnswererPC()
    got: List[Any] = []
    client.bind_message_callback(got.append)
    try:
        assert client.connect("proj_nonce", timeout_s=2.0) is True
        assert [m["type"] for m in ws.sent] == ["answerer_ready", "answer"]
        assert ws.sent[1]["sdp"] == "answer-sdp"
        assert client.pc.remote.sdp == "offer-sdp" and client.pc.remote.type == "offer"
        client.data_channel.handlers["message"](json.dumps({"type": "join_request", "request_code": "rc"}))
        assert got == [{"type": "join_request", "request_code": "rc"}]
    finally:
        client.close()
