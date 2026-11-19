"""Adaptador UI del puente para confirmaciones propias y preferencias recordadas (WP-9)."""

from __future__ import annotations

import logging
from typing import Any, Dict

from src.app.ui_adapters import _BaseUIAdapter
from src.core.domain.confirmations import REMEMBERABLE

logger = logging.getLogger("mcp2p.app.ui_adapters.confirmations")


def _fail(code: str, message: str, **extra: Any) -> Dict[str, Any]:
    return {"success": False, "code": code, "message": message, **extra}


class ConfirmationsUIAdapter(_BaseUIAdapter):
    """Expone `ui_confirm_action` y la gestión de confirmaciones recordadas solo a la GUI."""

    def _gate(self) -> Any:
        return getattr(self._bridge, "confirm_gate", None)

    def ui_confirm_action(self, token: Any, accepted: Any, remember: Any = False) -> Dict[str, Any]:
        """Resuelve una confirmación propia pendiente identificada por su token único."""
        try:
            gate = self._gate()
            if gate is None:
                return _fail("CONFIRMATION_UNAVAILABLE", "No hay puerta de confirmaciones disponible.")
            return gate.confirm(token=token, accepted=accepted, remember=remember)
        except Exception as exc:
            logger.warning("ui_confirm_action fallo: %s", exc)
            return _fail("INTERNAL_ERROR", str(exc))

    def ui_list_confirmation_prefs(self) -> Dict[str, Any]:
        """Devuelve la lista de acciones propias cuya confirmación está recordada."""
        try:
            gate = self._gate()
            if gate is None:
                return _fail("CONFIRMATION_UNAVAILABLE", "No hay puerta de confirmaciones disponible.")
            prefs = gate.list_prefs()
            return {"success": True, "code": "OK", "prefs": prefs}
        except Exception as exc:
            logger.warning("ui_list_confirmation_prefs fallo: %s", exc)
            return _fail("INTERNAL_ERROR", str(exc))

    def ui_forget_confirmation(self, action_id: Any) -> Dict[str, Any]:
        """Elimina la preferencia recordada de una acción recordable."""
        try:
            if not isinstance(action_id, str) or action_id not in REMEMBERABLE:
                return _fail("INVALID_ARGUMENT", "Indique un action_id recordable válido.")
            gate = self._gate()
            if gate is None:
                return _fail("CONFIRMATION_UNAVAILABLE", "No hay puerta de confirmaciones disponible.")
            removed = gate.forget(action_id)
            return {"success": True, "code": "OK", "action_id": action_id, "removed": bool(removed)}
        except Exception as exc:
            logger.warning("ui_forget_confirmation fallo: %s", exc)
            return _fail("INTERNAL_ERROR", str(exc))

    def ui_forget_all_confirmations(self) -> Dict[str, Any]:
        """Elimina todas las confirmaciones recordadas."""
        try:
            gate = self._gate()
            if gate is None:
                return _fail("CONFIRMATION_UNAVAILABLE", "No hay puerta de confirmaciones disponible.")
            removed_count = gate.forget_all()
            return {"success": True, "code": "OK", "removed_count": int(removed_count)}
        except Exception as exc:
            logger.warning("ui_forget_all_confirmations fallo: %s", exc)
            return _fail("INTERNAL_ERROR", str(exc))
