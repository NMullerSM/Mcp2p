"""Dominio puro del chat: participantes, entradas, canales, menciones y estados de entrega. Sin E/S."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

CHANNEL_KINDS: Tuple[str, ...] = ("agent", "agents", "node_devs", "group")
PARTICIPANT_KINDS: Tuple[str, ...] = ("agent", "human", "node_dev", "group", "system")
BUBBLE_COLORS: Tuple[str, ...] = tuple(f"c{i}" for i in range(1, 9))
ALL_MENTION = "todos"
# `@rol` nombra a los agentes con ese rol (el valor es el del rol en el dominio de terminales).
ROLE_MENTIONS: Mapping[str, str] = {
    "orquestador": "orchestrator",
    "implementador": "implementer",
    "auditor": "auditor",
    "mediador": "mediator",
    "par": "peer",
}
MAX_TEXT_BYTES = 8 * 1024

TICK_LABELS: Mapping[str, str] = {
    "queued": "En cola",
    "delivering": "Entregando en su terminal",
    "delivered": "Escrito en su terminal",
    "read": "Leído",
    "replied": "Respondido",
    "doubtful": "Entrega dudosa: revisa la terminal",
    "held": "Retenido (ver Permisos)",
    "expired": "Caducado",
    "discarded": "Descartado",
}

_MENTION_RE = re.compile(r"(?:(?<=\s)|^)@([\wáéíóúüñÁÉÍÓÚÜÑ-]{1,40})")


def normalize_token(text: str) -> str:
    """Minúsculas, sin tildes ni arroba: forma de comparación de una mención."""
    base = unicodedata.normalize("NFKD", str(text or "").strip().lstrip("@").lower())
    return "".join(ch for ch in base if not unicodedata.combining(ch))


@dataclass(frozen=True)
class Participant:
    """Alguien con quien se puede hablar o que aparece en el feed. `principal` es un texto opaco (`pane:…`,
    `human:local`, `node:…`, `dev:<nodo>/<usuario>`): un tipo nuevo de participante no cambia ningún contrato."""

    principal: str
    label: str
    kind: str = "agent"
    mention: str = ""
    role: Optional[str] = None
    role_label: Optional[str] = None
    cli_profile: Optional[str] = None
    aliases: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "principal": self.principal,
            "label": self.label,
            "kind": self.kind,
            "mention": self.mention,
            "role": self.role,
            "role_label": self.role_label,
            "cli_profile": self.cli_profile,
        }


@dataclass(frozen=True)
class ChatEntry:
    """Un mensaje del feed. `rev_ms` es la última vez que cambió (estado de entrega, respuestas): el cursor del feed
    es incremental por `rev_ms`, no por la fecha de creación, para que los ticks se actualicen."""

    id: str
    ts_ms: int
    sender: Participant
    text: str
    project_uuid: str = ""
    source: str = ""
    recipients: Tuple[Participant, ...] = ()
    kind: str = "note"
    thread_id: str = ""
    parent_id: Optional[str] = None
    hop: int = 0
    tick: str = "read"
    tick_label: str = ""
    origin: str = "local"
    rev_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        sender = self.sender.to_dict()
        sender["color"] = bubble_color(self.sender.principal)
        return {
            "id": self.id,
            "ts_ms": self.ts_ms,
            "rev_ms": self.rev_ms or self.ts_ms,
            "source": self.source,
            "sender": sender,
            "recipients": [{"principal": r.principal, "label": r.label} for r in self.recipients],
            "text": self.text,
            "kind": self.kind,
            "thread_id": self.thread_id,
            "parent_id": self.parent_id,
            "hop": self.hop,
            "tick": self.tick,
            "tick_label": self.tick_label or TICK_LABELS.get(self.tick, ""),
            "origin": self.origin,
        }


@dataclass(frozen=True)
class Channel:
    kind: str
    label: str
    available: bool
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "label": self.label, "available": self.available, "reason": self.reason}


@dataclass(frozen=True)
class SendResult:
    target: str
    ok: bool
    code: str = "OK"
    message: str = ""
    msg_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"target": self.target, "ok": self.ok, "code": self.code, "message": self.message, "msg_id": self.msg_id}


@dataclass(frozen=True)
class MentionParse:
    """Resultado de leer las `@menciones`: destinos resueltos (sin repetir), menciones sin resolver y si hubo `@todos`."""

    targets: Tuple[Participant, ...] = ()
    unknown: Tuple[str, ...] = ()
    ambiguous: Tuple[str, ...] = ()
    broadcast: bool = False
    found: Tuple[str, ...] = field(default_factory=tuple)


def bubble_color(principal: str) -> str:
    """Color estable (c1..c8) de un emisor: el mismo principal siempre tiene el mismo color."""
    digest = hashlib.sha1(str(principal or "").encode("utf-8")).digest()
    return BUBBLE_COLORS[digest[0] % len(BUBBLE_COLORS)]


def tick_for(state: str, delivery_state: Optional[str] = None, reply_count: int = 0) -> Tuple[str, str]:
    """Estado visible de un mensaje a partir del estado del relevo y de su entrega directa."""
    if state in ("queued", "parked", "expired", "discarded"):
        tick = {"queued": "queued", "parked": "held", "expired": "expired", "discarded": "discarded"}[state]
    elif reply_count > 0:
        tick = "replied"
    elif delivery_state == "unverifiable":
        tick = "doubtful"
    elif delivery_state == "injected":
        tick = "delivered"
    elif delivery_state in ("reserved", "written", "submitted"):
        tick = "delivering"
    else:
        tick = "read"
    return tick, TICK_LABELS[tick]


def parse_mentions(text: str, participants: Sequence[Participant]) -> MentionParse:
    """Resuelve las `@menciones` del texto contra los participantes: por su `mention`, por rol (`@orquestador`) o
    `@todos` (todos los agentes). Una mención que no existe se devuelve en `unknown` y el envío debe abortarse."""
    targets: List[Participant] = []
    unknown: List[str] = []
    ambiguous: List[str] = []
    found: List[str] = []
    broadcast = False
    by_mention: Dict[str, List[Participant]] = {}
    for p in participants:
        if p.kind == "group":
            continue
        for name in {normalize_token(p.mention), *(normalize_token(a) for a in p.aliases)} - {""}:
            by_mention.setdefault(name, []).append(p)
    agents = [p for p in participants if p.kind == "agent"]

    def add(candidates: Sequence[Participant]) -> None:
        for cand in candidates:
            if all(cand.principal != t.principal for t in targets):
                targets.append(cand)

    for match in _MENTION_RE.finditer(str(text or "")):
        token = normalize_token(match.group(1))
        if not token or token in found:
            continue
        found.append(token)
        if token == ALL_MENTION:
            broadcast = True
            add(agents)
        elif token in by_mention:
            if len(by_mention[token]) == 1:
                add(by_mention[token])
            else:
                ambiguous.append(match.group(1))
        elif token in ROLE_MENTIONS:
            matching = [p for p in agents if p.role == ROLE_MENTIONS[token]]
            if matching:
                add(matching)
            else:
                unknown.append(match.group(1))
        else:
            unknown.append(match.group(1))
    return MentionParse(
        targets=tuple(targets),
        unknown=tuple(unknown),
        ambiguous=tuple(ambiguous),
        broadcast=broadcast,
        found=tuple(found),
    )


def group_participants(participants: Sequence[Participant]) -> List[Participant]:
    """Entradas de autocompletado que no son una persona: `@todos` y un `@rol` por cada rol presente entre los agentes."""
    agents = [p for p in participants if p.kind == "agent"]
    out: List[Participant] = []
    if agents:
        out.append(Participant("group:all", "Todos los agentes", kind="group", mention="@" + ALL_MENTION))
    labels = {value: key for key, value in ROLE_MENTIONS.items()}
    for role in dict.fromkeys(p.role for p in agents if p.role in labels):
        nice = next((p.role_label for p in agents if p.role == role and p.role_label), labels[role].capitalize())
        out.append(Participant(f"group:role:{role}", f"{nice} (todos con ese rol)", kind="group",
                               mention="@" + labels[role], role=role))
    return out
