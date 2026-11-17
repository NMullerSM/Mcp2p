"""Puente de la GUI para el canal del operador (Ola 4, backend de F2.1; la UI la hace otra ola).

El HUMANO, desde la GUI, puede ver las sesiones de operador emparejadas (programa, pid, ejecutable, cwd, ascendencia,
proyectos, ultima actividad), revocarlas (sin dialogo: bajar autonomia es la direccion segura; revocar es definitivo
para ese proceso del harness) y consultar el estado del emparejamiento (canal escuchando, dialogos pendientes,
rechazos recientes). Aprobar un emparejamiento solo ocurre en el dialogo NATIVO que abre el Hub (el JS no puede).

`start_channel()`/`stop_channel()` los llama `main.py` al abrir/cerrar la ventana: no son funciones del JS.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional

from src.app.ui_adapters import _BaseUIAdapter

logger = logging.getLogger("mcp2p.app.ui_adapters.operator")


def _fail(code: str, message: str, **extra: Any) -> Dict[str, Any]:
    return {"success": False, "code": code, "message": message, **extra}


class OperatorUIAdapter(_BaseUIAdapter):
    def __init__(self, bridge: Any) -> None:
        super().__init__(bridge)
        self._lock = threading.RLock()
        self._service: Any = None
        self._channel: Any = None

    def _registry(self) -> Any:
        extensions = getattr(self._bridge, "extensions_ui", None)
        getter = getattr(extensions, "_get_pane_registry", None)
        return getter() if callable(getter) else None

    def _confirm(self, title: str, message: str) -> Optional[Dict[str, Any]]:
        confirmations = getattr(self._bridge, "confirmations", None)
        if confirmations is None:
            return _fail("CONFIRMATION_UNAVAILABLE", "No hay servicio de confirmacion nativa; se rechaza.")
        return confirmations.confirm(title, message)

    def service(self) -> Any:
        """`OperatorChannelService` del Hub de esta ventana (uno por puente)."""
        with self._lock:
            if self._service is None:
                kernel = self.core_kernel
                if kernel is None:
                    return None
                from src.adapters.persistence.operator_sqlite import SqliteOperatorSessionStore
                from src.core.factory import ApplicationFactory
                from src.core.services.operator_service import OperatorChannelService

                registry = self._registry()
                processes = getattr(registry, "process_resolver", None)
                if processes is None:
                    from src.adapters.identity.peer_process import PsutilPeerProcessResolver

                    processes = PsutilPeerProcessResolver()
                self._service = OperatorChannelService(
                    store=SqliteOperatorSessionStore(kernel.db),
                    processes=processes,
                    relay_provider=lambda: ApplicationFactory.message_relay_service(kernel, self._registry()),
                    pane_directory_provider=self._registry,
                    projects_provider=lambda: kernel.db.list_projects(),
                    confirm=self._confirm,
                )
            return self._service

    def start_channel(self, **channel_kwargs: Any) -> Dict[str, Any]:
        """Abre el canal local del operador (socket Unix 0600 / TCP loopback en Windows). Tolerante: si falla, la GUI
        sigue y el estado lo dice."""
        with self._lock:
            if self._channel is not None:
                return self._channel.info
            service = self.service()
            if service is None:
                return {"listening": False, "error": "sin kernel"}
            try:
                from src.mcp.operator_channel import OperatorChannel

                _ = service.relay  # el relevo ya sabe a quien entregar las respuestas al operador
                channel = OperatorChannel(service, **channel_kwargs)
                info = channel.start()
            except Exception as exc:
                logger.warning("No se pudo abrir el canal del operador: %s", exc)
                service.channel_info = {"listening": False, "error": str(exc)}
                return dict(service.channel_info)
            self._channel = channel
            return info

    def stop_channel(self) -> None:
        with self._lock:
            channel, self._channel = self._channel, None
        if channel is not None:
            try:
                channel.stop()
            except Exception as exc:
                logger.warning("Error cerrando el canal del operador: %s", exc)

    def operator_list_sessions(self, include_ended: Any = False) -> Dict[str, Any]:
        try:
            service = self.service()
            if service is None:
                return _fail("OPERATOR_UNAVAILABLE", "El canal del operador no esta disponible.")
            items = service.list_sessions(include_ended=include_ended is True)
            return {"success": True, "count": len(items), "items": items}
        except Exception as exc:
            logger.warning("operator_list_sessions fallo: %s", exc)
            return _fail("INTERNAL_ERROR", str(exc))

    def operator_revoke_session(self, session_id: Any) -> Dict[str, Any]:
        try:
            service = self.service()
            if service is None:
                return _fail("OPERATOR_UNAVAILABLE", "El canal del operador no esta disponible.")
            return service.revoke(session_id)
        except Exception as exc:
            logger.warning("operator_revoke_session fallo: %s", exc)
            return _fail("INTERNAL_ERROR", str(exc))

    def operator_pairing_status(self) -> Dict[str, Any]:
        try:
            service = self.service()
            if service is None:
                return _fail("OPERATOR_UNAVAILABLE", "El canal del operador no esta disponible.")
            return service.pairing_status()
        except Exception as exc:
            logger.warning("operator_pairing_status fallo: %s", exc)
            return _fail("INTERNAL_ERROR", str(exc))
