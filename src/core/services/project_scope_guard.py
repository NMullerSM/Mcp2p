"""Guardia de alcance por proyecto para las herramientas MCP antiguas (Ola 2, decision D8).

Una sesion de panel autenticada por el Hub esta ligada a UN proyecto (`PaneSessionRecord.project_uuid`). Cuando una
herramienta antigua recibe un `project_uuid` explicito distinto:

- modo ``warn`` (el de esta version): se registra un evento estructurado y la respuesta lleva `scope_warning`; la
  herramienta se ejecuta como antes para no romper flujos de agentes existentes.
- modo ``enforce`` (previsto para 2.2): la llamada se rechaza con ``PROJECT_SCOPE_FORBIDDEN`` sin ejecutarse.

Sin sesion de panel (cliente humano, *harness* o servidor stdio independiente) no hay restriccion: la guardia solo
actua sobre agentes lanzados en un panel. Las herramientas del relevo (`list_swarm_panes`, `send_pane_message`,
`await_next_assignment`) no usan esta guardia: estan en `enforce` desde el primer dia.

El modo sale de la variable de entorno ``MCP2P_PROJECT_SCOPE`` del proceso del Hub (no del agente) y, si no esta o
no es valida, de la constante ``PROJECT_SCOPE_MODE``.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger("mcp2p.core.services.project_scope")

SCOPE_MODE_WARN = "warn"
SCOPE_MODE_ENFORCE = "enforce"
PROJECT_SCOPE_MODES = (SCOPE_MODE_WARN, SCOPE_MODE_ENFORCE)
# Cambiar a SCOPE_MODE_ENFORCE en 2.2 (D8).
PROJECT_SCOPE_MODE = SCOPE_MODE_WARN
PROJECT_SCOPE_ENV = "MCP2P_PROJECT_SCOPE"
PROJECT_SCOPE_FORBIDDEN = "PROJECT_SCOPE_FORBIDDEN"


def resolve_project_scope_mode(env_value: Optional[str] = None) -> str:
    """Modo vigente: la variable de entorno si es valida; si no, `PROJECT_SCOPE_MODE`."""
    raw = os.environ.get(PROJECT_SCOPE_ENV) if env_value is None else env_value
    value = (raw or "").strip().lower()
    if value in PROJECT_SCOPE_MODES:
        return value
    if value:
        logger.warning("%s=%r no es un modo valido %s; se usa %r.", PROJECT_SCOPE_ENV, raw, PROJECT_SCOPE_MODES,
                       PROJECT_SCOPE_MODE)
    return PROJECT_SCOPE_MODE


@dataclass(frozen=True)
class ScopeDecision:
    """Resultado de la guardia: `allowed` y, si hubo cruce de proyecto, el aviso o el error."""

    allowed: bool
    mismatch: bool = False
    mode: str = PROJECT_SCOPE_MODE
    warning: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None


class ProjectScopeGuard:
    """Compara el proyecto pedido por una herramienta con el de la sesion de panel autenticada."""

    def __init__(self, mode: Optional[str] = None) -> None:
        self._fixed_mode = mode if mode in PROJECT_SCOPE_MODES else None

    @property
    def mode(self) -> str:
        return self._fixed_mode or resolve_project_scope_mode()

    def check(
        self,
        *,
        tool: str,
        requested_project_uuid: Optional[str],
        session: Optional[Any],
    ) -> ScopeDecision:
        """`session` es el `PaneSessionRecord` autenticado (o None). Sin sesion o sin proyecto pedido: permitido."""
        mode = self.mode
        if session is None:
            return ScopeDecision(allowed=True, mode=mode)
        requested = requested_project_uuid.strip() if isinstance(requested_project_uuid, str) else ""
        if not requested:
            return ScopeDecision(allowed=True, mode=mode)
        session_project = str(getattr(session, "project_uuid", "") or "")
        if requested == session_project:
            return ScopeDecision(allowed=True, mode=mode)

        event = {
            "event": "project_scope_mismatch",
            "tool": tool,
            "mode": mode,
            "pane_id": getattr(session, "pane_id", None),
            "node_uuid": getattr(session, "node_uuid", None),
            "session_project_uuid": session_project,
            "requested_project_uuid": requested,
        }
        logger.warning("project_scope_mismatch %s", json.dumps(event, ensure_ascii=False, sort_keys=True),
                       extra={"mcp2p_scope": event})
        if mode == SCOPE_MODE_ENFORCE:
            return ScopeDecision(
                allowed=False,
                mismatch=True,
                mode=mode,
                error={
                    "success": False,
                    "code": PROJECT_SCOPE_FORBIDDEN,
                    "error": "Este panel solo puede actuar sobre su propio proyecto.",
                    "session_project_uuid": session_project,
                },
            )
        return ScopeDecision(
            allowed=True,
            mismatch=True,
            mode=mode,
            warning={
                "code": "PROJECT_SCOPE_MISMATCH",
                "mode": mode,
                "session_project_uuid": session_project,
                "requested_project_uuid": requested,
                "message": (
                    "Este panel pertenece a otro proyecto. La llamada se ha ejecutado y registrado; en una "
                    "version proxima se rechazara (PROJECT_SCOPE_FORBIDDEN)."
                ),
            },
        )
