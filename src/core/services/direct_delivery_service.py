"""Entrega directa de la bandeja a la terminal del agente destino (WP-6b, D12-ter).

La bandeja sigue siendo la fuente de verdad: todo sobre se sella, firma, guarda y audita ANTES de cualquier escritura,
y si algo es dudoso el mensaje queda (o vuelve) en la bandeja. Este servicio decide CUÁNDO se puede escribir (condiciones
C1–C13 de 4.4), reserva el mensaje de forma atómica y delega la escritura en un `PaneInputPort`. Una sola entrega en
vuelo por destinatario, en orden FIFO (`recipient_seq`), sin bloquear a los demás destinatarios ni al hilo que despierta.

Hexagonal: no importa nada de `src/adapters/**` ni de `src/app/**`.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Deque, Dict, List, Optional, Sequence, Set, Tuple

from src.core.domain.agent_activity import AgentState
from src.core.domain.injection_text import (
    InjectionItem,
    InjectionPayload,
    build_human_paste,
    build_injection,
)
from src.core.ports.delivery import (
    AgentActivityPort,
    DeliveryStorePort,
    PaneInputPort,
    RemoteDeliveryPolicyPort,
)

logger = logging.getLogger(__name__)

MAX_HUMAN_TEXT_BYTES = 8 * 1024
MAX_HUMAN_TARGETS = 6
BATCH_MAX = 5
PEEK_LIMIT = 20

_ROLE_ES = {
    "orchestrator": "orquestador",
    "implementer": "implementador",
    "auditor": "auditor",
    "mediator": "mediador",
    "peer": "par",
}


@dataclass(frozen=True)
class DeliverySettings:
    default_mode: str = "direct"
    recheck_interval_s: float = 5.0
    confirm_timeout_s: float = 10.0
    human_quiet_s: float = 5.0
    dirty_line_window_s: float = 120.0
    min_output_silence_s: float = 1.5


@dataclass(frozen=True)
class Eligibility:
    ok: bool
    reason: Optional[str]


@dataclass
class InFlight:
    msg_ids: Tuple[str, ...]
    incarnation: str
    stage: str  # reserved | written | submitted
    started_ms: int
    submitted_ms: int = 0
    enter_sent: bool = False
    batch_id: Optional[str] = None
    confirm_event: str = ""
    pane_id: str = ""


def _clip(value: Any, limit: int = 160) -> str:
    return str(value)[:limit]


class DirectDeliveryService:
    def __init__(
        self,
        *,
        relay: Any,
        store: DeliveryStorePort,
        panes: Any,
        pane_input: PaneInputPort,
        activity: AgentActivityPort,
        capabilities: Callable[[Any], Dict[str, Any]],
        privilege_of: Callable[[Any], str],
        remote_policy: RemoteDeliveryPolicyPort,
        settings_reader: Callable[[], DeliverySettings],
        clock_ms: Callable[[], int],
        sleep: Callable[[float], None] = time.sleep,
        max_workers: int = 6,
    ) -> None:
        self._relay = relay
        self._store = store
        self._panes = panes
        self._pane_input = pane_input
        self._activity = activity
        self._capabilities = capabilities
        self._privilege_of = privilege_of
        self._remote_policy = remote_policy
        self._settings_reader = settings_reader
        self._clock_ms = clock_ms
        self._sleep = sleep
        self._lock = threading.Lock()
        self._recipient_locks: Dict[str, threading.Lock] = {}
        self._dirty: Set[str] = set()
        self._inflight: Dict[str, InFlight] = {}
        self._last_injection_ms: Dict[str, int] = {}
        self._last_reason: Dict[str, Optional[str]] = {}
        self._notices: Deque[Dict[str, Any]] = deque(maxlen=50)
        self._notice_listeners: List[Callable[[Dict[str, Any]], None]] = []
        self._executor = ThreadPoolExecutor(max_workers=max(1, int(max_workers)), thread_name_prefix="mcp2p-delivery")
        self._stop = threading.Event()
        self._timer: Optional[threading.Thread] = None
        self._started = False

    # -- ciclo de vida ------------------------------------------------------------------------------------------

    def start(self, *, start_timer: bool = True) -> Dict[str, Any]:
        """Recupera lo que dejó abierto el proceso anterior (4.15) y arranca el temporizador de repaso."""
        self._started = True
        summary = self.recover()
        if start_timer and (self._timer is None or not self._timer.is_alive()):
            self._stop.clear()
            self._timer = threading.Thread(target=self._timer_loop, name="mcp2p-delivery-timer", daemon=True)
            self._timer.start()
        return summary

    def stop(self) -> None:
        self._stop.set()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _timer_loop(self) -> None:
        while not self._stop.wait(max(0.2, float(self._settings().recheck_interval_s))):
            try:
                self.tick()
            except Exception:
                logger.debug("Fallo en el repaso periodico de la entrega directa", exc_info=True)

    def tick(self) -> None:
        """Repasa a los destinatarios con algo en cola y a los que esperan una confirmación."""
        now = int(self._clock_ms())
        nodes: Set[str] = set()
        queued_fn = getattr(self._store, "queued_recipients", None)
        if callable(queued_fn):
            for principal in queued_fn(now):
                nodes.add(str(principal).partition(":")[2])
        with self._lock:
            nodes.update(self._inflight.keys())
        for node in nodes:
            if node:
                self._schedule(node)

    # -- oyentes ------------------------------------------------------------------------------------------------

    def on_wake(self, node_uuid: str) -> None:
        """Llega algo a la bandeja de `node_uuid`. NO bloquea: marca «sucio» y delega en el ejecutor."""
        if node_uuid:
            self._schedule(str(node_uuid))

    def on_activity(self, node_uuid: str, snap: Any) -> None:
        self._confirm_from_activity(node_uuid, snap)
        if getattr(snap, "state", None) == AgentState.IDLE:
            self._schedule(node_uuid)

    def add_notice_listener(self, fn: Callable[[Dict[str, Any]], None]) -> None:
        if fn not in self._notice_listeners:
            self._notice_listeners.append(fn)

    def notices(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._notices)

    def _notify_human(self, node: str, message: str, *, code: str, msg_ids: Sequence[str] = ()) -> None:
        notice = {"node_uuid": node, "code": code, "message": message, "msg_ids": list(msg_ids),
                  "at_ms": int(self._clock_ms())}
        with self._lock:
            self._notices.append(notice)
        for fn in list(self._notice_listeners):
            try:
                fn(notice)
            except Exception:
                logger.debug("Oyente de avisos de entrega con error", exc_info=True)

    # -- planificación ------------------------------------------------------------------------------------------

    def _schedule(self, node: str) -> None:
        if self._stop.is_set():
            return
        with self._lock:
            self._dirty.add(node)
        try:
            self._executor.submit(self._drain, node)
        except RuntimeError:  # ejecutor ya apagado
            pass

    def _lock_for(self, node: str) -> threading.Lock:
        with self._lock:
            lock = self._recipient_locks.get(node)
            if lock is None:
                lock = self._recipient_locks[node] = threading.Lock()
            return lock

    def _drain(self, node: str) -> None:
        lock = self._lock_for(node)
        while True:
            if not lock.acquire(blocking=False):
                return  # otro hilo drena este destino; verá `_dirty`
            try:
                while True:
                    with self._lock:
                        if node not in self._dirty:
                            break
                        self._dirty.discard(node)
                    try:
                        self._pump_locked(node)
                    except Exception:
                        logger.warning("Fallo la pasada de entrega directa de %s", node, exc_info=True)
            finally:
                lock.release()
            # Cierra la ventana en la que otro despertar llegó entre la última comprobación y la liberación.
            with self._lock:
                if node not in self._dirty:
                    return

    def pump(self, node_uuid: str) -> Dict[str, Any]:
        """Una pasada síncrona sobre ese destinatario (la usan las pruebas y el repaso manual)."""
        lock = self._lock_for(node_uuid)
        with lock:
            return self._pump_locked(node_uuid)

    # -- elegibilidad -------------------------------------------------------------------------------------------

    def _settings(self) -> DeliverySettings:
        try:
            return self._settings_reader()
        except Exception:
            return DeliverySettings()

    def _rec(self, node: str) -> Optional[Any]:
        rec = self._panes.find_active_pane_by_node(node)
        if rec is None or getattr(rec, "status", "active") == "closed":
            return None
        return rec

    def effective_mode(self, node_uuid: str) -> Tuple[str, Optional[str]]:
        """Modo efectivo con solo C2–C5 (configuración y verificación): ('direct'|'inbox', motivo)."""
        rec = self._rec(node_uuid)
        if rec is None:
            return "inbox", "PANE_GONE"
        return self._mode_for(rec)

    def _mode_for(self, rec: Any) -> Tuple[str, Optional[str]]:
        configured = str(getattr(rec, "delivery_mode", "auto") or "auto")
        mode = self._settings().default_mode if configured == "auto" else configured
        if mode != "direct":
            return "inbox", "DELIVERY_MODE_INBOX"
        caps = self._capabilities(rec) or {}
        if not (caps.get("delivery_verified") and caps.get("activity_verified")):
            return "inbox", "CLI_NOT_VERIFIED"
        try:
            privilege = self._privilege_of(rec)
        except Exception:
            privilege = "elevated"
        if privilege != "standard":
            return "inbox", "TARGET_ELEVATED"
        return "direct", None

    def _human_typing(self, rec: Any, cfg: DeliverySettings) -> bool:
        pane_id = str(getattr(rec, "pane_id", ""))
        try:
            if self._panes.is_human_typing_active(pane_id, cooldown_seconds=cfg.human_quiet_s):
                return True
        except Exception:
            return True  # ante la duda, no escribir
        ks = float(getattr(rec, "last_human_keystroke_at", 0.0) or 0.0)
        sub = float(getattr(rec, "last_human_submit_at", 0.0) or 0.0)
        now_s = self._clock_ms() / 1000.0
        return ks > sub and (now_s - ks) < cfg.dirty_line_window_s

    def _activity_reason(self, node: str, incarnation: Optional[str]) -> Optional[str]:
        snap = self._activity.snapshot(node)
        snap_inc = getattr(snap, "incarnation", None)
        if incarnation and snap_inc and snap_inc != incarnation:
            return "AGENT_STATE_UNKNOWN"
        state = getattr(snap, "state", AgentState.UNKNOWN)
        if state == AgentState.WORKING:
            return "AGENT_BUSY"
        if state == AgentState.PERMISSION:
            return "AGENT_PERMISSION"
        if state != AgentState.IDLE:
            return "AGENT_STATE_UNKNOWN"
        last_idle = getattr(snap, "last_idle_at_ms", None)
        if last_idle is None:
            return "AGENT_STATE_UNKNOWN"
        if int(last_idle) <= int(self._last_injection_ms.get(node, 0)):
            return "AWAITING_IDLE_AFTER_INJECTION"
        return None

    def eligibility(self, node_uuid: str) -> Eligibility:
        """C1–C10 en este instante (C11–C13 se comprueban por mensaje)."""
        rec = self._rec(node_uuid)
        return self._eligibility(node_uuid, rec)

    def _eligibility(self, node: str, rec: Optional[Any]) -> Eligibility:
        paused = self._relay.delivery_paused(node)
        if paused:
            return Eligibility(False, paused)
        if rec is None:
            return Eligibility(False, "PANE_GONE")
        incarnation = self._pane_input.current_incarnation(str(rec.pane_id))
        if not incarnation:
            return Eligibility(False, "PANE_GONE")
        mode, why = self._mode_for(rec)
        if mode != "direct":
            return Eligibility(False, why)
        if self._relay.is_waiting(node):
            return Eligibility(False, "AGENT_IN_INBOX_WAIT")
        with self._lock:
            busy = node in self._inflight
        if busy:
            return Eligibility(False, "IN_FLIGHT")
        reason = self._activity_reason(node, incarnation)
        if reason:
            return Eligibility(False, reason)
        cfg = self._settings()
        age = self._pane_input.last_output_age_s(str(rec.pane_id))
        if age is not None and age < cfg.min_output_silence_s:
            return Eligibility(False, "RECENT_OUTPUT")
        if self._human_typing(rec, cfg):
            return Eligibility(False, "HUMAN_TYPING")
        return Eligibility(True, None)

    # -- la pasada ----------------------------------------------------------------------------------------------

    def _pump_locked(self, node: str) -> Dict[str, Any]:
        self._settle_if_due(node)
        rec = self._rec(node)
        el = self._eligibility(node, rec)
        self._last_reason[node] = el.reason
        if not el.ok or rec is None:
            return {"action": "none", "reason": el.reason}
        now = int(self._clock_ms())
        to = f"pane:{node}"
        project = str(rec.project_uuid)
        queued = self._store.peek_queued(to, project, limit=PEEK_LIMIT, now_ms=now)
        if not queued:
            return {"action": "none", "reason": None}
        if queued[0].get("delivery_state") == "pointer_sent":
            return {"action": "none", "reason": "TOO_LONG_POINTER"}
        caps = self._capabilities(rec) or {}
        paste_mode = str(caps.get("paste_mode") or "bracketed")
        max_bytes = int(caps.get("max_inject_bytes") or 2048)
        candidates = self._candidates(queued, project, batch=bool(getattr(rec, "delivery_batch", False)))
        if not candidates:
            return {"action": "none", "reason": "REMOTE_POLICY"}
        items = [self._item_for(row) for row in candidates]
        try:
            payload = build_injection(items, paste_mode=paste_mode, max_bytes=max_bytes)
        except ValueError:
            logger.warning("Mensaje no apto para la entrega directa; se queda en la bandeja", exc_info=True)
            return {"action": "none", "reason": "MESSAGE_NOT_INJECTABLE"}
        incarnation = self._pane_input.current_incarnation(str(rec.pane_id))
        if not incarnation:
            return {"action": "none", "reason": "PANE_GONE"}
        if payload.pointer_only:
            why = self._recheck(rec, node, incarnation, before_enter=False)
            if why:
                return {"action": "none", "reason": why}
            if not self._store.mark_pointer_sent(payload.msg_ids[0], to, project, incarnation=incarnation, now_ms=now):
                return {"action": "none", "reason": "TOO_LONG_POINTER"}
            if not self._write_and_submit(rec, node, payload, incarnation, claim=None, caps=caps):
                self._store.clear_pointer_sent(payload.msg_ids[0], now_ms=int(self._clock_ms()))
                return {"action": "none", "reason": self._last_reason.get(node)}
            return {"action": "pointer", "msg_ids": list(payload.msg_ids)}
        batch_id = uuid.uuid4().hex if len(payload.msg_ids) > 1 else None
        claimed = self._store.claim_for_injection(
            list(payload.msg_ids), to, incarnation=incarnation, batch_id=batch_id, now_ms=now, actor="hub:delivery"
        )
        if not claimed:
            return {"action": "none", "reason": None}  # el agente lo leyó por inbox_next
        if len(claimed) != len(payload.msg_ids):
            by_id = {r["msg_id"]: r for r in claimed}
            items = [self._item_for(by_id[i]) for i in payload.msg_ids if i in by_id]
            payload = build_injection(items, paste_mode=paste_mode, max_bytes=max_bytes)
        for row in claimed:
            self._relay.tracker.observe_delivery(
                to, chain_id=row["chain_id"], hop=int(row["hop"]), trust=row["trust"]
            )
        with self._lock:
            self._inflight[node] = InFlight(
                msg_ids=tuple(payload.msg_ids),
                incarnation=incarnation,
                stage="reserved",
                started_ms=now,
                batch_id=batch_id,
                confirm_event=str(caps.get("confirm_event") or ""),
                pane_id=str(rec.pane_id),
            )
        self._write_and_submit(rec, node, payload, incarnation, claim=claimed, caps=caps)
        return {"action": "injected", "msg_ids": list(payload.msg_ids)}

    def _candidates(self, queued: List[Dict[str, Any]], project: str, *, batch: bool) -> List[Dict[str, Any]]:
        """Primeros mensajes de la cola que pueden escribirse (C11, C12 y mismo proyecto); lo demás sigue en la bandeja."""
        out: List[Dict[str, Any]] = []
        for row in queued:
            if row.get("delivery_state") == "pointer_sent":
                break
            if str(row.get("project_uuid")) != project:
                continue
            if row.get("content") is None:
                continue  # texto purgado (C12)
            try:
                allowed = bool(self._remote_policy.allows_injection(row))
            except Exception:
                allowed = False
            if not allowed:
                continue
            out.append(row)
            if not batch:
                break
            if len(out) >= BATCH_MAX:
                break
        return out

    def _item_for(self, row: Dict[str, Any]) -> InjectionItem:
        label = self._relay.label_of(str(row["from_principal"]))
        remote = None
        if callable(getattr(self._relay, "remote_info", None)):
            try:
                remote = self._relay.remote_info(row["msg_id"])
            except Exception:
                remote = None
        kind = str(label.get("kind") or "")
        if remote:
            role = "remoto"
            sender = f"{remote.get('display_name') or 'nodo'}/Terminal"
        elif kind == "pane":
            role = _ROLE_ES.get(str(label.get("role") or ""), "par")
            pane_id = str(label.get("pane_id") or "")
            sender = f"Terminal {pane_id.split('_')[-1]}" if pane_id else "Terminal"
        elif kind == "operator":
            role, sender = "operador", "operador"
        elif kind == "human":
            role, sender = "humano", "humano"
        else:
            role, sender = "par", "desconocido"
        return InjectionItem(
            msg_id=str(row["msg_id"]),
            role_label=role,
            sender_label=sender,
            trust=str(row["trust"]),
            kind=str(row["kind"]),
            text=str(row["content"]),
        )

    # -- escritura -----------------------------------------------------------------------------------------------

    def _recheck(self, rec: Any, node: str, incarnation: str, *, before_enter: bool) -> Optional[str]:
        """Re-chequeo justo antes de escribir (C2, C6, C7, C9) o antes del Enter (C2, C9 y sin permiso pendiente)."""
        if self._rec(node) is None or self._pane_input.current_incarnation(str(rec.pane_id)) != incarnation:
            return "PANE_GONE"
        if self._human_typing(rec, self._settings()):
            return "HUMAN_TYPING"
        if before_enter:
            snap = self._activity.snapshot(node)
            if getattr(snap, "state", None) == AgentState.PERMISSION:
                return "AGENT_PERMISSION"
            return None
        if self._relay.is_waiting(node):
            return "AGENT_IN_INBOX_WAIT"
        return self._activity_reason(node, incarnation)

    def _clear_inflight(self, node: str) -> None:
        with self._lock:
            self._inflight.pop(node, None)

    def _write_and_submit(
        self, rec: Any, node: str, payload: InjectionPayload, incarnation: str, *, claim: Optional[List[Dict[str, Any]]],
        caps: Dict[str, Any],
    ) -> bool:
        """Pega, espera, re-chequea y envía el Enter. True si el Enter salió (la entrega queda `submitted`)."""
        pane_id = str(rec.pane_id)
        ids = list(payload.msg_ids)
        delay_s = max(0.05, int(caps.get("submit_delay_ms") or 300) / 1000.0)

        def revert(reason: str) -> bool:
            if claim is not None:
                self._store.revert_injection(ids, reason=reason, now_ms=int(self._clock_ms()))
            self._clear_inflight(node)
            self._last_reason[node] = reason
            return False

        def unverifiable(reason: str, message: str) -> bool:
            if claim is not None:
                self._store.set_delivery_state(ids, "unverifiable", reason=reason, now_ms=int(self._clock_ms()))
            self._clear_inflight(node)
            self._last_reason[node] = reason
            self._notify_human(node, message, code=reason, msg_ids=ids)
            return False

        if claim is not None:
            why = self._recheck(rec, node, incarnation, before_enter=False)
            if why:
                return revert(why)
        res = self._pane_input.write(pane_id, payload.paste, incarnation=incarnation)
        if not res.ok and res.bytes_written == 0:
            return revert(res.code)
        if not res.ok:
            return unverifiable(res.code, f"La escritura en la terminal {pane_id} quedó a medias: revisa la terminal.")
        if claim is not None:
            self._store.set_delivery_state(
                ids, "written", reason=None, now_ms=int(self._clock_ms()), bytes_written=res.bytes_written,
                sanitized_controls=payload.removed,
            )
            with self._lock:
                flight = self._inflight.get(node)
                if flight is not None:
                    flight.stage = "written"
        self._sleep(delay_s)
        stop = self._recheck(rec, node, incarnation, before_enter=True)
        if stop:
            if claim is not None:
                return unverifiable("ENTER_WITHHELD", f"Texto pegado sin enviar en {pane_id}: revisa la terminal.")
            self._last_reason[node] = stop
            self._notify_human(node, f"Texto pegado sin enviar en {pane_id}: revisa la terminal.",
                               code="ENTER_WITHHELD", msg_ids=ids)
            return False
        submit_at = int(self._clock_ms())
        self._last_injection_ms[node] = submit_at
        with self._lock:
            flight = self._inflight.get(node)
            if flight is not None:
                flight.enter_sent = True
                flight.submitted_ms = submit_at
        # El turno empieza con el Enter: se anota ANTES de escribirlo para que el hook de fin de turno (que puede
        # llegar al instante) no quede pisado por este aviso.
        self._activity.mark(node, "injection_submitted", submit_at, "injection")
        res2 = self._pane_input.write(pane_id, payload.submit, incarnation=incarnation)
        if not res2.ok:
            self._activity.mark(node, "turn_ended", int(self._clock_ms()), "hub")
            return unverifiable(res2.code, f"No se pudo enviar el mensaje en {pane_id}: revisa la terminal.")
        if claim is not None:
            self._store.set_delivery_state(ids, "submitted", reason=None, now_ms=submit_at)
            with self._lock:
                flight = self._inflight.get(node)
                if flight is not None:
                    flight.stage = "submitted"
        return True

    # -- confirmación --------------------------------------------------------------------------------------------

    def _confirm_from_activity(self, node: str, snap: Any) -> None:
        with self._lock:
            flight = self._inflight.get(node)
            if flight is None or not flight.enter_sent or not flight.confirm_event:
                return
            if getattr(snap, "last_event", "") != flight.confirm_event or getattr(snap, "source", "") != "hook":
                return
            snap_inc = getattr(snap, "incarnation", None)
            if snap_inc and snap_inc != flight.incarnation:
                return
            self._inflight.pop(node, None)
        self._store.set_delivery_state(
            list(flight.msg_ids), "injected", reason=None, now_ms=int(self._clock_ms())
        )

    def _settle_if_due(self, node: str) -> None:
        """Cierra una entrega `submitted` cuya confirmación venció o cuyo panel ya no es el mismo."""
        with self._lock:
            flight = self._inflight.get(node)
        if flight is None or not flight.enter_sent:
            return
        now = int(self._clock_ms())
        rec = self._rec(node)
        current = self._pane_input.current_incarnation(flight.pane_id) if rec is not None else None
        if rec is None or current != flight.incarnation:
            self._clear_inflight(node)
            self._store.set_delivery_state(
                list(flight.msg_ids), "unverifiable", reason="PANE_CHANGED", now_ms=now
            )
            self._notify_human(node, "La terminal cambió tras enviar un mensaje: revisa si lo recibió.",
                               code="PANE_CHANGED", msg_ids=flight.msg_ids)
            return
        timeout_ms = int(self._settings().confirm_timeout_s * 1000)
        if now - flight.submitted_ms < timeout_ms:
            return
        self._clear_inflight(node)
        if flight.confirm_event == "":
            self._store.set_delivery_state(
                list(flight.msg_ids), "injected", reason="UNCONFIRMED_BY_CLI", now_ms=now
            )
        else:
            self._store.set_delivery_state(
                list(flight.msg_ids), "unverifiable", reason="NO_TURN_START", now_ms=now
            )
            self._notify_human(node, "El agente no confirmó haber empezado a leer el mensaje: revisa la terminal.",
                               code="NO_TURN_START", msg_ids=flight.msg_ids)

    # -- el humano escribe desde el chat ----------------------------------------------------------------------------

    def type_as_human(self, pane_ids: Sequence[str], text: str, *, project_uuid: str) -> Dict[str, Any]:
        """Teclea `text` (saneado, sin cabecera) en esos paneles del proyecto: camino 2a de D12-ter."""
        ids = list(dict.fromkeys(str(p) for p in pane_ids))
        if not ids or len(ids) > MAX_HUMAN_TARGETS:
            return {"success": False, "code": "INVALID_TARGETS",
                    "error": f"Elige entre 1 y {MAX_HUMAN_TARGETS} terminales."}
        if not isinstance(text, str) or not text.strip():
            return {"success": False, "code": "EMPTY_MESSAGE", "error": "El texto no puede estar vacío."}
        try:
            size = len(text.encode("utf-8"))
        except UnicodeEncodeError:
            return {"success": False, "code": "INVALID_MESSAGE", "error": "El texto no es UTF-8 válido."}
        if size > MAX_HUMAN_TEXT_BYTES:
            return {"success": False, "code": "MESSAGE_TOO_LARGE",
                    "error": f"El texto supera {MAX_HUMAN_TEXT_BYTES} bytes."}
        recs: List[Any] = []
        for pane_id in ids:
            try:
                rec = self._panes.get_pane(pane_id)
            except ValueError:
                rec = None
            if rec is None or getattr(rec, "status", "active") == "closed":
                return {"success": False, "code": "PANE_GONE", "error": f"{pane_id} no está activa."}
            if str(rec.project_uuid) != str(project_uuid or ""):
                return {"success": False, "code": "PROJECT_SCOPE_FORBIDDEN",
                        "error": "Esa terminal es de otro proyecto."}
            recs.append(rec)
        results = []
        for rec in recs:
            results.append(self._type_one(rec, text))
        ok = all(r["ok"] for r in results)
        return {"success": ok, "results": results}

    def _type_one(self, rec: Any, text: str) -> Dict[str, Any]:
        pane_id = str(rec.pane_id)
        node = str(rec.node_uuid)
        lock = self._lock_for(node)
        if not lock.acquire(timeout=5.0):
            return {"pane_id": pane_id, "ok": False, "code": "IN_FLIGHT"}
        try:
            incarnation = self._pane_input.current_incarnation(pane_id)
            if not incarnation:
                return {"pane_id": pane_id, "ok": False, "code": "PANE_GONE"}
            caps = self._capabilities(rec) or {}
            paste_mode = str(caps.get("paste_mode") or "bracketed") if caps.get("delivery_verified") else "single_line"
            payload = build_human_paste(text, paste_mode=paste_mode)
            res = self._pane_input.write(pane_id, payload.paste, incarnation=incarnation)
            if not res.ok:
                return {"pane_id": pane_id, "ok": False, "code": res.code}
            self._sleep(max(0.05, int(caps.get("submit_delay_ms") or 300) / 1000.0))
            res2 = self._pane_input.write(pane_id, payload.submit, incarnation=incarnation)
            if not res2.ok:
                return {"pane_id": pane_id, "ok": False, "code": res2.code}
            # Enter del humano = turno humano: cierra la cadena causal del panel (como el teclado).
            self._panes.record_human_keystroke(pane_id, submitted=True)
            return {"pane_id": pane_id, "ok": True, "code": "OK"}
        finally:
            lock.release()

    # -- recuperación tras reinicio (4.15) -------------------------------------------------------------------------

    def recover(self) -> Dict[str, Any]:
        now = int(self._clock_ms())
        summary = {"reverted": 0, "ambiguous": 0, "pointer_cleared": 0}
        for row in self._store.open_deliveries():
            msg_id = row["msg_id"]
            state = row["state"]
            if state == "reserved":
                self._store.revert_injection([msg_id], reason="RECOVERED_RESERVED", now_ms=now)
                summary["reverted"] += 1
            elif state == "written":
                self._store.revert_injection([msg_id], reason="RECOVERED_UNSUBMITTED", now_ms=now, allow_written=True)
                summary["reverted"] += 1
            elif state == "submitted":
                self._store.set_delivery_state([msg_id], "unverifiable", reason="RECOVERED_AMBIGUOUS", now_ms=now)
                self._notify_human(
                    str(row.get("to_principal", "")).partition(":")[2],
                    "Tras reiniciar, no se sabe si un mensaje llegó a la terminal: revísalo en el chat.",
                    code="RECOVERED_AMBIGUOUS", msg_ids=[msg_id],
                )
                summary["ambiguous"] += 1
            elif state == "pointer_sent":
                self._store.clear_pointer_sent(msg_id, now_ms=now)
                summary["pointer_cleared"] += 1
        return summary

    # -- estado para la interfaz -------------------------------------------------------------------------------------

    def status(self, project_uuid: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        now = int(self._clock_ms())
        for pane in self._panes.list_panes(project_uuid=project_uuid):
            node = str(pane.get("node_uuid") or "")
            rec = self._rec(node) if node else None
            if rec is None:
                continue
            mode, mode_reason = self._mode_for(rec)
            el = self._eligibility(node, rec)
            with self._lock:
                in_flight = node in self._inflight
            queued = len(self._store.peek_queued(f"pane:{node}", project_uuid, limit=50, now_ms=now))
            out.append(
                {
                    "pane_id": str(rec.pane_id),
                    "effective_mode": mode,
                    "mode_reason": mode_reason,
                    "now_reason": el.reason,
                    "in_flight": in_flight,
                    "queued": queued,
                }
            )
        return out
