"""Clase de privilegio de un panel (37 §4.2, primera version minima para el canal del operador).

Dominio puro: decide la clase a partir de la clase de CLI del catalogo (`kind`) y de los `argv` REALES del arbol de
procesos del panel. Contra el *confused deputy*: un mensaje del operador a un destino ``elevated`` (un CLI que corre
sin pedir permisos, o una shell) nunca se entrega sin que el humano lo confirme en un dialogo nativo (D10).

Regla de fallo cerrado: perfil desconocido, shell o `argv` ilegible -> ``elevated``.

Limites honestos: solo se ve el `argv` del lanzamiento. Un CLI que cambia su modo de permisos en caliente (p. ej. un
atajo de teclado que activa «saltarse permisos») no se detecta; la lista de opciones cubre las conocidas de cada CLI
del catalogo y no es exhaustiva (un falso positivo solo hace que se pida confirmacion al humano).
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Iterable, Optional, Sequence

SHELL = "shell"
STANDARD = "standard"
ELEVATED = "elevated"

# Opciones que, en el argv de cualquier proceso del arbol del panel, marcan permisos amplios.
ELEVATED_FLAGS = frozenset(
    {
        "--dangerously-skip-permissions",  # Claude Code
        "--allow-dangerously-skip-permissions",  # Claude Code (permite activarlo en caliente)
        "--dangerously-bypass-approvals-and-sandbox",  # Codex
        "--yolo",  # Codex (alias) / otros
    }
)
# Pares opcion=valor (se aceptan `--opt valor` y `--opt=valor`).
ELEVATED_OPTION_VALUES = {
    "--permission-mode": frozenset({"bypasspermissions"}),  # Claude Code
    "--approval-mode": frozenset({"yolo"}),  # flag generico de aprobacion automatica
    "--sandbox": frozenset({"danger-full-access"}),  # Codex
    "-s": frozenset({"danger-full-access"}),  # Codex (corta)
    "--ask-for-approval": frozenset({"never"}),  # Codex
    "-a": frozenset({"never"}),  # Codex (corta)
}
# Opciones cortas que solo significan «sin permisos» en un CLI concreto.
PROFILE_SHORT_FLAGS: Dict[str, FrozenSet[str]] = {}


def _argv_is_elevated(argv: Sequence[str], profile: str) -> bool:
    short = PROFILE_SHORT_FLAGS.get(profile, frozenset())
    items = [str(a) for a in argv]
    for idx, raw in enumerate(items):
        arg = raw.strip()
        if arg in ELEVATED_FLAGS or arg in short:
            return True
        name, sep, value = arg.partition("=")
        allowed = ELEVATED_OPTION_VALUES.get(name)
        if allowed is None:
            continue
        if not sep:
            value = items[idx + 1] if idx + 1 < len(items) else ""
        if value.strip().lower() in allowed:
            return True
    return False


def classify_pane(
    *, profile: str, catalog_kind: Optional[str], argvs: Optional[Iterable[Sequence[str]]]
) -> str:
    """``shell`` | ``standard`` | ``elevated``. `argvs` = argv de cada proceso vivo del arbol del panel (None si no se
    pudieron leer). Una shell se trata como ``elevated`` para el operador (ejecuta lo que se le escriba)."""
    if catalog_kind is None:
        return ELEVATED
    if catalog_kind == "shell":
        return SHELL
    if argvs is None:
        return ELEVATED
    collected = list(argvs)
    if not collected:
        return ELEVATED
    for argv in collected:
        if _argv_is_elevated(argv, profile):
            return ELEVATED
    return STANDARD


def requires_human_confirmation(privilege_class: str) -> bool:
    """D10: confirmacion obligatoria para destinos ``elevated`` (y ``shell``, que lo es para el operador)."""
    return privilege_class in (ELEVATED, SHELL)
