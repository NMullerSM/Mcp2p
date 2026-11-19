"""Escritor del PTY para la entrega directa (D12-ter, camino 2).

ÚNICO punto de `src/` (junto a `ExtensionsLabUIAdapter.pty_write_input`, que es el teclado del humano) que llama a
`send_input` del conector de terminales. Solo lo importan `src/core/factory.py` y `delivery_ui.py`; lo vigila
`tests/test_d12ter_pty_write_paths.py`. No decide nada: escribe lo que `DirectDeliveryService` ya validó, con la
encarnación del PTY comprobada dentro del cerrojo de la sesión.
"""

from __future__ import annotations

from typing import Any, Optional

from src.core.ports.delivery import WriteResult

_GONE_CODES = frozenset({"SESSION_NOT_FOUND", "SESSION_NOT_ALIVE"})


class TerminalPaneInput:
    """Implementa `PaneInputPort` sobre el conector de terminales del kernel."""

    def __init__(self, connector: Any) -> None:
        self._connector = connector

    def current_incarnation(self, pane_id: str) -> Optional[str]:
        """`session_id` de la sesión viva del panel (identifica la encarnación del PTY) o None."""
        try:
            sessions = self._connector.list_sessions()
        except Exception:
            return None
        for item in sessions or []:
            if str(item.get("pane_id")) == str(pane_id) and item.get("alive"):
                sid = item.get("session_id")
                return str(sid) if sid else None
        return None

    def last_output_age_s(self, pane_id: str) -> Optional[float]:
        """Segundos desde la última salida del PTY (solo puede NEGAR la entrega, nunca habilitarla)."""
        try:
            info = self._connector.get_pane_activity(pane_id)
        except Exception:
            return None
        value = info.get("silence_s") if isinstance(info, dict) else None
        return float(value) if isinstance(value, (int, float)) else None

    def write(self, pane_id: str, data: str, *, incarnation: str) -> WriteResult:
        try:
            res = self._connector.send_input(pane_id, data, expected_session_id=incarnation)
        except Exception:
            return WriteResult(ok=False, bytes_written=0, code="IO_ERROR")
        if not isinstance(res, dict):
            return WriteResult(ok=False, bytes_written=0, code="IO_ERROR")
        written = int(res.get("bytes_written") or 0)
        if res.get("success") is True:
            return WriteResult(ok=True, bytes_written=written, code="OK")
        code = str(res.get("code") or "")
        if code == "INCARNATION_CHANGED":
            return WriteResult(ok=False, bytes_written=0, code="INCARNATION_CHANGED")
        if code in _GONE_CODES:
            return WriteResult(ok=False, bytes_written=written, code="PANE_GONE")
        return WriteResult(ok=False, bytes_written=written, code="IO_ERROR")
