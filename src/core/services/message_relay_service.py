"""Servicio del relevo entre paneles con sobre firmado, bandeja persistente y auditoria (Ola 3, F1.4 del dossier 37).

Cadena `submit`: principal autenticado por el Hub (sesion de panel + vinculo de proceso) -> validar -> resolver el
destino DENTRO del proyecto -> sellar (`from`, `chain_id`, `hop`, `trust`, `created_at` los pone el Hub) -> firmar con
la clave del dispositivo -> auditar y guardar en la misma transaccion -> avisar a quien espera.

Invariante de F0 (D12): este servicio NUNCA escribe en un PTY. El contenido es un dato que el agente pide con
`inbox_next`/`inbox_wait`; el despertar sin teclado es la Ola 4 (`AgentWakePort`).

Estados de un mensaje: ``queued -> delivered -> acked``; ``expired`` (caduco), ``parked`` (espera al humano: cadena
agotada o confirmacion pedida) y ``discarded`` (el humano lo descarto).
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from typing import Any, Callable, Dict, Iterable, List, Optional

from src.core.domain.envelope import EnvelopeError, build_header, content_hash, header_of
from src.core.domain.identity import Principal, PrincipalKind, TrustLevel, cap_agent_trust
from src.core.domain.relay_chain import CHAIN_DEPTH_EXCEEDED, DEFAULT_MAX_CHAIN_DEPTH, ChainTracker

logger = logging.getLogger("mcp2p.core.services.message_relay")

MAX_MESSAGE_BYTES = 8 * 1024
MAX_INBOX_DEPTH = 256
DEFAULT_MESSAGE_TTL_MS = 7 * 24 * 60 * 60 * 1000
CONTENT_RETENTION_DAYS = 30  # D19: el texto se borra a los 30 dias; los hashes de auditoria se conservan siempre
CONTENT_RETENTION_MS = CONTENT_RETENTION_DAYS * 24 * 60 * 60 * 1000
MAX_WAIT_MS = 300_000
TOOL_TIMEOUT_MS = MAX_WAIT_MS + 30_000  # limite por llamada MCP que se escribe en la configuracion de cada CLI
AGENT_KINDS = frozenset({"note", "task.request", "task.reply"})
OPERATOR_KINDS = frozenset({"note", "task.request"})  # el operador abre cadenas; no responde a nadie
GLOBAL_PAUSE_SCOPE = "global"
HUMAN_UI_ACTOR = "human_ui"
HUMAN_PRINCIPAL_ID = "local"  # remitente `human:local` de los mensajes que escribe el humano en la GUI
_PURGE_INTERVAL_MS = 60 * 60 * 1000


def _now_ms() -> int:
    return int(time.time() * 1000)


def _clamp_wait(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        number = int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, min(number, MAX_WAIT_MS))


def _err(code: str, error: str, **extra: Any) -> Dict[str, Any]:
    return {"success": False, "code": code, "error": error, **extra}


class MessageRelayService:
    """Relevo persistente entre principales del Hub. Thread-safe; lo compone SOLO el Hub (kernel de la GUI)."""

    def __init__(
        self,
        *,
        store: Any,
        identity: Any = None,
        identity_factory: Optional[Callable[[], Any]] = None,
        pane_directory: Any = None,
        clock_ms: Optional[Callable[[], int]] = None,
        max_chain_depth: int = DEFAULT_MAX_CHAIN_DEPTH,
        message_ttl_ms: int = DEFAULT_MESSAGE_TTL_MS,
        max_inbox_depth: int = MAX_INBOX_DEPTH,
        tracker: Optional[ChainTracker] = None,
    ) -> None:
        self.store = store
        self._identity = identity
        self._identity_factory = identity_factory
        self._identity_lock = threading.Lock()
        self.pane_directory = pane_directory
        self._clock_ms = clock_ms or _now_ms
        self.message_ttl_ms = int(message_ttl_ms)
        self.max_inbox_depth = int(max_inbox_depth)
        self.tracker = tracker or ChainTracker(max_depth=max_chain_depth, clock_ms=self._clock_ms)
        self._cond = threading.Condition()
        self._wake_listeners: List[Callable[[str], None]] = []
        self._last_purge_ms = 0
        # Canal del operador (Ola 4): `accepts_reply(session_id, project_uuid) -> bool` y `label(session_id) -> dict`.
        self.operator_directory: Any = None

    # -- composicion ---------------------------------------------------------------------------------------------

    @property
    def identity(self) -> Any:
        if self._identity is None:
            with self._identity_lock:
                if self._identity is None:
                    if self._identity_factory is None:
                        raise RuntimeError("MessageRelayService sin identidad de dispositivo")
                    self._identity = self._identity_factory()
        return self._identity

    def add_wake_listener(self, fn: Callable[[str], None]) -> None:
        """`fn(node_uuid)` se llama cuando llega algo a la bandeja de ese nodo (p. ej. la espera del alias antiguo)."""
        if fn not in self._wake_listeners:
            self._wake_listeners.append(fn)

    def remove_wake_listener(self, fn: Callable[[str], None]) -> None:
        try:
            self._wake_listeners.remove(fn)
        except ValueError:
            pass

    def _notify(self, node_uuid: str) -> None:
        with self._cond:
            self._cond.notify_all()
        for fn in list(self._wake_listeners):
            try:
                fn(node_uuid)
            except Exception:
                logger.debug("Oyente de bandeja con error", exc_info=True)

    # -- utilidades ----------------------------------------------------------------------------------------------

    @staticmethod
    def principal_of(rec: Any) -> Principal:
        return Principal.pane(str(rec.node_uuid), project_uuid=str(getattr(rec, "project_uuid", "") or ""))

    def _maintenance(self, now: int) -> None:
        if now - self._last_purge_ms < _PURGE_INTERVAL_MS:
            return
        self._last_purge_ms = now
        try:
            self.store.expire_due(now)
            self.store.purge_content(now - CONTENT_RETENTION_MS, now_ms=now)
        except Exception:
            logger.warning("Fallo el mantenimiento de la bandeja del relevo", exc_info=True)

    def purge_expired_content(self, now_ms: Optional[int] = None) -> int:
        now = int(now_ms if now_ms is not None else self._clock_ms())
        self.store.expire_due(now)
        return int(self.store.purge_content(now - CONTENT_RETENTION_MS, now_ms=now))

    def _pane_by_node(self, node_uuid: str, project_uuid: str) -> Optional[Any]:
        fn = getattr(self.pane_directory, "find_active_pane_by_node", None)
        if not callable(fn) or not node_uuid:
            return None
        return fn(node_uuid, project_uuid=project_uuid)

    def _resolve_target(self, project_uuid: str, target_node_uuid: Any, target_pane_id: Any) -> Optional[Any]:
        node = target_node_uuid.strip() if isinstance(target_node_uuid, str) else ""
        if node:
            return self._pane_by_node(node, project_uuid)
        if target_pane_id is None or not str(target_pane_id).strip():
            return None
        get_pane = getattr(self.pane_directory, "get_pane", None)
        if not callable(get_pane):
            return None
        try:
            pane = get_pane(target_pane_id)
        except ValueError:
            return None
        if pane is None or getattr(pane, "status", "active") == "closed":
            return None
        if str(getattr(pane, "project_uuid", "") or "") != project_uuid:
            return None
        return pane

    def _human_turn_ms(self, node_uuid: str) -> Optional[int]:
        fn = getattr(self.pane_directory, "last_human_submit_at", None)
        if not callable(fn):
            return None
        try:
            value = float(fn(node_uuid) or 0.0)
        except Exception:
            return None
        return int(value * 1000) if value > 0 else None

    def _paused(self, scope: str) -> bool:
        try:
            return bool(self.store.is_paused(scope))
        except Exception:
            return True  # fallo cerrado: si no se puede leer la parada, no se entrega

    def _label(self, principal_uri: str) -> Dict[str, Any]:
        """Procedencia legible (sin pid, token ni telemetria) para el receptor."""
        kind, _, ident = principal_uri.partition(":")
        out: Dict[str, Any] = {"principal": principal_uri, "kind": kind}
        if kind == "pane":
            out["node_uuid"] = ident
            pane = self._pane_by_node(ident, project_uuid=None) if self.pane_directory is not None else None
            if pane is not None:
                out["pane_id"] = getattr(pane, "pane_id", None)
                out["agent_name"] = getattr(pane, "agent_name", None)
            else:
                out["pane_id"] = None
                out["agent_name"] = None
        elif kind == "operator":
            out["session_id"] = ident
            label_fn = getattr(self.operator_directory, "label", None)
            if callable(label_fn):
                try:
                    out.update(label_fn(ident) or {})
                except Exception:
                    logger.debug("Etiqueta del operador no disponible", exc_info=True)
        return out

    # -- envio ---------------------------------------------------------------------------------------------------

    def submit(
        self,
        sender: Any,
        *,
        text: Any,
        target_node_uuid: Any = None,
        target_pane_id: Any = None,
        kind: str = "note",
        card_id: Optional[str] = None,
        confirm_with_human: bool = False,
        parent: Optional[Dict[str, Any]] = None,
        target_rec: Any = None,
        sender_principal: Optional[Principal] = None,
        trust_override: Optional[TrustLevel] = None,
    ) -> Dict[str, Any]:
        """`sender` es la sesion de panel AUTENTICADA (nunca algo declarado por el agente).

        `sender_principal` y `trust_override` los usa SOLO `submit_as_human` (accion del humano en la GUI): ninguna
        herramienta MCP los expone."""
        now = int(self._clock_ms())
        self._maintenance(now)
        from_p = sender_principal if sender_principal is not None else self.principal_of(sender)
        project = from_p.project_uuid
        bad = self._check_text(text, kind, AGENT_KINDS)
        if bad is not None:
            return bad
        if self._paused(f"pane:{from_p.id}"):
            return _err("RELAY_PAUSED", "El relevo de este panel esta en pausa; solo el humano puede reanudarlo.")

        target = target_rec if target_rec is not None else self._resolve_target(project, target_node_uuid, target_pane_id)
        if target is None:
            return _err("TARGET_NOT_FOUND", "No hay ningun panel activo con ese destino en este proyecto.")
        to_p = Principal.pane(str(target.node_uuid), project_uuid=project)
        if self.store.count_pending(to_p.uri) >= self.max_inbox_depth:
            self.store.append_audit(
                {"event": "rejected", "from_principal": from_p.uri, "to_principal": to_p.uri, "project_uuid": project,
                 "reason_code": "INBOX_FULL", "actor": from_p.uri, "ts_ms": now}
            )
            return _err("INBOX_FULL", f"Bandeja del destino llena (maximo {self.max_inbox_depth}).", blocked=True)

        link = self.tracker.next_link(
            from_p.uri,
            human_turn_at_ms=self._human_turn_ms(from_p.id),
            parent_chain_id=(parent or {}).get("chain_id"),
            parent_hop=(parent or {}).get("hop"),
        )
        trust = trust_override or cap_agent_trust(TrustLevel.PANE_AUTHENTICATED, chain_trust=link.origin_trust)
        if link.exceeded:
            park_reason: Optional[str] = CHAIN_DEPTH_EXCEEDED
        elif confirm_with_human:
            park_reason = "HUMAN_CONFIRMATION_REQUESTED"
        else:
            park_reason = None

        sealed = self._seal_and_store(
            from_uri=from_p.uri,
            to_uri=to_p.uri,
            project=project,
            text=text,
            kind=kind,
            card_id=card_id,
            link=link,
            trust=trust,
            park_reason=park_reason,
            submit_event="submitted",
            now=now,
            parent_msg_id=(parent or {}).get("msg_id"),
        )
        if not sealed.get("success"):
            return sealed

        common = {
            "msg_id": sealed["msg_id"],
            "message_uuid": sealed["msg_id"],
            "target_node_uuid": to_p.id,
            "chain": sealed["chain"],
            "trust": sealed["trust"],
        }
        if park_reason == CHAIN_DEPTH_EXCEEDED:
            return {
                "success": False, "code": CHAIN_DEPTH_EXCEEDED, "delivered": False, "parked": True, **common,
                "error": "Cadena de respuestas automaticas agotada: el mensaje queda en la bandeja del humano.",
            }
        if park_reason == "HUMAN_CONFIRMATION_REQUESTED":
            return {"success": True, "code": "PARKED_FOR_HUMAN", "delivered": False, "parked": True, **common}

        held = self._paused(GLOBAL_PAUSE_SCOPE) or self._paused(f"pane:{to_p.id}")
        self._notify(to_p.id)
        result = {"success": True, "queued": True, "inbox": True, "state": "queued", "delivered": not held, **common}
        if held:
            result["held"] = "RELAY_PAUSED"
        return result

    def human_key_unlocked(self) -> bool:
        """True si la clave humana del kernel esta disponible y desbloqueada (hay certificado de dispositivo)."""
        try:
            return self.identity.device_certificate() is not None
        except Exception:
            return False

    def submit_as_human(
        self, *, text: Any, target_rec: Any, kind: str = "note", project_uuid: str = ""
    ) -> Dict[str, Any]:
        """Mensaje que escribe el HUMANO desde la GUI a un panel: sobre firmado con `from = human:local`.

        Confianza (solo niveles ya definidos): `human_confirmed` si la clave humana esta desbloqueada; si no,
        `pane_authenticated`. Un agente no puede llegar aqui (no hay herramienta MCP) y no puede responder a este
        remitente (`NOT_REPLYABLE`): el humano lee la respuesta en la terminal del agente."""
        trust = TrustLevel.HUMAN_CONFIRMED if self.human_key_unlocked() else TrustLevel.PANE_AUTHENTICATED
        return self.submit(
            None,
            text=text,
            kind=kind,
            target_rec=target_rec,
            sender_principal=Principal(PrincipalKind.HUMAN, HUMAN_PRINCIPAL_ID, project_uuid=project_uuid),
            trust_override=trust,
        )

    def reply(self, sender: Any, *, msg_id: Any, text: Any, kind: str = "task.reply") -> Dict[str, Any]:
        """Respuesta a un sobre RECIBIDO por `sender`: hereda la cadena y sube el salto."""
        from_p = self.principal_of(sender)
        original = self.store.get_message(str(msg_id or "")) if msg_id else None
        if (
            original is None
            or original["to_principal"] != from_p.uri
            or original["state"] not in ("delivered", "acked")
        ):
            return _err("MESSAGE_NOT_FOUND", "No hay ningun mensaje recibido con ese msg_id.")
        if str(original["from_principal"]).startswith("operator:"):
            return self._reply_to_operator(sender, original, text)
        if not str(original["from_principal"]).startswith("pane:"):
            return _err("NOT_REPLYABLE", "Ese mensaje no admite respuesta.")
        target = self._pane_by_node(str(original["from_principal"])[5:], from_p.project_uuid)
        if target is None:
            return _err("TARGET_NOT_FOUND", "No hay ningun panel activo con ese destino en este proyecto.")
        return self.submit(
            sender,
            text=text,
            kind=kind if kind in AGENT_KINDS else "task.reply",
            card_id=original.get("card_id"),
            parent={"msg_id": original["msg_id"], "chain_id": original["chain_id"], "hop": int(original["hop"])},
            target_rec=target,
        )

    # -- canal del operador (Ola 4, F2.2) -----------------------------------------------------------------------------

    @staticmethod
    def _check_text(text: Any, kind: str, kinds: frozenset) -> Optional[Dict[str, Any]]:
        if not isinstance(text, str) or not text.strip():
            return _err("EMPTY_MESSAGE", "El mensaje no puede estar vacío.")
        try:
            size = len(text.encode("utf-8"))
        except UnicodeEncodeError:
            return _err("INVALID_MESSAGE", "El mensaje no es texto UTF-8 valido.")
        if size > MAX_MESSAGE_BYTES:
            return _err(
                "MESSAGE_TOO_LARGE", f"El mensaje supera el maximo de {MAX_MESSAGE_BYTES} bytes.", max_bytes=MAX_MESSAGE_BYTES
            )
        if kind not in kinds:
            return _err("INVALID_KIND", f"kind debe ser uno de {sorted(kinds)}.")
        return None

    def _seal_and_store(
        self,
        *,
        from_uri: str,
        to_uri: str,
        project: str,
        text: str,
        kind: str,
        card_id: Optional[str],
        link: Any,
        trust: TrustLevel,
        park_reason: Optional[str],
        submit_event: str,
        now: int,
        parent_msg_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Sella (`from`, cadena y confianza los pone el Hub), firma, audita y guarda en una transaccion."""
        msg_id = str(uuid.uuid4())
        identity = self.identity
        try:
            header = build_header(
                msg_id=msg_id, kind=kind, from_uri=from_uri, from_device=identity.device_uri(), to_uri=to_uri,
                project_uuid=project, chain_id=link.chain_id, hop=link.hop, parent_msg_id=parent_msg_id, content=text,
                created_at=now, expires_at=now + self.message_ttl_ms, trust=trust.value,
                device_cert=identity.device_certificate(),
            )
        except EnvelopeError as exc:
            return _err("INVALID_ENVELOPE", f"No se pudo sellar el sobre ({exc.code}).")
        envelope = identity.sign_header(header)
        state = "parked" if park_reason else "queued"
        row = {
            "msg_id": msg_id, "project_uuid": project, "from_principal": from_uri, "to_principal": to_uri, "kind": kind,
            "chain_id": link.chain_id, "hop": link.hop, "parent_msg_id": parent_msg_id, "trust": trust.value,
            "content_hash": header["content_hash"], "content": text,
            "envelope_json": json.dumps(envelope, sort_keys=True, ensure_ascii=False), "state": state,
            "state_reason": park_reason, "card_id": str(card_id) if card_id else None, "created_at_ms": now,
            "expires_at_ms": header["expires_at"], "updated_at_ms": now,
        }
        base_audit = {
            "msg_id": msg_id, "chain_id": link.chain_id, "hop": link.hop, "project_uuid": project,
            "from_principal": from_uri, "to_principal": to_uri, "trust": trust.value,
            "content_hash": header["content_hash"], "actor": from_uri,
        }
        audit = [{**base_audit, "event": submit_event, "decision": state}]
        if park_reason:
            audit.append({**base_audit, "event": "parked", "reason_code": park_reason})
        if not self.store.insert_message(row, audit):
            self.store.append_audit({**base_audit, "event": "rejected", "reason_code": "REPLAY_DETECTED", "ts_ms": now})
            return _err("REPLAY_DETECTED", "Ese sobre ya existe.")
        return {
            "success": True, "msg_id": msg_id, "state": state,
            "chain": {"chain_id": link.chain_id, "hop": link.hop, "max_depth": link.max_depth}, "trust": trust.value,
        }

    def submit_from_operator(
        self,
        operator_uri: str,
        *,
        project_uuid: str,
        target_rec: Any,
        text: Any,
        kind: str = "task.request",
        card_id: Optional[str] = None,
        park_reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Sobre del operador emparejado (`from = operator:<sesion>`, `trust = operator_delegated`). El operador abre
        SIEMPRE una cadena nueva (salto 0): nunca recibe sobres, asi que no tiene contexto que heredar. Con
        `park_reason` el sobre espera al humano (destino `elevated` o `confirm_with_human`)."""
        now = int(self._clock_ms())
        self._maintenance(now)
        bad = self._check_text(text, kind, OPERATOR_KINDS)
        if bad is not None:
            return bad
        if target_rec is None or str(getattr(target_rec, "project_uuid", "") or "") != project_uuid:
            return _err("TARGET_NOT_FOUND", "No hay ningun panel activo con ese destino en este proyecto.")
        to_p = Principal.pane(str(target_rec.node_uuid), project_uuid=project_uuid)
        if self.store.count_pending(to_p.uri) >= self.max_inbox_depth:
            self.store.append_audit(
                {"event": "rejected", "from_principal": operator_uri, "to_principal": to_p.uri,
                 "project_uuid": project_uuid, "reason_code": "INBOX_FULL", "actor": operator_uri, "ts_ms": now}
            )
            return _err("INBOX_FULL", f"Bandeja del destino llena (maximo {self.max_inbox_depth}).", blocked=True)
        link = self.tracker.next_link(operator_uri)
        sealed = self._seal_and_store(
            from_uri=operator_uri, to_uri=to_p.uri, project=project_uuid, text=text, kind=kind, card_id=card_id,
            link=link, trust=TrustLevel.OPERATOR_DELEGATED, park_reason=park_reason, submit_event="operator_submitted",
            now=now,
        )
        if not sealed.get("success"):
            return sealed
        common = {**sealed, "target_node_uuid": to_p.id}
        if park_reason:
            return {**common, "code": "PARKED_FOR_HUMAN", "delivered": False, "parked": True, "reason": park_reason}
        held = self._paused(GLOBAL_PAUSE_SCOPE) or self._paused(f"pane:{to_p.id}")
        self._notify(to_p.id)
        result = {**common, "queued": True, "delivered": not held}
        if held:
            result["held"] = "RELAY_PAUSED"
        return result

    def _reply_to_operator(self, sender: Any, original: Dict[str, Any], text: Any) -> Dict[str, Any]:
        """`inbox_reply` a un sobre del operador: va a su bandeja (la recoge con `operator_await_reply`)."""
        now = int(self._clock_ms())
        from_p = self.principal_of(sender)
        to_uri = str(original["from_principal"])
        session_id = to_uri.partition(":")[2]
        accepts = getattr(self.operator_directory, "accepts_reply", None)
        if not callable(accepts) or not accepts(session_id, from_p.project_uuid):
            return _err("TARGET_NOT_FOUND", "El operador que envio ese mensaje ya no tiene una sesion activa.")
        bad = self._check_text(text, "task.reply", AGENT_KINDS)
        if bad is not None:
            return bad
        if self._paused(f"pane:{from_p.id}"):
            return _err("RELAY_PAUSED", "El relevo de este panel esta en pausa; solo el humano puede reanudarlo.")
        link = self.tracker.next_link(
            from_p.uri,
            human_turn_at_ms=self._human_turn_ms(from_p.id),
            parent_chain_id=original["chain_id"],
            parent_hop=int(original["hop"]),
        )
        trust = cap_agent_trust(TrustLevel.PANE_AUTHENTICATED, chain_trust=link.origin_trust)
        park = CHAIN_DEPTH_EXCEEDED if link.exceeded else None
        sealed = self._seal_and_store(
            from_uri=from_p.uri, to_uri=to_uri, project=from_p.project_uuid, text=text, kind="task.reply",
            card_id=original.get("card_id"), link=link, trust=trust, park_reason=park, submit_event="submitted",
            now=now, parent_msg_id=original["msg_id"],
        )
        if not sealed.get("success"):
            return sealed
        if park:
            return {**sealed, "success": False, "code": CHAIN_DEPTH_EXCEEDED, "delivered": False, "parked": True,
                    "error": "Cadena de respuestas automaticas agotada: el mensaje queda en la bandeja del humano."}
        self._notify(session_id)
        return {**sealed, "queued": True, "inbox": True, "delivered": True, "to": to_uri, "message_uuid": sealed["msg_id"]}

    def _operator_row(self, operator_uri: str, msg_id: Any) -> Optional[Dict[str, Any]]:
        row = self.store.get_message(str(msg_id)) if isinstance(msg_id, str) and msg_id.strip() else None
        if row is None or row["from_principal"] != operator_uri:
            return None  # no se revela si existe un sobre de otro remitente
        return row

    def operator_status(self, operator_uri: str, msg_id: Any) -> Dict[str, Any]:
        """Estado de un sobre que envio ESTE operador: queued/delivered/acked/replied/parked/expired/discarded."""
        row = self._operator_row(operator_uri, msg_id)
        if row is None:
            return _err("MESSAGE_NOT_FOUND", "No hay ningun mensaje tuyo con ese msg_id.")
        replies = self.store.replies_to(row["msg_id"], operator_uri)
        return {
            "success": True,
            "msg_id": row["msg_id"],
            "state": "replied" if replies else row["state"],
            "message_state": row["state"],
            "state_reason": row.get("state_reason"),
            "reply_count": len(replies),
            "released_by_human": row.get("state_reason") == "HUMAN_RELEASED",
            "trust": row["trust"],
            "chain": {"chain_id": row["chain_id"], "hop": int(row["hop"]), "max_depth": self.tracker.max_depth},
            "target": self._label(row["to_principal"]),
            "project_uuid": row["project_uuid"],
            "created_at": int(row["created_at_ms"]),
            "expires_at": int(row["expires_at_ms"]),
        }

    def await_operator_reply(self, operator_uri: str, msg_id: Any, *, timeout_ms: Any = 0) -> Dict[str, Any]:
        """Espera (sin sondeo; tope 300 s) a que el panel destino responda con `inbox_reply` y entrega la respuesta."""
        row = self._operator_row(operator_uri, msg_id)
        if row is None:
            return _err("MESSAGE_NOT_FOUND", "No hay ningun mensaje tuyo con ese msg_id.")
        applied = _clamp_wait(timeout_ms)
        deadline = time.monotonic() + applied / 1000.0
        final_states = ("expired", "discarded")
        with self._cond:
            while True:
                paused = self._paused(GLOBAL_PAUSE_SCOPE)
                ready = not paused and bool(self.store.replies_to(row["msg_id"], operator_uri))
                current = self.store.get_message(row["msg_id"]) or row
                remaining = deadline - time.monotonic()
                if ready or current["state"] in final_states or remaining <= 0:
                    break
                self._cond.wait(timeout=min(remaining, 5.0))
        replies: List[Dict[str, Any]] = []
        if not self._paused(GLOBAL_PAUSE_SCOPE):
            kind, _, ident = operator_uri.partition(":")
            recipient = Principal(kind, ident)
            taken = self.store.take_replies(row["msg_id"], operator_uri, now_ms=int(self._clock_ms()), actor=operator_uri)
            replies = [self._present(r, recipient) for r in taken]
        status = self.operator_status(operator_uri, row["msg_id"])
        return {
            **status,
            "replies": replies,
            "timed_out": not replies and status["message_state"] not in final_states,
            "timeout_ms_applied": applied,
            "relay_paused": self._paused(GLOBAL_PAUSE_SCOPE),
        }

    # -- entrega -------------------------------------------------------------------------------------------------

    def _present(self, row: Dict[str, Any], recipient: Principal) -> Dict[str, Any]:
        envelope = json.loads(row["envelope_json"])
        content = row.get("content")
        try:
            verdict = self.identity.verify(envelope, content)
            verified = bool(verdict.ok) and envelope.get("to") == recipient.uri and envelope.get("from") == row["from_principal"]
        except Exception:
            verified = False
        header = header_of(envelope)
        sender = self._label(str(header.get("from")))
        sender["trust"] = header.get("trust")
        return {
            "mcp2p_envelope": {
                "v": header.get("v"),
                "msg_id": row["msg_id"],
                "kind": header.get("kind"),
                "verified": verified,
                "from": sender,
                "to": self._label(str(header.get("to"))),
                "project_uuid": header.get("project_uuid"),
                "chain": {"chain_id": header.get("chain_id"), "hop": header.get("hop"), "max_depth": self.tracker.max_depth},
                "parent_msg_id": header.get("parent_msg_id"),
                "trust": header.get("trust"),
                "content_hash": header.get("content_hash"),
                "created_at": header.get("created_at"),
                "expires_at": header.get("expires_at"),
                "card_id": row.get("card_id"),
                "released_by_human": row.get("state_reason") == "HUMAN_RELEASED",
                "redelivered": bool(row.get("redelivered")),
                "device_cert": header.get("device_cert") is not None,
                "content_purged": content is None,  # retencion D19: el texto se borro, el sobre y su hash no
            },
            "payload_untrusted_text": content,
        }

    def next_for(
        self, recipient_rec: Any, *, limit: int = 10, include_unacked: bool = False, auto_ack: bool = False
    ) -> Dict[str, Any]:
        """Entrega los sobres pendientes del principal autenticado (los marca `delivered`)."""
        now = int(self._clock_ms())
        self._maintenance(now)
        rec_p = self.principal_of(recipient_rec)
        if self._paused(GLOBAL_PAUSE_SCOPE) or self._paused(f"pane:{rec_p.id}"):
            return {"success": True, "messages": [], "relay_paused": True, "project_uuid": rec_p.project_uuid}
        rows = self.store.take_for_delivery(
            rec_p.uri, rec_p.project_uuid, limit=max(1, min(int(limit or 10), 50)), include_unacked=include_unacked,
            now_ms=now, actor=rec_p.uri,
        )
        messages = []
        for row in rows:
            self.tracker.observe_delivery(rec_p.uri, chain_id=row["chain_id"], hop=int(row["hop"]), trust=row["trust"])
            messages.append(self._present(row, rec_p))
        if auto_ack and rows:
            self.store.mark_acked([r["msg_id"] for r in rows], rec_p.uri, now_ms=now, actor=rec_p.uri)
        return {"success": True, "messages": messages, "relay_paused": False, "project_uuid": rec_p.project_uuid}

    def wait_for(self, recipient_rec: Any, *, timeout_ms: Any = 0, limit: int = 10) -> Dict[str, Any]:
        """Espera larga (sin sondeo activo) hasta que haya algo entregable o venza `timeout_ms` (tope 300 s)."""
        applied = _clamp_wait(timeout_ms)
        rec_p = self.principal_of(recipient_rec)
        deadline = time.monotonic() + applied / 1000.0
        with self._cond:
            while True:
                deliverable = not (self._paused(GLOBAL_PAUSE_SCOPE) or self._paused(f"pane:{rec_p.id}")) and \
                    self.store.has_deliverable(rec_p.uri, rec_p.project_uuid, int(self._clock_ms()))
                remaining = deadline - time.monotonic()
                if deliverable or remaining <= 0:
                    break
                self._cond.wait(timeout=min(remaining, 5.0))
        out = self.next_for(recipient_rec, limit=limit)
        out["timed_out"] = not out["messages"]
        out["timeout_ms_applied"] = applied
        return out

    def ack(self, recipient_rec: Any, msg_ids: Iterable[Any]) -> Dict[str, Any]:
        rec_p = self.principal_of(recipient_rec)
        wanted = [str(m) for m in msg_ids if isinstance(m, str) and m.strip()]
        acked = self.store.mark_acked(wanted, rec_p.uri, now_ms=int(self._clock_ms()), actor=rec_p.uri)
        return {"success": True, "acked": acked, "not_found": [m for m in wanted if m not in acked]}

    def legacy_messages_for(self, node_uuid: str, project_uuid: Optional[str], directory_rec: Any = None) -> List[Dict[str, Any]]:
        """Alias `await_next_assignment` (D20): entrega y confirma, con la forma antigua de F0."""
        rec = directory_rec or self._pane_by_node(node_uuid, project_uuid=project_uuid)
        if rec is None or (project_uuid is not None and str(getattr(rec, "project_uuid", "")) != project_uuid):
            return []
        out = self.next_for(rec, limit=50, auto_ack=True)
        legacy = []
        for item in out["messages"]:
            env = item["mcp2p_envelope"]
            sender = dict(env["from"])
            legacy.append(
                {
                    "id": env["msg_id"],
                    "kind": "direct_message",
                    "from": {"node_uuid": sender.get("node_uuid"), "pane_id": sender.get("pane_id"),
                             "agent_name": sender.get("agent_name"),
                             "verified_by": "hub_pane_session" if env["verified"] else "unverified"},
                    "to": {k: env["to"].get(k) for k in ("node_uuid", "pane_id", "agent_name")},
                    "text": item["payload_untrusted_text"],
                    "project_uuid": env["project_uuid"],
                    "timestamp": float(env["created_at"]) / 1000.0,
                    "msg_id": env["msg_id"],
                    "trust": env["trust"],
                    "chain": env["chain"],
                    **({"card_id": env["card_id"]} if env.get("card_id") else {}),
                }
            )
        return legacy

    # -- parada ----------------------------------------------------------------------------------------------------

    def pause_self(self, rec: Any, *, reason: str = "") -> Dict[str, Any]:
        """Cualquier principal puede BAJAR su autonomia: deja de recibir y de enviar hasta que el humano lo reanude."""
        p = self.principal_of(rec)
        clean = " ".join(str(reason or "").split())[:200]
        self.store.set_pause(f"pane:{p.id}", True, actor=p.uri, reason=clean or "SELF_PAUSE", now_ms=int(self._clock_ms()))
        return {"success": True, "paused": True, "scope": f"pane:{p.id}", "resumable_by": "human"}

    def set_global_pause(self, enabled: bool, *, actor: str = HUMAN_UI_ACTOR) -> Dict[str, Any]:
        self.store.set_pause(GLOBAL_PAUSE_SCOPE, bool(enabled), actor=actor, reason="KILL_SWITCH", now_ms=int(self._clock_ms()))
        if not enabled:
            self._notify("*")
        return self.pause_state()

    def resume_pane(self, node_uuid: str, *, actor: str = HUMAN_UI_ACTOR) -> Dict[str, Any]:
        self.store.set_pause(f"pane:{node_uuid}", False, actor=actor, reason="HUMAN_RESUMED", now_ms=int(self._clock_ms()))
        self._notify(node_uuid)
        return self.pause_state()

    def pause_state(self) -> Dict[str, Any]:
        pauses = self.store.list_pauses()
        return {
            "success": True,
            "global_paused": any(p["scope"] == GLOBAL_PAUSE_SCOPE for p in pauses),
            "paused_panes": [p["scope"][5:] for p in pauses if str(p["scope"]).startswith("pane:")],
        }

    # -- humano (GUI) -------------------------------------------------------------------------------------------

    def list_human_inbox(self, project_uuid: Optional[str]) -> List[Dict[str, Any]]:
        out = []
        for row in self.store.list_parked(project_uuid):
            out.append(
                {
                    "msg_id": row["msg_id"],
                    "project_uuid": row["project_uuid"],
                    "reason": row.get("state_reason"),
                    "from": self._label(row["from_principal"]),
                    "to": self._label(row["to_principal"]),
                    "kind": row["kind"],
                    "chain": {"chain_id": row["chain_id"], "hop": int(row["hop"]), "max_depth": self.tracker.max_depth},
                    "trust": row["trust"],
                    "card_id": row.get("card_id"),
                    "text": row.get("content"),
                    "content_hash": row["content_hash"],
                    "created_at": int(row["created_at_ms"]),
                    "expires_at": int(row["expires_at_ms"]),
                }
            )
        return out

    def resolve_parked(self, msg_id: str, decision: str, *, actor: str = HUMAN_UI_ACTOR) -> Dict[str, Any]:
        if decision not in ("release", "discard"):
            return _err("INVALID_DECISION", "decision debe ser 'release' o 'discard'.")
        row = self.store.resolve_parked(str(msg_id or ""), decision, now_ms=int(self._clock_ms()), actor=actor)
        if row is None:
            return _err("MESSAGE_NOT_FOUND", "No hay ningun mensaje aparcado con ese msg_id.")
        if decision == "release":
            self._notify(str(row["to_principal"]).partition(":")[2])
        return {"success": True, "msg_id": row["msg_id"], "state": row["state"], "decision": decision}

    def audit_status(self) -> Dict[str, Any]:
        chain = self.store.verify_chain()
        return {
            "success": True,
            "chain": chain,
            "events": self.store.audit_counts(),
            "messages": self.store.message_counts(),
            "retention_days": CONTENT_RETENTION_DAYS,
            "max_chain_depth": self.tracker.max_depth,
            **self.pause_state(),
        }

    # -- transporte (F4): sobre ya firmado que llega de fuera ---------------------------------------------------------

    def accept_signed_envelope(self, envelope: Dict[str, Any], content: str) -> Dict[str, Any]:
        """Ingiere un sobre firmado por ESTE dispositivo (p. ej. reentrega de un transporte). Verifica firma y hash y
        rechaza la repeticion por `msg_id`, dejando rastro en la auditoria."""
        now = int(self._clock_ms())
        verdict = self.identity.verify(envelope, content, now_ms=now)
        header = header_of(envelope) if isinstance(envelope, dict) else {}
        base = {
            "msg_id": header.get("msg_id"), "chain_id": header.get("chain_id"), "hop": header.get("hop"),
            "project_uuid": header.get("project_uuid"), "from_principal": header.get("from"),
            "to_principal": header.get("to"), "trust": header.get("trust"), "actor": "transport", "ts_ms": now,
        }
        if not verdict.ok:
            self.store.append_audit({**base, "event": "rejected", "reason_code": f"ENVELOPE_{verdict.code}"})
            return _err("ENVELOPE_REJECTED", "Sobre no valido.", reason=verdict.code)
        row = {
            "msg_id": header["msg_id"], "project_uuid": header["project_uuid"], "from_principal": header["from"],
            "to_principal": header["to"], "kind": header["kind"], "chain_id": header["chain_id"], "hop": header["hop"],
            "parent_msg_id": header["parent_msg_id"], "trust": header["trust"], "content_hash": content_hash(content),
            "content": content, "envelope_json": json.dumps(envelope, sort_keys=True, ensure_ascii=False),
            "state": "queued", "state_reason": None, "card_id": None, "created_at_ms": header["created_at"],
            "expires_at_ms": header["expires_at"], "updated_at_ms": now,
        }
        if not self.store.insert_message(row, [{**base, "event": "submitted", "decision": "queued",
                                                 "content_hash": header["content_hash"]}]):
            self.store.append_audit({**base, "event": "rejected", "reason_code": "REPLAY_DETECTED",
                                     "content_hash": header["content_hash"]})
            return _err("REPLAY_DETECTED", "Ese sobre ya se recibio.")
        self._notify(str(header["to"]).partition(":")[2])
        return {"success": True, "msg_id": header["msg_id"], "state": "queued"}
