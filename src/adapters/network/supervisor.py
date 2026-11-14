"""
Supervisor compuesto (`SafePeerNetworkSupervisor`) del `PeerNetworkPort` (EGM-48).

Gobierna N conectores con nombre (en producción: `direct` 1:1 y `lan` webhook) bajo un
único puerto, con aislamiento de fallos:
- Ninguna excepción de un conector (socket, TLS, ICE, timeout, bug) se propaga al núcleo
  ni al hilo GUI.
- *Circuit breaker* por conector: `failure_threshold` excepciones consecutivas lo abren
  durante `cooldown_s`; luego pasa a `half_open` y un éxito lo cierra.
- `send_to` prueba los conectores en orden y se detiene en el primero que entrega
  (un `False` no es fallo: significa "no alcanzable por este medio").
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("mcp2p.adapters.network.supervisor")


class SafePeerNetworkSupervisor:
    connector_mode = "composite"

    def __init__(
        self,
        connectors: Dict[str, Any],
        *,
        failure_threshold: int = 3,
        cooldown_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._lock = threading.RLock()
        self._connectors: Dict[str, Any] = dict(connectors)
        self.failure_threshold = max(1, int(failure_threshold))
        self.cooldown_s = max(0.0, float(cooldown_s))
        self._clock = clock
        self._circuits: Dict[str, Dict[str, Any]] = {
            name: {"state": "closed", "failures": 0, "opened_at": 0.0, "last_error": None}
            for name in self._connectors
        }

    # ------------------------------------------------------------------
    def get_connector(self, name: str) -> Optional[Any]:
        with self._lock:
            return self._connectors.get(name)

    def connector_names(self) -> List[str]:
        with self._lock:
            return list(self._connectors)

    def get_circuit_state(self) -> Dict[str, str]:
        with self._lock:
            return {name: self._state(name) for name in self._connectors}

    def _state(self, name: str) -> str:
        c = self._circuits[name]
        if c["state"] == "open" and (self._clock() - c["opened_at"]) >= self.cooldown_s:
            c["state"] = "half_open"
        return c["state"]

    def _available(self, name: str) -> bool:
        with self._lock:
            return self._state(name) != "open"

    def _record_success(self, name: str) -> None:
        with self._lock:
            c = self._circuits[name]
            c["state"], c["failures"], c["last_error"] = "closed", 0, None

    def _record_failure(self, name: str, exc: BaseException) -> None:
        with self._lock:
            c = self._circuits[name]
            c["failures"] += 1
            c["last_error"] = f"{type(exc).__name__}: {exc}"
            if c["state"] == "half_open" or c["failures"] >= self.failure_threshold:
                c["state"] = "open"
                c["opened_at"] = self._clock()
        logger.warning("Conector de red '%s' falló: %s", name, exc)

    def _guard(self, name: str, fn: Callable[[], Any], default: Any) -> Any:
        if not self._available(name):
            return default
        try:
            result = fn()
        except Exception as exc:
            self._record_failure(name, exc)
            return default
        self._record_success(name)
        return result

    @staticmethod
    def _safe_handler(name: str, handler: Callable[[Dict[str, Any]], None]) -> Callable[[Dict[str, Any]], None]:
        def _wrapped(envelope: Dict[str, Any]) -> None:
            try:
                handler(envelope)
            except Exception as exc:
                logger.warning("Handler de sobres entrantes (%s) lanzó: %s", name, exc)

        return _wrapped

    # ------------------------------------------------------------------
    # PeerNetworkPort
    # ------------------------------------------------------------------

    def start_listening(
        self,
        local_node_uuid: str,
        on_envelope: Callable[[Dict[str, Any]], None],
        handlers: Optional[Dict[str, Callable[[Dict[str, Any]], None]]] = None,
    ) -> bool:
        """Arranca la escucha en todos los conectores; `handlers` permite un callback por conector."""
        started = False
        for name, conn in list(self._connectors.items()):
            handler = (handlers or {}).get(name, on_envelope)
            ok = self._guard(
                name, lambda c=conn, h=handler, n=name: c.start_listening(local_node_uuid, self._safe_handler(n, h)), False
            )
            started = started or bool(ok)
        return started

    def send_to(self, target_node_uuid: str, envelope: Dict[str, Any]) -> bool:
        for name, conn in list(self._connectors.items()):
            if self._guard(name, lambda c=conn: bool(c.send_to(target_node_uuid, envelope)), False):
                return True
        return False

    def broadcast(self, project_uuid: str, envelope: Dict[str, Any]) -> Dict[str, Any]:
        per_connector: Dict[str, Any] = {}
        delivered = failed = 0
        for name, conn in list(self._connectors.items()):
            res = self._guard(
                name, lambda c=conn: c.broadcast(project_uuid, envelope), {"delivered": 0, "failed": 0, "error": "unavailable"}
            )
            per_connector[name] = res
            delivered += int(res.get("delivered", 0) or 0)
            failed += int(res.get("failed", 0) or 0)
        return {"delivered": delivered, "failed": failed, "per_connector": per_connector}

    def get_presence(self, project_uuid: Optional[str] = None) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for name, conn in list(self._connectors.items()):
            out.extend(self._guard(name, lambda c=conn: list(c.get_presence(project_uuid)), []))
        return out

    def reconfigure(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        results: Dict[str, Any] = {}
        for name, conn in list(self._connectors.items()):
            # La reconfiguración debe intentarse aunque el circuito esté abierto: es la vía de reparación.
            try:
                results[name] = conn.reconfigure(settings)
                self._record_success(name)
            except Exception as exc:
                self._record_failure(name, exc)
                results[name] = {"success": False, "error": str(exc)}
        return {"success": all(r.get("success", False) for r in results.values()), "per_connector": results}

    def get_status(self) -> Dict[str, Any]:
        connectors: Dict[str, Any] = {}
        for name, conn in list(self._connectors.items()):
            try:
                status = dict(conn.get_status())
            except Exception as exc:
                status = {"error": str(exc)}
            with self._lock:
                status["circuit_state"] = self._state(name)
                status["consecutive_failures"] = self._circuits[name]["failures"]
                status["last_error"] = self._circuits[name]["last_error"]
            connectors[name] = status
        return {"connector_mode": self.connector_mode, "connectors": connectors}

    def close(self) -> None:
        for name, conn in list(self._connectors.items()):
            try:
                conn.close()
            except Exception as exc:
                logger.debug("Error cerrando conector '%s': %s", name, exc)
