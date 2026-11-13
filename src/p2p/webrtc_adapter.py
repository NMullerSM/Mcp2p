"""
Adaptador WebRTC (aiortc) del puerto hexagonal P2PTransport.

Implementa una conexión real punto a punto usando WebRTC data channels
(aiortc) negociada a través de un pequeño cliente de señalización WebSocket
(`_SignalingClient`). `pc_factory` y `signaling_factory` son el punto de
inyección: por defecto apuntan a los constructores reales de `aiortc` y al
`_SignalingClient` de este módulo, pero las pruebas los sustituyen por dobles
que nunca tocan red real, lo que mantiene la suite de este módulo libre de
networking real o de objetos reales de aiortc/websockets.

Restricción global: no se permite que un fallo de red, de señalización o de
negociación ICE/SDP se propague como excepción — `try_connect` debe degradar
siempre a `False` (ver Review Focus de la tarea 3 del plan: este adaptador
nunca debe ser la causa de que una solicitud de unión falle con un crash).

Nota de alcance: el protocolo de señalización (formato de los mensajes JSON
intercambiados por WebSocket) es un formato mínimo propio de este adaptador,
ya que el servidor de señalización todavía no existe en el plan (tarea
posterior). Esta clase no está cubierta por pruebas de integración contra un
servidor real; sólo el camino con fábricas inyectadas lo está.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any, Callable, Dict, Optional

from src.p2p.transport import P2PTransport

logger = logging.getLogger("mcp2p.p2p.webrtc_adapter")

try:  # aiortc es una dependencia obligatoria, pero el import nunca debe tumbar el módulo.
    from aiortc import RTCPeerConnection, RTCSessionDescription
except Exception:  # pragma: no cover - defensivo; aiortc siempre debería estar instalado.
    RTCPeerConnection = None  # type: ignore[assignment]
    RTCSessionDescription = None  # type: ignore[assignment]

try:
    import websockets
except Exception:  # pragma: no cover - defensivo; websockets siempre debería estar instalado.
    websockets = None  # type: ignore[assignment]


class _SignalingClient:
    """
    Cliente mínimo de señalización WebRTC sobre WebSocket.

    Posee un hilo con su propio event loop de asyncio que vive mientras dure
    la conexión: la negociación SDP (createOffer/setLocalDescription/
    setRemoteDescription) y el canal de datos resultante deben permanecer
    atados al mismo loop para que las tareas internas de aiortc (ICE, DTLS,
    SCTP) sigan corriendo después de que `connect()` retorne — por eso no se
    usa un `asyncio.run()` nuevo por llamada, que cerraría el loop y mataría
    esas tareas en cuanto termina la negociación.
    """

    def __init__(self, url: str) -> None:
        self.url = url
        self.pc: Optional["RTCPeerConnection"] = None
        self.node_crypto: Optional[Any] = None
        self.data_channel: Optional[Any] = None
        self._message_callback: Optional[Callable[[dict], None]] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None

    def bind_message_callback(self, callback: Callable[[dict], None]) -> None:
        self._message_callback = callback

    def connect(self, rendezvous_id: str, timeout_s: float) -> bool:
        if self.pc is None or not self.url or websockets is None or RTCSessionDescription is None:
            return False
        try:
            self._start_loop()
            assert self._loop is not None
            future = asyncio.run_coroutine_threadsafe(self._negotiate(rendezvous_id, timeout_s), self._loop)
            try:
                result = bool(future.result(timeout=timeout_s))
            except Exception as exc:
                logger.debug(f"Negociación WebRTC falló o excedió el tiempo de espera: {exc}")
                result = False
            if not result:
                # Tanto si `_negotiate` lanzó/expiró como si retornó False de
                # forma limpia (p. ej. una respuesta de señalización inválida),
                # no queda ninguna conexión que mantener viva. `future.cancel()`
                # cancela la Task de negociación en curso incluso entre hilos
                # (run_coroutine_threadsafe encadena el cancel() del future
                # concurrente al cancel() real de la Task vía _chain_future),
                # y `close()` libera el pc y detiene el loop/hilo de fondo —
                # sin esto, el hilo quedaría corriendo `run_forever()` para
                # siempre y cualquier socket/tarea en curso quedaría colgado.
                future.cancel()
                self.close()
            return result
        except Exception as exc:
            logger.debug(f"Señalización WebRTC falló: {exc}")
            self.close()
            return False

    def send_data(self, data: str) -> bool:
        if self.data_channel is None or self._loop is None:
            return False

        async def _do_send() -> bool:
            self.data_channel.send(data)
            return True

        try:
            future = asyncio.run_coroutine_threadsafe(_do_send(), self._loop)
            return bool(future.result(timeout=5.0))
        except Exception as exc:
            logger.debug(f"Envío por data channel WebRTC falló: {exc}")
            return False

    def close(self) -> None:
        if self.pc is not None and self._loop is not None:
            try:
                future = asyncio.run_coroutine_threadsafe(self.pc.close(), self._loop)
                future.result(timeout=2.0)
            except Exception:
                pass
        self._stop_loop()
        self.pc = None
        self.data_channel = None

    def _start_loop(self) -> None:
        if self._loop is not None and self._loop.is_running():
            return
        ready = threading.Event()

        def _run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            ready.set()
            loop.run_forever()

        self._loop_thread = threading.Thread(target=_run, daemon=True)
        self._loop_thread.start()
        ready.wait(timeout=5.0)

    def _stop_loop(self) -> None:
        if self._loop is not None:
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except Exception:
                pass
        self._loop = None
        self._loop_thread = None

    async def _negotiate(self, rendezvous_id: str, timeout_s: float) -> bool:
        self.data_channel = self.pc.createDataChannel("mcp2p")
        self._attach_message_handler()

        offer = await self.pc.createOffer()
        await self.pc.setLocalDescription(offer)

        # Esta implementación intercambia una única oferta/respuesta (no hace
        # trickle ICE candidato por candidato), así que hay que esperar a que
        # termine la recolección de candidatos ICE antes de leer/enviar
        # localDescription.sdp, o la oferta llegaría al par sin candidatos.
        while self.pc.iceGatheringState != "complete":
            await asyncio.sleep(0.05)

        offer_msg: Dict[str, Any] = {
            "type": "offer",
            "rendezvous_id": rendezvous_id,
            "sdp": self.pc.localDescription.sdp,
        }
        if self.node_crypto is not None:
            try:
                offer_msg["sender_pubkey"] = self.node_crypto.public_key_base64
                offer_msg["sig"] = self.node_crypto.sign_payload(
                    {"rendezvous_id": rendezvous_id, "sdp": offer_msg["sdp"]}
                )
            except Exception as exc:
                logger.debug(f"No se pudo firmar la oferta WebRTC: {exc}")

        async with websockets.connect(self.url, open_timeout=timeout_s) as ws:
            await asyncio.wait_for(ws.send(json.dumps(offer_msg)), timeout=timeout_s)
            raw_answer = await asyncio.wait_for(ws.recv(), timeout=timeout_s)

        answer_msg = json.loads(raw_answer)
        if answer_msg.get("type") != "answer" or "sdp" not in answer_msg:
            return False

        answer = RTCSessionDescription(sdp=answer_msg["sdp"], type="answer")
        await self.pc.setRemoteDescription(answer)

        while self.data_channel.readyState != "open":
            await asyncio.sleep(0.05)

        return True

    def _attach_message_handler(self) -> None:
        dc = self.data_channel

        @dc.on("message")
        def _on_message(message: Any) -> None:
            callback = self._message_callback
            if callback is None:
                return
            try:
                data = json.loads(message) if isinstance(message, (str, bytes)) else message
            except Exception:
                data = {"raw": message}
            try:
                callback(data)
            except Exception as exc:
                logger.debug(f"Callback de mensaje P2P lanzó una excepción: {exc}")


def _default_pc_factory() -> Any:
    if RTCPeerConnection is None:  # pragma: no cover - defensivo
        raise RuntimeError("aiortc no está disponible")
    return RTCPeerConnection()


class WebRTCTransportAdapter(P2PTransport):
    """Adaptador real de P2PTransport sobre WebRTC data channels (aiortc)."""

    def __init__(
        self,
        signaling_url: str,
        node_crypto: Any,
        pc_factory: Optional[Callable[[], Any]] = None,
        signaling_factory: Optional[Callable[[str], Any]] = None,
    ) -> None:
        self._signaling_url = signaling_url
        self._node_crypto = node_crypto
        self._pc_factory = pc_factory or _default_pc_factory
        self._signaling_factory = signaling_factory or _SignalingClient
        self._pc: Optional[Any] = None
        self._signaling: Optional[Any] = None
        self._connected = False
        self._message_callback: Optional[Callable[[dict], None]] = None

    def try_connect(self, peer_rendezvous_id: str, timeout_s: float = 8.0) -> bool:
        self._connected = False
        if not self._signaling_url:
            return False
        try:
            pc = self._pc_factory()
            signaling = self._signaling_factory(self._signaling_url)
            # Wiring para el camino real: un doble de prueba ignora estos
            # atributos extra sin problema, ya que sólo usa connect().
            try:
                signaling.pc = pc
                signaling.node_crypto = self._node_crypto
            except Exception:
                pass

            if not signaling.connect(peer_rendezvous_id, timeout_s):
                return False

            self._pc = pc
            self._signaling = signaling
            if self._message_callback is not None and hasattr(signaling, "bind_message_callback"):
                signaling.bind_message_callback(self._message_callback)
            self._connected = True
            return True
        except Exception as exc:
            logger.debug(f"try_connect falló, degradando a False: {exc}")
            return False

    def send(self, message: dict) -> bool:
        if not self._connected or self._signaling is None:
            return False
        try:
            if not hasattr(self._signaling, "send_data"):
                return False
            return bool(self._signaling.send_data(json.dumps(message)))
        except Exception as exc:
            logger.debug(f"send falló, degradando a False: {exc}")
            return False

    def on_message(self, callback: Callable[[dict], None]) -> None:
        self._message_callback = callback
        if self._signaling is not None and hasattr(self._signaling, "bind_message_callback"):
            try:
                self._signaling.bind_message_callback(callback)
            except Exception as exc:
                logger.debug(f"No se pudo registrar el callback de mensajes: {exc}")

    def close(self) -> None:
        try:
            if self._signaling is not None and hasattr(self._signaling, "close"):
                self._signaling.close()
        except Exception as exc:
            logger.debug(f"close falló al cerrar la señalización: {exc}")
        finally:
            self._pc = None
            self._signaling = None
            self._connected = False
