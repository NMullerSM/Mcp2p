"""Reglas puras de política de nodos remotos y cuarentena (v2.0.2, WP-7).

Dominio puro sin E/S ni criptografía. Define la política por defecto según la relación
con el nodo (`same_owner` vs `foreign`), la función pura `decide_intake` y utilidades de
saneado de etiquetas declaradas y formato de huellas.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping, Optional

QUARANTINE_TTL_MS: int = 24 * 60 * 60 * 1000
CHAIN_APPROVAL_TTL_MS: int = 2 * 60 * 60 * 1000
PREVIOUS_NAME_VISIBLE_MS: int = 7 * 24 * 60 * 60 * 1000
MAX_NODE_DISPLAY_NAME_CHARS: int = 40

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


class NodePolicy(str, Enum):
    DIRECT = "direct"
    ASK_EACH = "ask_each"
    ASK_ONCE = "ask_once"
    BLOCKED = "blocked"


class NodeRelation(str, Enum):
    LOCAL = "local"
    SAME_OWNER = "same_owner"
    FOREIGN = "foreign"


DEFAULT_POLICY: Mapping[NodeRelation, NodePolicy] = MappingProxyType(
    {
        NodeRelation.SAME_OWNER: NodePolicy.DIRECT,
        NodeRelation.FOREIGN: NodePolicy.ASK_EACH,
    }
)


@dataclass(frozen=True)
class IntakeDecision:
    """Resultado puro de la decisión de admisión de un sobre remoto."""

    action: str  # deliver | park | drop
    reason: Optional[str]  # None | UNTRUSTED_NODE | UNTRUSTED_NODE_QUEUED | NODE_BLOCKED


def decide_intake(
    *,
    policy: NodePolicy,
    chain_approved: bool,
    pending_from_node: int,
) -> IntakeDecision:
    """Decide si un sobre remoto verificado se entrega (`deliver`), aparca (`park`) o descarta (`drop`).

    - `blocked` -> `drop` / `NODE_BLOCKED`
    - `direct` -> `deliver` / `None`
    - `ask_each` -> `deliver` / `None` si `chain_approved`, si no `park` / `UNTRUSTED_NODE`
    - `ask_once` -> `park` / `UNTRUSTED_NODE` si `pending_from_node == 0`, si no `park` / `UNTRUSTED_NODE_QUEUED`
    """
    resolved = policy if isinstance(policy, NodePolicy) else NodePolicy(str(policy))
    if resolved is NodePolicy.BLOCKED:
        return IntakeDecision(action="drop", reason="NODE_BLOCKED")
    if resolved is NodePolicy.DIRECT:
        return IntakeDecision(action="deliver", reason=None)
    if resolved is NodePolicy.ASK_EACH:
        if chain_approved:
            return IntakeDecision(action="deliver", reason=None)
        return IntakeDecision(action="park", reason="UNTRUSTED_NODE")
    # NodePolicy.ASK_ONCE
    pending = max(0, int(pending_from_node))
    if pending == 0:
        return IntakeDecision(action="park", reason="UNTRUSTED_NODE")
    return IntakeDecision(action="park", reason="UNTRUSTED_NODE_QUEUED")


def sanitize_node_name(value: object, *, max_len: int = MAX_NODE_DISPLAY_NAME_CHARS) -> str:
    """Sanea el nombre declarado de un nodo: elimina C0/C1/formatos invisibles y recorta a `max_len`."""
    if not isinstance(value, str):
        return ""
    without_ctrl = _CONTROL_RE.sub("", value)
    without_cf = "".join(ch for ch in without_ctrl if unicodedata.category(ch) != "Cf")
    collapsed = " ".join(without_cf.split())
    return collapsed[:max_len].strip()


def format_fingerprint_short(fingerprint: str) -> str:
    """Formatea una huella hex en bloques legibles `XXXX-XXXX-…-XXXX-XXXX` (primeros y últimos 8 hex)."""
    clean = "".join(ch for ch in str(fingerprint or "").strip() if ch.isalnum()).upper()
    if not clean:
        return ""
    if len(clean) >= 16:
        head = f"{clean[:4]}-{clean[4:8]}"
        tail = f"{clean[-8:-4]}-{clean[-4:]}"
        return f"{head}-…-{tail}"
    groups = [clean[i : i + 4] for i in range(0, len(clean), 4)]
    return "-".join(groups)
