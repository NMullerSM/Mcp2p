"""Identidad estable de un panel de terminal (Ola 2, T2.1 del dossier 36).

La identidad (`node_uuid`) de un panel es PERSISTENTE por (proyecto, ranura de panel): el mismo proyecto y la misma
ranura (`pane_1`..`pane_6`) dan el mismo `node_uuid` entre cierres, reaperturas y reinicios del Hub. Lo efimero es la
SESION: el token Bearer, el pid y el `session_id` del PTY cambian en cada arranque.

Formato: ``pane-<proyecto8>-<ranura>-<azar8>``

- ``proyecto8``: los 8 primeros hexadecimales del `project_uuid` si es un UUID; si no, los 8 primeros del SHA-256 del
  identificador; ``unscoped`` para un panel abierto sin proyecto activo.
- ``ranura``: el numero de `pane_N` (o el identificador saneado si no es numerico), en minusculas.
- ``azar8``: 32 bits aleatorios que hacen la identidad inadivinable y unica aunque dos proyectos compartan prefijo.

El formato es solo legible; la fuente de verdad es la fila de `pane_identities` (clave primaria proyecto+ranura).
"""

from __future__ import annotations

import hashlib
import re
import secrets
from typing import Optional

UNSCOPED_PROJECT_TAG = "unscoped"
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_SLOT_CLEAN_RE = re.compile(r"[^a-z0-9]")
PANE_NODE_UUID_RE = re.compile(r"^pane-(?:[0-9a-f]{8}|unscoped)-[a-z0-9]{1,12}-[0-9a-f]{8}$")
MAX_PROJECT_UUID_LEN = 200


def normalize_project_uuid(project_uuid: Optional[str]) -> str:
    """`project_uuid` limpio ('' = panel sin proyecto); rechaza tipos raros y caracteres de control."""
    if project_uuid is None:
        return ""
    if not isinstance(project_uuid, str):
        raise ValueError("project_uuid debe ser texto")
    value = project_uuid.strip()
    if len(value) > MAX_PROJECT_UUID_LEN or any(not ch.isprintable() for ch in value):
        raise ValueError("project_uuid invalido")
    return value


def project_tag(project_uuid: Optional[str]) -> str:
    """Etiqueta de 8 caracteres del proyecto para el `node_uuid` del panel."""
    value = normalize_project_uuid(project_uuid)
    if not value:
        return UNSCOPED_PROJECT_TAG
    if _UUID_RE.fullmatch(value):
        return value[:8].lower()
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def slot_tag(pane_slot: str) -> str:
    """`pane_3` -> `3`; otros identificadores se sanean a [a-z0-9] (max 12)."""
    raw = str(pane_slot or "").strip().lower()
    if raw.startswith("pane_"):
        raw = raw[5:]
    cleaned = _SLOT_CLEAN_RE.sub("", raw)[:12]
    if not cleaned:
        cleaned = hashlib.sha256(str(pane_slot).encode("utf-8")).hexdigest()[:8]
    return cleaned


def new_pane_node_uuid(project_uuid: Optional[str], pane_slot: str) -> str:
    """Genera una identidad nueva para (proyecto, ranura); la persistencia decide si se reutiliza."""
    return f"pane-{project_tag(project_uuid)}-{slot_tag(pane_slot)}-{secrets.token_hex(4)}"


def is_valid_pane_node_uuid(value: object) -> bool:
    """True si `value` tiene el formato de identidad estable de panel."""
    return isinstance(value, str) and PANE_NODE_UUID_RE.fullmatch(value) is not None
