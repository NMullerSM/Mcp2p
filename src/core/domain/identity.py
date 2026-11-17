"""Principales y niveles de confianza del relevo (F1.1 del dossier 37, Ola 3).

Dominio puro: sin E/S, sin criptografia. La procedencia (`from`) y el nivel de confianza (`trust`) de un sobre los
calcula SIEMPRE el Hub a partir de la autenticacion (sesion de panel + vinculo de proceso, sesion de operador o accion
del humano en la GUI). Ningun parametro de una herramienta MCP puede fijarlos (hay prueba AST).

Niveles (de menos a mas confianza). Solo se usan hoy ``unverified``, ``pane_authenticated``/``operator_delegated`` y
``system``; el resto queda reservado para olas posteriores y NO lo emite ningun codigo de esta version:

======  =====================  ==========================================================================
Nivel   Nombre                 Base de la prueba
======  =====================  ==========================================================================
T0      ``unverified``         Nada verificable (p. ej. contenido importado, firma ausente o invalida)
T1      ``pane_authenticated`` Token del panel + proceso par dentro del arbol del panel (EGM-105)
T1      ``operator_delegated`` Sesion de operador emparejada (reservado: canal del operador, Ola 4)
T2      ``device_certified``   Sobre de otro dispositivo con certificado firmado por su humano (reservado, P2P)
T3      ``human_confirmed``    Firma o clic del humano sobre el texto exacto (reservado, Ola 4)
T4      ``system``             Generado por el propio Hub (avisos, eventos Kanban dirigidos)
======  =====================  ==========================================================================

Regla de no escalada: un sobre emitido por un agente vale como mucho ``pane_authenticated`` y, dentro de una cadena,
como mucho el minimo de la cadena (`origin_trust`).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class PrincipalKind(str, Enum):
    """Clase de principal. `device` firma los sobres; no es remitente de mensajes."""

    PANE = "pane"
    HUMAN = "human"
    OPERATOR = "operator"
    DEVICE = "device"


class TrustLevel(str, Enum):
    UNVERIFIED = "unverified"
    PANE_AUTHENTICATED = "pane_authenticated"
    OPERATOR_DELEGATED = "operator_delegated"
    DEVICE_CERTIFIED = "device_certified"  # reservado (P2P, Ola 6)
    HUMAN_CONFIRMED = "human_confirmed"  # reservado (canal del operador / diálogo, Ola 4)
    SYSTEM = "system"


TRUST_RANK = {
    TrustLevel.UNVERIFIED: 0,
    TrustLevel.PANE_AUTHENTICATED: 1,
    TrustLevel.OPERATOR_DELEGATED: 1,
    TrustLevel.DEVICE_CERTIFIED: 2,
    TrustLevel.HUMAN_CONFIRMED: 3,
    TrustLevel.SYSTEM: 4,
}

# Niveles que el Hub puede emitir en esta version. Los reservados existen en el enum (para leer sobres de versiones
# futuras sin romper) pero ningun camino de codigo los asigna hoy.
ACTIVE_TRUST_LEVELS = frozenset({TrustLevel.UNVERIFIED, TrustLevel.PANE_AUTHENTICATED, TrustLevel.SYSTEM})
# Lo maximo que vale algo que emite un agente de panel, diga lo que diga su texto.
MAX_AGENT_TRUST = TrustLevel.PANE_AUTHENTICATED

_ID_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.:/\-]{0,199}$")


def trust_rank(level: "TrustLevel | str") -> int:
    return TRUST_RANK[TrustLevel(level)]


def min_trust(*levels: "TrustLevel | str") -> TrustLevel:
    """Nivel mas bajo de los dados (el de una cadena es el de su eslabon menos fiable)."""
    parsed = [TrustLevel(level) for level in levels]
    if not parsed:
        return TrustLevel.UNVERIFIED
    return min(parsed, key=lambda lv: TRUST_RANK[lv])


def cap_agent_trust(requested: "TrustLevel | str", *, chain_trust: Optional["TrustLevel | str"] = None) -> TrustLevel:
    """Aplica la regla de no escalada a lo que emite un agente: <= pane_authenticated y <= la cadena."""
    level = TrustLevel(requested)
    if TRUST_RANK[level] >= TRUST_RANK[MAX_AGENT_TRUST]:
        level = MAX_AGENT_TRUST  # mismo rango que operator_delegated, pero NUNCA se convierte en el
    if chain_trust is not None:
        level = min_trust(level, chain_trust)
    return level


@dataclass(frozen=True)
class Principal:
    """Principal autenticado por el Hub. `uri` es la forma estable que viaja en el sobre (`<kind>:<id>`)."""

    kind: PrincipalKind
    id: str
    project_uuid: str = ""
    label: str = ""

    def __post_init__(self) -> None:
        kind = PrincipalKind(self.kind)
        object.__setattr__(self, "kind", kind)
        if not isinstance(self.id, str) or not _ID_RE.match(self.id):
            raise ValueError("principal id invalido")
        if unicodedata.normalize("NFC", self.id) != self.id:
            raise ValueError("principal id no esta en NFC")

    @property
    def uri(self) -> str:
        return f"{self.kind.value}:{self.id}"

    @classmethod
    def pane(cls, node_uuid: str, *, project_uuid: str = "", label: str = "") -> "Principal":
        return cls(PrincipalKind.PANE, node_uuid, project_uuid=project_uuid, label=label)

    @classmethod
    def device(cls, device_id: str) -> "Principal":
        return cls(PrincipalKind.DEVICE, device_id)

    @classmethod
    def human(cls, fingerprint: str) -> "Principal":
        return cls(PrincipalKind.HUMAN, fingerprint)


def parse_principal_uri(uri: str) -> Principal:
    """`pane:<id>` -> Principal; ValueError si el formato o la clase no son validos."""
    if not isinstance(uri, str) or ":" not in uri:
        raise ValueError("URI de principal invalida")
    kind, _, ident = uri.partition(":")
    return Principal(PrincipalKind(kind), ident)
