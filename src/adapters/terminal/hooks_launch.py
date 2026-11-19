"""Aumentador de lanzamiento para configuración por sesión de hooks del CLI (v2.0.2).

Dueño: WP-6a. Stub vacío preparado por WP-0a.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.adapters.terminal.launch_augment import LaunchAddition, LaunchContext


def augment(ctx: "LaunchContext") -> "LaunchAddition":
    """Devuelve flags, entorno y archivos de hooks para el lanzamiento (rellena WP-6a)."""
    from src.adapters.terminal.launch_augment import LaunchAddition

    return LaunchAddition()
