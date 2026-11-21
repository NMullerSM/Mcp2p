"""
Registro de sesiones de paneles CLI (`PaneRegistry`), propagación de identidad HTTP
por petición (`ContextVar`) y ciclo de vida del Hub MCP embebido (`EmbeddedMcpHub`).

Cierra EGM-22 ligando cada panel a un token Bearer opaco de un solo propósito y a un
`node_uuid` inmutable validado en tiempo constante en el servidor MCP.
"""

from __future__ import annotations

import contextlib
import logging
import secrets
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from src.core.domain.context import ACTOR_AGENT_MCP, ActorContext
from src.core.domain.pane_identity import new_pane_node_uuid, normalize_project_uuid
from src.core.domain.roles import DEFAULT_ROLE, PaneRole, normalize_role, sanitize_mandate
from src.mcp.transport_options import resolve_http_bind

logger = logging.getLogger("mcp2p.mcp.pane_registry")


@dataclass
class PaneSessionRecord:
    """Estado de autenticación y actividad de un panel terminal conectado al Hub."""

    pane_id: str
    session_token: str
    node_uuid: str
    agent_name: str
    cli_profile: str
    role: str
    pid: Optional[int] = None
    status: str = "active"
    created_at: float = 0.0
    last_seen_at: float = 0.0
    last_human_keystroke_at: float = 0.0
    # Alcance (Ola 2, T2.2): el token de la sesion solo actua sobre este proyecto ('' = panel sin proyecto).
    project_uuid: str = ""
    cwd: Optional[str] = None
    # Auto-Relay POR PANEL (D2): apagado por defecto y persistido por (proyecto, ranura).
    auto_relay_enabled: bool = False
    # Vinculo con el proceso raiz del PTY (Ola 3, EGM-105): `pid` + `create_time` contra la reutilizacion de pids.
    # Sin `pid_create_time` el panel no esta ligado a ningun proceso y el relevo lo rechaza (fallo cerrado).
    pid_create_time: Optional[float] = None
    # Ultimo turno humano ENVIADO (tecleo con Enter en la GUI): cierra el contexto de cadena del panel (37 §3.5).
    last_human_submit_at: float = 0.0
    # Campos v2.0.2 (WP-3a): mandato y preferencias de entrega persistidos por (proyecto, ranura).
    mandate: str = ""
    delivery_mode: str = "auto"
    delivery_batch: bool = False

    def to_dict(self, include_token: bool = False, include_mandate: bool = False) -> Dict[str, Any]:
        """Serializa la ficha del panel sin exponer `session_token` ni `mandate` por defecto."""
        data: Dict[str, Any] = {
            "pane_id": self.pane_id,
            "node_uuid": self.node_uuid,
            "agent_name": self.agent_name,
            "cli_profile": self.cli_profile,
            "role": self.role,
            "pid": self.pid,
            "status": self.status,
            "created_at": self.created_at,
            "last_seen_at": self.last_seen_at,
            "last_human_keystroke_at": self.last_human_keystroke_at,
            "project_uuid": self.project_uuid,
            "cwd": self.cwd,
            "auto_relay_enabled": self.auto_relay_enabled,
            "delivery_mode": self.delivery_mode,
            "delivery_batch": self.delivery_batch,
        }
        if include_mandate:
            data["mandate"] = self.mandate
        if include_token:
            data["session_token"] = self.session_token
        return data


_CURRENT_PANE_SESSION: ContextVar[Optional[PaneSessionRecord]] = ContextVar(
    "mcp2p_current_pane_session",
    default=None,
)


def get_current_pane_session() -> Optional[PaneSessionRecord]:
    """Retorna la sesión de panel autenticada en el contexto actual, si existe."""
    return _CURRENT_PANE_SESSION.get()


@contextlib.contextmanager
def pane_session_context(
    record: Optional[PaneSessionRecord],
) -> Iterator[Optional[PaneSessionRecord]]:
    """Propaga de forma aislada la sesión autenticada de un request HTTP hacia `MCPToolsHandler`."""
    ctx_token = _CURRENT_PANE_SESSION.set(record)
    try:
        yield record
    finally:
        _CURRENT_PANE_SESSION.reset(ctx_token)


def _normalize_pane_id(pane_id: int | str) -> tuple[str, str]:
    """Normaliza identificadores `1` / `'1'` / `'pane_1'` a `('pane_1', '1')`."""
    if isinstance(pane_id, bool):
        raise ValueError("pane_id no puede ser booleano")
    if isinstance(pane_id, int):
        if pane_id < 0:
            raise ValueError(f"pane_id inválido: {pane_id}")
        num = str(pane_id)
        return f"pane_{num}", num
    raw = str(pane_id or "").strip()
    if not raw:
        raise ValueError("pane_id no puede estar vacío")
    if raw.isdigit():
        return f"pane_{raw}", raw
    if raw.startswith("pane_") and raw[5:]:
        return raw, raw[5:]
    return raw, raw


class PaneRegistry:
    """Registro thread-safe de paneles activos, tokens Bearer e interacción humana."""

    def __init__(
        self,
        *,
        collab_repo: Optional[Any] = None,
        clock: Optional[Callable[[], float]] = None,
        identity_repo: Optional[Any] = None,
        process_resolver: Optional[Any] = None,
    ) -> None:
        self._collab_repo = collab_repo
        # `PeerProcessResolver` (Ola 3): perezoso, psutil por defecto.
        self._process_resolver = process_resolver
        self._clock: Callable[[], float] = clock or time.time
        self._lock = threading.RLock()
        self._panes: Dict[str, PaneSessionRecord] = {}
        self._tokens: Dict[str, str] = {}
        self._human_keystrokes: Dict[str, float] = {}
        # Identidad estable por (proyecto, ranura): en BD si hay repositorio (sobrevive al reinicio del Hub) y,
        # si no, en memoria (estable mientras viva el proceso).
        self._identity_repo = identity_repo
        self._memory_identities: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self._human_submit_listeners: List[Callable[[str], None]] = []

    @property
    def process_resolver(self) -> Any:
        if self._process_resolver is None:
            from src.adapters.identity.peer_process import PsutilPeerProcessResolver

            self._process_resolver = PsutilPeerProcessResolver()
        return self._process_resolver

    def _process_create_time(self, pid: Optional[int]) -> Optional[float]:
        if not pid:
            return None
        try:
            ident = self.process_resolver.process_identity(int(pid))
        except Exception:
            return None
        return float(ident.create_time) if ident is not None else None

    def bind_process(
        self, pane_id: int | str, pid: int, *, create_time: Optional[float] = None
    ) -> Optional[PaneSessionRecord]:
        """Liga la sesion del panel al proceso raiz de su PTY (pid + create_time). None si el panel no esta activo.

        Si el proceso ya no existe, la sesion queda SIN vinculo y el relevo la rechaza (fallo cerrado)."""
        norm_pane_id, _ = _normalize_pane_id(pane_id)
        ctime = float(create_time) if create_time is not None else self._process_create_time(pid)
        with self._lock:
            rec = self._panes.get(norm_pane_id)
            if rec is None or rec.status == "closed":
                return None
            rec.pid = int(pid)
            rec.pid_create_time = ctime
            return rec

    def resolve_pane_identity(
        self, pane_id: int | str, project_uuid: Optional[str] = "", *, touch: bool = False
    ) -> Dict[str, Any]:
        """Identidad estable `{node_uuid, auto_relay_enabled}` de (proyecto, ranura); la crea la primera vez."""
        norm_pane_id, _ = _normalize_pane_id(pane_id)
        proj = normalize_project_uuid(project_uuid)
        if self._identity_repo is not None:
            return dict(self._identity_repo.get_or_create_pane_identity(proj, norm_pane_id, touch=touch))
        with self._lock:
            key = (proj, norm_pane_id)
            ident = self._memory_identities.get(key)
            if ident is None:
                ident = {
                    "project_uuid": proj,
                    "pane_slot": norm_pane_id,
                    "node_uuid": new_pane_node_uuid(proj, norm_pane_id),
                    "auto_relay_enabled": False,
                    "role": DEFAULT_ROLE.value,
                    "mandate": "",
                    "role_updated_at": None,
                    "delivery_mode": "auto",
                    "delivery_batch": False,
                }
                self._memory_identities[key] = ident
            return dict(ident)

    def register_pane(
        self,
        pane_id: int | str,
        *,
        node_uuid: Optional[str] = None,
        agent_name: Optional[str] = None,
        cli_profile: str = "claude",
        role: str = "implementer",
        pid: Optional[int] = None,
        session_token: Optional[str] = None,
        project_uuid: Optional[str] = "",
        cwd: Optional[str] = None,
        auto_relay_enabled: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Registra o reemplaza la SESION de un panel con un token nuevo; la identidad es la estable de
        (proyecto, ranura) salvo que se pase `node_uuid` explicito."""
        norm_pane_id, _ = _normalize_pane_id(pane_id)
        proj = normalize_project_uuid(project_uuid)
        now = float(self._clock())
        token = session_token or secrets.token_urlsafe(24)
        is_installer = str(role or "").strip().lower() == "installer"
        if node_uuid:
            resolved_node = node_uuid
            relay_flag = bool(auto_relay_enabled)
            resolved_role = "installer" if is_installer else normalize_role(role).value
            resolved_mandate = ""
            resolved_delivery_mode = "auto"
            resolved_delivery_batch = False
        else:
            identity = self.resolve_pane_identity(norm_pane_id, proj, touch=True)
            resolved_node = str(identity["node_uuid"])
            relay_flag = (
                bool(identity.get("auto_relay_enabled")) if auto_relay_enabled is None else bool(auto_relay_enabled)
            )
            resolved_role = "installer" if is_installer else normalize_role(identity.get("role")).value
            resolved_mandate = sanitize_mandate(identity.get("mandate"))
            resolved_delivery_mode = str(identity.get("delivery_mode") or "auto")
            resolved_delivery_batch = bool(identity.get("delivery_batch", False))
        resolved_agent = agent_name or f"{cli_profile}-{norm_pane_id}"
        pid_ctime = self._process_create_time(pid)

        with self._lock:
            previous = self._panes.get(norm_pane_id)
            if previous is not None and previous.session_token:
                self._tokens.pop(previous.session_token, None)

            # Garantizar que el mismo token no quede ligado a otro panel anterior.
            self._tokens[token] = norm_pane_id
            last_keystroke = self._human_keystrokes.get(norm_pane_id, 0.0)
            record = PaneSessionRecord(
                pane_id=norm_pane_id,
                session_token=token,
                node_uuid=resolved_node,
                agent_name=resolved_agent,
                cli_profile=cli_profile,
                role=resolved_role,
                pid=pid,
                pid_create_time=pid_ctime,
                status="active",
                created_at=now,
                last_seen_at=now,
                last_human_keystroke_at=last_keystroke,
                project_uuid=proj,
                cwd=cwd,
                auto_relay_enabled=relay_flag,
                mandate=resolved_mandate,
                delivery_mode=resolved_delivery_mode,
                delivery_batch=resolved_delivery_batch,
            )
            self._panes[norm_pane_id] = record
            payload = record.to_dict(include_token=True)

        if self._collab_repo is not None:
            reg_fn = getattr(self._collab_repo, "register_or_update_node", None)
            if callable(reg_fn):
                try:
                    reg_fn(
                        node_uuid=resolved_node,
                        developer_name=resolved_agent,
                        agent_id=cli_profile or resolved_agent,
                        status="online",
                        node_kind="agent",
                    )
                except Exception:
                    logger.debug(
                        "Fallo tolerado al registrar nodo %s en collab_repo",
                        resolved_node,
                        exc_info=True,
                    )
        return payload

    def authenticate_token(self, token: str) -> Optional[PaneSessionRecord]:
        """Autentica un token en tiempo constante sobre el mapa de tokens activos."""
        if not isinstance(token, str) or not token:
            return None

        with self._lock:
            matched_pane_id: Optional[str] = None
            for active_token, pane_key in self._tokens.items():
                if secrets.compare_digest(active_token, token):
                    matched_pane_id = pane_key

            if matched_pane_id is None:
                return None

            rec = self._panes.get(matched_pane_id)
            if rec is None or rec.status == "closed":
                return None

            rec.last_seen_at = float(self._clock())
            return rec

    def get_pane(self, pane_id: int | str) -> Optional[PaneSessionRecord]:
        """Obtiene el registro de sesión del panel si existe."""
        norm_pane_id, _ = _normalize_pane_id(pane_id)
        with self._lock:
            return self._panes.get(norm_pane_id)

    def resolve_actor_context(
        self,
        *,
        token: Optional[str] = None,
        pane_id: Optional[int | str] = None,
        fallback_node_uuid: Optional[str] = None,
    ) -> ActorContext:
        """Resuelve un `ActorContext` ligado al panel autenticado o al `pane_id` activo."""
        if token:
            rec = self.authenticate_token(token)
            if rec is not None:
                return ActorContext(
                    kind=ACTOR_AGENT_MCP,
                    node_uuid=rec.node_uuid,
                    pane_id=rec.pane_id,
                    session_id=rec.pane_id,
                )
        if pane_id is not None:
            rec = self.get_pane(pane_id)
            if rec is not None and rec.status != "closed":
                return ActorContext(
                    kind=ACTOR_AGENT_MCP,
                    node_uuid=rec.node_uuid,
                    pane_id=rec.pane_id,
                    session_id=rec.pane_id,
                )
        return ActorContext(
            kind=ACTOR_AGENT_MCP,
            node_uuid=fallback_node_uuid or "unknown",
        )

    def add_human_submit_listener(self, fn: Callable[[str], None]) -> None:
        """Registra un oyente llamado con `node_uuid` (fuera del cerrojo) cuando el humano envía una entrada."""
        with self._lock:
            self._human_submit_listeners.append(fn)

    def record_human_keystroke(
        self,
        pane_id: int | str,
        timestamp: Optional[float] = None,
        *,
        submitted: bool = False,
    ) -> bool:
        """Registra la marca temporal de la última pulsación humana sobre el panel.

        `submitted=True` (el humano pulso Enter) marca ademas un TURNO humano: cierra la cadena causal del panel."""
        norm_pane_id, _ = _normalize_pane_id(pane_id)
        notify_node_uuid: Optional[str] = None
        listeners: List[Callable[[str], None]] = []
        with self._lock:
            ts = float(timestamp) if timestamp is not None else float(self._clock())
            self._human_keystrokes[norm_pane_id] = ts
            rec = self._panes.get(norm_pane_id)
            if rec is not None:
                rec.last_human_keystroke_at = ts
                if submitted:
                    rec.last_human_submit_at = ts
                    notify_node_uuid = rec.node_uuid
                    listeners = list(self._human_submit_listeners)
        if notify_node_uuid and listeners:
            for fn in listeners:
                try:
                    fn(notify_node_uuid)
                except Exception:
                    logger.warning("Fallo en oyente de human_submit; se continua.", exc_info=True)
        return True

    def last_human_submit_at(self, node_uuid: str) -> float:
        """Ultimo turno humano enviado en el panel ACTIVO de esa identidad (0.0 si ninguno)."""
        rec = self.find_active_pane_by_node(node_uuid)
        return float(rec.last_human_submit_at) if rec is not None else 0.0

    def is_human_typing_active(
        self,
        pane_id: int | str,
        cooldown_seconds: float = 5.0,
        now: Optional[float] = None,
    ) -> bool:
        """Indica si hubo actividad reciente de teclado humano dentro de `cooldown_seconds`."""
        norm_pane_id, _ = _normalize_pane_id(pane_id)
        with self._lock:
            rec = self._panes.get(norm_pane_id)
            if norm_pane_id in self._human_keystrokes:
                last_ts = self._human_keystrokes[norm_pane_id]
            elif rec is not None and rec.last_human_keystroke_at > 0.0:
                last_ts = rec.last_human_keystroke_at
            else:
                return False
            current_now = float(now) if now is not None else float(self._clock())
            return (current_now - last_ts) < cooldown_seconds

    def mark_pane_closed(self, pane_id: int | str) -> Optional[PaneSessionRecord]:
        """Cierra la sesión del panel, revoca su token y marca el nodo offline en BD."""
        norm_pane_id, _ = _normalize_pane_id(pane_id)
        with self._lock:
            rec = self._panes.get(norm_pane_id)
            if rec is None:
                return None
            rec.status = "closed"
            self._tokens.pop(rec.session_token, None)
            node_uuid = rec.node_uuid

        if self._collab_repo is not None:
            offline_fn = getattr(self._collab_repo, "mark_node_offline", None)
            if callable(offline_fn):
                try:
                    offline_fn(node_uuid)
                except Exception:
                    logger.debug(
                        "Fallo tolerado al marcar nodo %s offline en collab_repo",
                        node_uuid,
                        exc_info=True,
                    )
        return rec

    def list_panes(
        self, *, include_closed: bool = False, project_uuid: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Lista las sesiones de paneles sin exponer los tokens; `project_uuid` filtra por proyecto exacto."""
        with self._lock:
            return [
                rec.to_dict(include_token=False)
                for rec in self._panes.values()
                if (include_closed or rec.status != "closed")
                and (project_uuid is None or rec.project_uuid == project_uuid)
            ]

    def find_active_pane_by_node(
        self, node_uuid: str, *, project_uuid: Optional[str] = None
    ) -> Optional[PaneSessionRecord]:
        """Panel ACTIVO con esa identidad (y de ese proyecto si se indica); None si no hay."""
        if not node_uuid:
            return None
        with self._lock:
            for rec in self._panes.values():
                if rec.status == "closed" or rec.node_uuid != node_uuid:
                    continue
                if project_uuid is not None and rec.project_uuid != project_uuid:
                    continue
                return rec
        return None

    def set_auto_relay(self, pane_id: int | str, enabled: bool) -> Optional[PaneSessionRecord]:
        """Cambia el Auto-Relay de un panel ACTIVO y lo persiste por (proyecto, ranura); None si no hay panel."""
        norm_pane_id, _ = _normalize_pane_id(pane_id)
        with self._lock:
            rec = self._panes.get(norm_pane_id)
            if rec is None or rec.status == "closed":
                return None
            proj = rec.project_uuid
        if self._identity_repo is not None:
            self._identity_repo.set_pane_auto_relay(proj, norm_pane_id, bool(enabled))
        else:
            with self._lock:
                ident = self._memory_identities.get((proj, norm_pane_id))
                if ident is not None:
                    ident["auto_relay_enabled"] = bool(enabled)
        with self._lock:
            rec.auto_relay_enabled = bool(enabled)
            return rec

    def set_delivery(
        self, pane_id: int | str, mode: Optional[str] = None, batch: Optional[bool] = None
    ) -> Optional[PaneSessionRecord]:
        """Cambia el modo de entrega (`auto|direct|inbox`) y/o el lote de un panel ACTIVO y lo persiste."""
        if mode is not None and mode not in ("auto", "direct", "inbox"):
            raise ValueError("mode debe ser auto, direct o inbox")
        norm_pane_id, _ = _normalize_pane_id(pane_id)
        with self._lock:
            rec = self._panes.get(norm_pane_id)
            if rec is None or rec.status == "closed":
                return None
            proj = rec.project_uuid
        if self._identity_repo is not None:
            self._identity_repo.set_pane_delivery(proj, norm_pane_id, mode, batch)
        else:
            with self._lock:
                ident = self._memory_identities.get((proj, norm_pane_id))
                if ident is not None:
                    if mode is not None:
                        ident["delivery_mode"] = mode
                    if batch is not None:
                        ident["delivery_batch"] = bool(batch)
        with self._lock:
            if mode is not None:
                rec.delivery_mode = mode
            if batch is not None:
                rec.delivery_batch = bool(batch)
            return rec

    def set_role(
        self,
        pane_id: int | str,
        role: PaneRole | str,
        mandate: Optional[str] = None,
    ) -> Optional[PaneSessionRecord]:
        """Cambia el rol y mandato de un panel ACTIVO y los persiste por (proyecto, ranura); None si no hay panel."""
        norm_pane_id, _ = _normalize_pane_id(pane_id)
        norm_role = normalize_role(role)
        with self._lock:
            rec = self._panes.get(norm_pane_id)
            if rec is None or rec.status == "closed":
                return None
            proj = rec.project_uuid
            clean_mandate = rec.mandate if mandate is None else sanitize_mandate(mandate)
        if self._identity_repo is not None:
            set_fn = getattr(self._identity_repo, "set_pane_role", None)
            if callable(set_fn):
                set_fn(proj, norm_pane_id, norm_role.value, clean_mandate)
            else:
                db_obj = getattr(self._identity_repo, "db", None)
                db_set = getattr(db_obj, "set_pane_role", None) if db_obj is not None else None
                if callable(db_set):
                    db_set(proj, norm_pane_id, norm_role.value, clean_mandate)
        else:
            with self._lock:
                ident = self._memory_identities.get((proj, norm_pane_id))
                if ident is not None:
                    ident["role"] = norm_role.value
                    ident["mandate"] = clean_mandate
        with self._lock:
            rec.role = norm_role.value
            rec.mandate = clean_mandate
            return rec


class EmbeddedMcpHub:
    """Ciclo de vida del Hub MCP embebido sobre `streamable-http` en loopback."""

    def __init__(
        self,
        *,
        kernel: Any,
        pane_registry: Optional[PaneRegistry] = None,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        self.kernel = kernel
        self.pane_registry = (
            pane_registry
            if pane_registry is not None
            else PaneRegistry(
                collab_repo=getattr(kernel, "collab_repo", None),
                identity_repo=getattr(kernel, "pane_identity_repo", None),
            )
        )
        self.host = host
        self.port = port
        self._lock = threading.RLock()
        self._server: Optional[Any] = None
        self._uv_server: Optional[Any] = None
        self._sock: Optional[Any] = None
        self._thread: Optional[threading.Thread] = None
        self._url: Optional[str] = None

    @property
    def url(self) -> Optional[str]:
        with self._lock:
            return self._url

    def relay_orchestrator(self) -> Optional[Any]:
        """`SwarmRelayOrchestrator` del Hub en marcha (para reiniciar el Loop Breaker); None si esta parado."""
        with self._lock:
            server = self._server
        handler = getattr(server, "mcp2p_handler", None) if server is not None else None
        getter = getattr(handler, "_get_relay_orchestrator", None)
        if not callable(getter):
            return None
        try:
            return getter()
        except Exception:
            return None

    def is_running(self) -> bool:
        with self._lock:
            return bool(
                self._thread is not None
                and self._thread.is_alive()
                and self._uv_server is not None
                and getattr(self._uv_server, "started", False)
                and not getattr(self._uv_server, "should_exit", False)
            )

    def _cleanup_failed_start(
        self,
        uv_server: Any,
        sock: Any,
        server: Any,
        thread: Optional[threading.Thread],
    ) -> None:
        if uv_server is not None:
            uv_server.should_exit = True
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.5)
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        if server is not None:
            handler = getattr(server, "mcp2p_handler", None)
            if handler is not None and callable(getattr(handler, "close", None)):
                handler.close()

    def start(self, timeout_s: float = 5.0) -> str:
        """Arranca el Hub MCP `streamable-http` en un hilo daemon de forma idempotente."""
        import anyio

        from src.mcp.server import build_streamable_http_server, create_mcp_server

        with self._lock:
            if self.is_running() and self._url:
                return self._url

            bind_host, bind_port = resolve_http_bind(self.host, self.port)
            server = create_mcp_server(
                kernel=self.kernel,
                transport="streamable-http",
                pane_registry=self.pane_registry,
            )
            uv_server, sock, url = build_streamable_http_server(server, bind_host, bind_port)

            startup_errors: List[BaseException] = []

            def _serve_target() -> None:
                try:
                    anyio.run(lambda: uv_server.serve(sockets=[sock]))
                except BaseException as exc:  # pragma: no cover - ruta de error de arranque
                    startup_errors.append(exc)

            thread = threading.Thread(
                target=_serve_target,
                name="mcp2p-embedded-hub",
                daemon=True,
            )
            thread.start()

            deadline = time.monotonic() + max(0.1, float(timeout_s))
            while not getattr(uv_server, "started", False):
                if startup_errors:
                    self._cleanup_failed_start(uv_server, sock, server, thread)
                    raise RuntimeError(f"Error arrancando EmbeddedMcpHub: {startup_errors[0]}") from startup_errors[0]
                if not thread.is_alive():
                    self._cleanup_failed_start(uv_server, sock, server, thread)
                    raise RuntimeError("EmbeddedMcpHub terminó inesperadamente antes de iniciar.")
                if time.monotonic() >= deadline:
                    self._cleanup_failed_start(uv_server, sock, server, thread)
                    raise TimeoutError(f"EmbeddedMcpHub no arrancó en {timeout_s}s ({url})")
                time.sleep(0.02)

            self._server = server
            self._uv_server = uv_server
            self._sock = sock
            self._thread = thread
            self._url = url
            return url

    def stop(self, timeout_s: float = 3.0) -> None:
        """Detiene limpiamente el Hub HTTP sin cerrar la base de datos del kernel compartido."""
        with self._lock:
            uv_server = self._uv_server
            thread = self._thread
            sock = self._sock
            server = self._server
            self._uv_server = None
            self._thread = None
            self._sock = None
            self._server = None
            self._url = None

        if uv_server is not None:
            uv_server.should_exit = True
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.1, float(timeout_s)))
            if thread.is_alive() and uv_server is not None:
                uv_server.force_exit = True
                thread.join(timeout=1.0)
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        if server is not None:
            handler = getattr(server, "mcp2p_handler", None)
            if handler is not None and callable(getattr(handler, "close", None)):
                handler.close()
