"""
Confirmacion NATIVA del lado Python (EGM-74): un dialogo del sistema que el JS del webview no puede
pulsar ni falsificar, mostrado antes de acciones que ejecutan o persisten codigo.

Limite honesto: el token de confirmacion de instalacion es anti-accidente (lo emite y lo valida el mismo
puente al que llama el JS). La barrera real contra un JS comprometido es este dialogo. Lo que un JS
comprometido teclee en una shell YA abierta (`pty_write_input`) no se puede confirmar: no hay un limite
de confianza entre "el usuario teclea" y "el JS teclea" en ese canal.

Sin ventana (modo headless o tests) la accion se rechaza con `CONFIRMATION_UNAVAILABLE`, salvo que el
entorno del proceso fije `MCP2P_TEST_AUTO_CONFIRM=1` (solo para pruebas; el JS no puede cambiar el entorno).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("mcp2p.app.native_confirm")

TEST_AUTO_CONFIRM_ENV = "MCP2P_TEST_AUTO_CONFIRM"
CONFIRMATION_DECLINED = "CONFIRMATION_DECLINED"
CONFIRMATION_UNAVAILABLE = "CONFIRMATION_UNAVAILABLE"
# Tras este numero de rechazos seguidos se corta el goteo de dialogos durante `_COOLDOWN_S` (anti-molestia).
_MAX_DECLINES = 3
_COOLDOWN_S = 60.0
_MAX_MESSAGE_CHARS = 1500


class NativeConfirmationService:
    """Un dialogo nativo a la vez; el texto mostrado es SIEMPRE el que construye Python."""

    def __init__(
        self,
        window_getter: Callable[[], Any],
        *,
        dialog: Optional[Callable[[str, str], Any]] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._window_getter = window_getter
        # `dialog(title, message) -> bool` inyectable para tests; por defecto, el de PyWebView.
        self.dialog = dialog
        self._clock = clock
        self._lock = threading.Lock()
        self._declined_streak = 0
        self._blocked_until = 0.0

    def _default_dialog(self) -> Optional[Callable[[str, str], Any]]:
        window = self._window_getter()
        fn = getattr(window, "create_confirmation_dialog", None) if window is not None else None
        return fn if callable(fn) else None

    def confirm(self, title: str, message: str) -> Optional[Dict[str, Any]]:
        """`None` si el usuario acepto; si no, la respuesta de error `{success: False, code, message}`."""
        if os.environ.get(TEST_AUTO_CONFIRM_ENV) == "1":
            logger.warning("Confirmacion nativa omitida por %s (solo pruebas): %s", TEST_AUTO_CONFIRM_ENV, title)
            return None
        dialog = self.dialog or self._default_dialog()
        if dialog is None:
            return {
                "success": False,
                "code": CONFIRMATION_UNAVAILABLE,
                "message": "No hay ventana para pedir confirmacion nativa; la accion se rechaza.",
            }
        text = message if len(message) <= _MAX_MESSAGE_CHARS else message[:_MAX_MESSAGE_CHARS] + "\n[...recortado]"
        with self._lock:  # un dialogo a la vez
            if self._clock() < self._blocked_until:
                return {
                    "success": False,
                    "code": CONFIRMATION_DECLINED,
                    "message": "Demasiadas confirmaciones rechazadas seguidas; espere un minuto.",
                }
            try:
                answer = dialog(title, f"{title}\n\n{text}")
            except Exception as exc:
                logger.warning("El dialogo nativo fallo (%s); se rechaza la accion.", exc)
                return {
                    "success": False,
                    "code": CONFIRMATION_UNAVAILABLE,
                    "message": "No se pudo mostrar el dialogo nativo de confirmacion; la accion se rechaza.",
                }
            if answer is True:
                self._declined_streak = 0
                return None
            self._declined_streak += 1
            if self._declined_streak >= _MAX_DECLINES:
                self._blocked_until = self._clock() + _COOLDOWN_S
                self._declined_streak = 0
        return {
            "success": False,
            "code": CONFIRMATION_DECLINED,
            "message": "La accion fue rechazada en el dialogo de confirmacion.",
        }
