"""
Supervisor con circuit breaker (`SafeTerminalHostSupervisor`) para `AgentTerminalPort`.

Sigue el patrón autocontenido de `src/adapters/network/supervisor.py`:
- Ninguna excepción del backend PTY primario se propaga al núcleo ni a la UI.
- Tras `failure_threshold` fallos internos consecutivos, el circuito pasa a `"open"`
  durante `cooldown_s`, delegando automáticamente al `NullAgentTerminalConnector`
  (`TERMINAL_ADAPTER_DEGRADED` + `manual_command` + `mcp_config`).
- Al vencer `cooldown_s`, pasa a `"half_open"` y un éxito lo devuelve a `"closed"`.
- Errores controlados de usuario/negocio devueltos sin excepción (`CLI_BINARY_NOT_FOUND`,
  `PANE_ALREADY_ACTIVE`, `SESSION_NOT_FOUND`, etc.) NO disparan el circuit breaker.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, List

from src.adapters.outbound.extension_gate import NullAgentTerminalConnector
from src.core.ports.connectors import AgentTerminalPort

logger = logging.getLogger("mcp2p.adapters.terminal.supervisor")


class SafeTerminalHostSupervisor:
    """Envoltorio tolerante a fallos con circuit breaker sobre un `AgentTerminalPort`."""

    def __init__(
        self,
        primary: AgentTerminalPort,
        fallback: NullAgentTerminalConnector,
        *,
        failure_threshold: int = 3,
        cooldown_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._lock = threading.RLock()
        self._primary = primary
        self._fallback = fallback
        self.failure_threshold = max(1, int(failure_threshold))
        self.cooldown_s = max(0.0, float(cooldown_s))
        self._clock = clock
        self._circuit: Dict[str, Any] = {
            "state": "closed",
            "failures": 0,
            "opened_at": 0.0,
            "last_error": None,
        }

    @property
    def primary(self) -> AgentTerminalPort:
        return self._primary

    @property
    def fallback(self) -> NullAgentTerminalConnector:
        return self._fallback

    @property
    def max_panes(self) -> int:
        """Maximo de paneles configurado en el conector primario (4 si el primario no lo define)."""
        value = getattr(self._primary, "max_panes", None)
        try:
            return int(value) if value else 4
        except (TypeError, ValueError):
            return 4

    def set_max_panes(self, value: int) -> int:
        setter = getattr(self._primary, "set_max_panes", None)
        if not callable(setter):
            raise ValueError("El conector de terminales activo no admite cambiar el maximo de paneles.")
        return int(setter(value))

    def _state(self) -> str:
        with self._lock:
            c = self._circuit
            if c["state"] == "open" and (self._clock() - c["opened_at"]) >= self.cooldown_s:
                c["state"] = "half_open"
            return str(c["state"])

    def get_circuit_state(self) -> Dict[str, str]:
        return {"primary": self._state()}

    def _record_success(self) -> None:
        with self._lock:
            c = self._circuit
            c["state"] = "closed"
            c["failures"] = 0
            c["last_error"] = None

    def _record_failure(self, exc: BaseException) -> None:
        with self._lock:
            c = self._circuit
            c["failures"] += 1
            c["last_error"] = f"{type(exc).__name__}: {exc}"
            if c["state"] == "half_open" or c["failures"] >= self.failure_threshold:
                c["state"] = "open"
                c["opened_at"] = self._clock()
        logger.warning("Conector primario de terminales falló: %s", exc)

    def _safe_primary_available(self) -> bool:
        try:
            avail_fn = getattr(self._primary, "is_available", None)
            if callable(avail_fn):
                return bool(avail_fn())
            return True
        except Exception as exc:
            self._record_failure(exc)
            return False

    def is_available(self) -> bool:
        if self._state() == "open":
            return False
        return self._safe_primary_available()

    def _invoke_fallback(self, method_name: str, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        circuit_state = self._state()
        if method_name == "poll_output":
            fb_poll = getattr(self._fallback, "poll_output", None)
            if callable(fb_poll):
                base = dict(fb_poll(*args, **kwargs))
            else:
                since_seq = kwargs.get("since_seq", args[1] if len(args) > 1 else 0)
                degraded_fn = getattr(self._fallback, "_degraded_response", None)
                base = dict(degraded_fn()) if callable(degraded_fn) else dict(self._fallback.terminate_session(*args, **kwargs))
                base.setdefault("alive", False)
                base.setdefault("exit_code", None)
                base.setdefault("rss_mb", 0.0)
                base.setdefault("since_seq", since_seq)
                base.setdefault("next_seq", since_seq)
                base.setdefault("truncated", False)
                base.setdefault("chunks", [])
                base.setdefault("text", "")
        else:
            fn = getattr(self._fallback, method_name)
            base = dict(fn(*args, **kwargs))

        base["supervisor_fallback"] = True
        base["circuit_state"] = circuit_state
        return base

    def _guard_dict(self, method_name: str, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        if self._state() == "open":
            return self._invoke_fallback(method_name, *args, **kwargs)

        if not self._safe_primary_available():
            return self._invoke_fallback(method_name, *args, **kwargs)

        try:
            fn = getattr(self._primary, method_name)
            res = fn(*args, **kwargs)
        except Exception as exc:
            self._record_failure(exc)
            return self._invoke_fallback(method_name, *args, **kwargs)

        if isinstance(res, dict) and res.get("host_failure"):
            self._record_failure(RuntimeError(str(res.get("error") or res.get("code") or "PTY host failure")))
            return self._invoke_fallback(method_name, *args, **kwargs)

        self._record_success()
        return res

    # ------------------------------------------------------------------
    # AgentTerminalPort + poll_output + close
    # ------------------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        circuit_state = self._state()
        primary_available = False
        primary_status: Dict[str, Any] = {}
        try:
            avail_fn = getattr(self._primary, "is_available", None)
            primary_available = bool(avail_fn()) if callable(avail_fn) else True
            primary_status = dict(self._primary.get_status())
        except Exception as exc:
            primary_available = False
            primary_status = {"error": str(exc), "available": False}

        try:
            fallback_status = dict(self._fallback.get_status())
        except Exception as exc:
            fallback_status = {"error": str(exc), "available": False}

        with self._lock:
            failures = int(self._circuit["failures"])
            last_error = self._circuit["last_error"]

        degraded = (circuit_state == "open") or (not primary_available)
        available = primary_available and (circuit_state != "open")

        return {
            "available": available,
            "degraded": degraded,
            "mode": primary_status.get("mode", "posix_pty") if not degraded else fallback_status.get("mode", "null_degraded"),
            "active_sessions": primary_status.get("active_sessions", 0) if not degraded else 0,
            "max_panes": primary_status.get("max_panes", 4),
            "hard_max_panes": primary_status.get("hard_max_panes", 6),
            "sessions": primary_status.get("sessions", []) if not degraded else [],
            "circuit_state": {"primary": circuit_state},
            "circuit": {
                "state": circuit_state,
                "consecutive_failures": failures,
                "failure_threshold": self.failure_threshold,
                "cooldown_s": self.cooldown_s,
                "last_error": last_error,
            },
            "primary": primary_status,
            "fallback": fallback_status,
        }

    def list_sessions(self) -> List[Dict[str, Any]]:
        if self._state() == "open":
            return list(self._fallback.list_sessions())
        if not self._safe_primary_available():
            return list(self._fallback.list_sessions())
        try:
            sessions = list(self._primary.list_sessions())
        except Exception as exc:
            self._record_failure(exc)
            return list(self._fallback.list_sessions())
        self._record_success()
        return sessions

    def spawn_session(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return self._guard_dict("spawn_session", *args, **kwargs)

    def send_input(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return self._guard_dict("send_input", *args, **kwargs)

    def resize_session(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return self._guard_dict("resize_session", *args, **kwargs)

    def pause_session(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return self._guard_dict("pause_session", *args, **kwargs)

    def poll_output(self, pane_id: Any = 1, since_seq: int = 0) -> Dict[str, Any]:
        return self._guard_dict("poll_output", pane_id, since_seq=since_seq)

    def close_session(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return self._guard_dict("close_session", *args, **kwargs)

    def terminate_session(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return self._guard_dict("terminate_session", *args, **kwargs)

    def close(self) -> None:
        for conn in (self._primary, self._fallback):
            close_fn = getattr(conn, "close", None)
            if callable(close_fn):
                try:
                    close_fn()
                except Exception as exc:
                    logger.debug("Error cerrando conector de terminales: %s", exc)
