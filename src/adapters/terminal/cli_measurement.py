"""Sonda sin interfaz para medir un CLI real (WP-5d, D12-ter camino 4).

Abre el CLI en una terminal PRIVADA (un conector propio de un solo panel, nunca las terminales del usuario) con la
misma preparación de hooks que usa producción, pega un mensaje de prueba, lo envía con Enter y devuelve lo observado.
Todo en segundo plano: no hay ventana ni se pinta nada. El comando y los argumentos de cada sistema los decide el
conector del SO (ConPTY en Windows, PTY POSIX en el resto) y el catálogo (resolución del programa).

`_send` es el ÚNICO sitio de este módulo que escribe en el PTY y solo sobre la terminal de prueba.
"""

from __future__ import annotations

import hmac
import json
import logging
import re
import secrets
import shutil
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from src.adapters.terminal.cli_catalog import CliCatalog, CliCatalogService
from src.adapters.terminal.hook_configs import agy_hooks_config
from src.core.domain.cli_measurement import (
    DEFAULT_SUBMIT_DELAY_MS,
    HOOK_INSTALL_BY_CLI,
    Attempt,
    HookEvent,
    normalize_event,
    prompt_lines,
    strip_ansi,
)

logger = logging.getLogger("mcp2p.adapters.terminal.cli_measurement")

_EVENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,47}$")
_SESSION_KEYS = frozenset({"session_id", "sessionid", "thread_id", "thread-id", "conversation_id", "conversationid"})
_MAX_BODY_BYTES = 64 * 1024
PROBE_PANE = 1
STEPS_PER_ATTEMPT = 5

ProgressFn = Callable[[str, int, int, str], None]


@dataclass(frozen=True)
class ProbeTimings:
    startup_cap_s: float = 40.0
    startup_quiet_s: float = 4.0
    startup_min_s: float = 4.0
    after_paste_s: float = 1.0
    submit_delay_s: float = DEFAULT_SUBMIT_DELAY_MS / 1000.0
    turn_start_grace_s: float = 20.0
    turn_end_cap_s: float = 150.0
    poll_s: float = 0.05


def _session_fields(payload: Any, prefix: str = "", depth: int = 0) -> List[str]:
    out: List[str] = []
    if depth > 4 or not isinstance(payload, Mapping):
        return out
    for key, val in payload.items():
        dotted = f"{prefix}.{key}" if prefix else str(key)
        if str(key).lower() in _SESSION_KEYS and isinstance(val, str) and val.strip():
            out.append(dotted)
        elif isinstance(val, Mapping):
            out.extend(_session_fields(val, dotted, depth + 1))
    return out


def _looks_like_trust_prompt(screen_text: str) -> bool:
    collapsed = re.sub(r"[^a-z]+", "", strip_ansi(screen_text).lower())
    return "trustthisfolder" in collapsed or "projectyoucreatedoroneyoutrust" in collapsed


def _payload_type(payload: Any) -> str:
    if not isinstance(payload, Mapping):
        return ""
    for candidate in (payload.get("type"), payload.get("hook_event_name")):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()[:64]
    inner = payload.get("event")
    if isinstance(inner, Mapping):
        value = inner.get("type")
        if isinstance(value, str) and value.strip():
            return value.strip()[:64]
    return ""


class HookListener:
    """Servidor HTTP de bucle local que hace de Hub para la prueba: guarda los avisos `POST /hooks/v1/<evento>`."""

    def __init__(self, clock_ms: Callable[[], int]) -> None:
        self._clock_ms = clock_ms
        self.token = f"probe-{secrets.token_urlsafe(18)}"
        self._events: List[HookEvent] = []
        self._lock = threading.Lock()
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.url = ""

    def start(self) -> None:
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def _reply(
                self,
                status: int,
                body: bytes = b'{"ok":true}',
                *,
                extra_headers: Optional[Sequence[tuple[str, str]]] = None,
            ) -> None:
                self.send_response(status)
                if body:
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                for hk, hv in extra_headers or ():
                    self.send_header(hk, hv)
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def _authorized(self) -> bool:
                return hmac.compare_digest(self.headers.get("Authorization") or "", f"Bearer {outer.token}")

            def _read_json_body(self) -> Optional[Any]:
                try:
                    length = int(self.headers.get("Content-Length") or "0")
                except ValueError:
                    self._reply(400, b'{"ok":false}')
                    return None
                if length < 0 or length > _MAX_BODY_BYTES:
                    self._reply(413, b'{"ok":false}')
                    return None
                raw = self.rfile.read(length) if length else b""
                try:
                    return json.loads(raw.decode("utf-8", errors="replace").strip() or "{}")
                except ValueError:
                    self._reply(400, b'{"ok":false}')
                    return None

            def _handle_mcp_post(self) -> None:
                payload = self._read_json_body()
                if payload is None:
                    return
                if not isinstance(payload, Mapping) or "id" not in payload or payload.get("id") is None:
                    self._reply(202, b"")
                    return
                req_id = payload.get("id")
                method = str(payload.get("method") or "")
                params = payload.get("params") if isinstance(payload.get("params"), Mapping) else {}
                if method == "initialize":
                    result: Dict[str, Any] = {
                        "protocolVersion": str(params.get("protocolVersion") or "2024-11-05"),
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "mcp2p-probe", "version": "2.0.2"},
                    }
                    reply_obj: Dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "result": result}
                elif method == "tools/list":
                    reply_obj = {"jsonrpc": "2.0", "id": req_id, "result": {"tools": []}}
                elif method == "ping":
                    reply_obj = {"jsonrpc": "2.0", "id": req_id, "result": {}}
                else:
                    reply_obj = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32601, "message": f"Method not found: {method}"},
                    }
                self._reply(200, json.dumps(reply_obj, ensure_ascii=False).encode("utf-8"))

            def do_GET(self) -> None:  # noqa: N802
                clean_path = self.path.split("?", 1)[0]
                if clean_path == "/mcp":
                    if not self._authorized():
                        self._reply(401, b'{"ok":false}')
                        return
                    self._reply(405, b'{"ok":false}', extra_headers=(("Allow", "POST"),))
                    return
                self._reply(404, b'{"ok":false}')

            def do_POST(self) -> None:  # noqa: N802
                clean_path = self.path.split("?", 1)[0]
                if clean_path == "/mcp":
                    if not self._authorized():
                        self._reply(401, b'{"ok":false}')
                        return
                    self._handle_mcp_post()
                    return
                if not clean_path.startswith("/hooks/v1/"):
                    self._reply(404, b'{"ok":false}')
                    return
                name = clean_path[len("/hooks/v1/"):]
                if _EVENT_RE.fullmatch(name) is None:
                    self._reply(400, b'{"ok":false}')
                    return
                if not self._authorized():
                    self._reply(401, b'{"ok":false}')
                    return
                payload = self._read_json_body()
                if payload is None:
                    return
                event = HookEvent(
                    event=name,
                    t_ms=int(outer._clock_ms()),
                    payload_type=_payload_type(payload),
                    session_fields=tuple(sorted(set(_session_fields(payload)))),
                )
                with outer._lock:
                    outer._events.append(event)
                self._reply(200, b'{"ok":true}')

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return None

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        port = int(self._server.server_address[1])
        self.url = f"http://127.0.0.1:{port}/mcp"
        self._thread = threading.Thread(target=self._server.serve_forever, name="cli-measure-hooks", daemon=True)
        self._thread.start()

    def events(self) -> List[HookEvent]:
        with self._lock:
            return list(self._events)

    def stop(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            try:
                server.shutdown()
                server.server_close()
            except Exception:
                pass


class MeasuringCatalogService(CliCatalogService):
    """Catálogo que, SOLO para la terminal de prueba, trata `activity` del CLI como verificado para que el conector
    prepare los hooks por el mismo camino que producción."""

    def __init__(self, base: CliCatalogService, cli_id: str, extra_launch_flags: Sequence[str] = ()) -> None:
        super().__init__(
            base._settings,
            repo_catalog_path=base._repo_path,
            user_catalog_path=base._user_path,
            include_user_catalog=base._include_user,
            which=base._which,
            platform=base._platform,
            home=base._home,
            environ=base._environ,
            measurements=base._measurements,
            include_measurements=False,  # medimos desde cero, sin fiarnos de lo medido antes
        )
        self._probe_cli = cli_id
        self._extra_flags = tuple(str(x) for x in extra_launch_flags)

    def catalog(self) -> CliCatalog:
        cat = super().catalog()
        spec = cat.get(self._probe_cli)
        if spec is None:
            return cat
        activity = dict(spec.activity)
        activity.update(
            status="verified",
            platforms=(self.platform,),
            hook_install=HOOK_INSTALL_BY_CLI.get(self._probe_cli, "none"),
        )
        profiles = dict(cat.profiles)
        launch_flags = tuple(spec.launch_flags) + self._extra_flags if self._extra_flags else spec.launch_flags
        profiles[self._probe_cli] = replace(spec, activity=activity, launch_flags=launch_flags)
        return CliCatalog(catalog_version=cat.catalog_version, profiles=profiles, warnings=cat.warnings)


def _default_connector_factory(catalog: CliCatalogService, work_dir: Path, hook_argv: Callable[[str], List[str]]) -> Any:
    from src.adapters.terminal.launch_augment import LaunchServices
    from src.adapters.terminal.mcp_injection import PaneMcpConfigStore

    store = PaneMcpConfigStore(work_dir / "_mcp_session")
    services = LaunchServices(hook_argv=hook_argv)
    kwargs: Dict[str, Any] = dict(
        max_panes=1,
        memory_probe=lambda: 10 ** 6,
        mcp_config_store=store,
        catalog_service=catalog,
        launch_services=services,
    )
    if sys.platform == "win32":
        from src.adapters.terminal.winpty_host import WinPtyHostConnector

        return WinPtyHostConnector(**kwargs)
    from src.adapters.terminal.pty_host import PosixPtyHostConnector

    return PosixPtyHostConnector(**kwargs)


class HeadlessCliProbe:
    """Un intento de medición: abre el CLI, pega, envía y devuelve lo observado (`Attempt`)."""

    def __init__(
        self,
        catalog: CliCatalogService,
        *,
        connector_factory: Optional[Callable[[CliCatalogService, Path, Callable[[str], List[str]]], Any]] = None,
        command_override: Optional[Sequence[str]] = None,
        timings: Optional[ProbeTimings] = None,
        clock_ms: Optional[Callable[[], int]] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._catalog = catalog
        self._factory = connector_factory or _default_connector_factory
        self._command = [str(x) for x in command_override] if command_override else None
        self._timings = timings or ProbeTimings()
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._sleep = sleep

    @staticmethod
    def _send(conn: Any, data: str) -> bool:
        res = conn.send_input(PROBE_PANE, data)
        return bool(isinstance(res, dict) and res.get("success"))

    def run(
        self,
        cli_id: str,
        *,
        paste_mode: str,
        progress: ProgressFn,
        cancel: threading.Event,
        step_offset: int = 0,
        steps_total: int = STEPS_PER_ATTEMPT,
        cwd: Optional[Path] = None,
    ) -> Attempt:
        t = self._timings
        work_dir = Path(tempfile.mkdtemp(prefix="mcp2p-medicion-"))
        if cli_id == "antigravity" or cwd is None:
            probe_cwd = work_dir / "cwd"
            probe_cwd.mkdir()
        else:
            probe_cwd = Path(cwd)
            if not probe_cwd.is_dir():
                shutil.rmtree(work_dir, ignore_errors=True)
                return Attempt(paste_mode=paste_mode, spawn_ok=False)
        listener = HookListener(self._clock_ms)
        conn: Any = None
        screen: List[str] = []
        cursor = 0

        def step(n: int, message: str) -> None:
            progress(f"{cli_id}:{n}", step_offset + n, steps_total, message)

        def pump() -> bool:
            nonlocal cursor
            res = conn.poll_output(PROBE_PANE, since_seq=cursor)
            cursor = int(res.get("next_seq", cursor))
            text = res.get("text") or ""
            if text:
                screen.append(text)
            return bool(res.get("alive", False))

        def pump_for(seconds: float) -> None:
            deadline = time.monotonic() + max(0.0, seconds)
            while time.monotonic() < deadline and not cancel.is_set():
                pump()
                self._sleep(t.poll_s)

        attempt_events: Callable[[], List[HookEvent]] = listener.events
        try:
            step(1, "Preparando la prueba")
            listener.start()
            hook_argv = _hook_argv_factory()
            extra_flags: List[str] = []
            if cli_id == "antigravity" and self._command is None:
                # agy rechaza `"hooks"` en --settings; descubre hooks en .agents/hooks.json y acepta trustedWorkspaces
                # solo para esta sesión en --settings. Todo vive dentro del temporal de la sonda.
                agents_dir = probe_cwd / ".agents"
                agents_dir.mkdir(exist_ok=True)
                hooks_file = agents_dir / "hooks.json"
                hooks_file.write_text(
                    json.dumps(agy_hooks_config(hook_argv, self._catalog.platform), indent=2, ensure_ascii=False)
                    + "\n",
                    encoding="utf-8",
                )
                settings_file = work_dir / "agy_settings.json"
                settings_file.write_text(
                    json.dumps({"trustedWorkspaces": [str(probe_cwd)]}, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
                settings_file.chmod(0o600)
                extra_flags.extend(["--settings", str(settings_file)])
            measuring = MeasuringCatalogService(self._catalog, cli_id, extra_launch_flags=extra_flags)
            conn = self._factory(measuring, work_dir, hook_argv)
            spawn = conn.spawn_session(
                PROBE_PANE,
                cli_profile=cli_id,
                command=self._command,
                cwd=probe_cwd,
                hub_url=listener.url,
                session_token=listener.token,
                use_trampoline=False,
            )
            if not spawn.get("success"):
                return Attempt(paste_mode=paste_mode, spawn_ok=False)

            step(2, "Abriendo el CLI y esperando sus avisos")
            started = time.monotonic()
            last_output = started
            while not cancel.is_set():
                before = cursor
                alive = pump()
                if cursor != before:
                    last_output = time.monotonic()
                    if _looks_like_trust_prompt("".join(screen)):
                        return Attempt(paste_mode=paste_mode, spawn_ok=False, trust_prompt_cwd=str(probe_cwd))
                now = time.monotonic()
                if not alive:
                    if _looks_like_trust_prompt("".join(screen)):
                        return Attempt(paste_mode=paste_mode, spawn_ok=False, trust_prompt_cwd=str(probe_cwd))
                    return Attempt(paste_mode=paste_mode, spawn_ok=False)
                if now - started >= t.startup_min_s and now - last_output >= t.startup_quiet_s:
                    break
                if now - started >= t.startup_cap_s:
                    break
                self._sleep(t.poll_s)
            if cancel.is_set():
                return Attempt(paste_mode=paste_mode, spawn_ok=True, events=tuple(attempt_events()), cancelled=True)

            step(3, "Pegando el mensaje de prueba")
            lines = prompt_lines(secrets.token_hex(3))
            text = "\n".join(lines)
            if paste_mode == "bracketed":
                payload = f"\x1b[200~{text}\x1b[201~"
            else:
                payload = " ".join(lines)
            screen.clear()
            self._send(conn, payload)
            pump_for(t.after_paste_s)
            screen_after_paste = "".join(screen)

            step(4, "Enviando y esperando la respuesta del agente")
            pump_for(max(0.0, t.submit_delay_s - t.after_paste_s))
            enter_at = int(self._clock_ms())
            self._send(conn, "\r")
            sent_at = time.monotonic()
            deadline = sent_at + t.turn_end_cap_s
            timed_out = True
            while time.monotonic() < deadline and not cancel.is_set():
                alive = pump()
                if not attempt_events() and time.monotonic() - sent_at > t.turn_start_grace_s:
                    break  # un CLI que no manda ningún aviso no merece esperar el tope entero
                evs = [e for e in attempt_events() if e.t_ms >= enter_at]
                if any(normalize_event(e) == "turn_ended" for e in evs):
                    timed_out = False
                    break
                if not alive:
                    timed_out = False
                    break
                self._sleep(t.poll_s)
            if cancel.is_set():
                return Attempt(
                    paste_mode=paste_mode, spawn_ok=True, events=tuple(attempt_events()), enter_at_ms=enter_at,
                    screen_after_paste=screen_after_paste, cancelled=True,
                )
            return Attempt(
                paste_mode=paste_mode,
                spawn_ok=True,
                events=tuple(attempt_events()),
                enter_at_ms=enter_at,
                screen_after_paste=screen_after_paste,
                timed_out=timed_out,
            )
        finally:
            step(5, "Cerrando la terminal de prueba")
            if conn is not None:
                try:
                    conn.close_session(PROBE_PANE)
                except Exception:
                    pass
                try:
                    conn.close()
                except Exception:
                    pass
            listener.stop()
            shutil.rmtree(work_dir, ignore_errors=True)


def _hook_argv_factory() -> Callable[[str], List[str]]:
    from src.adapters.terminal.hook_configs import hook_command_argv

    frozen = bool(getattr(sys, "frozen", False))
    return lambda event: hook_command_argv(event, frozen=frozen, executable=sys.executable)
