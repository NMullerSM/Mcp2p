"""Servicio del chat: mezcla fuentes, resuelve menciones y envía por el transporte de cada tipo de participante.

No conoce ninguna fuente concreta: todo se registra desde fuera (`register_source`, `register_transport`,
`register_roster`). Las entradas de empuje (`publish`) quedan en un búfer acotado por proyecto.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict, deque
from typing import Any, Deque, Dict, List, Optional

from src.chat.domain import (
    MAX_TEXT_BYTES,
    Channel,
    ChatEntry,
    Participant,
    SendResult,
    group_participants,
    parse_mentions,
)
from src.chat.ports import ChatSource, ChatTransport, RosterProvider

logger = logging.getLogger(__name__)

PUSH_BUFFER_SIZE = 500
MAX_FEED_LIMIT = 500
MAX_TARGETS = 12
NODE_DEVS_REASON = "Sin conexión con otros nodos"
GROUP_REASON = "Sin conexión con otros nodos"


class ChatService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sources: Dict[str, ChatSource] = {}
        self._rosters: Dict[str, RosterProvider] = {}
        self._transports: Dict[str, ChatTransport] = {}
        self._pushed: Dict[str, Deque[ChatEntry]] = defaultdict(lambda: deque(maxlen=PUSH_BUFFER_SIZE))

    # -- registro (puntos de conexión) -------------------------------------------------------------------------------

    def register_source(self, source: ChatSource) -> None:
        with self._lock:
            self._sources[source.id] = source

    def register_roster(self, provider: RosterProvider) -> None:
        with self._lock:
            self._rosters[provider.id] = provider

    def register_transport(self, participant_kind: str, transport: ChatTransport) -> None:
        with self._lock:
            self._transports[participant_kind] = transport

    def publish(self, entry: ChatEntry) -> None:
        """Entrada de empuje desde un *hook* entrante (P2P, servidor): aparece en el feed de su proyecto."""
        with self._lock:
            self._pushed[entry.project_uuid].append(entry)

    # -- consulta ----------------------------------------------------------------------------------------------------

    def channels(self) -> List[Channel]:
        with self._lock:
            kinds = set(self._transports)
        agents = "agent" in kinds
        devs = "node_dev" in kinds
        return [
            Channel("agent", "Agente", agents, None if agents else "No hay terminales conectadas"),
            Channel("agents", "Varios agentes", agents, None if agents else "No hay terminales conectadas"),
            Channel("node_devs", "Desarrolladores de otros nodos", devs, None if devs else NODE_DEVS_REASON),
            Channel("group", "Todo el grupo", devs and agents, None if (devs and agents) else GROUP_REASON),
        ]

    def participants(self, project_uuid: str) -> List[Participant]:
        with self._lock:
            providers = list(self._rosters.values())
        out: List[Participant] = []
        seen = set()
        for provider in providers:
            try:
                for p in provider.participants(project_uuid):
                    if p.principal not in seen:
                        seen.add(p.principal)
                        out.append(p)
            except Exception:
                logger.warning("El listado de participantes %s falló", provider.id, exc_info=True)
        return out + group_participants(out)

    def feed(self, project_uuid: str, since_rev_ms: int = 0, limit: int = 200) -> Dict[str, Any]:
        limit = max(1, min(int(limit or 200), MAX_FEED_LIMIT))
        since = max(0, int(since_rev_ms or 0))
        with self._lock:
            sources = list(self._sources.values())
            pushed = [e for e in self._pushed.get(project_uuid, ()) if (e.rev_ms or e.ts_ms) >= since]
        by_id: Dict[str, ChatEntry] = {}
        status: List[Dict[str, Any]] = []
        for source in sources:
            try:
                for entry in source.list_since(project_uuid, since, limit):
                    by_id[entry.id] = entry
                status.append({"id": source.id, "ok": True, "reason": None})
            except Exception as exc:
                logger.warning("La fuente de chat %s falló", source.id, exc_info=True)
                status.append({"id": source.id, "ok": False, "reason": "No se pudo leer: " + str(exc)[:120]})
        for entry in pushed:
            by_id[entry.id] = entry
        entries = sorted(by_id.values(), key=lambda e: (e.ts_ms, e.id))[-limit:]
        cursor = max((e.rev_ms or e.ts_ms for e in entries), default=since)
        return {"entries": [e.to_dict() for e in entries], "cursor_ms": max(cursor, since), "sources": status}

    # -- envío -------------------------------------------------------------------------------------------------------

    def send(self, project_uuid: str, text: Any) -> Dict[str, Any]:
        if not isinstance(text, str) or not text.strip():
            return _fail("EMPTY_MESSAGE", "Escribe el mensaje antes de enviarlo.")
        if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
            return _fail("MESSAGE_TOO_LONG", "El mensaje supera los 8 KiB.")
        participants = self.participants(project_uuid)
        parsed = parse_mentions(text, participants)
        if parsed.unknown:
            names = ", ".join("@" + u for u in parsed.unknown)
            return _fail("UNKNOWN_MENTION", f"No hay nadie con esa mención: {names}. No se envió nada.")
        if parsed.ambiguous:
            names = ", ".join("@" + a for a in parsed.ambiguous)
            return _fail(
                "AMBIGUOUS_MENTION",
                f"{names} coincide con varios agentes: usa la mención de uno concreto (por ejemplo @terminal2). "
                "No se envió nada.",
            )
        if not parsed.targets:
            return _fail("NO_RECIPIENT", "Menciona a quién va dirigido con @ (por ejemplo @orquestador o @todos).")
        if len(parsed.targets) > MAX_TARGETS:
            return _fail("TOO_MANY_TARGETS", f"Máximo {MAX_TARGETS} destinatarios por mensaje.")
        with self._lock:
            transports = dict(self._transports)
        results: List[SendResult] = []
        groups: Dict[str, List[Participant]] = defaultdict(list)
        for target in parsed.targets:
            groups[target.kind].append(target)
        for kind, members in groups.items():
            transport = transports.get(kind)
            if transport is None:
                results.extend(
                    SendResult(m.principal, False, "CHANNEL_UNAVAILABLE", "Ese canal no está conectado todavía.")
                    for m in members
                )
                continue
            try:
                results.extend(transport.send(project_uuid, members, text, "note"))
            except Exception as exc:
                logger.warning("El envío por %s falló", kind, exc_info=True)
                results.extend(SendResult(m.principal, False, "INTERNAL_ERROR", str(exc)[:160]) for m in members)
        ok = any(r.ok for r in results)
        return {
            "success": ok,
            "code": "OK" if ok else "SEND_FAILED",
            "results": [r.to_dict() for r in results],
            **({} if ok else {"message": "No se pudo enviar a ningún destino."}),
        }


def _fail(code: str, message: str) -> Dict[str, Any]:
    return {"success": False, "code": code, "message": message}
