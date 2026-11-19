"""Puerta de confirmaciones recordables y broker en memoria (WP-9).

Coordina:
- Acciones `native`: delegan siempre en `NativeConfirmationService.confirm(...)` sin leer la BD.
- Acciones `app`: consultan `app_settings["confirm.remember.<action_id>"]` SOLO si `action_id in REMEMBERABLE`,
  si no emiten un token único (TTL 120 s, ligado a `action_id` y `args_digest`) para `AppConfirmDialog`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.app.native_confirm import (
    CONFIRMATION_DECLINED,
    CONFIRMATION_UNAVAILABLE,
    NativeConfirmationService,
    _test_auto_confirm_enabled,
)
from src.app.version import __version__ as _APP_VERSION
from src.core.domain.confirmations import CONFIRMATION_TABLE, REMEMBERABLE

DEFAULT_CONFIRMATION_TTL_S = 120.0
MAX_PENDING_CONFIRMATIONS = 64
_MAX_TOKEN_LEN = 256
PREF_KEY_PREFIX = "confirm.remember."
CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
CONFIRMATION_TOKEN_INVALID = "CONFIRMATION_TOKEN_INVALID"
CONFIRMATION_TOKEN_EXPIRED = "CONFIRMATION_TOKEN_EXPIRED"


@dataclass(frozen=True)
class _PendingConfirmation:
    digest: bytes
    action_id: str
    args_digest: str
    execute: Callable[[], Dict[str, Any]]
    expires_at: float


def _hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


class AppConfirmationBroker:
    """Broker en memoria de tokens de confirmación de un solo uso (nunca persistido)."""

    def __init__(
        self,
        *,
        ttl_s: float = DEFAULT_CONFIRMATION_TTL_S,
        max_pending: int = MAX_PENDING_CONFIRMATIONS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl_s = float(ttl_s)
        self._max_pending = max(1, int(max_pending))
        self._clock = clock
        self._lock = threading.Lock()
        self._pending: Dict[bytes, _PendingConfirmation] = {}

    def issue(
        self,
        action_id: str,
        execute: Callable[[], Dict[str, Any]],
        *,
        args_digest: str = "",
    ) -> Tuple[str, float]:
        """Emite un token único con TTL 120 s ligado a `(action_id, args_digest)` y su closure."""
        token = secrets.token_urlsafe(32)
        digest = _hash_token(token)
        now = self._clock()
        clean_args_digest = str(args_digest or "")
        record = _PendingConfirmation(
            digest=digest,
            action_id=action_id,
            args_digest=clean_args_digest,
            execute=execute,
            expires_at=now + self.ttl_s,
        )
        with self._lock:
            self._prune_locked(now)
            for key in [
                k
                for k, v in self._pending.items()
                if v.action_id == action_id and v.args_digest == clean_args_digest
            ]:
                del self._pending[key]
            while len(self._pending) >= self._max_pending:
                oldest = min(self._pending.values(), key=lambda r: r.expires_at)
                del self._pending[oldest.digest]
            self._pending[digest] = record
        return token, self.ttl_s

    def pending_count(self) -> int:
        with self._lock:
            self._prune_locked(self._clock())
            return len(self._pending)

    def _prune_locked(self, now: float) -> None:
        # Se retienen brevemente los vencidos para responder CONFIRMATION_TOKEN_EXPIRED.
        grace = max(self.ttl_s, 60.0)
        for key in [k for k, v in self._pending.items() if now > v.expires_at + grace]:
            del self._pending[key]

    def consume_status(
        self, token: object
    ) -> Tuple[str, Optional[str], Optional[Callable[[], Dict[str, Any]]], str]:
        """Consume `token` y devuelve `(status, action_id, execute, args_digest)`."""
        if not isinstance(token, str) or not token or len(token) > _MAX_TOKEN_LEN:
            return CONFIRMATION_TOKEN_INVALID, None, None, ""
        candidate = _hash_token(token)
        now = self._clock()
        with self._lock:
            matched: Optional[_PendingConfirmation] = None
            for record in self._pending.values():
                if hmac.compare_digest(record.digest, candidate):
                    matched = record
            if matched is None:
                return CONFIRMATION_TOKEN_INVALID, None, None, ""
            del self._pending[matched.digest]
            self._prune_locked(now)
        if now > matched.expires_at:
            return CONFIRMATION_TOKEN_EXPIRED, matched.action_id, None, matched.args_digest
        return "OK", matched.action_id, matched.execute, matched.args_digest

    def consume(
        self,
        token: object,
        *,
        expected_action_id: Optional[str] = None,
        expected_args_digest: Optional[str] = None,
    ) -> Optional[Tuple[str, Callable[[], Dict[str, Any]]]]:
        """Consume un token válido y vigente; devuelve `(action_id, execute)` o `None`."""
        status, action_id, execute, args_digest = self.consume_status(token)
        if status != "OK" or action_id is None or execute is None:
            return None
        if expected_action_id is not None and not hmac.compare_digest(
            action_id.encode("utf-8"), expected_action_id.encode("utf-8")
        ):
            return None
        if expected_args_digest is not None and not hmac.compare_digest(
            args_digest.encode("utf-8"), expected_args_digest.encode("utf-8")
        ):
            return None
        return action_id, execute


class ConfirmationGate:
    """Punto único de decisión para confirmaciones nativas y propias recordables."""

    def __init__(
        self,
        native: Optional[NativeConfirmationService],
        db: Any,
        *,
        broker: Optional[AppConfirmationBroker] = None,
        app_version: Optional[str] = None,
    ) -> None:
        self.native = native
        self.db = db
        self._broker = broker or AppConfirmationBroker()
        self._app_version = app_version or _APP_VERSION

    @staticmethod
    def _pref_key(action_id: str) -> str:
        return f"{PREF_KEY_PREFIX}{action_id}"

    def _read_remembered_record(self, action_id: str) -> Optional[Dict[str, Any]]:
        if action_id not in REMEMBERABLE or self.db is None:
            return None
        getter = getattr(self.db, "get_setting", None)
        if not callable(getter):
            return None
        try:
            raw = getter(self._pref_key(action_id))
        except Exception:
            return None
        if not isinstance(raw, str) or not raw.strip():
            return None
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return None
        if not isinstance(parsed, dict):
            return None
        if type(parsed.get("v")) is not int or parsed.get("v") != 1:
            return None
        if parsed.get("remember") is not True:
            return None
        return parsed

    def _save_remembered(self, action_id: str) -> None:
        if action_id not in REMEMBERABLE or self.db is None:
            return
        setter = getattr(self.db, "set_setting", None)
        if not callable(setter):
            return
        payload = {
            "v": 1,
            "remember": True,
            "set_at": datetime.now(timezone.utc).isoformat(),
            "app_version": self._app_version,
        }
        setter(self._pref_key(action_id), json.dumps(payload, ensure_ascii=False))

    def require(
        self,
        action_id: str,
        title: str,
        message: str,
        execute: Callable[[], Dict[str, Any]],
        *,
        args_digest: str = "",
    ) -> Dict[str, Any]:
        """Exige confirmación según la superficie normativa de `action_id`."""
        spec = CONFIRMATION_TABLE.get(action_id)
        if spec is None:
            raise ValueError(f"Acción de confirmación desconocida: {action_id!r}")

        if spec.surface == "native":
            if self.native is None:
                return {
                    "success": False,
                    "code": CONFIRMATION_UNAVAILABLE,
                    "message": "No hay servicio de confirmacion nativa; la accion se rechaza.",
                }
            declined = self.native.confirm(title, message)
            if declined is not None:
                return declined
            return execute()

        if spec.surface != "app":
            raise ValueError(f"Superficie no soportada por ConfirmationGate: {spec.surface!r}")

        if action_id in REMEMBERABLE and self._read_remembered_record(action_id) is not None:
            return execute()

        if _test_auto_confirm_enabled():
            return execute()

        token, expires_in_s = self._broker.issue(action_id, execute, args_digest=args_digest)
        return {
            "success": False,
            "code": CONFIRMATION_REQUIRED,
            "message": title,
            "confirmation": {
                "token": token,
                "action_id": action_id,
                "title": title,
                "message": message,
                "rememberable": action_id in REMEMBERABLE,
                "expires_in_s": expires_in_s,
            },
        }

    def confirm(self, token: object, accepted: object, remember: object = False) -> Dict[str, Any]:
        """Valida `token`; si `accepted is True`, guarda preferencia si procede y ejecuta la acción."""
        status, action_id, execute, _args_digest = self._broker.consume_status(token)
        if status == CONFIRMATION_TOKEN_EXPIRED:
            return {
                "success": False,
                "code": CONFIRMATION_TOKEN_EXPIRED,
                "message": "La confirmación venció; vuelva a intentarlo.",
            }
        if status != "OK" or action_id is None or execute is None:
            return {
                "success": False,
                "code": CONFIRMATION_TOKEN_INVALID,
                "message": "Token de confirmación inválido o ya usado.",
            }
        if accepted is not True:
            return {
                "success": False,
                "code": CONFIRMATION_DECLINED,
                "message": "La accion fue rechazada en el dialogo de confirmacion.",
            }
        result = execute()
        if remember is True and action_id in REMEMBERABLE:
            if not (isinstance(result, dict) and result.get("success") is False):
                self._save_remembered(action_id)
        return result

    def list_prefs(self) -> List[Dict[str, Any]]:
        """Lista las preferencias recordadas válidas (solo acciones en `REMEMBERABLE`)."""
        items: List[Dict[str, Any]] = []
        for spec in CONFIRMATION_TABLE.values():
            if spec.action_id not in REMEMBERABLE:
                continue
            record = self._read_remembered_record(spec.action_id)
            if record is None:
                continue
            set_at = record.get("set_at")
            app_version = record.get("app_version")
            items.append(
                {
                    "action_id": spec.action_id,
                    "label_es": spec.label_es,
                    "risk": spec.risk,
                    "remember": True,
                    "set_at": set_at if isinstance(set_at, str) else None,
                    "app_version": app_version if isinstance(app_version, str) else None,
                }
            )
        return items

    def forget(self, action_id: object) -> bool:
        """Olvida la preferencia recordada de una acción recordable."""
        if not isinstance(action_id, str) or action_id not in REMEMBERABLE or self.db is None:
            return False
        deleter = getattr(self.db, "delete_setting", None)
        if not callable(deleter):
            return False
        return bool(deleter(self._pref_key(action_id)))

    def forget_all(self) -> int:
        """Olvida todas las preferencias recordadas válidas y devuelve cuántas había."""
        removed = 0
        for action_id in REMEMBERABLE:
            if self.forget(action_id):
                removed += 1
        return removed
