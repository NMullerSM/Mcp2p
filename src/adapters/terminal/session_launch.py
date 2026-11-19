"""Aumentador de lanzamiento para asignación o reanudación de sesión del CLI (v2.0.2).

Dueño: WP-4a. Stub vacío preparado por WP-0a.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.adapters.terminal.launch_augment import LaunchAddition, LaunchContext


def augment(ctx: "LaunchContext") -> "LaunchAddition":
    """Devuelve flags de sesión/reanudación por id para el lanzamiento (rellena WP-4a)."""
    from src.adapters.terminal.launch_augment import LaunchAddition

    return LaunchAddition()
