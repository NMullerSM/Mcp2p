"""Endpoint ASGI para recibir hooks de CLIs de agente en el Hub embebido (v2.0.2, WP-6a)."""

from __future__ import annotations

import json
import re
import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, Mapping, Optional

from src.core.domain.agent_activity import NORMALIZED_EVENTS
from src.mcp.pane_registry import get_current_pane_session
from src.mcp.peer_binding import relay_peer_allowed

HOOK_PATH_PREFIX = "/hooks/v1/"
MAX_HOOK_BODY_BYTES = 64 * 1024
DEFAULT_RATE_LIMIT_PER_SEC = 50

_EVENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,47}$")
_PAYLOAD_TYPE_RE = re.compile(r"[A-Za-z0-9_.-]{1,48}")
_CLI_SESSION_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")


class HooksRouter:
    """Enruta `POST /hooks/v1/*` hacia `hook_app` y el resto hacia `mcp_app`."""

    def __init__(self, mcp_app: Any, hook_app: Any) -> None:
        self.mcp_app = mcp_app
        self.hook_app = hook_app

    async def __call__(self, scope: Dict[str, Any], receive: Any, send: Any) -> None:
        if (
            scope.get("type") == "http"
            and (scope.get("method") or "").upper() == "POST"
            and (scope.get("path") or "").startswith(HOOK_PATH_PREFIX)
        ):
            await self.hook_app(scope, receive, send)
            return
        await self.mcp_app(scope, receive, send)


class HookEndpoint:
    """Endpoint ASGI `POST /hooks/v1/<evento>` montado detrás de `_PaneTokenAuthMiddleware`."""

    def __init__(
        self,
        activity: Any = None,
        *,
        activity_getter: Optional[Callable[[], Any]] = None,
        catalog_lookup: Optional[Callable[[str], Optional[Any]]] = None,
        clock: Optional[Callable[[], float]] = None,
        clock_ms: Optional[Callable[[], int]] = None,
        rate_limit_per_sec: int = DEFAULT_RATE_LIMIT_PER_SEC,
    ) -> None:
        self._activity_getter = activity_getter or (lambda: activity)
        self._catalog_lookup = catalog_lookup
        self._default_catalog: Any = None
        self._clock = clock or time.monotonic
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._rate_limit = max(1, int(rate_limit_per_sec))
        self._rate_lock = threading.Lock()
        self._rate_windows: Dict[str, Deque[float]] = {}

    def _lookup_spec(self, cli_profile: str) -> Optional[Any]:
        if self._catalog_lookup is not None:
            return self._catalog_lookup(cli_profile)
        if self._default_catalog is None:
            from src.adapters.terminal.cli_catalog import CliCatalogService

            self._default_catalog = CliCatalogService(include_user_catalog=False)
        return self._default_catalog.get_spec(cli_profile)

    def _allow_rate(self, pane_key: str) -> bool:
        now = float(self._clock())
        cutoff = now - 1.0
        with self._rate_lock:
            dq = self._rate_windows.get(pane_key)
            if dq is None:
                dq = deque()
                self._rate_windows[pane_key] = dq
            while dq and dq[0] <= cutoff:
                dq.popleft()
            if len(dq) >= self._rate_limit:
                return False
            dq.append(now)
            return True

    @staticmethod
    def _resolve_raw_key(raw_event: str, payload: Mapping[str, Any]) -> Optional[str]:
        if raw_event == "codex.notify":
            raw_type = payload.get("type")
            if isinstance(raw_type, str) and _PAYLOAD_TYPE_RE.fullmatch(raw_type) is not None:
                return f"codex.notify.{raw_type}"
            return None
        if raw_event == "opencode.event":
            raw_type = payload.get("type")
            if not isinstance(raw_type, str):
                ev_obj = payload.get("event")
                raw_type = ev_obj.get("type") if isinstance(ev_obj, Mapping) else None
            if isinstance(raw_type, str) and _PAYLOAD_TYPE_RE.fullmatch(raw_type) is not None:
                return f"opencode.event.{raw_type}"
            return None
        return raw_event

    @staticmethod
    def _extract_session_id(payload: Mapping[str, Any], field_name: str) -> Optional[str]:
        if not field_name:
            return None
        val: Any = None
        if field_name in payload:
            val = payload[field_name]
        elif "." in field_name:
            cur: Any = payload
            for part in field_name.split("."):
                if isinstance(cur, Mapping) and part in cur:
                    cur = cur[part]
                else:
                    cur = None
                    break
            val = cur
        if isinstance(val, str) and _CLI_SESSION_ID_RE.fullmatch(val) is not None:
            return val
        return None

    @staticmethod
    async def _send_json(send: Any, status: int, body: Dict[str, Any]) -> None:
        raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(raw)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": raw, "more_body": False})

    @staticmethod
    def _content_length_header(scope: Mapping[str, Any]) -> Optional[str]:
        for raw_name, raw_val in scope.get("headers") or ():
            if raw_name.decode("latin-1").lower() == "content-length":
                return raw_val.decode("latin-1").strip()
        return None

    async def __call__(self, scope: Dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            return

        if (scope.get("method") or "").upper() != "POST":
            await self._send_json(send, 405, {"ok": False, "code": "METHOD_NOT_ALLOWED"})
            return

        rec = get_current_pane_session()
        if rec is None:
            await self._send_json(send, 401, {"ok": False, "code": "UNAUTHORIZED"})
            return

        if not relay_peer_allowed():
            await self._send_json(send, 403, {"ok": False, "code": "PEER_PROCESS_MISMATCH"})
            return

        activity = self._activity_getter()
        if activity is None:
            await self._send_json(send, 503, {"ok": False, "code": "ACTIVITY_UNAVAILABLE"})
            return

        path = scope.get("path") or ""
        if not path.startswith(HOOK_PATH_PREFIX):
            await self._send_json(send, 404, {"ok": False, "code": "NOT_FOUND"})
            return
        raw_event = path[len(HOOK_PATH_PREFIX) :]
        if _EVENT_RE.fullmatch(raw_event) is None:
            await self._send_json(send, 400, {"ok": False, "code": "INVALID_EVENT"})
            return

        raw_cl = self._content_length_header(scope)
        if raw_cl is not None:
            try:
                cl = int(raw_cl)
            except ValueError:
                await self._send_json(send, 400, {"ok": False, "code": "INVALID_CONTENT_LENGTH"})
                return
            if cl < 0:
                await self._send_json(send, 400, {"ok": False, "code": "INVALID_CONTENT_LENGTH"})
                return
            if cl > MAX_HOOK_BODY_BYTES:
                await self._send_json(send, 413, {"ok": False, "code": "PAYLOAD_TOO_LARGE"})
                return

        buf = bytearray()
        more_body = True
        while more_body:
            msg = await receive()
            msg_type = msg.get("type")
            if msg_type == "http.disconnect":
                return
            if msg_type != "http.request":
                continue
            chunk = msg.get("body") or b""
            if len(buf) + len(chunk) > MAX_HOOK_BODY_BYTES:
                await self._send_json(send, 413, {"ok": False, "code": "PAYLOAD_TOO_LARGE"})
                return
            buf.extend(chunk)
            more_body = bool(msg.get("more_body", False))

        try:
            text = bytes(buf).decode("utf-8")
        except UnicodeDecodeError:
            await self._send_json(send, 400, {"ok": False, "code": "INVALID_JSON"})
            return

        stripped = text.strip()
        if not stripped:
            payload: Dict[str, Any] = {}
        else:
            try:
                parsed = json.loads(stripped)
            except (ValueError, TypeError):
                await self._send_json(send, 400, {"ok": False, "code": "INVALID_JSON"})
                return
            if not isinstance(parsed, dict):
                await self._send_json(send, 400, {"ok": False, "code": "INVALID_JSON"})
                return
            payload = parsed

        rate_key = str(getattr(rec, "pane_id", "") or getattr(rec, "node_uuid", "") or "")
        if not self._allow_rate(rate_key):
            await self._send_json(send, 429, {"ok": False, "code": "RATE_LIMITED"})
            return

        raw_key = self._resolve_raw_key(raw_event, payload)
        spec = self._lookup_spec(str(getattr(rec, "cli_profile", "") or ""))
        activity_cfg = getattr(spec, "activity", None) if spec is not None else None
        events_map = activity_cfg.get("events") if isinstance(activity_cfg, Mapping) else None
        normalized_event = events_map.get(raw_key) if (raw_key and isinstance(events_map, Mapping)) else None

        if not isinstance(normalized_event, str) or normalized_event not in NORMALIZED_EVENTS:
            await self._send_json(send, 202, {"ok": True, "ignored": True})
            return

        session_id_field = str(activity_cfg.get("session_id_field") or "") if isinstance(activity_cfg, Mapping) else ""
        cli_session_id = self._extract_session_id(payload, session_id_field)

        incarnation = getattr(rec, "incarnation", None)
        activity.ingest(
            rec.node_uuid,
            incarnation,
            normalized_event,
            int(self._clock_ms()),
            "hook",
            cli_session_id=cli_session_id,
        )
        await self._send_json(send, 200, {"ok": True})
