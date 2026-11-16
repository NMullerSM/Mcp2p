"""Servicio de aplicación para relevo push->pull del enjambre y guardas PTY."""

from __future__ import annotations

import inspect
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
    """Orquestador thread-safe de colas pull y despacho seguro hacia terminales PTY."""

    def __init__(
        self,
        *,
        event_bus: Optional[DomainEventBus] = None,
        bus: Optional[A2ABus] = None,
        pane_registry: Optional[Any] = None,
        terminal_connector: Optional[Any] = None,
        crypto: Optional[Any] = None,
        require_encrypted_key: bool = True,
        human_cooldown_seconds: float = 5.0,
        max_relay_rounds: int = 6,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._conditions: Dict[str, threading.Condition] = {}
        self._queues: Dict[str, Deque[Dict[str, Any]]] = defaultdict(deque)
        self._event_bus = event_bus
        self._bus = bus
        self.pane_registry = pane_registry
        self.terminal_connector = terminal_connector
        self.crypto = crypto
        self.require_encrypted_key = bool(require_encrypted_key)
        self.human_cooldown_seconds = float(human_cooldown_seconds)
        self.max_relay_rounds = max(1, int(max_relay_rounds))
        self._clock_explicit = clock is not None
        self._clock: Callable[[], float] = clock or time.time
        self._closed = False

        self._dedup_order: Dict[str, Deque[str]] = {}
        self._dedup_seen: Dict[str, Set[str]] = {}
        self._relay_rounds: Dict[Tuple[str, str], int] = defaultdict(int)
        self._loop_breaker_states: Dict[str, str] = {}
        # Turnos del relevo en cola por (proyecto:tarjeta|sesion, par de nodos): {key: ultimo remitente}.
        self._relay_last_sender: Dict[str, str] = {}
        self._dropped_oldest_total = 0
        self._deferred_dispatches: Deque[Dict[str, Any]] = deque(maxlen=512)

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
        proj_filter = project_uuid.strip() if isinstance(project_uuid, str) and project_uuid.strip() else None
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

    def _is_key_encrypted(self) -> bool:
        if self.crypto is None:
            return False
        attr = getattr(self.crypto, "is_encrypted", False)
        return bool(attr() if callable(attr) else attr)

    def _check_human_typing_active(self, pane_id: int | str) -> bool:
        if self.pane_registry is None:
            return False
        fn = getattr(self.pane_registry, "is_human_typing_active", None)
        if not callable(fn):
            return False
        if self._clock_explicit:
            try:
                sig = inspect.signature(fn)
                if "now" in sig.parameters or any(
                    param.kind == inspect.Parameter.VAR_KEYWORD
                    for param in sig.parameters.values()
                ):
                    return bool(
                        fn(
                            pane_id,
                            cooldown_seconds=self.human_cooldown_seconds,
                            now=self._clock(),
                        )
                    )
            except (TypeError, ValueError):
                pass
        return bool(fn(pane_id, cooldown_seconds=self.human_cooldown_seconds))

    # ------------------------------------------------------------------
    # API pública de colas pull, relevo y Loop Breaker
    # ------------------------------------------------------------------

    def enqueue_assignment(
        self,
        target_node_uuid: str,
        assignment: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Encola una asignación para ``target_node_uuid`` aplicando dedup y Loop Breaker DoD."""
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

    def await_next_assignment(
        self,
        node_uuid: str,
        *,
        project_uuid: Optional[str] = None,
        timeout_ms: int = 0,
    ) -> Dict[str, Any]:
        """Desencola la siguiente asignación o espera con ``Condition.wait`` sin busy-wait."""
        timeout_ms = clamp_await_timeout_ms(timeout_ms)
        target = (node_uuid or "").strip()
        if not target:
            return {
                "success": False,
                "has_assignment": False,
                "assignment": None,
                "code": "INVALID_NODE_UUID",
                "error": "node_uuid requerido para esperar asignaciones.",
            }

        with self._lock:
            if self._closed:
                return {
                    "success": False,
                    "has_assignment": False,
                    "assignment": None,
                    "timed_out": False,
                    "closed": True,
                }
            item = self._pop_matching_locked(target, project_uuid)
            if item is not None:
                return {
                    "success": True,
                    "has_assignment": True,
                    "assignment": item,
                    "timed_out": False,
                }
            if timeout_ms <= 0:
                return {
                    "success": True,
                    "has_assignment": False,
                    "assignment": None,
                    "timed_out": False,
                }

            deadline = time.monotonic() + (timeout_ms / 1000.0)
            cond = self._get_condition_locked(target)
            while not self._closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return {
                        "success": True,
                        "has_assignment": False,
                        "assignment": None,
                        "timed_out": True,
                    }
                cond.wait(timeout=remaining)
                if self._closed:
                    return {
                        "success": True,
                        "has_assignment": False,
                        "assignment": None,
                        "timed_out": False,
                        "closed": True,
                    }
                item = self._pop_matching_locked(target, project_uuid)
                if item is not None:
                    return {
                        "success": True,
                        "has_assignment": True,
                        "assignment": item,
                        "timed_out": False,
                    }

            return {
                "success": True,
                "has_assignment": False,
                "assignment": None,
                "timed_out": False,
                "closed": True,
            }

    def dispatch_to_pane(
        self,
        pane_id: int | str,
        prompt_text: str,
        *,
        project_uuid: str = "",
        card_id: str = "",
        session_id: str = "",
    ) -> Dict[str, Any]:
        """Despacha texto a un PTY solo tras superar Gate 0c, cooldown humano y Loop Breaker."""
        with self._lock:
            if self._closed:
                return {
                    "success": False,
                    "blocked": True,
                    "code": "ORCHESTRATOR_CLOSED",
                    "reason": "SwarmRelayOrchestrator cerrado.",
                }

            # Guarda 1: Gate 0c (fail-closed si la clave está en texto plano o falta crypto).
            if self.require_encrypted_key and not self._is_key_encrypted():
                return {
                    "success": False,
                    "blocked": True,
                    "code": "GATE_0C_PLAIN_KEY_RELAY_BLOCKED",
                    "reason": (
                        "Gate 0c: clave privada en texto plano o ausente; "
                        "el despacho automático hacia PTY exige clave cifrada."
                    ),
                }

            # Guarda 2: Anti-colisión humana (ventana de cooldown tras teclado humano).
            if self._check_human_typing_active(pane_id):
                self._deferred_dispatches.append(
                    {
                        "pane_id": pane_id,
                        "prompt_text": prompt_text,
                        "project_uuid": project_uuid,
                        "card_id": card_id,
                        "session_id": session_id,
                        "queued_at": self._clock(),
                    }
                )
                return {
                    "success": False,
                    "deferred": True,
                    "code": "HUMAN_TYPING_COOLDOWN_ACTIVE",
                    "cooldown_seconds": self.human_cooldown_seconds,
                    "deferred_count": len(self._deferred_dispatches),
                    "reason": "Actividad humana reciente en el panel; inyección PTY diferida.",
                }

            # Guarda 3: Loop Breaker DoD.
            loop_key = self._make_loop_key(
                project_uuid=project_uuid,
                card_id=card_id,
                session_id=session_id,
                fallback=str(pane_id),
            )
            assert loop_key is not None
            if (
                loop_key in self._loop_breaker_states
                or self._relay_rounds[("dispatch", loop_key)] >= self.max_relay_rounds
            ):
                self._loop_breaker_states[loop_key] = "LOOP_BREAKER_TRIPPED"
                rounds = self._relay_rounds[("dispatch", loop_key)]
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
                        f"Loop Breaker activado tras {rounds} rondas "
                        f"(máximo {self.max_relay_rounds}); requiere intervención humana (HITL)."
                    ),
                }

            if self.terminal_connector is None:
                return {
                    "success": False,
                    "blocked": True,
                    "code": "TERMINAL_CONNECTOR_UNAVAILABLE",
                    "reason": "No hay terminal_connector configurado para despacho PTY.",
                }

            self._relay_rounds[("dispatch", loop_key)] += 1
            current_round = self._relay_rounds[("dispatch", loop_key)]
            connector = self.terminal_connector

        try:
            send_res = connector.send_input(pane_id, prompt_text)
        except Exception as exc:
            return {
                "success": False,
                "code": "TERMINAL_SEND_FAILED",
                "error": str(exc),
                "pane_id": pane_id,
                "rounds": current_round,
                "assignment_key": loop_key,
            }

        if isinstance(send_res, dict):
            if send_res.get("success") is False:
                return {
                    **send_res,
                    "pane_id": pane_id,
                    "rounds": current_round,
                    "assignment_key": loop_key,
                }
            return {
                **send_res,
                "success": True,
                "dispatched": True,
                "pane_id": pane_id,
                "rounds": current_round,
                "assignment_key": loop_key,
            }

        if send_res is False:
            return {
                "success": False,
                "dispatched": False,
                "pane_id": pane_id,
                "rounds": current_round,
                "assignment_key": loop_key,
            }
        return {
            "success": True,
            "dispatched": True,
            "pane_id": pane_id,
            "rounds": current_round,
            "assignment_key": loop_key,
            "result": send_res,
        }

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

    def get_deferred_dispatches(self, pane_id: Optional[int | str] = None) -> List[Dict[str, Any]]:
        """Lista una instantánea de los despachos diferidos por escritura humana."""
        with self._lock:
            items = list(self._deferred_dispatches)
        if pane_id is None:
            return [dict(item) for item in items]
        return [dict(item) for item in items if str(item.get("pane_id")) == str(pane_id)]

    def flush_deferred_dispatches(self, pane_id: Optional[int | str] = None) -> Dict[str, Any]:
        """Reintenta despachar los mensajes diferidos una vez vencido el cooldown."""
        with self._lock:
            pending = list(self._deferred_dispatches)
            self._deferred_dispatches.clear()

        dispatched = 0
        still_deferred = 0
        blocked = 0
        requeue: List[Dict[str, Any]] = []
        for entry in pending:
            if pane_id is not None and str(entry.get("pane_id")) != str(pane_id):
                requeue.append(entry)
                continue
            res = self.dispatch_to_pane(
                entry["pane_id"],
                entry["prompt_text"],
                project_uuid=entry.get("project_uuid", ""),
                card_id=entry.get("card_id", ""),
                session_id=entry.get("session_id", ""),
            )
            if res.get("dispatched"):
                dispatched += 1
            elif res.get("deferred"):
                still_deferred += 1
            else:
                blocked += 1

        if requeue:
            with self._lock:
                for item in reversed(requeue):
                    self._deferred_dispatches.appendleft(item)

        return {
            "dispatched": dispatched,
            "still_deferred": still_deferred,
            "blocked": blocked,
        }

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
