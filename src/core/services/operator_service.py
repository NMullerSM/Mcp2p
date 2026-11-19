"""Canal del operador: emparejamiento por proceso y herramientas `operator_*` (Ola 4, F2.1/F2.2 del dossier 37).

El *harness* del dueño (Claude Code, Claude Desktop u otro cliente MCP) lanza `mcp2p --operator-proxy`, que se
conecta al Hub por un canal local del usuario (socket Unix 0600 o TCP loopback en Windows). El Hub identifica el
PROCESO que abrio la conexion (el proxy) y a su padre (el harness) por el sistema operativo, no por nada que declare
el cliente (D11: sin token en disco).

Emparejamiento: la primera conexion de un harness sin sesion abre un dialogo NATIVO con su ejecutable, pid, usuario,
cwd y ascendencia. Si el humano aprueba, se crea una fila en `operator_sessions` ligada a (pid, `create_time`,
ejecutable) del harness. La sesion deja de valer si ese proceso muere o su pid se reutiliza, si el humano la revoca
(revocar es definitivo para ese proceso) o tras `idle_timeout_ms` sin uso. Sin ventana -> `CONFIRMATION_UNAVAILABLE`
(fallo cerrado).

Confianza (D10): los sobres del operador llevan `trust = operator_delegated`. Un destino `elevated` (CLI sin permisos o
shell) o `confirm_with_human=True` deja el sobre `parked` hasta que el humano lo libera (`relay_resolve_parked`).
Autonomia (D17): L2 solo en su parte de mensajes; el despertar llega en la ola siguiente. Invariante D12: nada de esto
escribe en ningun PTY.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.core.domain.identity import Principal
from src.core.domain.privilege import ELEVATED, classify_pane, requires_human_confirmation
from src.core.ports.identity import ProcessIdentity

logger = logging.getLogger("mcp2p.core.services.operator")

DEFAULT_IDLE_TIMEOUT_MS = 2 * 60 * 60 * 1000
DEFAULT_PAIRING_WAIT_S = 120.0
DEFAULT_DECLINE_COOLDOWN_MS = 10 * 60 * 1000
AUTONOMY_LEVEL = "L2"  # D17: L2, en esta ola solo mensajes (sin despertar)
ALL_PROJECTS = "*"
HUMAN_UI_ACTOR = "human_ui"
MAX_CARD_ID = 64
MAX_REQUESTED_PROJECTS = 16

PARK_ELEVATED = "ELEVATED_TARGET_CONFIRMATION"
PARK_REQUESTED = "HUMAN_CONFIRMATION_REQUESTED"

# Codigos (los mensajes son para el humano o el LLM del harness; nunca llevan pids de otros procesos).
OPERATOR_NOT_PAIRED = "OPERATOR_NOT_PAIRED"
OPERATOR_PAIRING_PENDING = "OPERATOR_PAIRING_PENDING"
OPERATOR_SESSION_REVOKED = "OPERATOR_SESSION_REVOKED"
OPERATOR_SESSION_INVALID = "OPERATOR_SESSION_INVALID"
OPERATOR_PEER_UNVERIFIED = "OPERATOR_PEER_UNVERIFIED"
OPERATOR_FROM_PANE_FORBIDDEN = "OPERATOR_FROM_PANE_FORBIDDEN"
OPERATOR_FROM_HUB_FORBIDDEN = "OPERATOR_FROM_HUB_FORBIDDEN"
OPERATOR_PROJECT_UNKNOWN = "OPERATOR_PROJECT_UNKNOWN"
PROJECT_SCOPE_FORBIDDEN = "PROJECT_SCOPE_FORBIDDEN"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _err(code: str, error: str, **extra: Any) -> Dict[str, Any]:
    return {"success": False, "code": code, "error": error, **extra}


@dataclass
class OperatorPeer:
    """Una conexion al canal del operador: el proceso que la abrio (proxy) y su padre (el harness)."""

    transport: str
    peer: Optional[ProcessIdentity] = None
    harness: Optional[ProcessIdentity] = None
    harness_info: Dict[str, Any] = field(default_factory=dict)
    ancestors: List[Dict[str, Any]] = field(default_factory=list)
    requested_projects: List[str] = field(default_factory=list)
    client_label: str = ""
    rejection: Optional[Dict[str, Any]] = None  # error fijo de la conexion (p. ej. viene del arbol de un panel)
    conn_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    @property
    def harness_key(self) -> Optional[Tuple[int, float]]:
        if self.harness is None:
            return None
        return (int(self.harness.pid), round(float(self.harness.create_time), 2))


@dataclass
class _Pairing:
    event: threading.Event
    started_ms: int
    harness_name: str
    harness_pid: int
    result: Optional[Dict[str, Any]] = None


def _clean_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return "".join(ch for ch in text if ch.isprintable())[:limit]


class OperatorChannelService:
    """Logica del canal del operador, sin sockets (los pone `src/mcp/operator_channel.py`). Thread-safe."""

    def __init__(
        self,
        *,
        store: Any,
        processes: Any,
        relay_provider: Callable[[], Any],
        pane_directory_provider: Callable[[], Any],
        projects_provider: Callable[[], List[Dict[str, Any]]],
        confirm: Callable[[str, str], Optional[Dict[str, Any]]],
        catalog_kind: Optional[Callable[[str], Optional[str]]] = None,
        clock_ms: Optional[Callable[[], int]] = None,
        hub_pid: Optional[int] = None,
        idle_timeout_ms: int = DEFAULT_IDLE_TIMEOUT_MS,
        pairing_wait_s: float = DEFAULT_PAIRING_WAIT_S,
        decline_cooldown_ms: int = DEFAULT_DECLINE_COOLDOWN_MS,
    ) -> None:
        import os

        self.store = store
        self.processes = processes
        self._relay_provider = relay_provider
        self._panes_provider = pane_directory_provider
        self._projects_provider = projects_provider
        self._confirm = confirm
        self._catalog_kind = catalog_kind or _default_catalog_kind
        self._clock_ms = clock_ms or _now_ms
        self.hub_pid = int(hub_pid if hub_pid is not None else os.getpid())
        self.idle_timeout_ms = int(idle_timeout_ms)
        self.pairing_wait_s = float(pairing_wait_s)
        self.decline_cooldown_ms = int(decline_cooldown_ms)
        self._lock = threading.RLock()
        self._pairings: Dict[Tuple[int, float], _Pairing] = {}
        self._declined: Dict[Tuple[int, float], Tuple[int, str]] = {}
        self._connections: Dict[str, Tuple[OperatorPeer, Optional[Callable[[], None]]]] = {}
        self.channel_info: Dict[str, Any] = {"listening": False}

    # -- composicion -------------------------------------------------------------------------------------------

    @property
    def relay(self) -> Any:
        relay = self._relay_provider()
        if relay is not None and getattr(relay, "operator_directory", None) is None:
            relay.operator_directory = self
        return relay

    def _panes(self) -> Any:
        return self._panes_provider()

    def _audit(self, entry: Dict[str, Any]) -> None:
        try:
            relay = self.relay
            if relay is not None:
                relay.store.append_audit({"ts_ms": int(self._clock_ms()), **entry})
        except Exception:
            logger.warning("No se pudo anotar en la auditoria del relevo: %s", entry.get("event"), exc_info=True)

    # -- identificacion de la conexion -------------------------------------------------------------------------------

    def _pane_roots(self) -> List[ProcessIdentity]:
        registry = self._panes()
        roots: List[ProcessIdentity] = []
        if registry is None:
            return roots
        for item in registry.list_panes():
            rec = registry.get_pane(item["pane_id"])
            pid = getattr(rec, "pid", None)
            ctime = getattr(rec, "pid_create_time", None)
            if rec is not None and pid and ctime is not None:
                roots.append(ProcessIdentity(int(pid), float(ctime)))
        return roots

    def identify(self, peer: Optional[ProcessIdentity], *, transport: str, hello: Optional[Dict[str, Any]] = None) -> OperatorPeer:
        """Construye la ficha de la conexion a partir del proceso par que dio el SO. Nunca lanza: si algo no cuadra,
        la conexion queda con `rejection` y todas sus herramientas responden ese error."""
        hello = hello if isinstance(hello, dict) else {}
        requested = hello.get("projects") if isinstance(hello.get("projects"), list) else []
        op = OperatorPeer(
            transport=transport,
            peer=peer,
            requested_projects=[_clean_text(p, 80) for p in requested[:MAX_REQUESTED_PROJECTS] if isinstance(p, str)],
            client_label=_clean_text(hello.get("client"), 80),
        )
        try:
            if peer is None or not self.processes.is_alive(peer):
                op.rejection = _err(OPERATOR_PEER_UNVERIFIED, "No se pudo identificar el proceso que abrio la conexion.")
                return op
            harness = self.processes.parent_of(peer)
            if harness is None:
                op.rejection = _err(
                    OPERATOR_PEER_UNVERIFIED,
                    "El proceso que lanzo el proxy del operador ya no existe; vuelve a conectarlo desde tu harness.",
                )
                return op
            if self.hub_pid in (int(peer.pid), int(harness.pid)):
                op.rejection = _err(OPERATOR_FROM_HUB_FORBIDDEN, "El propio Hub no puede actuar como operador.")
                return op
            for root in self._pane_roots():
                if self.processes.descends_from(peer, root):
                    op.rejection = _err(
                        OPERATOR_FROM_PANE_FORBIDDEN,
                        "Un agente de un panel de MCP2P no puede actuar como operador; usa las herramientas de la "
                        "bandeja (send_message, inbox_next).",
                    )
                    return op
            info = self.processes.describe(harness)
            if info is None:
                op.rejection = _err(OPERATOR_PEER_UNVERIFIED, "No se pudo leer el proceso del harness.")
                return op
            op.harness = harness
            op.harness_info = info
            op.ancestors = self.processes.ancestors(harness, limit=6)
        except Exception:
            logger.warning("Fallo identificando una conexion del operador", exc_info=True)
            op.rejection = _err(OPERATOR_PEER_UNVERIFIED, "No se pudo identificar el proceso que abrio la conexion.")
        return op

    def connection_opened(self, op: OperatorPeer, close: Optional[Callable[[], None]] = None) -> None:
        """Registra la conexion y, si su harness no esta emparejado, abre el dialogo en segundo plano (no bloquea el
        saludo MCP: las herramientas esperan la decision)."""
        with self._lock:
            self._connections[op.conn_id] = (op, close)
        if op.rejection is not None or op.harness is None:
            return
        row = self.store.find_for_harness(op.harness.pid, op.harness.create_time)
        if row is None or row["state"] == "expired":
            self._start_pairing(op)

    def connection_closed(self, op: OperatorPeer) -> None:
        with self._lock:
            self._connections.pop(op.conn_id, None)

    # -- emparejamiento ------------------------------------------------------------------------------------------

    def _resolve_projects(self, op: OperatorPeer) -> Tuple[Optional[List[Dict[str, Any]]], Optional[Dict[str, Any]]]:
        """Proyectos que se ofrecen al humano: los pedidos por el proxy (solo restringen), los que contienen el cwd
        del harness o, si no hay ninguno, todos (y el dialogo lo dice en mayusculas)."""
        projects = [p for p in (self._projects_provider() or []) if p.get("project_uuid")]
        if op.requested_projects:
            chosen = [p for p in projects if p["project_uuid"] in op.requested_projects]
            if not chosen:
                return None, _err(OPERATOR_PROJECT_UNKNOWN, "Ninguno de los proyectos pedidos existe en este MCP2P.")
            return chosen, None
        cwd = str(op.harness_info.get("cwd") or "")
        matches: List[Dict[str, Any]] = []
        if cwd:
            try:
                here = Path(cwd).resolve()
            except (OSError, RuntimeError):
                here = None
            for proj in projects:
                for key in ("code_repo_path", "gov_repo_path", "base_path"):
                    raw = str(proj.get(key) or "").strip()
                    if not raw or here is None:
                        continue
                    try:
                        base = Path(raw).resolve()
                    except (OSError, RuntimeError):
                        continue
                    if here == base or base in here.parents:
                        matches.append(proj)
                        break
        if matches:
            return matches, None
        return [{"project_uuid": ALL_PROJECTS, "name": "TODOS los proyectos"}], None

    def _dialog_text(self, op: OperatorPeer, projects: List[Dict[str, Any]]) -> str:
        info = op.harness_info
        chain = " <- ".join(f"{a.get('name') or '?'} ({a.get('pid')})" for a in op.ancestors[1:]) or "(sin datos)"
        names = ", ".join(str(p.get("name") or p["project_uuid"]) for p in projects)
        transport = "socket local solo de tu usuario" if op.transport == "unix" else "TCP local (127.0.0.1)"
        declared = f"\nCliente declarado (no verificado): {op.client_label}" if op.client_label else ""
        hours = max(1, self.idle_timeout_ms // 3_600_000)
        return (
            "Un programa quiere conectarse a MCP2P como OPERADOR: podra enviar tareas a los agentes de tus terminales "
            "y leer sus respuestas.\n\n"
            f"Programa: {info.get('name') or '?'}\nEjecutable: {info.get('exe') or '?'}\n"
            f"PID: {info.get('pid')}   Usuario: {info.get('username') or '?'}\n"
            f"Carpeta de trabajo: {info.get('cwd') or '?'}\nLanzado por: {chain}\nConexion: {transport}{declared}\n\n"
            f"Proyectos: {names}\nAutonomia: {AUTONOMY_LEVEL} (solo mensajes: llegan a la bandeja y, con entrega directa activa en el destino, tambien a su terminal)\n"
            f"Caduca: al cerrar ese programa, si la revocas o tras {hours} h sin uso.\n"
            "Los destinos con permisos amplios o shells te pediran confirmacion mensaje a mensaje.\n\n"
            "Si no reconoces este programa, rechazalo."
        )

    def _start_pairing(self, op: OperatorPeer) -> Optional[_Pairing]:
        key = op.harness_key
        if key is None:
            return None
        now = int(self._clock_ms())
        with self._lock:
            pending = self._pairings.get(key)
            if pending is not None and not pending.event.is_set():
                return pending
            declined = self._declined.get(key)
            if declined is not None and now < declined[0]:
                return None
            pairing = _Pairing(threading.Event(), now, str(op.harness_info.get("name") or "?"), int(op.harness.pid))
            self._pairings[key] = pairing
        threading.Thread(target=self._pair, args=(op, key, pairing), name="mcp2p-operator-pairing", daemon=True).start()
        return pairing

    def _pair(self, op: OperatorPeer, key: Tuple[int, float], pairing: _Pairing) -> None:
        try:
            pairing.result = self._pair_blocking(op, key)
        except Exception:
            logger.warning("Fallo el emparejamiento del operador", exc_info=True)
            pairing.result = _err(OPERATOR_NOT_PAIRED, "No se pudo completar el emparejamiento.")
        finally:
            pairing.event.set()

    def _pair_blocking(self, op: OperatorPeer, key: Tuple[int, float]) -> Dict[str, Any]:
        assert op.harness is not None
        projects, problem = self._resolve_projects(op)
        if problem is not None or projects is None:
            return problem or _err(OPERATOR_PROJECT_UNKNOWN, "Sin proyectos.")
        verdict = self._confirm("Emparejar un operador con MCP2P", self._dialog_text(op, projects))
        now = int(self._clock_ms())
        if verdict is not None:
            code = str(verdict.get("code") or "CONFIRMATION_DECLINED")
            with self._lock:
                self._declined[key] = (now + self.decline_cooldown_ms, code)
            self._audit({"event": "operator_pairing_declined", "reason_code": code, "actor": HUMAN_UI_ACTOR,
                         "decision": "declined"})
            return _err(OPERATOR_NOT_PAIRED, "El emparejamiento no se aprobo en MCP2P.", pairing=code)
        # TOCTOU: el proceso aprobado debe seguir siendo EL MISMO (pid + create_time + ejecutable)
        info = self.processes.describe(op.harness)
        if info is None or info.get("exe") != op.harness_info.get("exe"):
            return _err(OPERATOR_SESSION_INVALID, "El programa que pidio emparejarse ya no esta en marcha.")
        session_id = str(uuid.uuid4())
        row = self.store.create(
            {
                "session_id": session_id,
                "state": "active",
                "harness_pid": int(op.harness.pid),
                "harness_create_time": float(op.harness.create_time),
                "harness_exe": str(info.get("exe") or ""),
                "harness_name": str(info.get("name") or ""),
                "harness_user": str(info.get("username") or ""),
                "harness_cwd": str(info.get("cwd") or ""),
                "ancestors": op.ancestors,
                "projects": [p["project_uuid"] for p in projects],
                "autonomy_level": AUTONOMY_LEVEL,
                "transport": op.transport,
                "created_at_ms": now,
                "last_seen_at_ms": now,
                "idle_timeout_ms": self.idle_timeout_ms,
            }
        )
        self._audit({"event": "operator_paired", "from_principal": f"operator:{session_id}", "actor": HUMAN_UI_ACTOR,
                     "decision": "approved"})
        return {"success": True, "session_id": session_id, "projects": row.get("projects")}

    # -- autorizacion por llamada ----------------------------------------------------------------------------------

    def _end(self, session_id: str, state: str, reason: str, actor: str) -> bool:
        ended = self.store.end(session_id, state, reason=reason, now_ms=int(self._clock_ms()), actor=actor)
        if ended:
            self._audit({"event": "operator_session_ended", "from_principal": f"operator:{session_id}",
                         "reason_code": reason, "actor": actor, "decision": state})
            self._close_connections(session_id)
        return ended

    def _close_connections(self, session_id: str) -> None:
        with self._lock:
            conns = list(self._connections.values())
        for op, close in conns:
            if close is None or op.harness is None:
                continue
            row = self.store.find_for_harness(op.harness.pid, op.harness.create_time)
            if row is not None and row["session_id"] == session_id:
                try:
                    close()
                except Exception:
                    logger.debug("No se pudo cerrar una conexion del operador", exc_info=True)

    def _harness_alive(self, row: Dict[str, Any]) -> bool:
        ident = ProcessIdentity(int(row["harness_pid"]), float(row["harness_create_time"]))
        if not self.processes.is_alive(ident):
            return False
        info = self.processes.describe(ident)
        return info is not None and info.get("exe") == row["harness_exe"]

    def _check_active(self, row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Revalida una sesion ACTIVA; la termina (y devuelve el error) si ya no vale."""
        now = int(self._clock_ms())
        if not self._harness_alive(row):
            self._end(row["session_id"], "dead", "HARNESS_GONE", "hub")
            return _err(OPERATOR_SESSION_INVALID, "La sesion del operador termino: el programa emparejado ya no existe.")
        if now - int(row["last_seen_at_ms"]) > int(row["idle_timeout_ms"]):
            self._end(row["session_id"], "expired", "IDLE_TIMEOUT", "hub")
            return _err("OPERATOR_SESSION_EXPIRED", "La sesion del operador caduco por inactividad.")
        return None

    def authorize(self, op: OperatorPeer) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """Sesion valida de la conexion o el error. Si el harness no esta emparejado, abre (o espera) el dialogo."""
        if op.rejection is not None:
            return None, op.rejection
        if op.harness is None or op.peer is None:
            return None, _err(OPERATOR_PEER_UNVERIFIED, "No se pudo identificar el proceso del harness.")
        if not self.processes.descends_from(op.peer, op.harness):
            stale = self.store.find_for_harness(op.harness.pid, op.harness.create_time)
            if stale is not None and stale["state"] == "active":
                self._check_active(stale)  # el harness murio o su pid se reutilizo: la sesion queda invalida ya
            return None, _err(OPERATOR_PEER_UNVERIFIED, "La conexion ya no pertenece al programa emparejado.")
        for attempt in range(2):
            row = self.store.find_for_harness(op.harness.pid, op.harness.create_time)
            if row is not None and row["state"] == "active":
                problem = self._check_active(row)
                if problem is None:
                    self.store.touch(row["session_id"], int(self._clock_ms()))
                    return row, None
                if problem["code"] != "OPERATOR_SESSION_EXPIRED":
                    return None, problem
                row = None  # caducada: se puede volver a pedir
            elif row is not None and row["state"] == "revoked":
                return None, _err(
                    OPERATOR_SESSION_REVOKED,
                    "El humano revoco la sesion de este programa. Para volver a emparejarlo, reinicialo.",
                )
            elif row is not None and row["state"] == "dead":
                return None, _err(OPERATOR_SESSION_INVALID, "La sesion del operador ya no es valida.")
            if attempt:
                break
            pairing = self._start_pairing(op)
            if pairing is None:
                with self._lock:
                    declined = self._declined.get(op.harness_key) if op.harness_key else None
                return None, _err(
                    OPERATOR_NOT_PAIRED,
                    "Este programa no esta emparejado con MCP2P (rechazado o sin ventana para aprobarlo). Espera unos "
                    "minutos y vuelve a intentarlo, o reinicia el programa.",
                    pairing=declined[1] if declined else None,
                )
            if not pairing.event.wait(timeout=self.pairing_wait_s):
                return None, _err(
                    OPERATOR_PAIRING_PENDING,
                    "Esperando a que el humano apruebe el emparejamiento en la ventana de MCP2P; vuelve a intentarlo.",
                )
            result = pairing.result or {}
            if not result.get("success"):
                return None, result or _err(OPERATOR_NOT_PAIRED, "Emparejamiento no aprobado.")
        return None, _err(OPERATOR_NOT_PAIRED, "Este programa no esta emparejado con MCP2P.")

    # -- directorio para el relevo -------------------------------------------------------------------------------

    @staticmethod
    def _allows(row: Dict[str, Any], project_uuid: str) -> bool:
        projects = row.get("projects") or []
        return ALL_PROJECTS in projects or project_uuid in projects

    def accepts_reply(self, session_id: str, project_uuid: str) -> bool:
        row = self.store.get(str(session_id))
        if row is None or row["state"] != "active" or not self._allows(row, project_uuid):
            return False
        if not self._harness_alive(row):
            self._end(row["session_id"], "dead", "HARNESS_GONE", "hub")
            return False
        return True

    def label(self, session_id: str) -> Dict[str, Any]:
        row = self.store.get(str(session_id))
        if row is None:
            return {"label": "Operador (sesion desconocida)"}
        return {"label": f"Operador: {row.get('harness_name') or '?'}", "harness_name": row.get("harness_name"),
                "autonomy_level": row.get("autonomy_level")}

    # -- destinos ----------------------------------------------------------------------------------------------

    def privilege_of(self, rec: Any) -> str:
        profile = str(getattr(rec, "cli_profile", "") or "")
        kind = self._catalog_kind(profile)
        argvs = None
        pid, ctime = getattr(rec, "pid", None), getattr(rec, "pid_create_time", None)
        if pid and ctime is not None:
            try:
                argvs = self.processes.tree_cmdlines(ProcessIdentity(int(pid), float(ctime)))
            except Exception:
                argvs = None
        return classify_pane(profile=profile, catalog_kind=kind, argvs=argvs)

    def _project_names(self) -> Dict[str, str]:
        return {str(p["project_uuid"]): str(p.get("name") or "") for p in (self._projects_provider() or [])}

    def _scope(self, row: Dict[str, Any], project_uuid: Any) -> Tuple[Optional[List[str]], Optional[Dict[str, Any]]]:
        names = self._project_names()
        allowed = list(names) if ALL_PROJECTS in (row.get("projects") or []) else [
            p for p in row.get("projects") or [] if p in names
        ]
        if project_uuid is None or (isinstance(project_uuid, str) and not project_uuid.strip()):
            return allowed, None
        wanted = str(project_uuid).strip()
        if wanted not in allowed:
            return None, _err(PROJECT_SCOPE_FORBIDDEN, "Ese proyecto no esta en el alcance de esta sesion de operador.")
        return [wanted], None

    def _resolve_target(self, project_uuid: str, target: Any) -> Optional[Any]:
        registry = self._panes()
        if registry is None or not isinstance(target, str) or not target.strip():
            return None
        target = target.strip()
        rec = registry.find_active_pane_by_node(target, project_uuid=project_uuid)
        if rec is not None:
            return rec
        try:
            rec = registry.get_pane(target)
        except ValueError:
            return None
        if rec is None or getattr(rec, "status", "active") == "closed" or rec.project_uuid != project_uuid:
            return None
        return rec

    def list_targets(self, row: Dict[str, Any], project_uuid: Any = None) -> Dict[str, Any]:
        scope, problem = self._scope(row, project_uuid)
        if problem is not None:
            return problem
        registry = self._panes()
        relay = self.relay
        names = self._project_names()
        targets: List[Dict[str, Any]] = []
        for item in (registry.list_panes() if registry is not None else []):
            if item.get("project_uuid") not in (scope or []):
                continue
            rec = registry.get_pane(item["pane_id"])
            if rec is None:
                continue
            privilege = self.privilege_of(rec)
            pane_uri = Principal.pane(str(rec.node_uuid)).uri
            paused = False
            pending = None
            if relay is not None:
                paused = relay._paused("global") or relay._paused(f"pane:{rec.node_uuid}")
                pending = relay.store.count_pending(pane_uri)
            targets.append(
                {
                    "target": rec.node_uuid,
                    "pane_id": rec.pane_id,
                    "agent_name": rec.agent_name,
                    "cli_profile": rec.cli_profile,
                    "project_uuid": rec.project_uuid,
                    "project_name": names.get(rec.project_uuid, ""),
                    "status": rec.status,
                    "worker_mode": bool(getattr(rec, "worker_mode", False)),
                    "auto_relay_enabled": bool(getattr(rec, "auto_relay_enabled", False)),
                    "privilege_class": privilege,
                    "requires_human_confirmation": requires_human_confirmation(privilege),
                    "relay_paused": bool(paused),
                    "inbox_pending": pending,
                }
            )
        return {
            "success": True,
            "session_id": row["session_id"],
            "projects": [{"project_uuid": p, "name": names.get(p, "")} for p in (scope or [])],
            "autonomy_level": row.get("autonomy_level"),
            "targets": targets,
        }

    def send_task(
        self,
        row: Dict[str, Any],
        *,
        target: Any,
        text: Any,
        project_uuid: Any,
        kind: Any = "task.request",
        confirm_with_human: Any = False,
        card_id: Any = None,
    ) -> Dict[str, Any]:
        if not isinstance(project_uuid, str) or not project_uuid.strip():
            return _err("MISSING_PROJECT", "Indica project_uuid (operator_list_targets lo muestra).")
        scope, problem = self._scope(row, project_uuid)
        if problem is not None or not scope:
            return problem or _err(PROJECT_SCOPE_FORBIDDEN, "Proyecto fuera de alcance.")
        project = scope[0]
        if card_id is not None and (not isinstance(card_id, str) or len(card_id) > MAX_CARD_ID):
            return _err("INVALID_CARD_ID", f"card_id debe ser texto de hasta {MAX_CARD_ID} caracteres.")
        rec = self._resolve_target(project, target)
        if rec is None:
            return _err("TARGET_NOT_FOUND", "No hay ningun panel activo con ese destino en este proyecto.")
        privilege = self.privilege_of(rec)
        park = PARK_ELEVATED if requires_human_confirmation(privilege) else (
            PARK_REQUESTED if confirm_with_human is True else None
        )
        relay = self.relay
        if relay is None:
            return _err("HUB_REQUIRED", "El relevo del Hub no esta disponible.")
        result = relay.submit_from_operator(
            f"operator:{row['session_id']}",
            project_uuid=project,
            target_rec=rec,
            text=text,
            kind=kind if isinstance(kind, str) else "",
            card_id=card_id,
            park_reason=park,
        )
        if result.get("success"):
            result["privilege_class"] = privilege
            result["wake"] = (
                "none: el agente lo lee con inbox_next/inbox_wait (modo trabajador) o cuando el humano se lo pida; "
                "el despertar automatico llega en la ola siguiente"
            )
            if park == PARK_ELEVATED:
                result["note"] = "Destino con permisos amplios: el humano debe aprobar el mensaje en MCP2P."
        return result

    def message_status(self, row: Dict[str, Any], msg_id: Any) -> Dict[str, Any]:
        return self.relay.operator_status(f"operator:{row['session_id']}", msg_id)

    def await_reply(self, row: Dict[str, Any], msg_id: Any, timeout_ms: Any) -> Dict[str, Any]:
        return self.relay.await_operator_reply(f"operator:{row['session_id']}", msg_id, timeout_ms=timeout_ms)

    def call(self, op: OperatorPeer, action: str, **kwargs: Any) -> Dict[str, Any]:
        """Punto unico de las herramientas: autoriza la conexion y despacha. Nunca lanza."""
        try:
            row, problem = self.authorize(op)
            if problem is not None or row is None:
                return problem or _err(OPERATOR_NOT_PAIRED, "Sin sesion de operador.")
            fn = {
                "list_targets": self.list_targets,
                "send_task": self.send_task,
                "message_status": self.message_status,
                "await_reply": self.await_reply,
            }[action]
            return fn(row, **kwargs)
        except Exception as exc:
            logger.warning("Herramienta del operador %s fallo: %s", action, exc, exc_info=True)
            return _err("INTERNAL_ERROR", "Error interno del canal del operador.")

    # -- GUI (solo el humano) ---------------------------------------------------------------------------------------

    def list_sessions(self, include_ended: bool = False) -> List[Dict[str, Any]]:
        names = self._project_names()
        rows = self.store.list(None if include_ended else ("active",))
        out = []
        for row in rows:
            if row["state"] == "active":
                self._check_active(row)  # marca muertas o caducadas antes de mostrarlas
                row = self.store.get(row["session_id"]) or row
                if row["state"] != "active" and not include_ended:
                    continue
            projects = row.get("projects") or []
            with self._lock:
                connections = sum(
                    1 for op, _ in self._connections.values()
                    if op.harness is not None and int(op.harness.pid) == int(row["harness_pid"])
                    and abs(float(op.harness.create_time) - float(row["harness_create_time"])) < 0.01
                )
            out.append(
                {
                    "session_id": row["session_id"],
                    "state": row["state"],
                    "state_reason": row.get("state_reason"),
                    "harness": {"pid": row["harness_pid"], "name": row.get("harness_name"), "exe": row["harness_exe"],
                                "user": row.get("harness_user"), "cwd": row.get("harness_cwd")},
                    "ancestors": row.get("ancestors") or [],
                    "projects": [{"project_uuid": p, "name": "TODOS" if p == ALL_PROJECTS else names.get(p, "")}
                                 for p in projects],
                    "autonomy_level": row.get("autonomy_level"),
                    "transport": row.get("transport"),
                    "created_at": int(row["created_at_ms"]),
                    "last_seen_at": int(row["last_seen_at_ms"]),
                    "idle_timeout_ms": int(row["idle_timeout_ms"]),
                    "ended_at": row.get("ended_at_ms"),
                    "connections": connections,
                }
            )
        return out

    def revoke(self, session_id: Any, actor: str = HUMAN_UI_ACTOR) -> Dict[str, Any]:
        """Revocar es bajar autonomia: sin dialogo. Es definitivo para ese proceso del harness."""
        if not isinstance(session_id, str) or not session_id.strip():
            return _err("INVALID_ARGUMENT", "Indica el session_id.")
        if not self._end(session_id.strip(), "revoked", "HUMAN_REVOKED", actor):
            return _err("SESSION_NOT_FOUND", "No hay ninguna sesion de operador activa con ese identificador.")
        return {"success": True, "session_id": session_id.strip(), "state": "revoked"}

    def pairing_status(self) -> Dict[str, Any]:
        now = int(self._clock_ms())
        with self._lock:
            pending = [
                {"harness_pid": p.harness_pid, "harness_name": p.harness_name, "since": p.started_ms}
                for p in self._pairings.values() if not p.event.is_set()
            ]
            declined = [
                {"harness_pid": key[0], "code": code, "retry_after": until}
                for key, (until, code) in self._declined.items() if until > now
            ]
            connections = len(self._connections)
        return {
            "success": True,
            "channel": dict(self.channel_info),
            "pending": pending,
            "declined": declined,
            "connections": connections,
            "active_sessions": len(self.store.list(("active",))),
            "autonomy_level": AUTONOMY_LEVEL,
            "idle_timeout_ms": self.idle_timeout_ms,
        }


def _default_catalog_kind(profile: str) -> Optional[str]:
    """Clase del CLI segun el catalogo (`agent_cli`, `shell`, ...); None si el perfil no existe."""
    try:
        from src.adapters.terminal.cli_catalog import load_catalog

        spec = load_catalog().profiles.get(profile)
    except Exception:
        return None
    return getattr(spec, "kind", None) if spec is not None else None


__all__ = [
    "ELEVATED",
    "OperatorChannelService",
    "OperatorPeer",
]
