"""
Sub-adaptador UI para gestión de proyectos, entorno, auto-updater, GitHub PAT y telemetría local.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import sys
from typing import Any, Dict, Optional, Tuple

from src.app.identity import local_node_uuid
from src.app.ui_adapters import _BaseUIAdapter
from src.core.domain import join as _domain_join  # EGM-57: reglas puras del flujo de unión

logger = logging.getLogger("mcp2p.api_bridge.system")


class SystemProjectsUIAdapter(_BaseUIAdapter):
    """Gestiona proyectos, ventana/tema nativo, GitHub PAT, auto-update y diagnóstico MCP."""

    def ensure_local_node(self) -> None:
        default_node = local_node_uuid()
        default_user = self._dev_name()
        try:
            gh_user = self.db.get_setting("github_pat_username")
            if gh_user:
                default_user = gh_user
        except Exception:
            pass
        self.db.register_or_update_node(
            node_uuid=default_node,
            developer_name=default_user,
            agent_id=f"agent_{default_user}",
            reviewer_sla_hours=24,
            status="online",
            node_kind="human",
        )

    @staticmethod
    def is_pending_join_stub(proj: Optional[Dict[str, Any]]) -> bool:
        return _domain_join.is_pending_join_stub(proj)

    @staticmethod
    def pending_join_refusal(proj: Dict[str, Any], action: str) -> Dict[str, Any]:
        return _domain_join.pending_join_refusal(proj, action)

    @staticmethod
    def parse_github_owner_repo(raw_url: str) -> Optional[Tuple[str, str]]:
        return _domain_join.parse_github_owner_repo(raw_url)

    def ensure_active_project(self) -> None:
        for proj in self.db.list_projects():
            if not self.is_pending_join_stub(proj):
                self._bridge.active_project_uuid = proj["project_uuid"]
                return

    def set_window(self, window: Any) -> None:
        self._bridge._window = window

    def set_ui_theme(self, theme: str) -> Dict[str, Any]:
        try:
            self.db.set_setting("ui_theme", theme)
            hex_color = "#f8fafc" if theme == "light" else "#0f1013"
            if platform.system() == "Windows" and self._bridge._window is not None:
                try:
                    form = getattr(self._bridge._window, "native", None)
                    if form is not None:
                        def _apply():
                            from System.Drawing import Color, ColorTranslator
                            form.BackColor = ColorTranslator.FromHtml(hex_color)
                            r, g, b = (int(hex_color[i:i+2], 16) for i in (1, 3, 5))
                            if hasattr(form, "webview") and form.webview is not None:
                                form.webview.DefaultBackgroundColor = Color.FromArgb(255, r, g, b)
                        if hasattr(form, "Invoke"):
                            form.Invoke(_apply)
                        else:
                            _apply()
                except Exception as we:
                    logger.debug(f"[FLK-05] No se pudo sincronizar fondo nativo en Windows: {we}")
            return {"success": True, "theme": theme}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def list_projects(self) -> Dict[str, Any]:
        try:
            projects = self.db.list_projects()
            return {"success": True, "projects": projects, "active_uuid": self._bridge.active_project_uuid}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def create_project(
        self,
        name: str,
        code_repo_url: str = "",
        gov_repo_url: str = "",
        auto_publish: bool = False,
        reviewer_sla_hours: int = 24,
    ) -> Dict[str, Any]:
        try:
            if not name.strip():
                return {"success": False, "error": "El nombre del proyecto es obligatorio"}
            proj = self.db.create_project(
                name=name.strip(),
                code_repo_url=code_repo_url,
                gov_repo_url=gov_repo_url,
                reviewer_sla_hours=reviewer_sla_hours,
                owner_public_key=self.crypto.public_key_base64,
            )
            self._bridge.active_project_uuid = proj["project_uuid"]

            if auto_publish:
                try:
                    self._bridge.publish_project_to_github(proj["project_uuid"], repo_target="both")
                except Exception as ce:
                    logger.warning(f"Auto-publish to GitHub omitted: {ce}")

            return {"success": True, "project": proj}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def select_project(self, project_uuid: str) -> Dict[str, Any]:
        try:
            proj = self.db.get_project_by_uuid(project_uuid)
            if not proj:
                return {"success": False, "error": "Proyecto no encontrado"}
            if self.is_pending_join_stub(proj):
                return self.pending_join_refusal(proj, "abrir el proyecto como espacio de trabajo")
            self._bridge.active_project_uuid = project_uuid
            self.ensure_local_node_registered()
            return {"success": True, "project": proj}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def delete_project(self, project_uuid: str) -> Dict[str, Any]:
        try:
            success = self.db.delete_project(project_uuid)
            if not success:
                return {"success": False, "error": f"Proyecto no encontrado: {project_uuid}"}
            if self._bridge.active_project_uuid == project_uuid:
                self._bridge.active_project_uuid = None
                self.ensure_active_project()
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_mcp_config_snippet(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        try:
            target_uuid = self._resolve_project_uuid(project_uuid)
            proj = self.db.get_project_by_uuid(target_uuid) if target_uuid else None
            p_name = proj["name"] if proj else "Proyecto MCP2P"

            if getattr(sys, "frozen", False):
                mcp_command = sys.executable
                mcp_args = ["--mcp-server", "--transport", "stdio"]
            else:
                mcp_command = sys.executable
                mcp_args = ["-m", "src.mcp.server", "--transport", "stdio"]

            mcp_json_config = {
                "mcpServers": {
                    "mcp2p": {
                        "command": mcp_command,
                        "args": mcp_args,
                        "env": {
                            "MCP2P_ACTIVE_PROJECT": target_uuid or ""
                        },
                    }
                }
            }

            agent_seed_prompt = (
                f"Actúa como un agente colaborador del enjambre MCP2P para el proyecto '{p_name}' (UUID: `{target_uuid}`).\n"
                f"Estás conectado mediante el servidor local MCP de MCP2P.\n"
                f"Protocolo de colaboración:\n"
                f"1. Antes de codificar, inspecciona el tablero con `get_kanban_board` y reclama tu tarea con `claim_kanban_task` para obtener el lease atómico.\n"
                f"2. Consulta la gobernanza viva y decisiones técnicas con `inspect_okf_governance`.\n"
                f"3. Si necesitas proponer un cambio arquitectónico o PR, utiliza el canal dialéctico de 3 turnos con `send_coordination_rfc` (Turno 1: PROPOSAL).\n"
                f"4. Al concluir tu tarea, libera el lease con `release_kanban_task` asociando la rama del código."
            )

            return {
                "success": True,
                "project_uuid": target_uuid,
                "project_name": p_name,
                "mcp_json": json.dumps(mcp_json_config, indent=2, ensure_ascii=False),
                "agent_seed_prompt": agent_seed_prompt,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_github_status(self) -> Dict[str, Any]:
        try:
            status = self.github.get_auth_status()
            return {"success": True, "github": status}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def save_github_pat(self, token: str) -> Dict[str, Any]:
        try:
            return self.github.save_pat_token(token)
        except Exception as e:
            return {"success": False, "error": str(e)}

    def clear_github_pat(self) -> Dict[str, Any]:
        try:
            return self.github.clear_pat_token()
        except Exception as e:
            return {"success": False, "error": str(e)}

    def start_auto_update(self, download_url: str, version: str) -> Dict[str, Any]:
        return self._bridge._updater.start_download(download_url, version)

    def get_update_progress(self) -> Dict[str, Any]:
        return self._bridge._updater.get_progress()

    def apply_update_and_restart(self) -> Dict[str, Any]:
        return self._bridge._updater.apply_update_and_restart()

    def get_install_mode(self) -> Dict[str, Any]:
        # `kind` (windows_installer / windows_portable / linux_deb / ...) decide el asset de la release.
        return self._bridge._updater.get_install_info()

    def open_release_page(self, url: Optional[str] = None) -> Dict[str, Any]:
        return self._bridge._updater.open_release_page(url)

    def get_update_log(self, max_lines: int = 200) -> Dict[str, Any]:
        return self._bridge._updater.get_log(max_lines)

    def get_active_project(self) -> Dict[str, Any]:
        target_uuid = self._resolve_project_uuid()
        proj = self.db.get_project_by_uuid(target_uuid) if target_uuid else None
        return {"success": True, "project": proj}

    def get_project_sync_pulse(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        target_uuid = project_uuid or self._bridge.active_project_uuid
        if not target_uuid:
            return {"success": False, "error": "Sin proyecto activo", "fingerprint": ""}
        synth = self._view_synthesizer()
        if synth is not None:
            return synth.synthesize_sync_fingerprint(target_uuid)
        return self.db.get_project_sync_fingerprint(target_uuid)

    def ensure_local_node_registered(self) -> None:
        local_node = local_node_uuid()
        try:
            self.db.register_or_update_node(
                node_uuid=local_node,
                developer_name="Desarrollador Local (Tú)",
                agent_id="agent_local_developer",
                reviewer_sla_hours=24,
                status="online",
                node_kind="human",
            )
        except Exception:
            pass

    def get_mcp_and_agents_status(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        local_node = local_node_uuid()
        nodes = self.db.get_swarm_nodes()
        mcp_tools = [
            {"name": "list_projects", "description": "Lista proyectos y doble repositorio"},
            {"name": "get_kanban_board", "description": "Consulta tarjetas a coste 0 tokens"},
            {"name": "claim_kanban_task", "description": "Adquisición de tarea con lease exclusivo"},
            {"name": "create_kanban_task", "description": "Crea tareas en backlog con criterios de aceptación"},
            {"name": "release_kanban_task", "description": "Libera tarea o mueve a revisión con rama"},
            {"name": "send_coordination_rfc", "description": "Canal A2A dialéctico de 3 turnos"},
            {"name": "get_coordination_session", "description": "Historial de síntesis dialéctica"},
            {"name": "inspect_okf_governance", "description": "Auditoría de integridad OKF y memoria huérfana"},
            {"name": "register_agent_node", "description": "Heartbeat y registro de agente enjambre"},
            {"name": "get_project_context", "description": "Aislamiento de rutas y contexto del proyecto"},
        ]
        return {
            "success": True,
            "mcp_server": {
                "server_name": "mcp2p-server",
                "active_node_uuid": local_node,
                "protocol_version": "2024-11-05",
                "transport": "stdio / sse",
                "tools_count": len(mcp_tools),
                "tools": mcp_tools,
                "status": "online",
            },
            "connected_agents": nodes,
        }

    def get_system_info(self) -> Dict[str, Any]:
        active_proj = (
            self.db.get_project_by_uuid(self._bridge.active_project_uuid)
            if self._bridge.active_project_uuid
            else None
        )
        return {
            "success": True,
            "os": platform.system(),
            "os_release": platform.release(),
            "python_version": sys.version.split()[0],
            "db_path": str(self.db.db_path),
            "wal_mode": True,
            "heuristics": "Nielsen 10 + Hicks Law",
            "active_node": local_node_uuid(),
            "active_project": active_proj,
        }
