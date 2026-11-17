"""Servicio de aplicación para el relevo push->pull del enjambre.

F0 del dossier 37: el relevo NUNCA escribe en el `stdin` de un PTY. Ola 3: los mensajes directos entre paneles viven
en la bandeja PERSISTENTE de `MessageRelayService` (sobre firmado, cadena causal, auditoria); este orquestador solo
conserva las colas pull de eventos Kanban/Tribunal y los avisos del Auto-Relay (en memoria). `await_next_assignment`
(alias D20) mezcla ambos: la fuente de mensajes se conecta con `attach_message_source`."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import defaultdict, deque
from typing import TYPE_CHECKING, Any, Callable, Deque, Dict, List, Optional, Set, Tuple

if TYPE_CHECKING:
    from src.a2a.bus import A2ABus
    from src.core.events import DomainEventBus

logger = logging.getLogger("mcp2p.core.services.swarm_relay")

_MAX_DEDUP_IDS_PER_NODE = 4096
# Tope de cada cola pull (incluida la comodin `*`): al llenarse se descarta el elemento MAS ANTIGUO (EGM-72).
MAX_QUEUE_DEPTH = 1024
# Tope de colas distintas: el destino puede venir de un payload, no debe poder crear colas sin limite.
MAX_TARGET_QUEUES = 256
# Una espera ocupa un hilo del threadpool del Hub: se acota (EGM-76). El agente vuelve a llamar al vencer.
MAX_AWAIT_TIMEOUT_MS = 300_000
# Tope del texto de un mensaje directo entre paneles, en bytes UTF-8 (EGM-96). Definicion unica en el relevo (Ola 3).
from src.core.services.message_relay_service import MAX_MESSAGE_BYTES as MAX_DIRECT_MESSAGE_BYTES  # noqa: E402
# Tope de la bandeja de cada nodo destino: al llenarse se RECHAZA el mensaje nuevo (el remitente lo sabe).
MAX_INBOX_DEPTH = 256
# La procedencia la fija el servidor a partir de la sesion de panel autenticada por el Hub; nunca el remitente.
PROVENANCE_VERIFIED_BY = "hub_pane_session"



def clamp_await_timeout_ms(value: Any) -> int:
    """`timeout_ms` externo -> entero en `0..MAX_AWAIT_TIMEOUT_MS` (basura o negativo = 0)."""
    if isinstance(value, bool):
        return 0
    try:
        number = int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, min(number, MAX_AWAIT_TIMEOUT_MS))
_IGNORED_INFORMATIVE_SIGNALS = frozenset({"HEARTBEAT", "PING", "PONG", "LEASE_CLAIMED"})
_EXPLICIT_TARGET_KEYS = (
    "target_node_uuid",
    "recipient_node_uuid",
    "to_node_uuid",
    "assignee_node_uuid",
    "next_assignee_node_uuid",
    "arbiter_node_uuid",
    "target_node",
)


class SwarmRelayOrchestrator:
    """Orquestador thread-safe de colas pull y de la bandeja de mensajes por nodo (sin escribir en ningun PTY)."""

    def __init__(
        self,
        *,
        event_bus: Optional[DomainEventBus] = None,
        bus: Optional[A2ABus] = None,
        pane_registry: Optional[Any] = None,
        max_relay_rounds: int = 6,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._conditions: Dict[str, threading.Condition] = {}
        self._queues: Dict[str, Deque[Dict[str, Any]]] = defaultdict(deque)
        self._event_bus = event_bus
        self._bus = bus
        self.pane_registry = pane_registry
        self.max_relay_rounds = max(1, int(max_relay_rounds))
        self._clock: Callable[[], float] = clock or time.time
        # Auto-Relay (D2): es POR PANEL y vive en el `PaneRegistry` (persistido por proyecto y ranura). Solo
        # significa «dejar avisos en la bandeja de ESE panel»; no escribe en ninguna terminal.
        self._closed = False
        # Bandeja por nodo destino: mensajes directos y avisos con procedencia estructurada (en memoria).
        self._inbox: Dict[str, Deque[Dict[str, Any]]] = defaultdict(deque)

        self._dedup_order: Dict[str, Deque[str]] = {}
        self._dedup_seen: Dict[str, Set[str]] = {}
        self._relay_rounds: Dict[Tuple[str, str], int] = defaultdict(int)
        self._loop_breaker_states: Dict[str, str] = {}
        # Turnos del relevo en cola por (proyecto:tarjeta|sesion, par de nodos): {key: ultimo remitente}.
        self._relay_last_sender: Dict[str, str] = {}
        self._dropped_oldest_total = 0
        self._active_waiters: Dict[str, int] = defaultdict(int)
        # Ola 3: fuente de mensajes directos persistentes (alias `await_next_assignment`): fn(node, proyecto) -> lista.
        self._message_source: Optional[Callable[[str, Optional[str]], List[Dict[str, Any]]]] = None

        if self._event_bus is not None:
            self._event_bus.subscribe("*", self._on_domain_event)
        if self._bus is not None:
            self._bus.subscribe("*", self._on_a2a_message)

    # ------------------------------------------------------------------
    # Utilidades internas de sincronización y resolución de claves
    # ------------------------------------------------------------------

    def _get_condition_locked(self, node_uuid: str) -> threading.Condition:
        cond = self._conditions.get(node_uuid)
        if cond is None:
            cond = threading.Condition(self._lock)
            self._conditions[node_uuid] = cond
        return cond

    def _notify_waiters_locked(self, target_node_uuid: str) -> None:
        if target_node_uuid == "*":
            for cond in list(self._conditions.values()):
                cond.notify_all()
            return
        for key in (target_node_uuid, "*"):
            cond = self._conditions.get(key)
            if cond is not None:
                cond.notify_all()

    @staticmethod
    def _extract_target_node(
        container: Dict[str, Any],
        *,
        allow_explicit_node_uuid: bool = False,
    ) -> Optional[str]:
        candidates: List[Dict[str, Any]] = [container]
        for nested_key in ("payload", "data", "card"):
            nested = container.get(nested_key)
            if isinstance(nested, dict):
                candidates.append(nested)

        for source in candidates:
            for key in _EXPLICIT_TARGET_KEYS:
                val = source.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
            if allow_explicit_node_uuid:
                val = source.get("node_uuid")
                if isinstance(val, str) and val.strip():
                    return val.strip()
        return None

    def _queue_for_locked(self, target: str) -> Optional[Deque[Dict[str, Any]]]:
        """Cola del destino acotada en numero (`MAX_TARGET_QUEUES`); None si no cabe otra distinta."""
        queue = self._queues.get(target)
        if queue is not None:
            return queue
        if len(self._queues) >= MAX_TARGET_QUEUES:
            for key in [k for k, q in self._queues.items() if not q and k != "*"]:
                del self._queues[key]
            if len(self._queues) >= MAX_TARGET_QUEUES:
                return None
        return self._queues[target]

    @classmethod
    def _extract_sender_node(cls, assignment: Dict[str, Any]) -> str:
        """Remitente del evento (autenticado en el Hub); vacio si el evento no lo declara."""
        return cls._extract_nested_str(
            assignment, "sender_node_uuid", "actor_node_uuid", "from_node_uuid", "node_uuid", "claimed_by_node_uuid"
        )

    @staticmethod
    def _extract_nested_str(assignment: Dict[str, Any], *keys: str) -> str:
        containers: List[Dict[str, Any]] = [assignment]
        for nested_key in ("payload", "data"):
            nested = assignment.get(nested_key)
            if isinstance(nested, dict):
                containers.append(nested)
        for source in containers:
            for key in keys:
                val = source.get(key)
                if val is not None and str(val).strip():
                    return str(val).strip()
        return ""

    @staticmethod
    def _make_loop_key(
        *,
        project_uuid: str = "",
        card_id: str = "",
        session_id: str = "",
        assignment_key: str = "",
        fallback: str = "",
    ) -> Optional[str]:
        explicit_key = assignment_key.strip() if assignment_key else ""
        if explicit_key:
            return explicit_key
        entity = card_id.strip() or session_id.strip() or fallback.strip()
        if not entity:
            return None
        proj = project_uuid.strip() if project_uuid else "*"
        return f"{proj}:{entity}"

    def _is_duplicate_locked(self, target_node_uuid: str, dedup_id: str) -> bool:
        seen = self._dedup_seen.get(target_node_uuid)
        if seen is not None and dedup_id in seen:
            return True
        if seen is None:
            seen = set()
            self._dedup_seen[target_node_uuid] = seen
            self._dedup_order[target_node_uuid] = deque()
        order = self._dedup_order[target_node_uuid]
        if len(order) >= _MAX_DEDUP_IDS_PER_NODE:
            evicted = order.popleft()
            seen.discard(evicted)
        order.append(dedup_id)
        seen.add(dedup_id)
        return False

    def _pop_matching_locked(
        self,
        node_uuid: str,
        project_uuid: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        # Ola 2: None = sin filtro (uso interno); un texto (incluido '') filtra por ese proyecto EXACTO.
        proj_filter = project_uuid.strip() if isinstance(project_uuid, str) else None
        search_keys = [node_uuid] if node_uuid == "*" else [node_uuid, "*"]
        for key in search_keys:
            queue = self._queues.get(key)
            if not queue:
                continue
            for idx, item in enumerate(queue):
                if proj_filter is not None and item.get("project_uuid") != proj_filter:
                    continue
                del queue[idx]
                return dict(item)
        return None

    def _remove_stale_card_created_locked(self, project_uuid: str, card_id: str) -> None:
        if not card_id:
            return
        wildcard_q = self._queues.get("*")
        if not wildcard_q:
            return
        kept: Deque[Dict[str, Any]] = deque()
        for item in wildcard_q:
            is_stale_created = (
                item.get("source") == "domain_event"
                and item.get("event_type") == "kanban.card_created"
                and item.get("project_uuid") == project_uuid
                and item.get("card_id") == card_id
            )
            if not is_stale_created:
                kept.append(item)
        self._queues["*"] = kept

    # ------------------------------------------------------------------
    # API pública de colas pull, relevo y Loop Breaker
    # ------------------------------------------------------------------

    def enqueue_assignment(
        self,
        target_node_uuid: str,
        assignment: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Encola una asignación para ``target_node_uuid`` aplicando dedup y Loop Breaker DoD."""
        return self._enqueue(target_node_uuid, assignment)

    def attach_message_source(
        self, source: Optional[Callable[[str, Optional[str]], List[Dict[str, Any]]]]
    ) -> None:
        """Conecta la bandeja persistente (Ola 3): `source(node_uuid, project_uuid)` entrega y confirma mensajes."""
        with self._lock:
            self._message_source = source

    def wake(self, node_uuid: str) -> None:
        """Despierta a quien espera en `await_next_assignment` porque llego algo a la bandeja persistente."""
        with self._lock:
            self._notify_waiters_locked((node_uuid or "").strip() or "*")

    def _enqueue(
        self,
        target_node_uuid: str,
        assignment: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Dedup + Loop Breaker comunes de la cola pull (eventos Kanban/Tribunal/A2A)."""
        target = (target_node_uuid or "").strip() or "*"
        if not isinstance(assignment, dict):
            return {
                "success": False,
                "code": "INVALID_ASSIGNMENT",
                "reason": "assignment debe ser un diccionario.",
            }

        item = dict(assignment)
        item.setdefault("assignment_uuid", str(uuid.uuid4()))
        item.setdefault("target_node_uuid", target)
        item.setdefault("enqueued_at", self._clock())

        project_uuid = self._extract_nested_str(item, "project_uuid")
        if project_uuid:
            item.setdefault("project_uuid", project_uuid)
        card_id = self._extract_nested_str(item, "card_id")
        session_id = self._extract_nested_str(item, "session_id", "dispute_id")
        dedup_id = self._extract_nested_str(item, "dedup_key", "event_id", "message_uuid", "assignment_id")
        # EGM-72: el `assignment_key` que trae el payload lo controla el agente (rotarlo evadia el freno):
        # la clave de ronda sale SOLO de la tarjeta/sesion y del par de nodos, derivados en el servidor.
        sender = self._extract_sender_node(item)
        base_key = self._make_loop_key(project_uuid=project_uuid, card_id=card_id, session_id=session_id)
        if base_key is None:
            loop_key = None
        elif target == "*":
            # EGM-83: el trafico real del Hub (RFC, eventos kanban) va a la cola "*": no hay par de nodos que
            # derivar. Con el par {remitente, "*"} cada agente tenia su propia clave y el ping-pong A<->B no
            # sumaba nunca. En difusion la clave es la de la tarjeta (y la sesion RFC si la hay) y una ronda
            # es un cambio de remitente.
            scope = f"|s:{session_id}" if card_id and session_id else ""
            loop_key = f"{base_key}{scope}|*"
        else:
            loop_key = f"{base_key}|{'|'.join(sorted({sender, target}))}"

        with self._lock:
            if self._closed:
                return {
                    "success": False,
                    "blocked": True,
                    "code": "ORCHESTRATOR_CLOSED",
                    "reason": "SwarmRelayOrchestrator cerrado.",
                }
            if dedup_id and self._is_duplicate_locked(target, dedup_id):
                return {
                    "success": True,
                    "deduplicated": True,
                    "target_node_uuid": target,
                    "assignment_uuid": item.get("assignment_uuid"),
                }

            rounds = 0
            if loop_key is not None:
                # Una ronda es un CAMBIO DE SENTIDO entre los dos nodos (respuesta -> nuevo despacho); varios
                # eventos seguidos del mismo remitente (ediciones legitimas) son un solo turno.
                # Un evento sin remitente no se puede atribuir a ningun nodo: nunca abre una ronda (EGM-83).
                same_turn = not sender or self._relay_last_sender.get(loop_key) == sender
                if loop_key in self._loop_breaker_states or (
                    not same_turn and self._relay_rounds[("relay", loop_key)] >= self.max_relay_rounds
                ):
                    self._loop_breaker_states[loop_key] = "LOOP_BREAKER_TRIPPED"
                    rounds = self._relay_rounds[("relay", loop_key)]
                    return {
                        "success": False,
                        "blocked": True,
                        "status": "LOOP_BREAKER_TRIPPED",
                        "code": "RELAY_MAX_ROUNDS_EXCEEDED",
                        "requires_hitl": True,
                        "assignment_key": loop_key,
                        "rounds": rounds,
                        "max_relay_rounds": self.max_relay_rounds,
                        "reason": (
                            f"Loop Breaker activado tras {rounds} rondas de relevo "
                            f"(máximo {self.max_relay_rounds}); requiere intervención humana (HITL)."
                        ),
                    }
                if not same_turn:
                    self._relay_rounds[("relay", loop_key)] += 1
                    self._relay_last_sender[loop_key] = sender
                rounds = self._relay_rounds[("relay", loop_key)]
                item["relay_round"] = rounds
                item["assignment_key"] = loop_key

            queue = self._queue_for_locked(target)
            if queue is None:
                return {
                    "success": False,
                    "blocked": True,
                    "code": "QUEUE_TARGET_LIMIT",
                    "reason": f"Demasiados destinos distintos en cola (maximo {MAX_TARGET_QUEUES}).",
                }
            dropped = False
            if len(queue) >= MAX_QUEUE_DEPTH:
                queue.popleft()  # descarte documentado del mas antiguo
                self._dropped_oldest_total += 1
                dropped = True
                if self._dropped_oldest_total == 1 or self._dropped_oldest_total % 1000 == 0:
                    logger.warning(
                        "Cola de relevo '%s' llena (%d): se descarta lo mas antiguo (%d descartados en total).",
                        target, MAX_QUEUE_DEPTH, self._dropped_oldest_total,
                    )
            queue.append(item)
            queue_depth = len(queue)
            self._notify_waiters_locked(target)

        return {
            "success": True,
            "queued": True,
            "target_node_uuid": target,
            "assignment_uuid": item["assignment_uuid"],
            "queue_depth": queue_depth,
            "rounds": rounds,
            "assignment_key": loop_key,
            "dropped_oldest": dropped,
        }

    def _pop_inbox_locked(self, node_uuid: str, project_uuid: Optional[str]) -> List[Dict[str, Any]]:
        """Vacia la bandeja de `node_uuid` (solo los mensajes del proyecto filtrado, si se pidio)."""
        proj_filter = project_uuid.strip() if isinstance(project_uuid, str) else None
        inbox = self._inbox.get(node_uuid)
        if not inbox:
            return []
        taken: List[Dict[str, Any]] = []
        kept: Deque[Dict[str, Any]] = deque()
        for msg in inbox:
            if proj_filter is not None and msg.get("project_uuid") != proj_filter:
                kept.append(msg)
            else:
                taken.append(dict(msg))
        self._inbox[node_uuid] = kept
        return taken

    def _ready_locked(
        self,
        node_uuid: str,
        project_uuid: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        """Resultado de espera si hay una asignacion o mensajes de bandeja listos; None si no hay nada."""
        item = self._pop_matching_locked(node_uuid, project_uuid)
        messages = self._pop_inbox_locked(node_uuid, project_uuid) if node_uuid != "*" else []
        if node_uuid != "*" and self._message_source is not None:
            try:
                messages = list(self._message_source(node_uuid, project_uuid)) + messages
            except Exception:
                logger.warning("No se pudo leer la bandeja persistente de %s", node_uuid, exc_info=True)
        if item is None and not messages:
            return None
        return {
            "success": True,
            "has_assignment": item is not None,
            "assignment": item,
            "has_messages": bool(messages),
            "messages": messages,
            "timed_out": False,
        }

    def await_next_assignment(
        self,
        node_uuid: str,
        *,
        project_uuid: Optional[str] = None,
        timeout_ms: int = 0,
    ) -> Dict[str, Any]:
        """Entrega la siguiente asignación y los mensajes de bandeja del nodo; espera con ``Condition.wait``.

        `messages` lleva cada mensaje con su procedencia (`from`) como campo estructurado aparte del `text`.
        """
        timeout_ms = clamp_await_timeout_ms(timeout_ms)
        target = (node_uuid or "").strip()
        empty: Dict[str, Any] = {
            "has_assignment": False,
            "assignment": None,
            "has_messages": False,
            "messages": [],
        }
        if not target:
            return {
                "success": False,
                **empty,
                "code": "INVALID_NODE_UUID",
                "error": "node_uuid requerido para esperar asignaciones.",
            }

        with self._lock:
            if self._closed:
                return {"success": False, **empty, "timed_out": False, "closed": True}
            ready = self._ready_locked(target, project_uuid)
            if ready is not None:
                return ready
            if timeout_ms <= 0:
                return {"success": True, **empty, "timed_out": False}

            deadline = time.monotonic() + (timeout_ms / 1000.0)
            cond = self._get_condition_locked(target)
            self._active_waiters[target] += 1
            try:
                while not self._closed:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return {"success": True, **empty, "timed_out": True}
                    cond.wait(timeout=remaining)
                    if self._closed:
                        return {"success": True, **empty, "timed_out": False, "closed": True}
                    ready = self._ready_locked(target, project_uuid)
                    if ready is not None:
                        return ready
            finally:
                remaining_waiters = self._active_waiters.get(target, 1) - 1
                if remaining_waiters <= 0:
                    self._active_waiters.pop(target, None)
                else:
                    self._active_waiters[target] = remaining_waiters

            return {"success": True, **empty, "timed_out": False, "closed": True}

    def get_loop_breaker_state(
        self,
        *,
        project_uuid: str = "",
        card_id: str = "",
        session_id: str = "",
        assignment_key: str = "",
    ) -> Dict[str, Any]:
        """Consulta el estado actual de rondas y disparo del Loop Breaker."""
        key = self._make_loop_key(
            project_uuid=project_uuid,
            card_id=card_id,
            session_id=session_id,
            assignment_key=assignment_key,
        )
        if key is None:
            return {"status": "UNKNOWN", "relay_rounds": 0, "dispatch_rounds": 0}
        with self._lock:
            keys = self._keys_for_locked(key)
            relay_r = max([self._relay_rounds.get(("relay", k), 0) for k in keys] or [0])
            disp_r = max([self._relay_rounds.get(("dispatch", k), 0) for k in keys] or [0])
            tripped = [k for k in keys if self._loop_breaker_states.get(k) == "LOOP_BREAKER_TRIPPED"]
            status = "LOOP_BREAKER_TRIPPED" if tripped else "ACTIVE"
            return {
                "assignment_key": key,
                "status": status,
                "relay_rounds": relay_r,
                "dispatch_rounds": disp_r,
                "requires_hitl": bool(tripped),
                "tripped_keys": tripped,
                "max_relay_rounds": self.max_relay_rounds,
            }

    def _keys_for_locked(self, base_key: str) -> List[str]:
        """Claves del par (tarjeta/sesion, nodos): la base y todas las `base|nodoA|nodoB`."""
        known: Set[str] = set(self._loop_breaker_states)
        known.update(k for (_, k) in self._relay_rounds)
        known.update(self._relay_last_sender)
        return [k for k in known if k == base_key or k.startswith(base_key + "|")]

    def list_tripped_loop_breakers(self) -> List[str]:
        """Claves con el Loop Breaker disparado (para que la UI ofrezca el reinicio humano)."""
        with self._lock:
            return sorted(k for k, v in self._loop_breaker_states.items() if v == "LOOP_BREAKER_TRIPPED")

    def reset_loop_breaker(
        self,
        *,
        project_uuid: str = "",
        card_id: str = "",
        session_id: str = "",
        assignment_key: str = "",
    ) -> bool:
        """Restablece el estado del Loop Breaker tras intervención humana (HITL)."""
        key = self._make_loop_key(
            project_uuid=project_uuid,
            card_id=card_id,
            session_id=session_id,
            assignment_key=assignment_key,
        )
        if key is None:
            return False
        with self._lock:
            for k in self._keys_for_locked(key):
                self._loop_breaker_states.pop(k, None)
                self._relay_rounds.pop(("relay", k), None)
                self._relay_rounds.pop(("dispatch", k), None)
                self._relay_last_sender.pop(k, None)
        return True

    # ------------------------------------------------------------------
    # Auto-Relay controlado y mensajería directa entre nodos/paneles
    # ------------------------------------------------------------------

    def _active_panes(self) -> List[Dict[str, Any]]:
        """Paneles activos del registro como diccionarios (sin token)."""
        if self.pane_registry is None:
            return []
        list_panes_fn = getattr(self.pane_registry, "list_panes", None)
        if not callable(list_panes_fn):
            return []
        panes: List[Dict[str, Any]] = []
        for raw in list_panes_fn() or []:
            pane = dict(raw) if isinstance(raw, dict) else (
                raw.to_dict(include_token=False) if hasattr(raw, "to_dict") else None
            )
            if pane and str(pane.get("status") or "active").lower() != "closed":
                panes.append(pane)
        return panes

    def get_auto_relay_status(self) -> Dict[str, Any]:
        """Estado del Auto-Relay por panel (avisos en bandeja) y de los Loop Breakers disparados (vista de la GUI)."""
        relay_panes = sorted(
            str(p.get("pane_id")) for p in self._active_panes() if bool(p.get("auto_relay_enabled"))
        )
        with self._lock:
            return {
                "success": True,
                "auto_relay_enabled": bool(relay_panes),
                "auto_relay_panes": relay_panes,
                "inbox_pending": sum(len(q) for q in self._inbox.values()),
                "tripped_cases": sorted(
                    k for k, v in self._loop_breaker_states.items() if v == "LOOP_BREAKER_TRIPPED"
                ),
            }

    def _find_active_pane_for_node(
        self, node_uuid: str, *, project_uuid: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Panel activo con ese `node_uuid` (del proyecto exacto si se indica)."""
        if not node_uuid:
            return None
        for pane in self._active_panes():
            if str(pane.get("node_uuid") or "").strip() != node_uuid:
                continue
            if project_uuid is not None and str(pane.get("project_uuid") or "") != project_uuid:
                continue
            return pane
        return None

    @staticmethod
    def _sanitize_single_line(text: str, *, max_len: int = 240) -> str:
        """Una linea imprimible: sin saltos ni caracteres de control (los avisos solo son datos, pero se limpian)."""
        printable = "".join(ch if ch.isprintable() else " " for ch in str(text or ""))
        cleaned = " ".join(printable.split())
        if len(cleaned) > max_len:
            return cleaned[: max_len - 3] + "..."
        return cleaned

    def _pane_provenance(self, node_uuid: str) -> Dict[str, Any]:
        """Datos publicos del panel de `node_uuid` (sin pid, token ni telemetria de tecleo)."""
        pane = self._find_active_pane_for_node(node_uuid) if node_uuid else None
        return {
            "node_uuid": node_uuid,
            "pane_id": (pane or {}).get("pane_id"),
            "agent_name": (pane or {}).get("agent_name"),
        }

    def _build_inbox_message(
        self,
        item: Dict[str, Any],
        target: str,
        *,
        kind: str,
        text: str,
    ) -> Dict[str, Any]:
        """Mensaje de bandeja: el texto va aparte de la procedencia, que la fija el servidor."""
        sender = self._extract_sender_node(item)
        provenance = self._pane_provenance(sender)
        provenance["verified_by"] = PROVENANCE_VERIFIED_BY if sender else "hub_internal"
        msg: Dict[str, Any] = {
            "id": str(item.get("message_uuid") or item.get("assignment_uuid") or uuid.uuid4()),
            "kind": kind,
            "from": provenance,
            "to": self._pane_provenance(target),
            "text": text,
            "project_uuid": str(item.get("project_uuid") or ""),
            "timestamp": float(item.get("enqueued_at") or self._clock()),
        }
        for key in ("card_id", "session_id", "assignment_uuid"):
            if item.get(key):
                msg[key] = str(item[key])
        return msg

    def _push_inbox_locked(self, target: str, msg: Dict[str, Any]) -> Optional[str]:
        """Apila `msg` en la bandeja de `target`; devuelve un codigo de error o None si cupo."""
        inbox = self._inbox.get(target)
        if inbox is None:
            if len(self._inbox) >= MAX_TARGET_QUEUES:
                for key in [k for k, q in self._inbox.items() if not q]:
                    del self._inbox[key]
                if len(self._inbox) >= MAX_TARGET_QUEUES:
                    return "QUEUE_TARGET_LIMIT"
            inbox = self._inbox[target]
        if len(inbox) >= MAX_INBOX_DEPTH:
            return "INBOX_FULL"
        inbox.append(msg)
        self._notify_waiters_locked(target)
        return None

    def _maybe_leave_inbox_notice(
        self,
        target_node_uuid: str,
        assignment: Dict[str, Any],
        *,
        assignment_uuid: str = "",
    ) -> None:
        """Si el PANEL destino tiene Auto-Relay activo deja un AVISO en su bandeja; jamas escribe en su terminal."""
        target = (target_node_uuid or "").strip()
        if not target or target == "*":
            return
        if str(assignment.get("source") or "") == "direct_message":
            return  # el mensaje directo ya es, el mismo, el contenido de la bandeja
        with self._lock:
            if self._closed:
                return
            if self._active_waiters.get(target, 0) > 0:
                return  # ya esta esperando: recibira la asignacion directamente
        sender = self._extract_sender_node(assignment)
        if sender and sender == target:
            return
        target_pane = self._find_active_pane_for_node(target)
        if target_pane is None or not bool(target_pane.get("auto_relay_enabled")):
            return  # D2: el aviso solo va a un panel con su Auto-Relay encendido
        if str(target_pane.get("project_uuid") or "") != str(assignment.get("project_uuid") or ""):
            return  # Ola 2: nunca avisar a un panel de otro proyecto

        kind = self._sanitize_single_line(
            str(assignment.get("event_type") or assignment.get("kind") or "ASSIGNMENT"), max_len=64
        )
        card_id = self._sanitize_single_line(str(assignment.get("card_id") or "-"), max_len=64)
        notice_src = dict(assignment)
        notice_src["assignment_uuid"] = assignment_uuid or str(uuid.uuid4())
        notice_src.pop("message_uuid", None)
        text = f"Asignacion pendiente ({kind}, tarjeta {card_id}): recogela con await_next_assignment."
        msg = self._build_inbox_message(notice_src, target, kind="relay_notice", text=text)
        with self._lock:
            if self._closed:
                return
            err = self._push_inbox_locked(target, msg)
        if err:
            logger.debug("Aviso de Auto-Relay descartado para %s: %s", target, err)

    # ------------------------------------------------------------------
    # Suscriptores de DomainEventBus y A2ABus
    # ------------------------------------------------------------------

    def _on_domain_event(self, event: Any) -> None:
        if isinstance(event, dict):
            event_type = str(event.get("event_type") or "")
            project_uuid = str(event.get("project_uuid") or "")
            raw_payload = event.get("payload")
            payload = dict(raw_payload) if isinstance(raw_payload, dict) else {}
            created_at = str(event.get("created_at") or "")
        else:
            event_type = str(getattr(event, "event_type", "") or "")
            project_uuid = str(getattr(event, "project_uuid", "") or "")
            raw_payload = getattr(event, "payload", None)
            payload = dict(raw_payload) if isinstance(raw_payload, dict) else {}
            created_at = str(getattr(event, "created_at", "") or "")

        if not (event_type.startswith("kanban.") or event_type.startswith("tribunal.")):
            return

        explicit_target = self._extract_target_node(
            {"payload": payload},
            allow_explicit_node_uuid=True,
        )
        if event_type in ("kanban.card_updated", "kanban.card_created"):
            assignee_node = str(payload.get("assignee_node_uuid") or "").strip()
            actor_node = str(payload.get("actor_node") or payload.get("actor_node_uuid") or "").strip()
            if assignee_node and assignee_node != actor_node:
                explicit_target = assignee_node
            elif assignee_node and assignee_node == actor_node and explicit_target == assignee_node:
                explicit_target = None
        card_id = str(payload.get("card_id") or "")
        session_id = str(payload.get("session_id") or payload.get("dispute_id") or "")

        if event_type == "kanban.reconciled_from_okf" and not explicit_target:
            return

        if event_type == "kanban.lease_claimed" and not explicit_target:
            with self._lock:
                self._remove_stale_card_created_locked(project_uuid, card_id)
            return

        if event_type == "kanban.lease_released":
            with self._lock:
                self._remove_stale_card_created_locked(project_uuid, card_id)

        target = explicit_target or "*"
        assignment: Dict[str, Any] = {
            "source": "domain_event",
            "event_type": event_type,
            "project_uuid": project_uuid,
            "created_at": created_at,
            "payload": payload,
        }
        if card_id:
            assignment["card_id"] = card_id
        if session_id:
            assignment["session_id"] = session_id
        if payload.get("event_id"):
            assignment["event_id"] = str(payload["event_id"])
        if payload.get("assignment_key"):
            assignment["assignment_key"] = str(payload["assignment_key"])

        res = self.enqueue_assignment(target, assignment)
        if res.get("queued") and not res.get("deduplicated") and target != "*":
            self._maybe_leave_inbox_notice(target, assignment, assignment_uuid=str(res.get("assignment_uuid") or ""))
        if not res.get("success") and res.get("code") == "RELAY_MAX_ROUNDS_EXCEEDED":
            logger.warning(
                "Loop Breaker activado en DomainEvent %s para %s: %s",
                event_type,
                res.get("assignment_key"),
                res.get("reason"),
            )

    def _on_a2a_message(self, event: Dict[str, Any]) -> None:
        if not isinstance(event, dict):
            return

        signal_type = str(event.get("signal_type") or "").upper()
        intent = str(event.get("intent") or "").upper()
        payload = dict(event.get("payload")) if isinstance(event.get("payload"), dict) else {}
        data = dict(event.get("data")) if isinstance(event.get("data"), dict) else {}
        explicit_target = self._extract_target_node(event, allow_explicit_node_uuid=False)

        if signal_type in _IGNORED_INFORMATIVE_SIGNALS and not explicit_target:
            return
        if signal_type == "DIRECT_MESSAGE":
            # El mensaje directo llega a la bandeja persistente (`MessageRelayService`), con remitente autenticado por el
            # Hub; un evento A2A con esta señal (p. ej. de un par remoto) no puede entrar por aqui (F0).
            return

        msg_kind = str(
            event.get("event_type")
            or event.get("type")
            or event.get("message_type")
            or payload.get("message_type")
            or signal_type
            or ("RFC_TURN" if intent == "COORDINATION" else intent)
            or "A2A_MESSAGE"
        ).upper()

        project_uuid = str(event.get("project_uuid") or "")
        card_id = str(event.get("card_id") or payload.get("card_id") or data.get("card_id") or "")
        session_id = str(
            event.get("session_id")
            or payload.get("session_id")
            or data.get("session_id")
            or ""
        )

        target = explicit_target or "*"
        assignment: Dict[str, Any] = {
            "source": "a2a_bus",
            "intent": intent or msg_kind,
            "kind": msg_kind,
            "signal_type": signal_type or msg_kind,
            "project_uuid": project_uuid,
            "sender_node_uuid": event.get("sender_node_uuid"),
            "payload": payload or data,
        }
        if event.get("message_uuid"):
            assignment["message_uuid"] = str(event["message_uuid"])
        if card_id:
            assignment["card_id"] = card_id
        if session_id:
            assignment["session_id"] = session_id
        if event.get("assignment_key") or payload.get("assignment_key"):
            assignment["assignment_key"] = str(
                event.get("assignment_key") or payload.get("assignment_key")
            )

        res = self.enqueue_assignment(target, assignment)
        if res.get("queued") and not res.get("deduplicated") and target != "*":
            self._maybe_leave_inbox_notice(target, assignment, assignment_uuid=str(res.get("assignment_uuid") or ""))
        if not res.get("success") and res.get("code") == "RELAY_MAX_ROUNDS_EXCEEDED":
            logger.warning(
                "Loop Breaker activado en mensaje A2A %s para %s: %s",
                msg_kind,
                res.get("assignment_key"),
                res.get("reason"),
            )

    def close(self) -> None:
        """Desuscribe los callbacks de buses y despierta waiters bloqueados."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for cond in list(self._conditions.values()):
                cond.notify_all()

        if self._event_bus is not None:
            unsubscribe_fn = getattr(self._event_bus, "unsubscribe", None)
            if callable(unsubscribe_fn):
                try:
                    unsubscribe_fn("*", self._on_domain_event)
                except Exception:
                    logger.debug("Error tolerado al desuscribir DomainEventBus", exc_info=True)

        if self._bus is not None:
            unsubscribe_fn = getattr(self._bus, "unsubscribe", None)
            if callable(unsubscribe_fn):
                try:
                    unsubscribe_fn("*", self._on_a2a_message)
                except Exception:
                    logger.debug("Error tolerado al desuscribir A2ABus", exc_info=True)
