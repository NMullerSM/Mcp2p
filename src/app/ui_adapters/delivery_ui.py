"""Adaptador UI de la entrega directa (WP-6b): modo por terminal, estado, hilos y texto del humano.

Todo lo que sale de aquí es acción del HUMANO desde la GUI. Nada de esto existe como herramienta MCP.
`wire_delivery` compone `DirectDeliveryService` cuando ya existen el conector de terminales y el Hub (primer panel).
"""

from __future__ import annotations

import logging
import sys
import threading
from typing import Any, Dict, List, Optional

from src.app.ui_adapters import _BaseUIAdapter
from src.core.services.direct_delivery_service import DeliverySettings

logger = logging.getLogger("mcp2p.app.ui_adapters.delivery")

DEFAULT_MODE_SETTING = "terminal.delivery_default"
BATCH_DEFAULT_SETTING = "terminal.delivery_batch_default"
_PLATFORMS = ("linux", "darwin", "win32")


def _fail(code: str, message: str, **extra: Any) -> Dict[str, Any]:
    return {"success": False, "code": code, "message": message, **extra}


def _setting(db: Any, key: str, default: str) -> str:
    try:
        value = db.get_setting(key, default)
    except Exception:
        return default
    return value if isinstance(value, str) and value else default


class DeliveryUIAdapter(_BaseUIAdapter):
    def _registry(self) -> Any:
        extensions = getattr(self._bridge, "extensions_ui", None)
        getter = getattr(extensions, "_get_pane_registry", None)
        return getter() if callable(getter) else None

    def _service(self) -> Any:
        kernel = self.core_kernel
        return getattr(kernel, "direct_delivery_service", None) if kernel is not None else None

    def _relay(self) -> Any:
        relay_ui = getattr(self._bridge, "relay_ui", None)
        return relay_ui._service() if relay_ui is not None else None

    def _catalog(self) -> Any:
        extensions = getattr(self._bridge, "extensions_ui", None)
        return extensions._catalog_service() if extensions is not None else None

    def _default_mode(self) -> str:
        mode = _setting(self.db, DEFAULT_MODE_SETTING, "direct")
        return mode if mode in ("direct", "inbox") else "direct"

    # -- modo por terminal ---------------------------------------------------------------------------------------

    def pty_set_delivery_mode(self, pane_id: Any, mode: Any) -> Dict[str, Any]:
        try:
            if mode not in ("auto", "direct", "inbox"):
                return _fail("INVALID_ARGUMENT", "El modo debe ser auto, direct o inbox.")
            registry = self._registry()
            try:
                rec = registry.get_pane(pane_id)
            except (TypeError, ValueError):
                rec = None
            if rec is None or rec.status == "closed":
                return _fail("PANE_NOT_FOUND", "No hay un panel activo con ese identificador.")

            def _apply() -> Dict[str, Any]:
                updated = registry.set_delivery(rec.pane_id, mode=mode)
                if updated is None:
                    return _fail("PANE_NOT_FOUND", "El panel se cerró antes de aplicar el cambio.")
                self._poke(updated)
                return {"success": True, "code": "OK", "pane_id": updated.pane_id, "delivery_mode": updated.delivery_mode}

            if mode != "direct":
                return _apply()
            gate = getattr(self._bridge, "confirm_gate", None)
            if gate is None:
                return _fail("CONFIRMATION_UNAVAILABLE", "No hay servicio de confirmación; la acción se rechaza.")
            return gate.require(
                "delivery.enable_pane",
                "Activar la entrega directa en una terminal",
                f"Con la entrega directa, MCP2P escribirá en la terminal {str(rec.pane_id).split('_')[-1]} "
                f"({rec.cli_profile}) los mensajes de otros agentes cuando el agente quede libre, con una cabecera "
                "que indica quién los envía. Nunca mientras usted escribe ni a terminales con permisos saltados.",
                _apply,
                args_digest=f"delivery:{rec.project_uuid}:{rec.pane_id}:direct",
            )
        except Exception as exc:
            return _fail("INTERNAL_ERROR", str(exc))

    def pty_set_delivery_batch(self, pane_id: Any, enabled: Any) -> Dict[str, Any]:
        try:
            if not isinstance(enabled, bool):
                return _fail("INVALID_ARGUMENT", "enabled debe ser booleano.")
            registry = self._registry()
            try:
                updated = registry.set_delivery(pane_id, batch=enabled)
            except (TypeError, ValueError):
                updated = None
            if updated is None:
                return _fail("PANE_NOT_FOUND", "No hay un panel activo con ese identificador.")
            return {"success": True, "code": "OK", "pane_id": updated.pane_id, "delivery_batch": bool(updated.delivery_batch)}
        except Exception as exc:
            return _fail("INTERNAL_ERROR", str(exc))

    def _poke(self, rec: Any) -> None:
        service = self._service()
        if service is not None:
            service.on_wake(str(rec.node_uuid))

    # -- ajustes globales ----------------------------------------------------------------------------------------

    def relay_set_delivery_default(self, mode: Any) -> Dict[str, Any]:
        if mode not in ("direct", "inbox"):
            return _fail("INVALID_ARGUMENT", "El valor debe ser direct o inbox.")
        try:
            self.db.set_setting(DEFAULT_MODE_SETTING, mode)
        except Exception as exc:
            return _fail("INTERNAL_ERROR", str(exc))
        return {"success": True, "code": "OK", "default_mode": mode}

    def relay_get_delivery_overview(self) -> Dict[str, Any]:
        try:
            verified: List[Dict[str, Any]] = []
            catalog = self._catalog()
            for spec in catalog.catalog().profiles.values():
                if spec.kind == "shell":
                    continue
                for platform in _PLATFORMS:
                    ok = bool(spec.capability_verified("delivery", platform) and spec.capability_verified("activity", platform))
                    verified.append({"cli_id": spec.id, "display_name": spec.display_name, "platform": platform,
                                     "verified": ok})
            return {
                "success": True,
                "code": "OK",
                "default_mode": self._default_mode(),
                "batch_default": _setting(self.db, BATCH_DEFAULT_SETTING, "0") == "1",
                "verified": verified,
            }
        except Exception as exc:
            return _fail("INTERNAL_ERROR", str(exc))

    # -- estado, hilos y texto del humano ----------------------------------------------------------------------------

    def relay_get_delivery_status(self, project_uuid: Any) -> Dict[str, Any]:
        try:
            service = self._service()
            if service is None:
                return {"success": True, "code": "OK", "panes": [], "notices": []}
            return {"success": True, "code": "OK", "panes": service.status(str(project_uuid or "")),
                    "notices": service.notices()}
        except Exception as exc:
            return _fail("INTERNAL_ERROR", str(exc))

    def relay_list_threads(self, project_uuid: Any, since_ms: Any = 0, limit: Any = 200) -> Dict[str, Any]:
        try:
            relay = self._relay()
            if relay is None:
                return _fail("RELAY_UNAVAILABLE", "El relevo no está disponible.")
            rows = relay.thread_view(str(project_uuid or ""), since_ms=int(since_ms or 0), limit=int(limit or 200))
            return {"success": True, "code": "OK", "threads": rows}
        except Exception as exc:
            return _fail("INTERNAL_ERROR", str(exc))

    def relay_type_to_panes(self, project_uuid: Any, pane_ids: Any, text: Any) -> Dict[str, Any]:
        try:
            service = self._service()
            if service is None:
                return _fail("DELIVERY_UNAVAILABLE", "Abre al menos una terminal antes de escribir en ellas.")
            if not isinstance(pane_ids, (list, tuple)):
                return _fail("INVALID_ARGUMENT", "pane_ids debe ser una lista de terminales.")
            out = service.type_as_human([str(p) for p in pane_ids], text, project_uuid=str(project_uuid or ""))
            if out.get("success") is False and "message" not in out and "error" in out:
                out = {**out, "message": out["error"]}
            return out
        except Exception as exc:
            return _fail("INTERNAL_ERROR", str(exc))


def _build_capabilities(catalog_getter: Any):
    def capabilities(rec: Any) -> Dict[str, Any]:
        svc = catalog_getter()
        spec = svc.get_spec(str(getattr(rec, "cli_profile", "") or "")) if svc is not None else None
        if spec is None:
            return {"delivery_verified": False, "activity_verified": False}
        plat = sys.platform
        delivery = dict(spec.delivery)
        return {
            "delivery_verified": bool(spec.capability_verified("delivery", plat)),
            "activity_verified": bool(spec.capability_verified("activity", plat)),
            "paste_mode": str(delivery.get("paste_mode") or "bracketed"),
            "submit_delay_ms": int(delivery.get("submit_delay_ms") or 300),
            "max_inject_bytes": int(delivery.get("max_inject_bytes") or 2048),
            "confirm_event": str(delivery.get("confirm_event") or ""),
        }

    return capabilities


def _build_privilege_of(catalog_getter: Any):
    from src.adapters.identity.peer_process import PsutilPeerProcessResolver
    from src.core.domain.privilege import classify_pane
    from src.core.ports.identity import ProcessIdentity

    processes = PsutilPeerProcessResolver()

    def privilege_of(rec: Any) -> str:
        profile = str(getattr(rec, "cli_profile", "") or "")
        svc = catalog_getter()
        spec = svc.get_spec(profile) if svc is not None else None
        kind = getattr(spec, "kind", None) if spec is not None else None
        argvs = None
        pid, ctime = getattr(rec, "pid", None), getattr(rec, "pid_create_time", None)
        if pid and ctime is not None:
            try:
                argvs = processes.tree_cmdlines(ProcessIdentity(int(pid), float(ctime)))
            except Exception:
                argvs = None
        return classify_pane(profile=profile, catalog_kind=kind, argvs=argvs)

    return privilege_of


def wire_delivery(bridge: Any) -> None:
    """Compone la entrega directa en el primer panel abierto (ya existen el conector de terminales y el Hub)."""
    kernel = getattr(bridge, "core_kernel", None)
    extensions = getattr(bridge, "extensions_ui", None)
    lifecycle = getattr(extensions, "pane_lifecycle", None)
    if kernel is None or lifecycle is None:
        return

    ensure_lock = threading.Lock()

    def _ensure(_ev: Any = None) -> None:
        with ensure_lock:
            _ensure_locked()

    def _ensure_locked() -> None:
        if getattr(kernel, "direct_delivery_service", None) is not None:
            return
        from src.adapters.terminal.direct_delivery_writer import TerminalPaneInput
        from src.core.factory import ApplicationFactory
        from src.core.ports.delivery import LocalOnlyDeliveryPolicy

        registry = extensions._get_pane_registry()
        relay = ApplicationFactory.message_relay_service(kernel, pane_directory=registry)
        activity = ApplicationFactory.agent_activity_registry(kernel)
        remote = getattr(kernel, "remote_intake_service", None)
        policy = remote if remote is not None and callable(getattr(remote, "allows_injection", None)) else \
            LocalOnlyDeliveryPolicy(lambda mid: callable(relay.remote_info) and relay.remote_info(mid) is not None)

        def _settings() -> DeliverySettings:
            mode = _setting(bridge.db, DEFAULT_MODE_SETTING, "direct")
            return DeliverySettings(default_mode=mode if mode in ("direct", "inbox") else "direct")

        service = ApplicationFactory.direct_delivery_service(
            kernel,
            pane_directory=registry,
            pane_input=TerminalPaneInput(extensions._get_terminal_connector()),
            privilege_of=_build_privilege_of(extensions._catalog_service),
            capabilities=_build_capabilities(extensions._catalog_service),
            settings_reader=_settings,
            remote_policy=policy,
        )
        relay.add_wake_listener(service.on_wake)
        activity.subscribe(service.on_activity)
        service.start()
        if callable(getattr(lifecycle, "on_app_closing", None)):
            lifecycle.on_app_closing(lambda _alive: service.stop())

    lifecycle.on_spawned(_ensure)
