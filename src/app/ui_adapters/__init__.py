"""
Sub-adaptadores UI hexagonales para descomponer `ApiBridge` por dominio funcional.
Ningún sub-adaptador ejecuta SQL crudo (`get_connection` / `_write_lock`).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

from src.app.identity import local_node_uuid
from src.core.domain.context import ActorContext

if TYPE_CHECKING:
    from src.app.api_bridge import ApiBridge


class _BaseUIAdapter:
    """Base compartida para los sub-adaptadores UI delegados por ApiBridge."""

    def __init__(self, bridge: "ApiBridge") -> None:
        self._bridge = bridge

    @property
    def db(self) -> Any:
        return self._bridge.db

    @property
    def bus(self) -> Any:
        return self._bridge.bus

    @property
    def okf(self) -> Any:
        return self._bridge.okf

    @property
    def crypto(self) -> Any:
        return self._bridge.crypto

    @property
    def transport(self) -> Any:
        return self._bridge.transport

    @property
    def github(self) -> Any:
        return self._bridge.github

    @property
    def core_kernel(self) -> Any:
        return getattr(self._bridge, "core_kernel", None)

    @property
    def extension_gate(self) -> Any:
        return getattr(self._bridge, "extension_gate", None)

    @property
    def mesh(self) -> Any:
        return getattr(self._bridge, "mesh", None)

    def _kanban_service(self) -> Any:
        kernel = self.core_kernel
        return getattr(kernel, "kanban_service", None) if kernel is not None else None

    def _tribunal_service(self) -> Any:
        kernel = self.core_kernel
        return getattr(kernel, "tribunal_service", None) if kernel is not None else None

    def _view_synthesizer(self) -> Any:
        kernel = self.core_kernel
        return getattr(kernel, "view_synthesizer", None) if kernel is not None else None

    def _resolve_project_uuid(self, project_uuid: Optional[str] = None) -> Optional[str]:
        target_uuid = project_uuid or self._bridge.active_project_uuid
        if not target_uuid:
            self._bridge._ensure_active_project()
            target_uuid = self._bridge.active_project_uuid
        return target_uuid

    def _get_project_or_error(
        self, project_uuid: Optional[str] = None
    ) -> Tuple[Optional[str], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        target_uuid = self._resolve_project_uuid(project_uuid)
        proj = self.db.get_project_by_uuid(target_uuid) if target_uuid else None
        if not proj:
            return target_uuid, None, {"success": False, "error": "Proyecto no encontrado"}
        return target_uuid, proj, None

    @staticmethod
    def _local_node(override: Optional[str] = None) -> str:
        return override or local_node_uuid()

    @staticmethod
    def _dev_name() -> str:
        return os.environ.get("USER", os.environ.get("USERNAME", "developer"))

    def _human_actor(self, node_override: Optional[str] = None) -> ActorContext:
        return ActorContext(
            kind="human_ui",
            node_uuid=self._local_node(node_override),
            human_presence_verified=True,
        )

    def _subprocess_run(self, *args: Any, **kwargs: Any) -> Any:
        import subprocess as _sp
        import sys as _sys
        bridge_mod = _sys.modules.get("src.app.api_bridge")
        sp_mod = getattr(bridge_mod, "subprocess", _sp) if bridge_mod is not None else _sp
        return sp_mod.run(*args, **kwargs)


from src.app.ui_adapters.system_projects_ui import SystemProjectsUIAdapter
from src.app.ui_adapters.kanban_okf_ui import KanbanOkfUIAdapter
from src.app.ui_adapters.git_branches_ui import GitBranchesUIAdapter
from src.app.ui_adapters.tribunal_hitl_ui import TribunalHitlUIAdapter
from src.app.ui_adapters.collab_mesh_ui import CollabMeshUIAdapter
from src.app.ui_adapters.extensions_lab_ui import ExtensionsLabUIAdapter

__all__ = [
    "_BaseUIAdapter",
    "SystemProjectsUIAdapter",
    "KanbanOkfUIAdapter",
    "GitBranchesUIAdapter",
    "TribunalHitlUIAdapter",
    "CollabMeshUIAdapter",
    "ExtensionsLabUIAdapter",
]
