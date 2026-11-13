"""
Pruebas del adaptador WebRTC (aiortc) del puerto P2PTransport.

La mayoría de estas pruebas usan dobles de prueba inyectados (pc_factory,
signaling_factory) para que no toquen red real ni objetos reales de
aiortc/websockets. Dos pruebas (al final del archivo) ejercen el
`_SignalingClient` real directamente, pero reemplazando `websockets.connect`
por un doble en memoria y `pc` por un doble mínimo que sólo imita la
superficie async que `_negotiate()` usa — así se puede comprobar que el loop
y el hilo de fondo se liberan correctamente sin depender de una red real ni
de un servidor de señalización (que todavía no existe en el plan).
"""

import json
import threading
import time

from src.p2p.webrtc_adapter import WebRTCTransportAdapter
from src.p2p import webrtc_adapter as webrtc_adapter_module
from src.p2p.webrtc_adapter import _SignalingClient


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


# --- Pruebas de regresión: fuga de hilo/event loop en _SignalingClient real ---
#
# Estas pruebas ejercen el `_SignalingClient` real (no el doble de las pruebas
# anteriores) con un pc y un módulo `websockets` falsos, para comprobar que
# `connect()` libera siempre el loop/hilo de fondo que crea — tanto cuando la
# negociación termina limpiamente con una respuesta de señalización inválida
# (sin excepción) como cuando se agota el timeout. Antes del fix, sólo la rama
# `except Exception` llamaba a `_stop_loop()`, así que un retorno limpio en
# `False` dejaba el hilo corriendo `run_forever()` para siempre.


class _FakeDataChannelForNegotiation:
    def __init__(self):
        self.readyState = "open"

    def on(self, event_name):
        def _decorator(fn):
            return fn

        return _decorator

    def send(self, data):
        pass


class _FakeSessionDescription:
    def __init__(self, sdp="fake-sdp", type="offer"):
        self.sdp = sdp
        self.type = type


class _FakeRealishPeerConnection:
    """Imita sólo la superficie async de aiortc.RTCPeerConnection que `_negotiate()` usa."""

    def __init__(self):
        self.iceGatheringState = "complete"
        self.localDescription = _FakeSessionDescription()
        self._dc = _FakeDataChannelForNegotiation()

    def createDataChannel(self, label):
        return self._dc

    async def createOffer(self):
        return self.localDescription

    async def setLocalDescription(self, desc):
        self.localDescription = desc

    async def setRemoteDescription(self, desc):
        return None

    async def close(self):
        return None


class _FakeWSConnectionContext:
    def __init__(self, ws):
        self._ws = ws

    async def __aenter__(self):
        return self._ws

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeWebSocketWithAnswer:
    def __init__(self, answer_payload):
        self._answer_payload = answer_payload

    async def send(self, data):
        pass

    async def recv(self):
        return json.dumps(self._answer_payload)


class _FakeWebSocketHangs:
    async def send(self, data):
        pass

    async def recv(self):
        import asyncio

        await asyncio.sleep(10)  # mucho más que el timeout_s usado por la prueba
        return "{}"


class _FakeWebsocketsModule:
    def __init__(self, ws):
        self._ws = ws

    def connect(self, url, open_timeout=None):
        return _FakeWSConnectionContext(self._ws)


def _alive_threads_created_during(before_threads):
    return [t for t in set(threading.enumerate()) - before_threads if t.is_alive()]


def _wait_for_no_extra_threads(before_threads, deadline_s=2.0):
    deadline = time.time() + deadline_s
    while time.time() < deadline:
        if not _alive_threads_created_during(before_threads):
            return
        time.sleep(0.05)


def test_signaling_client_stops_loop_and_thread_on_clean_false_result(monkeypatch):
    # El par responde, pero con un mensaje de señalización inválido/inesperado
    # (sin "type": "answer" con "sdp"): _negotiate() retorna False sin lanzar.
    monkeypatch.setattr(
        webrtc_adapter_module,
        "websockets",
        _FakeWebsocketsModule(_FakeWebSocketWithAnswer({"type": "bogus"})),
    )

    before_threads = set(threading.enumerate())
    client = _SignalingClient("wss://example.test/signal")
    client.pc = _FakeRealishPeerConnection()

    assert client.connect("proj_x_nonce123", timeout_s=2.0) is False

    _wait_for_no_extra_threads(before_threads)
    assert _alive_threads_created_during(before_threads) == []
    assert client._loop is None
    assert client._loop_thread is None


def test_signaling_client_stops_loop_and_thread_on_timeout(monkeypatch):
    # El par nunca responde: el `recv()` del websocket se queda colgado mucho
    # más tiempo que timeout_s, así que connect() debe expirar por timeout,
    # cancelar la negociación en curso, y aun así liberar el loop/hilo.
    monkeypatch.setattr(
        webrtc_adapter_module,
        "websockets",
        _FakeWebsocketsModule(_FakeWebSocketHangs()),
    )

    before_threads = set(threading.enumerate())
    client = _SignalingClient("wss://example.test/signal")
    client.pc = _FakeRealishPeerConnection()

    start = time.time()
    assert client.connect("proj_x_nonce123", timeout_s=0.3) is False
    elapsed = time.time() - start
    assert elapsed < 5.0  # no se quedó esperando el sleep(10) simulado

    _wait_for_no_extra_threads(before_threads)
    assert _alive_threads_created_during(before_threads) == []
    assert client._loop is None
    assert client._loop_thread is None
