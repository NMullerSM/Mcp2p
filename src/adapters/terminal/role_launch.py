"""Aumentador de lanzamiento para rol y mandato del panel (v2.0.2).

Dueño: WP-3a. Stub vacío preparado por WP-0a.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.adapters.terminal.launch_augment import LaunchAddition, LaunchContext


def augment(ctx: "LaunchContext") -> "LaunchAddition":
    """Devuelve flags y archivos de rol/mandato para el lanzamiento (rellena WP-3a)."""
    from src.adapters.terminal.launch_augment import LaunchAddition

    return LaunchAddition()
