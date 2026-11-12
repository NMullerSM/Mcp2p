"""
Pruebas del adaptador WebRTC (aiortc) del puerto P2PTransport.

Estas pruebas usan dobles de prueba inyectados (pc_factory, signaling_factory)
para que ninguna de ellas toque red real ni objetos reales de aiortc/websockets.
El camino de producción (fábricas por defecto) se ejerce únicamente a través de
lectura cuidadosa de la API real de aiortc, no mediante estas pruebas.
"""

from src.p2p.webrtc_adapter import WebRTCTransportAdapter


class FakeCrypto:
    """Doble mínimo de NodeCryptoManager: expone su superficie pública usada aquí."""

    public_key_base64 = "fake-pubkey-base64"

    def sign_payload(self, payload):
        return "fake-signature-base64"


class FakePeerConnection:
    def __init__(self):
        self.closed = False
        self.sent = []


class FakeSignalingClient:
    def __init__(self, url):
        self.url = url
        self.connected = False

    def connect(self, rendezvous_id, timeout_s):
        self.connected = True
        return True  # simula un par alcanzable


def test_try_connect_uses_injected_factories_and_returns_true_on_success():
    adapter = WebRTCTransportAdapter(
        signaling_url="wss://example.test/signal",
        node_crypto=FakeCrypto(),
        pc_factory=FakePeerConnection,
        signaling_factory=FakeSignalingClient,
    )
    assert adapter.try_connect("proj_x_nonce123", timeout_s=1.0) is True


def test_try_connect_returns_false_when_signaling_unreachable():
    class UnreachableSignalingClient(FakeSignalingClient):
        def connect(self, rendezvous_id, timeout_s):
            return False

    adapter = WebRTCTransportAdapter(
        signaling_url="wss://example.test/signal",
        node_crypto=FakeCrypto(),
        pc_factory=FakePeerConnection,
        signaling_factory=UnreachableSignalingClient,
    )
    assert adapter.try_connect("proj_x_nonce123", timeout_s=1.0) is False


def test_try_connect_never_raises_on_empty_signaling_url():
    # Global Constraint: DEFAULT_SIGNALING_URL may be "" — must degrade silently.
    adapter = WebRTCTransportAdapter(signaling_url="", node_crypto=FakeCrypto())
    assert adapter.try_connect("proj_x_nonce123", timeout_s=1.0) is False


def test_send_before_connect_returns_false():
    adapter = WebRTCTransportAdapter(signaling_url="", node_crypto=FakeCrypto())
    assert adapter.send({"type": "join_request"}) is False


def test_try_connect_never_raises_when_pc_factory_raises():
    def exploding_pc_factory():
        raise RuntimeError("simulated aiortc/native failure")

    adapter = WebRTCTransportAdapter(
        signaling_url="wss://example.test/signal",
        node_crypto=FakeCrypto(),
        pc_factory=exploding_pc_factory,
        signaling_factory=FakeSignalingClient,
    )
    assert adapter.try_connect("proj_x_nonce123", timeout_s=1.0) is False


def test_try_connect_never_raises_when_signaling_connect_raises():
    class ExplodingSignalingClient(FakeSignalingClient):
        def connect(self, rendezvous_id, timeout_s):
            raise ConnectionError("simulated signaling server failure")

    adapter = WebRTCTransportAdapter(
        signaling_url="wss://example.test/signal",
        node_crypto=FakeCrypto(),
        pc_factory=FakePeerConnection,
        signaling_factory=ExplodingSignalingClient,
    )
    assert adapter.try_connect("proj_x_nonce123", timeout_s=1.0) is False


def test_try_connect_never_raises_when_signaling_factory_raises():
    def exploding_signaling_factory(url):
        raise OSError("simulated DNS/socket failure")

    adapter = WebRTCTransportAdapter(
        signaling_url="wss://example.test/signal",
        node_crypto=FakeCrypto(),
        pc_factory=FakePeerConnection,
        signaling_factory=exploding_signaling_factory,
    )
    assert adapter.try_connect("proj_x_nonce123", timeout_s=1.0) is False


def test_send_after_successful_connect_with_fake_without_send_data_returns_false():
    # The fake peer connection/signaling double doesn't implement a real data
    # channel; send() must degrade safely instead of raising AttributeError.
    adapter = WebRTCTransportAdapter(
        signaling_url="wss://example.test/signal",
        node_crypto=FakeCrypto(),
        pc_factory=FakePeerConnection,
        signaling_factory=FakeSignalingClient,
    )
    assert adapter.try_connect("proj_x_nonce123", timeout_s=1.0) is True
    assert adapter.send({"type": "join_request"}) is False


def test_close_never_raises_without_prior_connect():
    adapter = WebRTCTransportAdapter(signaling_url="", node_crypto=FakeCrypto())
    adapter.close()  # must not raise


def test_on_message_before_connect_does_not_raise():
    adapter = WebRTCTransportAdapter(signaling_url="", node_crypto=FakeCrypto())
    adapter.on_message(lambda msg: None)  # must not raise
