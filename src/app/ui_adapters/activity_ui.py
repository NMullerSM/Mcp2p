"""Cableado de composición para la actividad de agentes por hooks (v2.0.2, WP-6a)."""

from __future__ import annotations

import sys
import time
from typing import Any, List

from src.adapters.terminal.hook_configs import hook_command_argv
from src.core.factory import ApplicationFactory


def _now_ms() -> int:
    return int(time.time() * 1000)


def wire_activity(bridge: Any) -> None:
    """Crea `kernel.agent_activity` y conecta ciclo de vida de paneles, turno humano, `hook_argv` y sesiones."""
    kernel = getattr(bridge, "core_kernel", None)
    if kernel is None:
        return

    activity = ApplicationFactory.agent_activity_registry(kernel)
    extensions = getattr(bridge, "extensions_ui", None)

    launch_services = getattr(extensions, "launch_services", None) if extensions is not None else None
    if launch_services is not None:

        def _hook_argv(event: str) -> List[str]:
            return hook_command_argv(
                event,
                frozen=bool(getattr(sys, "frozen", False)),
                executable=sys.executable,
            )

        launch_services.hook_argv = _hook_argv

    reg_getter = getattr(extensions, "_get_pane_registry", None) if extensions is not None else None
    lifecycle = getattr(extensions, "pane_lifecycle", None) if extensions is not None else None
    if lifecycle is not None:

        def _on_spawned(ev: Any) -> None:
            node_uuid = str(getattr(ev, "node_uuid", "") or "")
            session_id = str(getattr(ev, "session_id", "") or "")
            if node_uuid:
                activity.reset(node_uuid, session_id)

        def _on_closed_by_user(project_uuid: str, pane_id: str) -> None:
            reg = reg_getter() if callable(reg_getter) else None
            if reg is None:
                return
            rec = None
            try:
                rec = reg.get_pane(pane_id)
            except Exception:
                rec = None
            node_uuid = getattr(rec, "node_uuid", None) if rec is not None else None
            if not node_uuid and callable(getattr(reg, "resolve_pane_identity", None)):
                try:
                    ident = reg.resolve_pane_identity(pane_id, project_uuid)
                    node_uuid = ident.get("node_uuid") if isinstance(ident, dict) else None
                except Exception:
                    node_uuid = None
            if node_uuid:
                activity.ingest(str(node_uuid), None, "pane_exited", _now_ms(), "hub")

        lifecycle.on_spawned(_on_spawned)
        lifecycle.on_closed_by_user(_on_closed_by_user)

    reg = reg_getter() if callable(reg_getter) else None
    if reg is not None and callable(getattr(reg, "add_human_submit_listener", None)):

        def _on_human_submit(node_uuid: str) -> None:
            if node_uuid:
                activity.ingest(node_uuid, None, "human_submitted", _now_ms(), "human")

        reg.add_human_submit_listener(_on_human_submit)

    def _on_cli_session_id(node_uuid: str, cli_session_id: str) -> None:
        svc = getattr(kernel, "terminal_session_service", None)
        record_fn = getattr(svc, "record_cli_session_id", None) if svc is not None else None
        if not callable(record_fn):
            return
        current_reg = reg_getter() if callable(reg_getter) else None
        if current_reg is None:
            return
        rec = current_reg.find_active_pane_by_node(node_uuid)
        if rec is None:
            return
        record_fn(
            project_uuid=rec.project_uuid,
            pane_slot=rec.pane_id,
            cli_session_id=cli_session_id,
            source="hook",
        )

    activity.on_cli_session_id(_on_cli_session_id)
