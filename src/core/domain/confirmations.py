"""Tabla cerrada de confirmaciones de MCP2P 2.0.2 (dominio puro, sin E/S).

Define cada acción confirmable (`ConfirmationSpec`), su nivel de riesgo, la superficie
de confirmación (`app` | `native` | `password`) y si puede recordarse (`REMEMBERABLE`).
Ninguna acción nativa ni de riesgo alto puede recordarse ni usar diálogo propio.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import FrozenSet, Mapping


@dataclass(frozen=True)
class ConfirmationSpec:
    """Especificación normativa de una acción sujeta a confirmación del usuario."""

    action_id: str
    risk: str
    surface: str
    rememberable: bool
    label_es: str


_SPECS: tuple[ConfirmationSpec, ...] = (
    ConfirmationSpec(
        action_id="auto_relay.enable",
        risk="low",
        surface="app",
        rememberable=True,
        label_es="Activar Auto-Relay en un panel",
    ),
    ConfirmationSpec(
        action_id="worker_mode.enable",
        risk="low",
        surface="app",
        rememberable=True,
        label_es="Activar modo trabajador en un panel",
    ),
    ConfirmationSpec(
        action_id="delivery.enable_pane",
        risk="medium",
        surface="app",
        rememberable=True,
        label_es="Activar entrega directa en un panel",
    ),
    ConfirmationSpec(
        action_id="cli.install.package_manager",
        risk="medium",
        surface="app",
        rememberable=True,
        label_es="Instalar CLI del catálogo con gestor de paquetes",
    ),
    ConfirmationSpec(
        action_id="cli.install.script",
        risk="medium_high",
        surface="app",
        rememberable=False,
        label_es="Instalar CLI con script descargado",
    ),
    ConfirmationSpec(
        action_id="relay.discard_parked",
        risk="low",
        surface="app",
        rememberable=False,
        label_es="Descartar un mensaje retenido",
    ),
    ConfirmationSpec(
        action_id="cli.set_binary_path",
        risk="high",
        surface="native",
        rememberable=False,
        label_es="Fijar el ejecutable de un CLI",
    ),
    ConfirmationSpec(
        action_id="pane.custom_command",
        risk="high",
        surface="native",
        rememberable=False,
        label_es="Abrir panel con comando propio",
    ),
    ConfirmationSpec(
        action_id="agy.register_global",
        risk="high",
        surface="native",
        rememberable=False,
        label_es="Escribir en la configuración global de agy",
    ),
    ConfirmationSpec(
        action_id="relay.send_to_elevated",
        risk="high",
        surface="native",
        rememberable=False,
        label_es="Enviar a un agente con permisos saltados",
    ),
    ConfirmationSpec(
        action_id="relay.release_parked",
        risk="high",
        surface="native",
        rememberable=False,
        label_es="Entregar un mensaje retenido o en cuarentena",
    ),
    ConfirmationSpec(
        action_id="relay.global_resume",
        risk="medium",
        surface="native",
        rememberable=False,
        label_es="Reanudar el relevo global",
    ),
    ConfirmationSpec(
        action_id="relay.resume_pane",
        risk="medium",
        surface="native",
        rememberable=False,
        label_es="Reanudar un panel en pausa",
    ),
    ConfirmationSpec(
        action_id="loop_breaker.reset",
        risk="medium",
        surface="native",
        rememberable=False,
        label_es="Reiniciar el Loop Breaker",
    ),
    ConfirmationSpec(
        action_id="operator.pair",
        risk="high",
        surface="native",
        rememberable=False,
        label_es="Emparejar un operador",
    ),
    ConfirmationSpec(
        action_id="node.trust_direct",
        risk="high",
        surface="native",
        rememberable=False,
        label_es="Pasar un nodo remoto a confianza directa",
    ),
    ConfirmationSpec(
        action_id="external_url.open",
        risk="medium",
        surface="app",
        rememberable=False,
        label_es="Abrir una URL externa desde un terminal",
    ),
    ConfirmationSpec(
        action_id="human_key.unlock",
        risk="high",
        surface="password",
        rememberable=False,
        label_es="Desbloquear la clave humana",
    ),
)

CONFIRMATION_TABLE: Mapping[str, ConfirmationSpec] = MappingProxyType(
    {spec.action_id: spec for spec in _SPECS}
)

REMEMBERABLE: FrozenSet[str] = frozenset(
    spec.action_id
    for spec in CONFIRMATION_TABLE.values()
    if spec.surface == "app" and spec.rememberable
)
