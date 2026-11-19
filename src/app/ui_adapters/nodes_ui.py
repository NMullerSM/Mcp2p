"""Adaptador UI del puente para nodos remotos, política de confianza y cuarentena (v2.0.2, WP-7)."""

from __future__ import annotations

import logging
import platform
import threading
import time
from typing import Any, Dict, Optional

from src.app.ui_adapters import _BaseUIAdapter
from src.core.domain.node_policy import (
    NodePolicy,
    format_fingerprint_short,
    sanitize_node_name,
)

logger = logging.getLogger("mcp2p.app.ui_adapters.nodes")

_QUARANTINE_SWEEP_INTERVAL_S = 600.0


def _fail(code: str, message: str, **extra: Any) -> Dict[str, Any]:
    return {"success": False, "code": code, "message": message, **extra}


class NodesUIAdapter(_BaseUIAdapter):
    """Expone a la GUI la gestión de nodos remotos conocidos y la identidad local del equipo."""

    def _service(self) -> Any:
        kernel = self.core_kernel
        if kernel is None:
            return None
        from src.core.factory import ApplicationFactory

        registry = None
        extensions = getattr(self._bridge, "extensions_ui", None)
        getter = getattr(extensions, "_get_pane_registry", None)
        if callable(getter):
            registry = getter()
        return ApplicationFactory.remote_intake_service(kernel, pane_directory=registry)

    def _relay(self) -> Any:
        kernel = self.core_kernel
        if kernel is None:
            return None
        from src.core.factory import ApplicationFactory

        registry = None
        extensions = getattr(self._bridge, "extensions_ui", None)
        getter = getattr(extensions, "_get_pane_registry", None)
        if callable(getter):
            registry = getter()
        return ApplicationFactory.message_relay_service(kernel, pane_directory=registry)

    def _confirm_native(self, title: str, message: str) -> Optional[Dict[str, Any]]:
        confirmations = getattr(self._bridge, "confirmations", None)
        if confirmations is None:
            return _fail("CONFIRMATION_UNAVAILABLE", "No hay servicio de confirmacion nativa; la accion se rechaza.")
        return confirmations.confirm(title, message)

    def relay_list_nodes(self) -> Dict[str, Any]:
        try:
            service = self._service()
            if service is None:
                return _fail("RELAY_UNAVAILABLE", "El servicio de nodos no está disponible.")
            nodes = service.list_nodes()
            return {"success": True, "nodes": nodes}
        except Exception as exc:
            logger.warning("relay_list_nodes fallo: %s", exc)
            return _fail("INTERNAL_ERROR", str(exc))

    def relay_set_node_policy(self, device_fingerprint: Any, policy: Any) -> Dict[str, Any]:
        try:
            if not isinstance(device_fingerprint, str) or not device_fingerprint.strip():
                return _fail("INVALID_ARGUMENT", "Indique la huella del nodo remoto.")
            fp = device_fingerprint.strip()
            if not isinstance(policy, str):
                return _fail("INVALID_ARGUMENT", "Indique una política válida.")
            try:
                policy_enum = NodePolicy(policy.strip())
            except ValueError:
                allowed = ", ".join(p.value for p in NodePolicy)
                return _fail("INVALID_ARGUMENT", f"Política inválida: {policy!r}. Permitidas: {allowed}.")

            service = self._service()
            if service is None:
                return _fail("RELAY_UNAVAILABLE", "El servicio de nodos no está disponible.")
            node = service.get_node(fp)
            if node is None:
                return _fail("NODE_NOT_FOUND", "No existe ningún nodo registrado con esa huella.")

            if policy_enum is NodePolicy.DIRECT:
                relation = str(node.get("relation") or "foreign")
                relation_es = "mismo dueño" if relation == "same_owner" else "ajeno"
                declined = self._confirm_native(
                    "Confiar siempre en este nodo remoto",
                    f"Nombre declarado: {node.get('display_name')}\n"
                    f"Relación: {relation_es} ({relation})\n"
                    f"Huella completa: {node.get('device_fingerprint')}\n\n"
                    "Si confías en este nodo, sus mensajes futuros llegarán directamente a la bandeja de la "
                    "terminal destino sin pasar por cuarentena.",
                )
                if declined is not None:
                    return {**declined, "device_fingerprint": fp}

            res = service.set_policy(fp, policy_enum.value, actor="human_ui")
            if res.get("success") is False:
                return _fail(
                    str(res.get("code") or "INVALID_ARGUMENT"),
                    str(res.get("error") or "No se pudo actualizar la política del nodo."),
                )
            return res
        except Exception as exc:
            logger.warning("relay_set_node_policy fallo: %s", exc)
            return _fail("INTERNAL_ERROR", str(exc))

    def relay_get_local_node(self) -> Dict[str, Any]:
        try:
            stored = self.db.get_setting("node.display_name")
            display_name = (
                sanitize_node_name(stored)
                if stored
                else (sanitize_node_name(platform.node()) or "Este equipo")
            )
            if not display_name:
                display_name = "Este equipo"
            fingerprint: Optional[str] = None
            relay = self._relay()
            if relay is not None:
                try:
                    fingerprint = str(relay.identity.fingerprint())
                except Exception:
                    fingerprint = None
            kernel = self.core_kernel
            crypto = getattr(kernel, "crypto", None) if kernel is not None else None
            human_key_available = bool(crypto is not None and not getattr(crypto, "is_locked", True))
            return {
                "success": True,
                "display_name": display_name,
                "fingerprint": fingerprint,
                "fingerprint_short": format_fingerprint_short(fingerprint or ""),
                "human_key_available": human_key_available,
            }
        except Exception as exc:
            logger.warning("relay_get_local_node fallo: %s", exc)
            return _fail("INTERNAL_ERROR", str(exc))

    def relay_set_local_node_name(self, name: Any) -> Dict[str, Any]:
        try:
            if not isinstance(name, str):
                return _fail("INVALID_ARGUMENT", "El nombre del equipo debe ser texto.")
            clean = sanitize_node_name(name)
            if not clean:
                return _fail("INVALID_ARGUMENT", "El nombre del equipo no puede estar vacío.")
            self.db.set_setting("node.display_name", clean)
            return {"success": True, "display_name": clean}
        except Exception as exc:
            logger.warning("relay_set_local_node_name fallo: %s", exc)
            return _fail("INTERNAL_ERROR", str(exc))


def wire_nodes(bridge: Any) -> None:
    """Conecta `RemoteIntakeService` al relevo y arranca el barrido daemon de cuarentena."""
    kernel = getattr(bridge, "core_kernel", None)
    if kernel is None:
        return
    from src.core.factory import ApplicationFactory

    extensions = getattr(bridge, "extensions_ui", None)
    getter = getattr(extensions, "_get_pane_registry", None)
    registry = getter() if callable(getter) else None

    relay = ApplicationFactory.message_relay_service(kernel, pane_directory=registry)
    service = ApplicationFactory.remote_intake_service(kernel, pane_directory=registry)
    relay.add_release_listener(service.on_release)
    relay.remote_info = service.remote_info
    relay.quarantine_expirer = service.expire_quarantine

    if getattr(bridge, "_nodes_quarantine_stop", None) is not None:
        return

    stop_event = threading.Event()
    bridge._nodes_quarantine_stop = stop_event

    def _sweep_loop() -> None:
        while not stop_event.wait(_QUARANTINE_SWEEP_INTERVAL_S):
            try:
                service.expire_quarantine(int(time.time() * 1000))
            except Exception:
                logger.debug("Error en barrido periódico de cuarentena", exc_info=True)

    worker = threading.Thread(
        target=_sweep_loop,
        name="mcp2p-quarantine-expire",
        daemon=True,
    )
    worker.start()

    lifecycle = getattr(extensions, "pane_lifecycle", None)
    if lifecycle is not None and callable(getattr(lifecycle, "on_app_closing", None)):
        lifecycle.on_app_closing(lambda _alive: stop_event.set())
