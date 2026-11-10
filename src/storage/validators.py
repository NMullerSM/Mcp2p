"""Validadores de identificadores que terminan en rutas de filesystem o refs de git."""
from __future__ import annotations

import re
from pathlib import Path

SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}


class InvalidIdentifierError(ValueError):
    """Identificador o ruta rechazados por seguridad (path traversal, caracteres no permitidos)."""


def is_safe_id(value: object) -> bool:
    return isinstance(value, str) and SAFE_ID_RE.fullmatch(value) is not None and value.upper() not in _WINDOWS_RESERVED


def validate_safe_id(value: object, field: str = "id") -> str:
    if not is_safe_id(value):
        shown = repr(value)[:80]
        raise InvalidIdentifierError(
            f"{field} inválido: {shown}. Solo se permiten letras, números, '-' y '_' "
            f"(1-64 caracteres, empezando por letra o número)."
        )
    return value  # type: ignore[return-value]


def ensure_within(base: Path | str, candidate: Path | str) -> Path:
    """Devuelve candidate resuelto si queda dentro de base (o es base); si no, lanza InvalidIdentifierError."""
    base_r = Path(base).resolve()
    cand_r = Path(candidate).resolve()
    if cand_r != base_r and base_r not in cand_r.parents:
        raise InvalidIdentifierError(f"Ruta fuera del directorio permitido: {candidate}")
    return cand_r
