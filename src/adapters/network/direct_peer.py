"""
Conector 1:1 directo (`DirectPeerNetworkConnector`) del `PeerNetworkPort` (`23_` Tarea 3.2,
solo la parte 1:1; el relay hospedado en tiempo real queda fuera de alcance, ver `25_`).

Envuelve un `P2PTransport` (WebRTC/aiortc o `NullTransportAdapter`) y añade lo que el
transporte crudo no tenía:
- Reconfiguración EN CALIENTE de la URL de señalización (EGM-47): cierra el transporte
  anterior y construye el nuevo con la única fábrica `build_p2p_transport`.
- Reconexión con *backoff* exponencial acotado (`connect`).
- Direccionamiento estricto: si el canal se abrió hacia un `target_node_uuid` concreto,
  `send_to` rechaza cualquier otro destino.

`build_p2p_transport` es la ÚNICA fábrica de transporte 1:1 del proceso; antes había dos
copias (`src/app/main.py` y `CollabMeshUIAdapter.set_signaling_server_url`) con reglas
distintas para la URL vacía.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from src.p2p.transport import NullTransportAdapter, P2PTransport

logger = logging.getLogger("mcp2p.adapters.network.direct")


def build_p2p_transport(signaling_url: str, node_crypto: Any, adapter_cls: Optional[Any] = None) -> P2PTransport:
    """
    Construye el transporte 1:1 real (WebRTC/aiortc) o degrada a `NullTransportAdapter`:
    - URL vacía -> `NullTransportAdapter` (sin importar aiortc);
    - CUALQUIER fallo de construcción (incluido `ImportError` de aiortc) -> `NullTransportAdapter`.
    """
    if not signaling_url:
        return NullTransportAdapter()
    try:
        cls = adapter_cls
        if cls is None:
            from src.p2p.webrtc_adapter import WebRTCTransportAdapter as cls  # type: ignore[no-redef]
        return cls(signaling_url, node_crypto)
    except Exception as exc:
        logger.warning("No se pudo construir el transporte WebRTC, degradando a NullTransportAdapter: %s", exc)
        return NullTransportAdapter()


class DirectPeerNetworkConnector:
    connector_mode = "direct_peer"

    def __init__(
        self,
        transport_factory: Callable[[str], P2PTransport],
        signaling_url: str = "",
        *,
        transport: Optional[P2PTransport] = None,
        max_attempts: int = 3,
        backoff_base_s: float = 0.5,
        backoff_max_s: float = 4.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._lock = threading.RLock()
        self._factory = transport_factory
        self._signaling_url = signaling_url or ""
        self._transport: P2PTransport = transport if transport is not None else transport_factory(self._signaling_url)
        self._on_envelope: Optional[Callable[[Dict[str, Any]], None]] = None
        self._bound_peer: Optional[str] = None
        self.max_attempts = max(1, int(max_attempts))
        self.backoff_base_s = max(0.0, float(backoff_base_s))
        self.backoff_max_s = max(self.backoff_base_s, float(backoff_max_s))
        self._sleep = sleep

    # ------------------------------------------------------------------
    @property
    def transport(self) -> P2PTransport:
        with self._lock:
            return self._transport

    @property
    def signaling_url(self) -> str:
        return self._signaling_url

    def adopt_transport(self, transport: P2PTransport) -> None:
        """Toma como propio un transporte ya construido (p. ej. inyectado por `ApiBridge.set_transport`)."""
        with self._lock:
            if transport is self._transport:
                return
            self._transport = transport
            self._bound_peer = None
            if self._on_envelope is not None:
                transport.on_message(self._on_envelope)

    def connect(
        self,
        rendezvous_id: str,
        target_node_uuid: Optional[str] = None,
        timeout_s: float = 8.0,
        max_attempts: Optional[int] = None,
    ) -> bool:
        """Intenta abrir el canal 1:1 con *backoff* exponencial acotado; nunca lanza."""
        attempts = self.max_attempts if max_attempts is None else max(1, int(max_attempts))
        for i in range(attempts):
            try:
                ok = bool(self.transport.try_connect(rendezvous_id, timeout_s))
            except Exception as exc:
                logger.debug("try_connect lanzó (intento %d): %s", i + 1, exc)
                ok = False
            if ok:
                with self._lock:
                    self._bound_peer = target_node_uuid or None
                return True
            if i < attempts - 1:
                self._sleep(min(self.backoff_max_s, self.backoff_base_s * (2 ** i)))
        return False

    # ------------------------------------------------------------------
    # PeerNetworkPort
    # ------------------------------------------------------------------

    def start_listening(self, local_node_uuid: str, on_envelope: Callable[[Dict[str, Any]], None]) -> bool:
        with self._lock:
            self._on_envelope = on_envelope
            self._transport.on_message(on_envelope)
        return True

    def send_to(self, target_node_uuid: str, envelope: Dict[str, Any]) -> bool:
        with self._lock:
            bound = self._bound_peer
            transport = self._transport
        if bound and target_node_uuid and target_node_uuid != bound:
            logger.debug("send_to rechazado: canal 1:1 ligado a %s, destino %s", bound, target_node_uuid)
            return False
        try:
            return bool(transport.send(envelope))
        except Exception as exc:
            logger.debug("send por canal 1:1 falló: %s", exc)
            return False

    def broadcast(self, project_uuid: str, envelope: Dict[str, Any]) -> Dict[str, Any]:
        ok = self.send_to(self._bound_peer or "", envelope)
        return {"delivered": 1 if ok else 0, "failed": 0 if ok else 1, "failures": [] if ok else [{"error": "not_delivered"}]}

    def get_presence(self, project_uuid: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            bound = self._bound_peer
        if not bound:
            return []
        return [{"node_uuid": bound, "connector_mode": self.connector_mode, "bound": True}]

    def reconfigure(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        """
        EGM-47: `signaling_url` cambia en caliente. Cierra el transporte anterior, construye
        el nuevo con la fábrica y re-registra el callback de entrada. Nunca lanza.
        """
        if "signaling_url" not in settings:
            return {"success": True, "applied": []}
        new_url = str(settings.get("signaling_url") or "")
        with self._lock:
            old = self._transport
            try:
                old.close()
            except Exception as exc:
                logger.debug("Error cerrando transporte previo: %s", exc)
            try:
                new_transport = self._factory(new_url)
            except Exception as exc:
                logger.warning("Fábrica de transporte falló para %r: %s", new_url, exc)
                new_transport = NullTransportAdapter()
            self._transport = new_transport
            self._signaling_url = new_url
            self._bound_peer = None
            if self._on_envelope is not None:
                new_transport.on_message(self._on_envelope)
        return {
            "success": True,
            "applied": ["signaling_url"],
            "signaling_url": new_url,
            "transport": type(new_transport).__name__,
        }

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "connector_mode": self.connector_mode,
                "transport": type(self._transport).__name__,
                "signaling_url_configured": bool(self._signaling_url),
                "bound_peer": self._bound_peer,
            }

    def close(self) -> None:
        with self._lock:
            transport = self._transport
            self._bound_peer = None
        try:
            transport.close()
        except Exception as exc:
            logger.debug("Error cerrando transporte 1:1: %s", exc)
