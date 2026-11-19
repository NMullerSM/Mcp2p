"""Adaptador UI del puente para gestión de rol y mandato por panel (v2.0.2, WP-3a)."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional, Tuple

from src.adapters.terminal.cli_catalog import current_platform, load_repo_catalog
from src.adapters.terminal.role_launch import readonly_mode_for_role, role_launch_verified
from src.app.ui_adapters import _BaseUIAdapter
from src.core.domain.roles import ROLE_LABELS_ES, PaneRole, normalize_role, sanitize_mandate

logger = logging.getLogger("mcp2p.app.ui_adapters.roles")


def _fail(code: str, message: str, **extra: Any) -> Dict[str, Any]:
    return {"success": False, "code": code, "message": message, **extra}


class RolesUIAdapter(_BaseUIAdapter):
    """Expone al humano (GUI) la lectura y asignación de rol y mandato de un panel."""

    def _pane_registry(self) -> Any:
        extensions = getattr(self._bridge, "extensions_ui", None)
        getter = getattr(extensions, "_get_pane_registry", None)
        return getter() if callable(getter) else None

    def _catalog_spec_and_platform(self, cli_profile: str) -> Tuple[Optional[Any], str]:
        platform = current_platform()
        extensions = getattr(self._bridge, "extensions_ui", None)
        cat_getter = getattr(extensions, "_catalog_service", None)
        if callable(cat_getter):
            try:
                svc = cat_getter()
                platform = getattr(svc, "platform", None) or platform
                spec = svc.get_spec(cli_profile)
                if spec is not None and getattr(spec, "source", "repo") == "repo":
                    return spec, platform
            except Exception:
                pass
        try:
            spec = load_repo_catalog().get_spec(cli_profile)
            return spec, platform
        except Exception:
            return None, platform

    def _audit_role_change(self, rec: Any, role_value: str) -> None:
        kernel = self.core_kernel
        if kernel is None:
            return
        from src.core.factory import ApplicationFactory

        registry = self._pane_registry()
        service = ApplicationFactory.message_relay_service(kernel, registry)
        store = getattr(service, "store", None) if service is not None else None
        if store is None:
            return
        store.append_audit(
            {
                "event": "role_changed",
                "project_uuid": getattr(rec, "project_uuid", "") or "",
                "from_principal": "human:local",
                "to_principal": f"pane:{rec.node_uuid}",
                "decision": role_value,
                "reason_code": None,
                "actor": "human_ui",
                "ts_ms": int(time.time() * 1000),
            }
        )

    def _build_role_payload(self, rec: Any) -> Dict[str, Any]:
        role_enum = normalize_role(getattr(rec, "role", None))
        spec, platform = self._catalog_spec_and_platform(str(getattr(rec, "cli_profile", "") or ""))
        return {
            "success": True,
            "code": "OK",
            "pane_id": rec.pane_id,
            "project_uuid": rec.project_uuid,
            "role": role_enum.value,
            "role_label": ROLE_LABELS_ES[role_enum],
            "mandate": str(getattr(rec, "mandate", "") or ""),
            "readonly": readonly_mode_for_role(role_enum, spec, platform),
            "relaunch_recommended": bool(
                role_launch_verified(spec, platform) and getattr(rec, "status", "active") != "closed"
            ),
        }

    def pty_set_pane_role(self, pane_id: Any, role: Any, mandate: Optional[str] = None) -> Dict[str, Any]:
        """Asigna el rol y mandato de un panel activo, persiste por (proyecto, ranura) y audita `role_changed`."""
        try:
            if pane_id is None or isinstance(pane_id, bool) or not str(pane_id).strip():
                return _fail("INVALID_ARGUMENT", "Indique el identificador del panel.")
            if not isinstance(role, (str, PaneRole)):
                return _fail("INVALID_ARGUMENT", "role debe ser un texto válido.")
            raw_role = role.value if isinstance(role, PaneRole) else role.strip().lower()
            try:
                role_enum = PaneRole(raw_role)
            except ValueError:
                allowed = ", ".join(r.value for r in PaneRole)
                return _fail("INVALID_ARGUMENT", f"Rol inválido: {role!r}. Permitidos: {allowed}.")
            if mandate is not None and not isinstance(mandate, str):
                return _fail("INVALID_ARGUMENT", "mandate debe ser texto o null.")

            registry = self._pane_registry()
            if registry is None:
                return _fail("PANE_NOT_FOUND", "El registro de paneles no está disponible.")
            try:
                rec = registry.get_pane(pane_id)
            except (TypeError, ValueError):
                rec = None
            if rec is None or rec.status == "closed":
                return _fail("PANE_NOT_FOUND", "No hay un panel activo con ese identificador.")

            clean_mandate = None if mandate is None else sanitize_mandate(mandate)
            updated = registry.set_role(rec.pane_id, role_enum, clean_mandate)
            if updated is None:
                return _fail("PANE_NOT_FOUND", "El panel se cerró antes de aplicar el cambio.")

            self._audit_role_change(updated, role_enum.value)
            return self._build_role_payload(updated)
        except Exception as exc:
            logger.warning("pty_set_pane_role falló: %s", exc)
            return _fail("INTERNAL_ERROR", str(exc))

    def pty_get_pane_role(self, pane_id: Any) -> Dict[str, Any]:
        """Devuelve el rol, etiqueta, mandato y recomendación de relanzamiento del panel activo."""
        try:
            if pane_id is None or isinstance(pane_id, bool) or not str(pane_id).strip():
                return _fail("INVALID_ARGUMENT", "Indique el identificador del panel.")
            registry = self._pane_registry()
            if registry is None:
                return _fail("PANE_NOT_FOUND", "El registro de paneles no está disponible.")
            try:
                rec = registry.get_pane(pane_id)
            except (TypeError, ValueError):
                rec = None
            if rec is None or rec.status == "closed":
                return _fail("PANE_NOT_FOUND", "No hay un panel activo con ese identificador.")
            return self._build_role_payload(rec)
        except Exception as exc:
            logger.warning("pty_get_pane_role falló: %s", exc)
            return _fail("INTERNAL_ERROR", str(exc))
