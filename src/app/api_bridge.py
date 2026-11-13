"""
MCP2P JavaScript API Bridge
Enlace asíncrono y seguro entre la interfaz gráfica (PyWebView) y el backend en Python.
Soporte nativo para multi-proyecto con aislamiento estricto de doble repositorio.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import platform
import random
import re
import secrets
import subprocess
import sys
import urllib.parse
import yaml
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from src.storage.db import DatabaseManager
from src.a2a.bus import A2ABus, A2AIntent
from src.storage.okf_adapter import OKFAdapter
from src.p2p.crypto import NodeCryptoManager
from src.storage.github_sync import GitHubSyncManager
from src.storage.validators import InvalidIdentifierError, validate_safe_id
from src.app.auto_updater import AutoUpdateManager
from src.app.paths import get_bundle_dir, get_user_data_dir, is_system_installed

logger = logging.getLogger("mcp2p.api_bridge")

# TODO(maintainer): set to the deployed signaling/TURN relay URL once it exists; empty = live P2P disabled, async fallback only.
DEFAULT_SIGNALING_URL = ""


class ApiBridge:
    """Clase puente cuyos métodos son expuestos en window.pywebview.api en el frontend."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None) -> None:
        self.db = db_manager or DatabaseManager.get_instance()
        self.bus = A2ABus(db=self.db)
        self.okf = OKFAdapter()
        self.crypto = NodeCryptoManager()
        self.github = GitHubSyncManager(db_manager=self.db)
        self._updater = AutoUpdateManager(get_bundle_dir(), get_user_data_dir())
        self._window: Optional[Any] = None
        self._ensure_local_node()
        self.ensure_local_node_registered()
        self.active_project_uuid: Optional[str] = None
        self._ensure_active_project()

    def _ensure_local_node(self) -> None:
        default_node = os.environ.get("MCP2P_NODE_UUID", f"node_{platform.node().lower()[:12]}")
        default_user = os.environ.get("USER", os.environ.get("USERNAME", "developer"))
        # Si hay usuario de GitHub autenticado vía PAT, priorizarlo como identidad
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
            node_kind="human"
        )

    def _ensure_active_project(self) -> None:
        projects = self.db.list_projects()
        if projects:
            self.active_project_uuid = projects[0]["project_uuid"]

    def set_window(self, window: Any) -> None:
        """Asigna la referencia a la ventana principal de PyWebView."""
        self._window = window

    def set_ui_theme(self, theme: str) -> Dict[str, Any]:
        """Guarda la preferencia de tema (dark/light) y sincroniza el fondo nativo en Windows."""
        try:
            self.db.set_setting("ui_theme", theme)
            hex_color = "#f8fafc" if theme == "light" else "#0f1013"
            if platform.system() == "Windows" and self._window is not None:
                try:
                    form = getattr(self._window, "native", None)
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

    # ==========================
    # Gestión de Proyectos
    # ==========================

    def list_projects(self) -> Dict[str, Any]:
        """Lista todos los proyectos disponibles."""
        try:
            projects = self.db.list_projects()
            return {"success": True, "projects": projects, "active_uuid": self.active_project_uuid}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def create_project(
        self,
        name: str,
        code_repo_url: str = "",
        gov_repo_url: str = "",
        auto_publish: bool = False,
        reviewer_sla_hours: int = 24
    ) -> Dict[str, Any]:
        """Crea un nuevo proyecto con doble repositorio y rama huérfana, con opción de publicación autónoma a GitHub."""
        try:
            if not name.strip():
                return {"success": False, "error": "El nombre del proyecto es obligatorio"}
            proj = self.db.create_project(
                name=name.strip(),
                code_repo_url=code_repo_url,
                gov_repo_url=gov_repo_url,
                reviewer_sla_hours=reviewer_sla_hours
            )
            self.active_project_uuid = proj["project_uuid"]

            # Auto-publicación si se solicita y hay credenciales de GitHub disponibles
            if auto_publish:
                try:
                    self.publish_project_to_github(proj["project_uuid"], repo_target="both")
                except Exception as ce:
                    logger.warning(f"Auto-publish to GitHub omitted: {ce}")

            return {"success": True, "project": proj}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def select_project(self, project_uuid: str) -> Dict[str, Any]:
        """Cambia el contexto activo de la aplicación al proyecto seleccionado."""
        try:
            proj = self.db.get_project_by_uuid(project_uuid)
            if not proj:
                return {"success": False, "error": "Proyecto no encontrado"}
            self.active_project_uuid = project_uuid
            self.ensure_local_node_registered()
            return {"success": True, "project": proj}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def delete_project(self, project_uuid: str) -> Dict[str, Any]:
        """Elimina un proyecto y todas sus tareas/mensajes asociados en cascada."""
        try:
            success = self.db.delete_project(project_uuid)
            if not success:
                return {"success": False, "error": f"Proyecto no encontrado: {project_uuid}"}
            if self.active_project_uuid == project_uuid:
                self.active_project_uuid = None
                self._ensure_active_project()
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def generate_p2p_invite(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        """Genera un enlace y token de invitación P2P firmado criptográficamente para compartir el proyecto."""
        try:
            target_uuid = project_uuid or self.active_project_uuid
            if not target_uuid:
                self._ensure_active_project()
                target_uuid = self.active_project_uuid

            proj = self.db.get_project_by_uuid(target_uuid)
            if not proj:
                return {"success": False, "error": "Proyecto no encontrado"}

            local_node = os.environ.get("MCP2P_NODE_UUID", f"node_{platform.node().lower()[:12]}")
            gh_status = self.github.get_auth_status()

            payload = {
                "project_uuid": proj["project_uuid"],
                "name": proj["name"],
                "code_repo_url": proj.get("code_repo_url", ""),
                "gov_repo_url": proj.get("gov_repo_url", ""),
                "sender_node_uuid": local_node,
                "sender_github": gh_status.get("username", ""),
                "sender_pubkey": self.crypto.public_key_base64,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "rendezvous_nonce": secrets.token_hex(8)
            }

            token = self.crypto.create_signed_invite_token(payload)
            invite_url = f"mcp2p://join?token={token}"

            return {
                "success": True,
                "project_uuid": proj["project_uuid"],
                "project_name": proj["name"],
                "token": token,
                "invite_url": invite_url,
                "payload": payload
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_signaling_server_url(self) -> str:
        """Devuelve la URL del servidor de señalización WebRTC configurada (o el valor por defecto)."""
        return self.db.get_setting("p2p_signaling_url", default=DEFAULT_SIGNALING_URL)

    def set_signaling_server_url(self, url: str) -> Dict[str, Any]:
        """Configura la URL del servidor de señalización WebRTC, validando su esquema."""
        if url != "" and not (url.startswith("wss://") or url.startswith("ws://")):
            return {"success": False, "error": "La URL debe estar vacía o comenzar con ws:// o wss://"}
        self.db.set_setting("p2p_signaling_url", url)
        return {"success": True}

    def join_p2p_project(self, invite_str: str) -> Dict[str, Any]:
        """
        Se une a un proyecto P2P a partir de un enlace o token firmado criptográficamente.
        Si incluye URLs de GitHub, clona ambos repositorios (código y gobernanza),
        desacopla la rama huérfana de memoria e importa el estado de gobernanza OKF.
        """
        try:
            raw = invite_str.strip()
            token = raw
            if "token=" in raw:
                parsed = urllib.parse.urlparse(raw)
                query = urllib.parse.parse_qs(parsed.query)
                token = query.get("token", [""])[0]

            if not token:
                return {"success": False, "error": "Token de invitación P2P no válido"}

            is_valid, payload, err = self.crypto.decode_and_verify_invite_token(token)
            if not is_valid:
                return {"success": False, "error": err or "Firma criptográfica inválida"}

            p_uuid = payload.get("project_uuid")
            p_name = payload.get("name")
            if not p_uuid or not p_name:
                return {"success": False, "error": "El token no contiene los datos del proyecto requeridos"}

            try:
                validate_safe_id(p_uuid, "project_uuid")
            except InvalidIdentifierError as ve:
                return {"success": False, "error": str(ve)}

            # Verificar si ya existe localmente
            existing = self.db.get_project_by_uuid(p_uuid)
            if existing:
                self.active_project_uuid = p_uuid
                return {"success": True, "project": existing, "message": "Proyecto ya registrado. Se activó el contexto."}

            code_url = payload.get("code_repo_url", "").strip()
            gov_url = payload.get("gov_repo_url", "").strip()

            clean_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in p_name.lower())
            short_id = p_uuid.replace("proj_", "")[:8]
            base_dir = self.db.db_path.parent / "workspace" / f"{clean_name}_{short_id}"
            app_dir = base_dir / f"app_{clean_name}"
            gov_dir = base_dir / f"okf_{clean_name}_{short_id}"

            # Si hay repos remotos, clonar de forma segura sin inicializar vacíos
            if gov_url or code_url:
                # 1. Intentar aceptar invitaciones pendientes automáticamente en GitHub
                try:
                    self.github.accept_pending_invitations()
                except Exception as acc_e:
                    logger.debug(f"Auto-accept invitations omitido: {acc_e}")

                # 2. Clonar repositorio de gobernanza OKF
                if gov_url and not (gov_dir / ".git").exists():
                    res_gov = self.github.git_clone(gov_url, gov_dir)
                    if res_gov.get("status") == "pending_access":
                        return {
                            "success": False,
                            "status": "pending_access",
                            "error": "Acceso denegado a los repositorios de GitHub. Acepta la invitación de repositorio o verifica tus credenciales PAT.",
                            "project_uuid": p_uuid,
                            "sender_github": payload.get("sender_github")
                        }
                    if not res_gov.get("success"):
                        return {"success": False, "error": f"Fallo al clonar repositorio de gobernanza: {res_gov.get('error')}"}

                # 3. Clonar repositorio de código
                if code_url and not (app_dir / ".git").exists():
                    res_app = self.github.git_clone(code_url, app_dir)
                    if res_app.get("status") == "pending_access":
                        return {
                            "success": False,
                            "status": "pending_access",
                            "error": "Acceso denegado a los repositorios de GitHub. Acepta la invitación de repositorio o verifica tus credenciales PAT.",
                            "project_uuid": p_uuid,
                            "sender_github": payload.get("sender_github")
                        }
                    if not res_app.get("success"):
                        return {"success": False, "error": f"Fallo al clonar repositorio de código: {res_app.get('error')}"}

                # 4. Asegurar rama huérfana desacoplada en gobernanza
                if gov_dir.exists() and (gov_dir / ".git").exists():
                    try:
                        self.ensure_orphan_worktree(gov_dir, p_uuid)
                    except Exception as oe:
                        logger.warning(f"No se pudo inicializar orphan worktree tras clonar: {oe}")

                    # 5. Reconciliar OKF en base de datos local
                    try:
                        self.okf.reconcile_kanban_from_okf(gov_dir, p_uuid, self.db)
                    except Exception as re_e:
                        logger.warning(f"Error al reconciliar OKF tras clonar: {re_e}")

                # 6. Registrar proyecto en base de datos local
                now_str = datetime.now(timezone.utc).isoformat()
                p_sla = int(payload.get("reviewer_sla_hours") or 24)
                with self.db._write_lock, self.db.get_connection() as conn:
                    with conn:
                        conn.execute(
                            """
                            INSERT INTO projects (
                                project_uuid, name, base_path, code_repo_path, gov_repo_path,
                                code_repo_url, gov_repo_url, default_sla_hours, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                            """,
                            (
                                p_uuid,
                                p_name,
                                str(base_dir),
                                str(app_dir),
                                str(gov_dir),
                                code_url,
                                gov_url,
                                p_sla,
                                now_str,
                                now_str
                            )
                        )
                proj = self.db.get_project_by_uuid(p_uuid)
            else:
                # Modo local / sin repositorios remotos
                proj = self.db.create_project(
                    name=p_name,
                    code_repo_url=code_url,
                    gov_repo_url=gov_url,
                    project_uuid=p_uuid
                )

            self.active_project_uuid = p_uuid

            # Si viene un nodo emisor, registrarlo
            sender_node = payload.get("sender_node_uuid")
            if sender_node:
                self.db.register_or_update_node(
                    node_uuid=sender_node,
                    developer_name=payload.get("sender_github") or f"Peer {sender_node[:8]}",
                    agent_id=f"agent_{sender_node[:8]}",
                    reviewer_sla_hours=24,
                    status="online",
                    node_kind="human"
                )
                self.db.add_or_request_collaborator(
                    project_uuid=p_uuid,
                    node_uuid=sender_node,
                    github_username=payload.get("sender_github", ""),
                    public_key=payload.get("sender_pubkey", ""),
                    role="owner",
                    status="approved"
                )

            # Registrar también al nodo local como colaborador
            local_node = os.environ.get("MCP2P_NODE_UUID", f"node_{platform.node().lower()[:12]}")
            gh_status = self.github.get_auth_status()
            self.db.add_or_request_collaborator(
                project_uuid=p_uuid,
                node_uuid=local_node,
                github_username=gh_status.get("username", ""),
                public_key=self.crypto.public_key_base64,
                role="collaborator",
                status="approved"
            )

            # Disparar sincronización inicial de tres niveles
            try:
                self.sync_three_tiers(p_uuid)
            except Exception as se:
                logger.debug(f"Sync three tiers inicial pospuesta: {se}")

            return {
                "success": True,
                "project": proj,
                "message": "Conectado al proyecto P2P exitosamente y repositorios clonados"
            }
        except Exception as e:
            return {"success": False, "error": f"Error al unirse al proyecto P2P: {str(e)}"}

    # ==========================
    # Colaboradores & Aprobación HITL
    # ==========================

    def get_project_collaborators(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        """Obtiene la lista de colaboradores y solicitudes de un proyecto."""
        try:
            target_uuid = project_uuid or self.active_project_uuid
            if not target_uuid:
                self._ensure_active_project()
                target_uuid = self.active_project_uuid
            collaborators = self.db.get_project_collaborators(target_uuid)
            return {"success": True, "collaborators": collaborators}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def approve_collaborator(self, project_uuid: str, node_uuid: str) -> Dict[str, Any]:
        """
        Aprobación explícita (Human-in-the-Loop) de un colaborador por el dueño del proyecto.
        Actualiza la BD, agrega la clave a conventions/trusted_nodes.yaml, invita la cuenta
        en GitHub para ambos repositorios y realiza push de gobernanza.
        """
        try:
            res = self.db.approve_collaborator(project_uuid, node_uuid)
            if not res:
                return {"success": False, "error": "No se pudo actualizar el colaborador en la base de datos"}

            collabs = self.db.get_project_collaborators(project_uuid)
            collab = next((c for c in collabs if c.get("node_uuid") == node_uuid), None)
            proj = self.db.get_project_by_uuid(project_uuid)
            invitations_sent = []
            gov_commit_error = None

            if collab and proj:
                pub_key = collab.get("public_key")
                gh_user = collab.get("github_username")
                gov_path = Path(proj.get("gov_repo_path", ""))

                # 1. Registrar clave pública en conventions/trusted_nodes.yaml
                if pub_key and gov_path.exists():
                    try:
                        self.okf.add_trusted_node_pubkey(gov_path, node_uuid, pub_key, gh_user or "peer")
                    except Exception as okf_e:
                        logger.warning(f"No se pudo escribir en trusted_nodes.yaml: {okf_e}")

                # 2. Invitar usuario a los repositorios de GitHub
                if gh_user:
                    for repo_field in ("code_repo_url", "gov_repo_url"):
                        raw_url = proj.get(repo_field, "")
                        m = re.search(r"github\.com[/:]([^/]+)/([^/.]+)", raw_url)
                        if m:
                            owner, repo_name = m.group(1), m.group(2)
                            inv_res = self.github.add_repo_collaborator(owner, repo_name, gh_user)
                            invitations_sent.append({"repo": f"{owner}/{repo_name}", "result": inv_res})

                # 3. Commit y push en repo de gobernanza
                if gov_path.exists() and (gov_path / ".git").exists():
                    try:
                        creationflags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
                        subprocess.run(["git", "add", "."], cwd=gov_path, capture_output=True, creationflags=creationflags)
                        staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=gov_path, capture_output=True, creationflags=creationflags)
                        commit_ok = True
                        if staged.returncode != 0:
                            commit_res = subprocess.run(
                                ["git", "commit", "-m", f"chore(gov): authorize peer {node_uuid[:8]} (@{gh_user or 'node'}) in trusted_nodes.yaml"],
                                cwd=gov_path, capture_output=True, encoding="utf-8", errors="replace",
                                creationflags=creationflags
                            )
                            commit_ok = commit_res.returncode == 0
                            if not commit_ok:
                                gov_commit_error = (commit_res.stderr or commit_res.stdout or "").strip()[:300]
                                logger.warning(f"approve_collaborator: commit de trusted_nodes.yaml falló: {gov_commit_error}")
                        if commit_ok and self.github.has_remote(gov_path, "origin"):
                            self.github.git_push_branch(gov_path, branch="main", remote="origin")
                    except Exception as push_err:
                        logger.debug(f"Push de trusted_nodes omitido: {push_err}")

            return {"success": True, "invitations": invitations_sent, **({"gov_commit_error": gov_commit_error} if gov_commit_error else {})}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def reject_collaborator(self, project_uuid: str, node_uuid: str) -> Dict[str, Any]:
        """Rechaza la solicitud de un colaborador."""
        try:
            res = self.db.reject_collaborator(project_uuid, node_uuid)
            return {"success": res}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def remove_collaborator(self, project_uuid: str, node_uuid: str) -> Dict[str, Any]:
        """Elimina o revoca la membresía de un colaborador o agente del proyecto."""
        try:
            res = self.db.remove_collaborator(project_uuid, node_uuid)
            return {"success": res}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def update_project_sla(self, project_uuid: str, sla_hours: int) -> Dict[str, Any]:
        """Actualiza el período de revisión SLA por defecto del proyecto."""
        try:
            res = self.db.update_project_sla(project_uuid, sla_hours)
            return {"success": res, "sla_hours": sla_hours}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ==========================
    # Gobernanza OKF (ADRs & Skills)
    # ==========================

    def get_okf_overview(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        """Recupera la lista de ADRs y Skills del repositorio OKF para visualización."""
        try:
            target_uuid = project_uuid or self.active_project_uuid
            if not target_uuid:
                self._ensure_active_project()
                target_uuid = self.active_project_uuid

            proj = self.db.get_project_by_uuid(target_uuid)
            if not proj:
                return {"success": False, "error": "Proyecto no encontrado"}

            gov_path = proj.get("gov_repo_path")
            if gov_path:
                self.okf.ensure_okf_structure(gov_path, proj.get("name", "Proyecto"), target_uuid)

            adrs = self.okf.get_okf_adrs(gov_path)
            skills = self.okf.get_okf_skills(gov_path)
            validation = self.okf.validate_okf_integrity(gov_path)

            return {
                "success": True,
                "project_uuid": target_uuid,
                "adrs": adrs,
                "skills": skills,
                "validation": validation
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def repair_project_okf(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        """Repara o inicializa la estructura OKF faltante del proyecto especificado o activo."""
        try:
            target_uuid = project_uuid or self.active_project_uuid
            if not target_uuid:
                self._ensure_active_project()
                target_uuid = self.active_project_uuid

            proj = self.db.get_project_by_uuid(target_uuid)
            if not proj:
                return {"success": False, "error": "Proyecto no encontrado"}

            gov_path = proj.get("gov_repo_path")
            if not gov_path:
                return {"success": False, "error": "Ruta de gobernanza no configurada"}

            self.okf.ensure_okf_structure(gov_path, proj.get("name", "Proyecto"), target_uuid)
            report = self.okf.validate_okf_integrity(gov_path)
            return {"success": True, "report": report, "message": "Estructura OKF reparada y sincronizada"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ==========================
    # Conector MCP para Agentes (Claude / Cursor / Antigravity)
    # ==========================

    def get_mcp_config_snippet(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        """Genera el bloque JSON de conexión MCP y el Prompt Semilla para copiar al agente."""
        try:
            target_uuid = project_uuid or self.active_project_uuid
            if not target_uuid:
                self._ensure_active_project()
                target_uuid = self.active_project_uuid

            proj = self.db.get_project_by_uuid(target_uuid)
            p_name = proj["name"] if proj else "Proyecto MCP2P"

            # En el binario empaquetado (PyInstaller) sys.executable es Mcp2p.exe/Mcp2p,
            # que no acepta "-m <modulo>" porque no es un interprete de Python: el propio
            # binario debe invocarse con --mcp-server para que arranque el servidor MCP
            # en el mismo proceso. En modo desarrollo sys.executable sigue siendo el
            # python del venv, así que "-m src.mcp.server" funciona como antes.
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
                        }
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
                "agent_seed_prompt": agent_seed_prompt
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ==========================
    # Estado de GitHub
    # ==========================

    def get_github_status(self) -> Dict[str, Any]:
        """Verifica el estado de autenticación y usuario de GitHub."""
        try:
            status = self.github.get_auth_status()
            return {"success": True, "github": status}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def save_github_pat(self, token: str) -> Dict[str, Any]:
        """Valida y guarda un Personal Access Token (PAT) de GitHub."""
        try:
            return self.github.save_pat_token(token)
        except Exception as e:
            return {"success": False, "error": str(e)}

    def clear_github_pat(self) -> Dict[str, Any]:
        """Elimina el Personal Access Token (PAT) guardado."""
        try:
            return self.github.clear_pat_token()
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ==========================
    # Auto-actualización y reinicio silencioso
    # ==========================

    def start_auto_update(self, download_url: str, version: str) -> Dict[str, Any]:
        """Inicia la descarga en segundo plano del instalador de la nueva versión."""
        return self._updater.start_download(download_url, version)

    def get_update_progress(self) -> Dict[str, Any]:
        """Retorna el progreso porcentual y estado de la descarga."""
        return self._updater.get_progress()

    def apply_update_and_restart(self) -> Dict[str, Any]:
        """Aplica la actualización silenciosa y reinicia."""
        return self._updater.apply_update_and_restart()

    def get_install_mode(self) -> Dict[str, Any]:
        """Informa al frontend si esta instancia corre instalada a nivel de sistema
        (.deb en /opt, o en Program Files) o en modo portable, para que el modal de
        actualización ofrezca el asset correcto (.deb vs .tar.gz / .exe)."""
        mode = "system" if is_system_installed() else ("portable" if getattr(sys, "frozen", False) else "dev")
        return {"success": True, "platform": sys.platform, "mode": mode}

    def get_active_project(self) -> Dict[str, Any]:
        """Devuelve el proyecto actualmente activo."""
        if not self.active_project_uuid:
            self._ensure_active_project()
        proj = self.db.get_project_by_uuid(self.active_project_uuid) if self.active_project_uuid else None
        return {"success": True, "project": proj}

    # ==========================
    # Tablero Kanban por Proyecto
    # ==========================

    def get_kanban_board(self, status: Optional[str] = None) -> Dict[str, Any]:
        """Obtiene las tarjetas del proyecto activo."""
        try:
            if not self.active_project_uuid:
                self._ensure_active_project()
            cards = self.db.get_cards(project_uuid=self.active_project_uuid, status=status)
            return {"success": True, "cards": cards, "project_uuid": self.active_project_uuid}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def create_kanban_card(
        self,
        card_id: str,
        title: str,
        description: str = "",
        acceptance_criteria: str = "",
        assignee_node_uuid: str = ""
    ) -> Dict[str, Any]:
        """Crea una nueva tarjeta en el proyecto activo con criterios de aceptación opcionales.
        Si se indica assignee_node_uuid, nace ya asignada a ese colaborador aprobado."""
        try:
            if not self.active_project_uuid:
                self._ensure_active_project()
            if not card_id.strip() or not title.strip():
                return {"success": False, "error": "El ID y el título son obligatorios"}
            card = self.db.create_card(
                project_uuid=self.active_project_uuid,
                card_id=card_id.strip(),
                title=title.strip(),
                description=description.strip(),
                acceptance_criteria=acceptance_criteria.strip(),
                assignee_node_uuid=assignee_node_uuid.strip() or None
            )
            return {"success": True, "card": card}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def update_kanban_card_details(
        self,
        card_id: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        acceptance_criteria: Optional[str] = None,
        status: Optional[str] = None,
        branch_ref: Optional[str] = None,
        assignee_node_uuid: Optional[str] = None
    ) -> Dict[str, Any]:
        """Actualiza los detalles completos de una tarjeta: título, descripción, criterios de
        aceptación, estado, rama y asignación (None = no tocarla, '' = desasignar)."""
        try:
            if not self.active_project_uuid:
                self._ensure_active_project()
            card = self.db.update_card_details(
                project_uuid=self.active_project_uuid,
                card_id=card_id,
                title=title,
                description=description,
                acceptance_criteria=acceptance_criteria,
                status=status,
                branch_ref=branch_ref,
                assignee_node_uuid=assignee_node_uuid
            )
            return {"success": True, "card": card}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def claim_task_lease(self, card_id: str, node_uuid: str, duration_minutes: int = 30) -> Dict[str, Any]:
        """Reclama un lease atómico en el proyecto activo."""
        try:
            if not self.active_project_uuid:
                self._ensure_active_project()
            card = self.db.claim_card_lease(
                project_uuid=self.active_project_uuid,
                card_id=card_id,
                node_uuid=node_uuid,
                duration_minutes=duration_minutes
            )
            return {"success": True, "card": card}
        except PermissionError as pe:
            return {"success": False, "error": str(pe), "code": "LEASE_COLLISION"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def update_task_status(self, card_id: str, new_status: str, branch_ref: Optional[str] = None) -> Dict[str, Any]:
        """Actualiza el estado de una tarjeta en el proyecto activo."""
        try:
            if not self.active_project_uuid:
                self._ensure_active_project()
            card = self.db.update_card_status(
                project_uuid=self.active_project_uuid,
                card_id=card_id,
                new_status=new_status,
                branch_ref=branch_ref
            )
            return {"success": True, "card": card}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ==========================
    # Enjambre y Telemetría
    # ==========================

    def get_swarm_nodes(self) -> Dict[str, Any]:
        """Obtiene los nodos registrados del enjambre P2P."""
        try:
            nodes = self.db.get_swarm_nodes()
            return {"success": True, "nodes": nodes}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_a2a_feed(self, limit: int = 30) -> Dict[str, Any]:
        """Obtiene los eventos recientes del canal A2A del proyecto activo."""
        try:
            if not self.active_project_uuid:
                self._ensure_active_project()
            messages = self.db.get_recent_a2a_messages(project_uuid=self.active_project_uuid, limit=limit)
            return {"success": True, "messages": messages}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def send_a2a_signal(self, signal_type: str, data: Dict[str, Any], card_id: Optional[str] = None) -> Dict[str, Any]:
        """Envía una señal operativa a 0 tokens por el canal A2A."""
        try:
            if not self.active_project_uuid:
                self._ensure_active_project()
            local_node = os.environ.get("MCP2P_NODE_UUID", f"node_{platform.node().lower()[:12]}")
            packet = self.bus.send_informative(
                project_uuid=self.active_project_uuid,
                sender_node_uuid=local_node,
                signal_type=signal_type,
                data=data,
                card_id=card_id
            )
            return {"success": True, "packet": packet}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_a2a_session_turns(self, session_id: str) -> Dict[str, Any]:
        """Consulta los turnos del RFC dialéctico de una sesión."""
        try:
            if not self.active_project_uuid:
                self._ensure_active_project()
            turns = self.bus.get_session_turns(project_uuid=self.active_project_uuid, session_id=session_id)
            return {"success": True, "turns": turns}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def validate_project_okf(self) -> Dict[str, Any]:
        """Valida la integridad de la documentación OKF del proyecto activo."""
        try:
            if not self.active_project_uuid:
                self._ensure_active_project()
            proj = self.db.get_project_by_uuid(self.active_project_uuid)
            if not proj:
                return {"success": False, "error": "Proyecto activo no encontrado"}
            gov_path = proj.get("gov_repo_path")
            if not gov_path:
                return {"success": False, "error": "Ruta de gobernanza no configurada"}
            self.okf.ensure_okf_structure(gov_path, proj.get("name", "Proyecto"), self.active_project_uuid)
            report = self.okf.validate_okf_integrity(gov_path)
            return {"success": True, "report": report}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def write_memory_snapshot(self, snapshot_data: Dict[str, Any]) -> Dict[str, Any]:
        """Escribe un snapshot en la rama huérfana de memoria del proyecto activo."""
        try:
            if not self.active_project_uuid:
                self._ensure_active_project()
            proj = self.db.get_project_by_uuid(self.active_project_uuid)
            if not proj:
                return {"success": False, "error": "Proyecto activo no encontrado"}
            gov_path = proj.get("gov_repo_path")
            res = self.okf.write_orphan_memory_snapshot(
                gov_base_path=gov_path,
                project_uuid=self.active_project_uuid,
                snapshot_data=snapshot_data
            )
            return {"success": True, "result": res}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def read_memory_snapshot(self) -> Dict[str, Any]:
        """Lee el último snapshot de la rama huérfana del proyecto activo."""
        try:
            if not self.active_project_uuid:
                self._ensure_active_project()
            proj = self.db.get_project_by_uuid(self.active_project_uuid)
            if not proj:
                return {"success": False, "error": "Proyecto activo no encontrado"}
            gov_path = proj.get("gov_repo_path")
            mem = self.okf.read_orphan_memory_snapshot(
                gov_base_path=gov_path,
                project_uuid=self.active_project_uuid
            )
            return {"success": True, "snapshot": mem}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ==========================
    # Ramas Git y Consenso Dialéctico
    # ==========================

    def get_branches_and_consensus(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        """Obtiene el historial visual de ramas Git, commits y sesiones dialécticas de consenso A2A."""
        try:
            target_uuid = project_uuid or self.active_project_uuid
            if not target_uuid:
                self._ensure_active_project()
                target_uuid = self.active_project_uuid

            proj = self.db.get_project_by_uuid(target_uuid)
            if not proj:
                return {"success": False, "error": "Proyecto no encontrado"}

            code_repo = proj.get("code_repo_path")
            gov_repo = proj.get("gov_repo_path")

            git_app = self.github.get_git_branches_and_log(code_repo) if code_repo else {"branches": [], "commits": []}
            git_gov = self.github.get_git_branches_and_log(gov_repo) if gov_repo else {"branches": [], "commits": []}

            # Obtener tarjetas para enlaces bidireccionales
            cards = self.db.get_cards(target_uuid)

            # Sesiones dialécticas desde a2a_messages
            with self.db.get_connection() as conn:
                msgs = conn.execute(
                    "SELECT * FROM a2a_messages WHERE project_uuid = ? ORDER BY created_at ASC;",
                    (target_uuid,)
                ).fetchall()
                tickets = conn.execute(
                    "SELECT * FROM dispute_tickets WHERE project_uuid = ? ORDER BY created_at DESC;",
                    (target_uuid,)
                ).fetchall()

            sessions_map: Dict[str, Any] = {}
            for row in msgs:
                try:
                    payload = json.loads(row["payload_rfc_yaml"])
                except Exception:
                    payload = {"raw": row["payload_rfc_yaml"]}

                session_id = payload.get("session_id") or row["message_uuid"]
                if session_id not in sessions_map:
                    sessions_map[session_id] = {
                        "session_id": session_id,
                        "card_id": row["card_id"],
                        "component": payload.get("component", "General"),
                        "intent": payload.get("intent", ""),
                        "status": "IN_DISCUSSION",
                        "verdict_signal": payload.get("verdict_signal"),
                        "agreed_branch": None,
                        "turns": []
                    }

                turn_data = {
                    "turn": payload.get("turn", 1),
                    "stage": payload.get("stage", "PROPOSAL"),
                    "sender_node": row["sender_node_uuid"],
                    "verdict_signal": payload.get("verdict_signal"),
                    "critique": payload.get("critique"),
                    "alternative": payload.get("alternative"),
                    "rationale": payload.get("rationale"),
                    "diff_ref": payload.get("diff_ref"),
                    "final_decision": payload.get("final_decision"),
                    "synthesis_notes": payload.get("synthesis_notes"),
                    "timestamp": payload.get("timestamp") or row["created_at"]
                }
                sessions_map[session_id]["turns"].append(turn_data)

                if payload.get("verdict_signal"):
                    sessions_map[session_id]["verdict_signal"] = payload.get("verdict_signal")

                if payload.get("final_decision") == "AGREED":
                    sessions_map[session_id]["status"] = "AGREED"
                    sessions_map[session_id]["verdict_signal"] = sessions_map[session_id]["verdict_signal"] or "ACCEPT"
                    sessions_map[session_id]["agreed_branch"] = payload.get("agreed_branch") or payload.get("diff_ref")
                elif payload.get("final_decision") == "REJECTED":
                    sessions_map[session_id]["status"] = "REJECTED"
                    sessions_map[session_id]["verdict_signal"] = "REJECT"

            sessions_list = list(sessions_map.values())

            return {
                "success": True,
                "project_uuid": target_uuid,
                "git": git_app,
                "git_app": git_app,
                "git_gov": git_gov,
                "cards": cards,
                "sessions": sessions_list,
                "consensus_sessions": sessions_list,
                "disputes": [dict(t) for t in tickets]
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def git_fetch_action(self, project_uuid: Optional[str] = None, repo_target: str = "app") -> Dict[str, Any]:
        """Ejecuta git fetch en el repositorio de código o gobernanza del proyecto."""
        target_uuid = project_uuid or self.active_project_uuid
        proj = self.db.get_project_by_uuid(target_uuid)
        repo_path = proj.get("gov_repo_path") if repo_target == "gov" else proj.get("code_repo_path")
        if not repo_path:
            return {"success": False, "error": f"Repositorio ({repo_target}) no configurado"}
        return self.github.git_fetch(repo_path)

    def git_pull_action(self, project_uuid: Optional[str] = None, branch: Optional[str] = None, repo_target: str = "app") -> Dict[str, Any]:
        """Ejecuta git pull en el repositorio especificado."""
        target_uuid = project_uuid or self.active_project_uuid
        proj = self.db.get_project_by_uuid(target_uuid)
        repo_path = proj.get("gov_repo_path") if repo_target == "gov" else proj.get("code_repo_path")
        if not repo_path:
            return {"success": False, "error": f"Repositorio ({repo_target}) no configurado"}
        return self.github.git_pull(repo_path, branch=branch)

    def git_push_action(self, project_uuid: Optional[str] = None, branch: Optional[str] = None, repo_target: str = "app") -> Dict[str, Any]:
        """Publica la rama local en el remoto configurado."""
        target_uuid = project_uuid or self.active_project_uuid
        proj = self.db.get_project_by_uuid(target_uuid)
        repo_path = proj.get("gov_repo_path") if repo_target == "gov" else proj.get("code_repo_path")
        if not repo_path:
            return {"success": False, "error": f"Repositorio ({repo_target}) no configurado"}
        if not branch:
            b_info = self.github.get_git_branches_and_log(repo_path)
            branch = b_info.get("current_branch", "main")
        return self.github.git_push_branch(repo_path, branch=branch)

    def git_merge_action(self, project_uuid: Optional[str] = None, source_branch: str = "", target_branch: str = "main", repo_target: str = "app") -> Dict[str, Any]:
        """Fusiona una rama de feature a la rama destino."""
        target_uuid = project_uuid or self.active_project_uuid
        proj = self.db.get_project_by_uuid(target_uuid)
        repo_path = proj.get("gov_repo_path") if repo_target == "gov" else proj.get("code_repo_path")
        if not repo_path:
            return {"success": False, "error": f"Repositorio ({repo_target}) no configurado"}
        if not source_branch:
            return {"success": False, "error": "Debe especificar la rama origen a fusionar"}
        return self.github.git_merge(repo_path, source_branch=source_branch, target_branch=target_branch)

    def git_create_branch_action(self, project_uuid: Optional[str] = None, branch_name: str = "", repo_target: str = "app") -> Dict[str, Any]:
        """Crea una nueva rama en el repositorio seleccionado."""
        target_uuid = project_uuid or self.active_project_uuid
        proj = self.db.get_project_by_uuid(target_uuid)
        repo_path = proj.get("gov_repo_path") if repo_target == "gov" else proj.get("code_repo_path")
        if not repo_path or not branch_name.strip():
            return {"success": False, "error": "Nombre de rama inválido o repositorio inexistente"}
        return self.github.git_create_branch(repo_path, branch_name.strip())

    def set_project_remote_url(
        self,
        project_uuid: Optional[str] = None,
        remote_url: str = "",
        remote_name: str = "origin",
        repo_target: str = "app"
    ) -> Dict[str, Any]:
        """Configura o actualiza la URL remota del repositorio del proyecto (app o gov)."""
        target_uuid = project_uuid or self.active_project_uuid
        proj = self.db.get_project_by_uuid(target_uuid)
        if not proj:
            return {"success": False, "error": "Proyecto no encontrado"}
        repo_path = proj.get("gov_repo_path") if repo_target == "gov" else proj.get("code_repo_path")
        if not repo_path:
            return {"success": False, "error": f"Repositorio {repo_target} no configurado"}
        return self.github.set_remote_url(repo_path, url=remote_url, remote=remote_name)

    def ensure_orphan_worktree(self, gov_repo_path: str | Path, project_uuid: str) -> Path:
        """
        Garantiza la existencia y montaje del worktree aislado para la rama huérfana de memoria
        conversacional y telemetría (.git/orphan_worktree).
        """
        gov_path = Path(gov_repo_path).resolve()
        wt_path = gov_path / ".git" / "orphan_worktree"
        orphan_branch = f"orphan-conversa-memory-{project_uuid}"

        if wt_path.exists() and (wt_path / ".git").exists():
            return wt_path

        creationflags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0

        # 1. Comprobar si la rama huérfana ya existe localmente
        ref_check = subprocess.run(
            ["git", "show-ref", "--verify", f"refs/heads/{orphan_branch}"],
            cwd=str(gov_path),
            capture_output=True,
            creationflags=creationflags
        )

        # 2. Si no existe localmente, intentar traerla de origin si hay remoto
        if ref_check.returncode != 0 and self.github.has_remote(gov_path, "origin"):
            self.github.git_fetch(gov_path, "origin")
            fetch_orphan = subprocess.run(
                ["git", "fetch", "origin", f"{orphan_branch}:{orphan_branch}"],
                cwd=str(gov_path),
                capture_output=True,
                creationflags=creationflags
            )
            if fetch_orphan.returncode == 0:
                ref_check.returncode = 0

        # 3. Montar el worktree
        if ref_check.returncode == 0:
            wt_res = subprocess.run(
                ["git", "worktree", "add", str(wt_path), orphan_branch],
                cwd=str(gov_path),
                capture_output=True,
                creationflags=creationflags
            )
            if wt_res.returncode != 0 and not (wt_path / ".git").exists():
                err = (wt_res.stderr or b"").decode("utf-8", errors="replace").strip()
                raise RuntimeError(f"No se pudo montar el worktree huérfano en {wt_path}: {err[:300]}")
        else:
            # Crear rama huérfana desde cero desacoplada
            wt_res = subprocess.run(
                ["git", "worktree", "add", "--orphan", "-b", orphan_branch, str(wt_path)],
                cwd=str(gov_path),
                capture_output=True,
                creationflags=creationflags
            )
            if wt_res.returncode != 0 and not (wt_path / ".git").exists():
                err = (wt_res.stderr or b"").decode("utf-8", errors="replace").strip()
                raise RuntimeError(f"No se pudo montar el worktree huérfano en {wt_path}: {err[:300]}")
            if wt_path.exists():
                readme = wt_path / "README_CONVERSA.md"
                if not readme.exists():
                    readme.write_text(
                        f"# Memoria Conversacional Desacoplada\n\nUUID: `{project_uuid}`\nRama: `{orphan_branch}`\n",
                        encoding="utf-8"
                    )
                subprocess.run(["git", "add", "."], cwd=str(wt_path), capture_output=True, creationflags=creationflags)
                commit_res = subprocess.run(
                    ["git", "commit", "-m", f"init(orphan): initialize decoupled conversational memory for {project_uuid}"],
                    cwd=str(wt_path),
                    capture_output=True,
                    creationflags=creationflags
                )
                if commit_res.returncode != 0:
                    logger.warning(
                        "ensure_orphan_worktree: commit inicial del README falló en %s: %s",
                        wt_path, (commit_res.stderr or b"").decode("utf-8", errors="replace").strip()[:300]
                    )

        return wt_path

    def publish_project_to_github(
        self,
        project_uuid: Optional[str] = None,
        repo_target: str = "both",
        repo_name: Optional[str] = None,
        is_private: bool = True
    ) -> Dict[str, Any]:
        """
        Crea automáticamente los repositorios en GitHub vía API y publica las ramas activas
        y la memoria huérfana de forma autónoma.
        Soporta repo_target="both", "app" o "gov".
        """
        target_uuid = project_uuid or self.active_project_uuid
        if not target_uuid:
            self._ensure_active_project()
            target_uuid = self.active_project_uuid

        proj = self.db.get_project_by_uuid(target_uuid)
        if not proj:
            return {"success": False, "error": "Proyecto no encontrado"}

        clean_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in proj.get("name", "proyecto").lower())

        if repo_target == "both":
            app_path = proj.get("code_repo_path")
            app_name = f"mcp2p-{clean_name}"
            app_res = self.github.auto_create_and_publish_repo(
                repo_path=app_path,
                repo_name=app_name,
                description=f"Repositorio de código local-first para {proj.get('name')}",
                private=is_private
            )

            gov_path = proj.get("gov_repo_path")
            gov_name = f"mcp2p-{clean_name}-okf"
            gov_res = self.github.auto_create_and_publish_repo(
                repo_path=gov_path,
                repo_name=gov_name,
                description=f"Repositorio de gobernanza OKF para {proj.get('name')}",
                private=is_private
            )

            orphan_res = {"success": False, "error": "No ejecutado"}
            if gov_path and Path(gov_path).exists():
                try:
                    wt_path = self.ensure_orphan_worktree(gov_path, target_uuid)
                    orphan_branch = f"orphan-conversa-memory-{target_uuid}"
                    orphan_res = self.github.git_push_branch(
                        gov_path,
                        branch=orphan_branch,
                        remote="origin"
                    )
                except Exception as oe:
                    orphan_res = {"success": False, "error": str(oe)}

            with self.db._write_lock, self.db.get_connection() as conn:
                with conn:
                    if app_res.get("success") and app_res.get("repo_url"):
                        conn.execute("UPDATE projects SET code_repo_url = ? WHERE project_uuid = ?;", (app_res["repo_url"], target_uuid))
                    if gov_res.get("success") and gov_res.get("repo_url"):
                        conn.execute("UPDATE projects SET gov_repo_url = ? WHERE project_uuid = ?;", (gov_res["repo_url"], target_uuid))

            overall_success = app_res.get("success", False) and gov_res.get("success", False)
            return {
                "success": overall_success,
                "app": app_res,
                "gov": gov_res,
                "orphan": orphan_res,
                "repo_url": app_res.get("repo_url") or gov_res.get("repo_url"),
                "branch": app_res.get("branch", "main"),
                "message": "Repositorios de código, gobernanza y memoria conversacional publicados con éxito" if overall_success else "Publicación parcial completada"
            }

        repo_path = proj.get("gov_repo_path") if repo_target == "gov" else proj.get("code_repo_path")
        if not repo_path:
            return {"success": False, "error": f"Ruta del repositorio ({repo_target}) no configurada"}

        if not repo_name:
            if repo_target == "gov":
                target_repo_name = f"mcp2p-{clean_name}-okf"
            else:
                target_repo_name = f"mcp2p-{clean_name}"
        else:
            target_repo_name = repo_name.strip()

        desc = f"Repositorio autónomo local-first para {proj.get('name')} ({repo_target.upper()})"
        res = self.github.auto_create_and_publish_repo(
            repo_path=repo_path,
            repo_name=target_repo_name,
            description=desc,
            private=is_private
        )

        if res.get("success") and res.get("repo_url"):
            with self.db._write_lock, self.db.get_connection() as conn:
                with conn:
                    if repo_target == "gov":
                        conn.execute("UPDATE projects SET gov_repo_url = ? WHERE project_uuid = ?;", (res["repo_url"], target_uuid))
                        try:
                            self.ensure_orphan_worktree(repo_path, target_uuid)
                            orphan_branch = f"orphan-conversa-memory-{target_uuid}"
                            self.github.git_push_branch(repo_path, branch=orphan_branch, remote="origin")
                        except Exception:
                            pass
                    else:
                        conn.execute("UPDATE projects SET code_repo_url = ? WHERE project_uuid = ?;", (res["repo_url"], target_uuid))

        return res

    def sync_three_tiers(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        """
        Sincronización determinista de tres niveles (Three Tiers) para colaboración P2P autónoma:
        1. Huérfana (A2A & Diálogos): Vuelca outbox local a JSONL por nodo, sincroniza rama y lee eventos remotos.
        2. OKF (Gobernanza): Pull --rebase de gov/main y reconciliación de Kanban/conventions a SQLite.
        3. App (Código): Fetch de app/origin, validación de worktrees y PRs.
        """
        target_uuid = project_uuid or self.active_project_uuid
        if not target_uuid:
            self._ensure_active_project()
            target_uuid = self.active_project_uuid

        proj = self.db.get_project_by_uuid(target_uuid)
        if not proj:
            return {"success": False, "error": "Proyecto no encontrado para sincronización"}

        creationflags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
        local_node = os.environ.get("MCP2P_NODE_UUID", f"node_{platform.node().lower()[:12]}")
        gov_path = Path(proj.get("gov_repo_path", "")).resolve()
        app_path = Path(proj.get("code_repo_path", "")).resolve()

        # ==========================================
        # Nivel 1: Memoria Huérfana y Eventos A2A
        # ==========================================
        tier1_res = {"flushed": 0, "imported": 0, "status": "ok"}
        events_committed = False
        try:
            wt_path = self.ensure_orphan_worktree(gov_path, target_uuid)
            orphan_branch = f"orphan-conversa-memory-{target_uuid}"

            # 1.1 Volcar eventos pendientes de outbox_events a archivo append-only por nodo
            pending_events = self.db.get_pending_outbox_events(target_uuid, limit=500)
            if pending_events:
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                events_dir = wt_path / "nodes" / local_node / "events"
                events_dir.mkdir(parents=True, exist_ok=True)
                events_file = events_dir / f"{today}.jsonl"
                pre_size = events_file.stat().st_size if events_file.exists() else 0

                with events_file.open("a", encoding="utf-8") as f:
                    for ev in pending_events:
                        evt_uuid = ev["event_uuid"]
                        intent = "INFORMATIVE"
                        card_id = None
                        with self.db.get_connection() as conn:
                            msg_row = conn.execute("SELECT intent, card_id FROM a2a_messages WHERE message_uuid = ?;", (evt_uuid,)).fetchone()
                            if msg_row:
                                intent = msg_row["intent"]
                                card_id = msg_row["card_id"]

                        line_data = {
                            "event_uuid": evt_uuid,
                            "project_uuid": ev["project_uuid"],
                            "node_uuid": ev["node_uuid"],
                            "event_type": ev["event_type"],
                            "payload_raw": ev["payload_raw"],
                            "created_at": ev["created_at"],
                            "intent": intent,
                            "card_id": card_id
                        }
                        f.write(json.dumps(line_data, ensure_ascii=False) + "\n")

                subprocess.run(["git", "add", "."], cwd=str(wt_path), capture_output=True, creationflags=creationflags)
                commit_res = subprocess.run(
                    ["git", "commit", "-m", f"sync(orphan): flush {len(pending_events)} events from node {local_node}"],
                    cwd=str(wt_path), capture_output=True, encoding="utf-8", errors="replace",
                    creationflags=creationflags
                )
                if commit_res.returncode == 0:
                    events_committed = True
                    tier1_res["flushed"] = len(pending_events)
                else:
                    err = (commit_res.stderr or commit_res.stdout or "").strip()
                    tier1_res["status"] = f"commit_failed: {err[:300]}"
                    logger.warning(f"sync_three_tiers: commit de outbox falló, eventos quedan pendientes: {err[:300]}")
                    with events_file.open("r+b") as tf:
                        tf.truncate(pre_size)

            # 1.2 Pull con rebase y push al remoto si existe
            if self.github.has_remote(gov_path, "origin"):
                self.github.git_pull(wt_path, remote="origin", branch=orphan_branch, rebase=True)
                self.github.git_push_branch(wt_path, branch=orphan_branch, remote="origin")

            # 1.3 Importar eventos JSONL de otros nodos a SQLite de forma idempotente
            imported_count = 0
            nodes_base = wt_path / "nodes"
            if nodes_base.is_dir():
                for node_folder in nodes_base.iterdir():
                    if not node_folder.is_dir():
                        continue
                    ev_folder = node_folder / "events"
                    if not ev_folder.is_dir():
                        continue
                    for jf in ev_folder.glob("*.jsonl"):
                        try:
                            with jf.open("r", encoding="utf-8") as f:
                                for line in f:
                                    line_str = line.strip()
                                    if not line_str:
                                        continue
                                    evt = json.loads(line_str)
                                    evt_uuid = evt.get("event_uuid")
                                    if not evt_uuid:
                                        continue

                                    with self.db.get_connection() as conn:
                                        exists = conn.execute("SELECT 1 FROM a2a_messages WHERE message_uuid = ?;", (evt_uuid,)).fetchone()
                                    if not exists:
                                        self.db.log_a2a_message(
                                            project_uuid=target_uuid,
                                            message_uuid=evt_uuid,
                                            sender_node_uuid=evt.get("node_uuid", "unknown"),
                                            intent=evt.get("intent", "INFORMATIVE"),
                                            payload_rfc_yaml=evt.get("payload_raw", "{}"),
                                            card_id=evt.get("card_id")
                                        )
                                        self.db.enqueue_outbox_event(
                                            project_uuid=target_uuid,
                                            node_uuid=evt.get("node_uuid", "unknown"),
                                            event_type=evt.get("event_type", "A2A_REPLICATED"),
                                            payload_raw=evt.get("payload_raw", "{}"),
                                            event_uuid=evt_uuid
                                        )
                                        self.db.update_outbox_sync_status(evt_uuid, "acked")
                                        imported_count += 1
                                        try:
                                            self.bus._dispatch_local(evt)
                                        except Exception:
                                            pass
                        except Exception as jf_err:
                            logger.debug(f"Error procesando {jf}: {jf_err}")

            if events_committed:
                for ev in pending_events:
                    self.db.update_outbox_sync_status(ev["event_uuid"], "acked")

            tier1_res["imported"] = imported_count
        except Exception as t1_err:
            tier1_res["status"] = f"error: {t1_err}"

        # ==========================================
        # Nivel 2: Repositorio de Gobernanza OKF
        # ==========================================
        tier2_res = {"status": "ok"}
        try:
            if gov_path.exists() and self.github.has_remote(gov_path, "origin"):
                pull_gov = self.github.git_pull(gov_path, remote="origin", branch="main", rebase=True)
                tier2_res["pull"] = pull_gov.get("success", False)

            if gov_path.exists():
                reconcile_res = self.okf.reconcile_kanban_from_okf(gov_path, target_uuid, self.db)
                tier2_res["reconciled"] = reconcile_res
        except Exception as t2_err:
            tier2_res["status"] = f"error: {t2_err}"

        # ==========================================
        # Nivel 3: Repositorio de Código de Aplicación
        # ==========================================
        tier3_res = {"status": "ok"}
        try:
            if app_path.exists() and self.github.has_remote(app_path, "origin"):
                fetch_app = self.github.git_fetch(app_path, remote="origin")
                tier3_res["fetch"] = fetch_app.get("success", False)
        except Exception as t3_err:
            tier3_res["status"] = f"error: {t3_err}"

        return {
            "success": True,
            "project_uuid": target_uuid,
            "tier1_orphan": tier1_res,
            "tier2_okf": tier2_res,
            "tier3_app": tier3_res,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    def get_project_sync_pulse(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        """Obtiene la huella reactiva de cambios en tiempo real del proyecto."""
        target_uuid = project_uuid or self.active_project_uuid
        if not target_uuid:
            return {"success": False, "error": "Sin proyecto activo", "fingerprint": ""}
        return self.db.get_project_sync_fingerprint(target_uuid)

    def ensure_local_node_registered(self) -> None:
        """Asegura el registro inicial del nodo local principal en SQLite."""
        local_node = os.environ.get("MCP2P_NODE_UUID", f"node_{platform.node().lower()[:12]}")
        try:
            self.db.register_or_update_node(
                node_uuid=local_node,
                developer_name="Desarrollador Local (Tú)",
                agent_id="agent_local_developer",
                reviewer_sla_hours=24,
                status="online",
                node_kind="human"
            )
        except Exception:
            pass

    def get_mcp_and_agents_status(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        """Obtiene el estado de la pasarela MCP local y los agentes conectados al enjambre (lectura pura)."""
        target_uuid = project_uuid or self.active_project_uuid
        local_node = os.environ.get("MCP2P_NODE_UUID", f"node_{platform.node().lower()[:12]}")
        nodes = self.db.get_swarm_nodes()
        # Herramientas del servidor MCP expuestas
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
            {"name": "get_project_context", "description": "Aislamiento de rutas y contexto del proyecto"}
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
                "status": "online"
            },
            "connected_agents": nodes
        }

    # ==========================
    # Árbol y Visor OKF en Crudo
    # ==========================

    def get_okf_raw_tree(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        """Obtiene el árbol completo de carpetas y archivos en crudo de docs/okf."""
        try:
            target_uuid = project_uuid or self.active_project_uuid
            if not target_uuid:
                self._ensure_active_project()
                target_uuid = self.active_project_uuid

            proj = self.db.get_project_by_uuid(target_uuid)
            if not proj:
                return {"success": False, "error": "Proyecto no encontrado"}

            gov_path = proj.get("gov_repo_path")
            if not gov_path:
                return {"success": False, "error": "Ruta de gobernanza no configurada"}

            self.okf.ensure_okf_structure(gov_path, proj.get("name", "Proyecto"), target_uuid)
            docs_okf = Path(gov_path) / "docs" / "okf"

            tree = []
            if docs_okf.is_dir():
                for root, dirs, files in os.walk(docs_okf):
                    rel_root = os.path.relpath(root, docs_okf)
                    for f in sorted(files):
                        rel_path = f if rel_root == "." else os.path.join(rel_root, f)
                        full_p = Path(root) / f
                        tree.append({
                            "path": rel_path,
                            "name": f,
                            "folder": rel_root if rel_root != "." else "",
                            "size_bytes": full_p.stat().st_size if full_p.exists() else 0
                        })

            return {"success": True, "tree": tree, "base_path": str(docs_okf)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def read_okf_file(self, project_uuid: Optional[str] = None, file_path: str = "") -> Dict[str, Any]:
        """Lee el contenido en crudo de un archivo de docs/okf."""
        try:
            target_uuid = project_uuid or self.active_project_uuid
            if not target_uuid:
                self._ensure_active_project()
                target_uuid = self.active_project_uuid

            proj = self.db.get_project_by_uuid(target_uuid)
            if not proj:
                return {"success": False, "error": "Proyecto no encontrado"}

            gov_path = proj.get("gov_repo_path")
            docs_okf = Path(gov_path) / "docs" / "okf"
            target_file = (docs_okf / file_path).resolve()

            # Evitar Path Traversal
            if not str(target_file).startswith(str(docs_okf.resolve())):
                return {"success": False, "error": "Acceso denegado fuera de docs/okf"}

            if not target_file.is_file():
                return {"success": False, "error": f"Archivo no encontrado: {file_path}"}

            content = target_file.read_text(encoding="utf-8", errors="ignore")
            return {
                "success": True,
                "file_path": file_path,
                "name": target_file.name,
                "content": content
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_system_info(self) -> Dict[str, Any]:
        """Información del sistema local y del proyecto activo."""
        active_proj = self.db.get_project_by_uuid(self.active_project_uuid) if self.active_project_uuid else None
        return {
            "success": True,
            "os": platform.system(),
            "os_release": platform.release(),
            "python_version": sys.version.split()[0],
            "db_path": str(self.db.db_path),
            "wal_mode": True,
            "heuristics": "Nielsen 10 + Hicks Law",
            "active_node": os.environ.get("MCP2P_NODE_UUID", f"node_{platform.node().lower()[:12]}"),
            "active_project": active_proj
        }

    # ==========================
    # Bandeja HITL de Aprobación Humana (Etapa 4)
    # ==========================

    def _get_blind_options_mapping(self, ticket_id: str, viewer_node: str) -> Tuple[Dict[str, str], Dict[str, str]]:
        """
        Calcula una permutación pseudoaleatoria determinista para el par (ticket_id, viewer_node) (EGM-07).
        Devuelve (alias_to_canonical, canonical_to_alias).
        """
        seed = hashlib.sha256(f"{ticket_id}:{viewer_node}".encode("utf-8")).digest()
        shuffled = random.Random(seed).sample(["OPTION_A", "OPTION_B", "THIRD_WAY_C"], 3)
        alias_names = ["ALFA", "BETA", "GAMMA"]
        alias_to_canonical = {alias_names[i]: shuffled[i] for i in range(3)}
        canonical_to_alias = {shuffled[i]: alias_names[i] for i in range(3)}
        return alias_to_canonical, canonical_to_alias

    def _anonymize_text(self, text: str, canonical_to_alias: Dict[str, str]) -> str:
        """
        Reescribe referencias a opciones y roles dialécticos en propuestas y argumentos cualitativos (EGM-07).
        """
        if not text:
            return ""
        alias_a = canonical_to_alias.get("OPTION_A", "ALFA")
        alias_b = canonical_to_alias.get("OPTION_B", "BETA")
        alias_c = canonical_to_alias.get("THIRD_WAY_C", "GAMMA")
        patterns = [
            (r"\bOPTION_A\b", f"Opción {alias_a}"),
            (r"\bOPTION_B\b", f"Opción {alias_b}"),
            (r"\bTHIRD_WAY_C\b", f"Opción {alias_c}"),
            (r"\bOpci[oó]n\s+A\b", f"Opción {alias_a}"),
            (r"\bOpci[oó]n\s+B\b", f"Opción {alias_b}"),
            (r"\bOpci[oó]n\s+C\b", f"Opción {alias_c}"),
            (r"\bTesis\s+A\b", f"Propuesta {alias_a}"),
            (r"\bAnt[ií]tesis\s+B\b", f"Propuesta {alias_b}"),
            (r"\bS[ií]ntesis\s+C\b", f"Propuesta {alias_c}"),
            (r"\bTesis\b", f"Propuesta {alias_a}"),
            (r"\bAnt[ií]tesis\b", f"Propuesta {alias_b}"),
            (r"\bS[ií]ntesis\b", f"Propuesta {alias_c}"),
            (r"\bTercera\s+V[ií]a\b", f"Propuesta {alias_c}"),
        ]
        res = text
        for pat, repl in patterns:
            res = re.sub(pat, repl, res, flags=re.IGNORECASE)
        return res

    def get_pending_human_approvals(
        self,
        project_uuid: Optional[str] = None,
        viewer_node: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Recupera en una sola llamada todos los elementos pendientes de aprobación humana:
        1. Pull Requests con recomendación del agente y reloj de tiempo de gracia.
        2. Decisiones técnicas pendientes de firma Ed25519 o promoción.
        3. Tickets de Juicio de Honor donde falta el voto humano (peso 2.0).
        """
        try:
            target_uuid = project_uuid or self.active_project_uuid
            if not target_uuid:
                self._ensure_active_project()
                target_uuid = self.active_project_uuid

            proj = self.db.get_project_by_uuid(target_uuid)
            if not proj:
                return {"success": False, "error": "Proyecto no encontrado"}

            gov_path = Path(proj["gov_repo_path"])
            app_path = Path(proj["code_repo_path"])
            local_node = viewer_node or os.environ.get("MCP2P_NODE_UUID", f"node_{platform.node().lower()[:12]}")

            # 1. Decisiones técnicas en docs/okf/architecture/decisions/
            decisions = []
            docs_base = gov_path / "docs" / "okf" if (gov_path / "docs" / "okf").exists() else gov_path
            dec_dir = docs_base / "architecture" / "decisions"
            if dec_dir.is_dir():
                for f in sorted(dec_dir.glob("*.md")):
                    txt = f.read_text(encoding="utf-8", errors="ignore")
                    item_id = f.stem
                    id_match = re.search(r"^id:\s*([^\n\r]+)", txt, re.MULTILINE)
                    if id_match:
                        item_id = id_match.group(1).strip()
                    elif "-" in f.stem:
                        parts = f.stem.split("-")
                        if len(parts) >= 2 and parts[0].isalpha() and parts[1].isdigit():
                            item_id = f"{parts[0]}-{parts[1]}"
                    title_match = re.search(r"^#\s+(.+)$", txt, re.MULTILINE)
                    clean_title = title_match.group(1).strip() if title_match else f.stem
                    related_cards = re.findall(r"(TASK-\d+)", txt)
                    decisions.append({
                        "file_name": f.name,
                        "item_id": item_id,
                        "title": clean_title,
                        "excerpt": txt[:360],
                        "full_content": txt,
                        "related_cards": list(dict.fromkeys(related_cards)),
                        "status": "pending_approval"
                    })

            # 2. Juicios de Honor abiertos en SQLite (Con 3 Fases Dialécticas + Matriz Anti-Sycophancy)
            disputes = []
            collabs = self.db.get_project_collaborators(target_uuid)
            all_nodes = [c["node_uuid"] for c in collabs if c.get("status") == "approved"]
            if not all_nodes:
                all_nodes = [n["node_uuid"] for n in self.db.get_swarm_nodes()]

            with self.db.get_connection() as conn:
                cur = conn.execute(
                    """
                    SELECT * FROM dispute_tickets 
                    WHERE project_uuid = ? 
                      AND (status = 'open' 
                           OR (status = 'challenged' AND resolution_mode = 'awaiting_external_arbiter' AND arbiter_node_uuid = ?))
                    ORDER BY created_at DESC;
                    """,
                    (target_uuid, local_node)
                )
                rows = cur.fetchall()

            for row in rows:
                t = dict(row)
                t_id = t["ticket_id"]
                # EGM-01: Registrar entrega del ticket al nodo local
                now_iso = datetime.now(timezone.utc).isoformat()
                try:
                    self.db.record_vote_delivery(t_id, local_node, now_iso)
                except Exception:
                    pass

                summary = self.db.get_jury_votes_summary(t_id)
                raw_human_votes = summary.get("human_votes", [])
                has_voted = any(hv.get("node_uuid") == local_node for hv in raw_human_votes)

                # NEW-04: Respetar propuesta C real o mantenerla vacía si no existe
                proposal_c = t.get("proposal_c_ref") or ""

                raw_matrix = t.get("jury_matrix_json") or "[]"
                try:
                    matrix_list = json.loads(raw_matrix) if raw_matrix else []
                except Exception:
                    matrix_list = []

                contenders = [c for c in [t.get("contender_a_node"), t.get("contender_b_node")] if c]
                arbiter_uuid = t.get("arbiter_node_uuid")
                h_eligible = self.bus.eligible_humans_count(target_uuid, contenders, arbiter_node_uuid=arbiter_uuid)

                if not matrix_list:
                    gen_matrix = self.bus.jury_engine.generate_neutral_jury_matrix(
                        eligible_humans_count=h_eligible,
                        ticket_id=t_id,
                        seed=t_id
                    )
                    matrix_list = gen_matrix.get("evaluators", [])
                    self.db.update_dispute_synthesis_and_matrix(
                        ticket_id=t_id,
                        proposal_c_ref=proposal_c,
                        jury_matrix=matrix_list
                    )

                # Mapeo determinista de voto ciego (EGM-07)
                alias_to_canonical, canonical_to_alias = self._get_blind_options_mapping(t_id, local_node)

                # Construir mayéutica de subagentes
                agent_scores = summary.get("agent_scores", [])
                mayeutic_discussions = []
                grouped_by_juror: Dict[str, List[Dict[str, Any]]] = {}
                for sc in agent_scores:
                    j_id = sc.get("juror_id", "subagent_neutral")
                    grouped_by_juror.setdefault(j_id, []).append(sc)

                for idx, m_spec in enumerate(matrix_list):
                    real_jid = m_spec.get("juror_id", f"subagent_neutral_{idx+1}")
                    j_evals = grouped_by_juror.get(real_jid, [])
                    rationale_text = ""
                    for ev in j_evals:
                        if ev.get("rationale_summary"):
                            rationale_text = ev["rationale_summary"]
                            break

                    is_pending = not bool(rationale_text)
                    if is_pending:
                        display_text = "⏳ Pendiente de evaluación por el subagente jurado."
                    else:
                        display_text = self._anonymize_text(rationale_text, canonical_to_alias) if not has_voted else rationale_text

                    juror_display_id = f"Jurado {idx + 1}" if not has_voted else real_jid
                    role_display = "EVALUADOR_NEUTRAL" if not has_voted else m_spec.get("role", "NEUTRAL_MATRIX_JUROR")

                    mayeutic_entry: Dict[str, Any] = {
                        "juror_id": juror_display_id,
                        "role": role_display,
                        "focus_vectors": m_spec.get("focus_vectors", []),
                        "focus_labels": m_spec.get("focus_labels", []),
                        "focus_count": m_spec.get("focus_count", 0),
                        "pending": is_pending,
                        "mayeutic_argument": display_text,
                        "scores_hidden_until_human_vote": not has_voted
                    }
                    if has_voted:
                        mayeutic_entry["temperature"] = m_spec.get("temperature", 0.2)
                        mayeutic_entry["temperature_profile"] = m_spec.get("temperature_profile", "analytical_strict")
                        mayeutic_entry["revealed_scores"] = j_evals
                    mayeutic_discussions.append(mayeutic_entry)

                voter_universe = self.bus.jury_engine.resolve_voter_universe(all_nodes, contenders, arbiter_node_uuid=arbiter_uuid)

                # Construir options_view (EGM-07)
                canonical_proposals = {
                    "OPTION_A": t.get("proposal_a_ref", ""),
                    "OPTION_B": t.get("proposal_b_ref", ""),
                    "THIRD_WAY_C": proposal_c
                }
                options_view = []
                for alias in ["ALFA", "BETA", "GAMMA"]:
                    canon = alias_to_canonical[alias]
                    raw_prop = canonical_proposals.get(canon, "")
                    is_available = True if (canon != "THIRD_WAY_C" or bool(proposal_c)) else False
                    opt_entry: Dict[str, Any] = {
                        "alias_id": alias,
                        "alias_label": f"Opción {alias}",
                        "text": self._anonymize_text(raw_prop, canonical_to_alias) if not has_voted else raw_prop,
                        "is_available": is_available
                    }
                    if has_voted or t.get("status") != "open":
                        opt_entry["canonical_option"] = canon
                        opt_entry["author"] = t.get("contender_a_node") if canon == "OPTION_A" else (t.get("contender_b_node") if canon == "OPTION_B" else "Subagente Sintetizador")
                    options_view.append(opt_entry)

                t["options_view"] = options_view
                t["has_voted"] = has_voted
                t["jury_matrix"] = matrix_list
                t["mayeutic_discussions"] = mayeutic_discussions
                t["voter_universe"] = voter_universe

                if not has_voted and t.get("status") == "open":
                    # Redacción estricta antes de votar (EGM-07 / NEW-05)
                    t["votes_summary"] = {
                        "human_votes": {
                            "human_votes_cast": len(raw_human_votes),
                            "human_votes_expected": h_eligible
                        },
                        "agent_scores_count": len(agent_scores),
                        "bias_protection": "Voto ciego activado: opciones anonimizadas con alias estables. Puntuaciones ocultas hasta emitir tu voto."
                    }
                    # Ocultar claves canónicas que filtrarían autoría o texto crudo sin anonimizar
                    t.pop("proposal_a_ref", None)
                    t.pop("proposal_b_ref", None)
                    t.pop("proposal_c_ref", None)
                    t.pop("contender_a_node", None)
                    t.pop("contender_b_node", None)
                    t.pop("jury_matrix_json", None)
                else:
                    t["votes_summary"] = summary
                    t["reveal_map"] = {
                        alias: {
                            "canonical_option": alias_to_canonical[alias],
                            "author": t.get("contender_a_node") if alias_to_canonical[alias] == "OPTION_A" else (t.get("contender_b_node") if alias_to_canonical[alias] == "OPTION_B" else "Subagente Sintetizador"),
                            "label": "Tesis (A)" if alias_to_canonical[alias] == "OPTION_A" else ("Antítesis (B)" if alias_to_canonical[alias] == "OPTION_B" else "Síntesis (C)")
                        }
                        for alias in ["ALFA", "BETA", "GAMMA"]
                    }

                disputes.append(t)

            # 3. Pull requests / Tareas en Revisión pendientes con detalle completo
            prs = []
            cards_in_review = self.db.get_cards(target_uuid, status="in_review")
            git_app_info = self.github.get_git_branches_and_log(app_path) if app_path.exists() else {"commits": [], "branches": []}
            all_commits = git_app_info.get("commits", [])

            for c in cards_in_review:
                c_id = c["card_id"]
                b_ref = c.get("branch_ref") or ""
                related_commits = [
                    cm for cm in all_commits
                    if c_id.lower() in (cm.get("message", "") + " " + cm.get("decorations", "")).lower()
                    or (b_ref and b_ref.lower() in cm.get("decorations", "").lower())
                ][:6]
                if not related_commits and all_commits:
                    related_commits = all_commits[:3]

                prs.append({
                    "card_id": c_id,
                    "title": c["title"],
                    "description": c.get("description", ""),
                    "acceptance_criteria": c.get("acceptance_criteria", ""),
                    "branch_ref": b_ref,
                    "assignee_node_uuid": c.get("assignee_node_uuid"),
                    "status": c.get("status"),
                    "commits": related_commits,
                    "updated_at": c.get("updated_at")
                })

            # 4. Histórico y Trazabilidad de Decisiones y Juicios Ratificados (Precedentes OKF)
            history_precedents = []
            docs_okf = gov_path / "docs" / "okf" if (gov_path / "docs" / "okf").exists() else gov_path
            precedents_file = docs_okf / "architecture" / "precedents_index.yaml"
            if precedents_file.exists():
                try:
                    p_index = yaml.safe_load(precedents_file.read_text(encoding="utf-8")) or {}
                    history_precedents = p_index.get("precedents", [])
                except Exception as prec_err:
                    logger.debug("Error leyendo precedents_index.yaml: %s", prec_err)

            # Incluir tickets resueltos desde SQLite con su dictamen
            with self.db.get_connection() as conn:
                res_cur = conn.execute(
                    "SELECT * FROM dispute_tickets WHERE project_uuid = ? AND status != 'open' ORDER BY created_at DESC LIMIT 30;",
                    (target_uuid,)
                )
                for row in res_cur.fetchall():
                    row_dict = dict(row)
                    summary = self.db.get_jury_votes_summary(row_dict["ticket_id"])
                    row_dict["votes_summary"] = summary
                    # Si no está en history_precedents, añadirlo
                    if not any(p.get("id") == f"RULING-{row_dict['ticket_id']}" or p.get("id") == row_dict['ticket_id'] for p in history_precedents):
                        history_precedents.append({
                            "id": f"RULING-{row_dict['ticket_id']}",
                            "type": "JURY_RULING",
                            "title": row_dict.get("title"),
                            "winning_option": row_dict.get("winning_quadrant"),
                            "card_id": row_dict.get("card_id"),
                            "status": row_dict.get("status"),
                            "final_adr_path": row_dict.get("final_adr_path")
                        })

            alignment_status = proj.get("okf_alignment_status", "aligned_okf")
            dossier_md = proj.get("alignment_dossier_markdown", "")
            has_pending_bootstrap = (alignment_status == "pending_bootstrap")

            return {
                "success": True,
                "project_uuid": target_uuid,
                "alignment_status": alignment_status,
                "has_pending_bootstrap": has_pending_bootstrap,
                "alignment_dossier_markdown": dossier_md,
                "pending_decisions": decisions,
                "open_disputes": disputes,
                "pending_prs": prs,
                "history_precedents": history_precedents,
                "total_count": len(decisions) + len(disputes) + len(prs) + (1 if has_pending_bootstrap else 0)
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_hitl_item_detail(
        self,
        project_uuid: Optional[str] = None,
        item_type: str = "",
        item_id: str = ""
    ) -> Dict[str, Any]:
        """
        Devuelve el expediente completo de un ítem de la bandeja HITL (decision, jury_vote o pr_merge)
        para su inspección profunda en el modal frontal de lectura y mayéutica.
        """
        try:
            target_uuid = project_uuid or self.active_project_uuid
            if not target_uuid:
                self._ensure_active_project()
                target_uuid = self.active_project_uuid

            proj = self.db.get_project_by_uuid(target_uuid)
            gov_path = Path(proj["gov_repo_path"]) if proj and proj.get("gov_repo_path") else None

            # Si es consulta de expediente de alineación inicial (Bootstrap Dossier)
            if item_type in ("bootstrap_dossier", "bootstrap"):
                dossier_res = self.get_alignment_dossier(target_uuid)
                return {
                    "success": True,
                    "item_type": "bootstrap_dossier",
                    "item": {
                        "item_id": "ALIGNMENT_DOSSIER",
                        "title": f"Dossier Unificado de Alineación Inicial OKF v2 — {proj.get('name') if proj else ''}",
                        "full_content": dossier_res.get("dossier_markdown", ""),
                        "status": proj.get("okf_alignment_status", "pending_bootstrap") if proj else "pending_bootstrap"
                    }
                }

            # Si es consulta de precedente / fallo resuelto histórico (ruling o adr)
            if item_type in ("ruling", "resolved_jury"):
                docs_okf = gov_path / "docs" / "okf" if (gov_path and (gov_path / "docs" / "okf").exists()) else gov_path
                ruling_file = None
                if docs_okf and (docs_okf / "architecture" / "rulings").is_dir():
                    for f in (docs_okf / "architecture" / "rulings").glob("*.yaml"):
                        if item_id in f.stem:
                            ruling_file = f
                            break
                if ruling_file and ruling_file.exists():
                    ruling_data = yaml.safe_load(ruling_file.read_text(encoding="utf-8")) or {}
                    return {"success": True, "item_type": "ruling", "item": ruling_data}

            bundle = self.get_pending_human_approvals(target_uuid)
            if not bundle.get("success"):
                return bundle

            if item_type == "decision":
                for d in bundle.get("pending_decisions", []):
                    if d.get("item_id") == item_id or d.get("file_name") == item_id:
                        return {"success": True, "item_type": "decision", "item": d}
                # Buscar en ADRs aprobados si ya fue promovido
                if gov_path:
                    docs_okf = gov_path / "docs" / "okf" if (gov_path / "docs" / "okf").exists() else gov_path
                    if (docs_okf / "architecture" / "adrs").is_dir():
                        for f in (docs_okf / "architecture" / "adrs").glob("*.md"):
                            if item_id in f.stem:
                                txt = f.read_text(encoding="utf-8", errors="ignore")
                                return {
                                    "success": True,
                                    "item_type": "decision",
                                    "item": {
                                        "item_id": item_id,
                                        "title": f.stem,
                                        "full_content": txt,
                                        "status": "approved_adr"
                                    }
                                }
                return {"success": False, "error": f"Decisión {item_id} no encontrada"}

            elif item_type in ("jury_vote", "dispute"):
                for disp in bundle.get("open_disputes", []):
                    if disp.get("ticket_id") == item_id:
                        # Incluir también mensajes A2A relacionados con la tarjeta si existen
                        card_id = disp.get("card_id")
                        dialogue_turns = []
                        if card_id:
                            with self.db.get_connection() as conn:
                                rows = conn.execute(
                                    "SELECT * FROM a2a_messages WHERE project_uuid = ? AND card_id = ? ORDER BY created_at ASC;",
                                    (target_uuid, card_id)
                                ).fetchall()
                                for r in rows:
                                    try:
                                        p = json.loads(r["payload_rfc_yaml"])
                                    except Exception:
                                        p = {"raw": r["payload_rfc_yaml"]}
                                    dialogue_turns.append({
                                        "message_uuid": r["message_uuid"],
                                        "sender_node_uuid": r["sender_node_uuid"],
                                        "intent": r["intent"],
                                        "payload": p,
                                        "created_at": r["created_at"]
                                    })
                        disp["dialogue_turns"] = dialogue_turns
                        return {"success": True, "item_type": "jury_vote", "item": disp}
                return {"success": False, "error": f"Ticket de disputa {item_id} no encontrado"}

            elif item_type in ("pr_merge", "task_review"):
                for pr in bundle.get("pending_prs", []):
                    if pr.get("card_id") == item_id:
                        return {"success": True, "item_type": "pr_merge", "item": pr}
                # Fallback: buscar tarjeta directamente en DB
                card = self.db.get_card_by_id(target_uuid, item_id)
                if card:
                    return {"success": True, "item_type": "pr_merge", "item": card}
                return {"success": False, "error": f"Tarea en revisión {item_id} no encontrada"}

            return {"success": False, "error": f"Tipo de ítem desconocido: {item_type}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def _parse_github_owner_repo(raw_url: str) -> Optional[Tuple[str, str]]:
        """Extrae (owner, repo) de una URL https/ssh de GitHub; None si no es resoluble."""
        m = re.search(r"github\.com[/:]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", (raw_url or "").strip())
        if not m:
            return None
        return m.group(1), m.group(2)

    def sign_and_approve_item(
        self,
        project_uuid: str,
        item_type: str,
        item_id: str,
        decision: str = "APPROVED",
        comment: str = "",
        voter_node: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Firma y aprueba un ítem utilizando la clave privada Ed25519 del desarrollador local.
        item_type: 'decision' (promueve a ADR), 'jury_vote' (emite voto 2.0), o 'pr_merge'.
        """
        try:
            proj = self.db.get_project_by_uuid(project_uuid)
            if not proj:
                return {"success": False, "error": "Proyecto no encontrado"}

            gov_path = Path(proj["gov_repo_path"])
            app_path = Path(proj["code_repo_path"])
            local_node = voter_node or os.environ.get("MCP2P_NODE_UUID", f"node_{platform.node().lower()[:12]}")
            dev_name = os.environ.get("USER", os.environ.get("USERNAME", "developer"))

            if item_type == "decision":
                # Leer decisión
                docs_base = gov_path / "docs" / "okf" if (gov_path / "docs" / "okf").exists() else gov_path
                dec_dir = docs_base / "architecture" / "decisions"
                dec_file = None
                for f in dec_dir.glob("*.md"):
                    if item_id in f.name:
                        dec_file = f
                        break
                if not dec_file:
                    return {"success": False, "error": f"Decisión {item_id} no encontrada"}

                content = dec_file.read_text(encoding="utf-8")
                # Estampar firma humana Ed25519
                sig_block = self.crypto.sign_human_approval(
                    artifact_id=item_id,
                    content_text=content,
                    node_uuid=local_node,
                    developer_name=dev_name,
                    decision=decision
                )
                # Promover decisión a ADR
                adr_path = self.okf.move_decision_lifecycle(
                    gov_base_path=gov_path,
                    dec_id=item_id,
                    target_state="approved" if decision == "APPROVED" else "discarded",
                    reason_or_consequences=comment or "Aprobado formalmente mediante firma HITL.",
                    signature_block=sig_block
                )
                try:
                    self.okf.export_precedents_index(gov_path)
                except Exception:
                    pass
                return {
                    "success": True,
                    "approved": decision == "APPROVED",
                    "item_id": item_id,
                    "adr_path": str(adr_path),
                    "signature": sig_block
                }

            elif item_type == "jury_vote":
                # decision: puede ser alias 'ALFA', 'BETA', 'GAMMA' o canónico 'OPTION_A', 'OPTION_B', 'THIRD_WAY_C' (EGM-07)
                dec_upper = str(decision).strip().upper()
                alias_to_canonical, canonical_to_alias = self._get_blind_options_mapping(item_id, local_node)
                
                if dec_upper in alias_to_canonical:
                    canonical_decision = alias_to_canonical[dec_upper]
                elif dec_upper in ("OPTION_A", "OPTION_B", "THIRD_WAY_C"):
                    canonical_decision = dec_upper
                else:
                    return {"success": False, "error": f"Opción de voto inválida '{decision}'. Debe ser ALFA, BETA o GAMMA."}

                # Estampar firma humana Ed25519 sobre la opción canónica y proyecto (EGM-05)
                sig_content = f"VOTE:{canonical_decision}:TICKET:{item_id}:PROJECT:{project_uuid}"
                sig_block = self.crypto.sign_human_approval(
                    artifact_id=item_id,
                    content_text=sig_content,
                    node_uuid=local_node,
                    developer_name=dev_name,
                    decision=canonical_decision
                )
                vote_res = self.bus.submit_human_jury_vote(
                    ticket_id=item_id,
                    node_uuid=local_node,
                    developer_name=dev_name,
                    voted_option=canonical_decision,
                    signature_ed25519=sig_block["signature_ed25519"],
                    public_key=sig_block.get("public_key", ""),
                    signed_at=sig_block.get("signed_at", ""),
                    content_sha256=sig_block.get("content_sha256", ""),
                    project_uuid=project_uuid
                )
                # Computar veredicto automáticamente (EGM-10: bus.compute_jury_verdict ya exporta y commitea si está ratificado)
                verdict = self.bus.compute_jury_verdict(project_uuid, item_id)
                return {
                    "success": True,
                    "vote": vote_res,
                    "verdict": verdict,
                    "signature": sig_block,
                    "canonical_decision": canonical_decision,
                    "voted_alias": canonical_to_alias.get(canonical_decision, dec_upper)
                }

            elif item_type == "pr_merge":
                # item_id: card_id (ej. TASK-102) o pr_number
                sig_block = self.crypto.sign_human_approval(
                    artifact_id=item_id,
                    content_text=f"PR_REVIEW:{decision}:ITEM:{item_id}",
                    node_uuid=local_node,
                    developer_name=dev_name,
                    decision=decision
                )
                card = self.db.get_card_by_id(project_uuid, item_id)
                if decision in ("REJECTED", "REJECT"):
                    if card:
                        self.db.update_card_status(project_uuid, item_id, "rejected")
                    return {
                        "success": True,
                        "merged": False,
                        "rejected": True,
                        "item_id": item_id,
                        "signature": sig_block
                    }
                elif decision in ("DISCARDED", "RETURN_TO_PROGRESS"):
                    if card:
                        self.db.update_card_status(project_uuid, item_id, "in_progress")
                    return {
                        "success": True,
                        "merged": False,
                        "returned_to_progress": True,
                        "item_id": item_id,
                        "signature": sig_block
                    }

                if card:
                    branch_ref = (card.get("branch_ref") or "").strip()
                    if not branch_ref or branch_ref == "main":
                        merge_res: Dict[str, Any] = {
                            "success": True, "merged": True, "skipped_git_merge": True,
                            "reason": "La tarjeta no tiene una rama propia que fusionar."
                        }
                    elif not app_path.exists():
                        merge_res = {"success": False, "error": f"No existe el repositorio de código local: {app_path}"}
                    else:
                        git_m = self.github.git_merge(app_path, source_branch=branch_ref, target_branch="main")
                        ok = bool(git_m.get("success"))
                        merge_res = {"success": ok, "merged": ok, "git_merge": git_m}
                        if not ok:
                            merge_res["error"] = f"Fallo al fusionar '{branch_ref}' en main: {git_m.get('error') or 'error desconocido'}"
                elif item_id.isdigit():
                    owner_repo = self._parse_github_owner_repo(proj.get("code_repo_url", ""))
                    if not owner_repo:
                        merge_res = {
                            "success": False,
                            "error": f"No se pudo resolver owner/repo de GitHub desde code_repo_url del proyecto; no se fusionó la PR #{item_id}."
                        }
                    else:
                        merge_res = self.github.evaluate_and_merge_pull_request(
                            repo_owner=owner_repo[0],
                            repo_name=owner_repo[1],
                            pr_number=int(item_id),
                            local_repo_path=app_path,
                            force_auto_merge=True
                        )
                else:
                    merge_res = {"success": False, "error": f"No existe la tarjeta '{item_id}' ni es un número de PR válido."}

                merge_ok = bool(merge_res.get("success"))
                if card and merge_ok:
                    self.db.update_card_status(project_uuid, item_id, "merged")

                result: Dict[str, Any] = {
                    "success": merge_ok,
                    "merged": merge_ok,
                    "item_id": item_id,
                    "details": merge_res,
                    "signature": sig_block
                }
                if not merge_ok:
                    result["error"] = merge_res.get("error") or merge_res.get("reason") or "La fusión no se completó."
                return result

            elif item_type in ("bootstrap_dossier", "bootstrap"):
                return self.ratify_okf_bootstrap(project_uuid=project_uuid, comment=comment)

            else:
                return {"success": False, "error": f"item_type desconocido: {item_type}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # =========================================================================
    # Métodos de Ingesta Cognitiva (Bootstrap) y Auditoría de Deriva (Drift)
    # =========================================================================

    def scan_and_bootstrap_okf(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        """
        Escanea exhaustivamente el código y documentación del repositorio en app/
        y genera el Dossier Unificado de Alineación Inicial OKF v2.
        """
        try:
            target_uuid = project_uuid or self.active_project_uuid
            if not target_uuid:
                self._ensure_active_project()
                target_uuid = self.active_project_uuid

            proj = self.db.get_project_by_uuid(target_uuid)
            if not proj:
                return {"success": False, "error": "Proyecto no encontrado"}

            code_path = proj.get("code_repo_path")
            if not code_path or not Path(code_path).exists():
                return {"success": False, "error": f"Ruta de código inválida o inexistente: {code_path}"}

            from src.storage.code_scanner import CodebaseAnalyzer
            analyzer = CodebaseAnalyzer(code_path)
            analysis = analyzer.analyze_full_repository()
            dossier_md = analyzer.generate_alignment_dossier(proj.get("name", "Proyecto"), target_uuid)

            # Persistir en SQLite
            self.db.update_project_alignment(
                project_uuid=target_uuid,
                status="pending_bootstrap",
                dossier_markdown=dossier_md
            )

            stack_data = analysis["stack"].__dict__ if hasattr(analysis["stack"], "__dict__") else analysis["stack"]

            return {
                "success": True,
                "project_uuid": target_uuid,
                "alignment_status": "pending_bootstrap",
                "stack": stack_data,
                "total_adrs_extracted": len(analysis["adrs"]),
                "total_requirements": len(analysis["requirements"]),
                "backlog_cards_suggested": len(analysis["backlog_suggestions"]),
                "dossier_markdown": dossier_md
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_alignment_dossier(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        """
        Obtiene el contenido del Dossier Unificado de Alineación OKF.
        """
        try:
            target_uuid = project_uuid or self.active_project_uuid
            if not target_uuid:
                self._ensure_active_project()
                target_uuid = self.active_project_uuid

            proj = self.db.get_project_by_uuid(target_uuid)
            if not proj:
                return {"success": False, "error": "Proyecto no encontrado"}

            dossier = proj.get("alignment_dossier_markdown")
            status = proj.get("okf_alignment_status", "aligned_okf")

            if not dossier and proj.get("code_repo_path") and Path(proj["code_repo_path"]).exists():
                from src.storage.code_scanner import CodebaseAnalyzer
                analyzer = CodebaseAnalyzer(proj["code_repo_path"])
                dossier = analyzer.generate_alignment_dossier(proj.get("name", "Proyecto"), target_uuid)
                self.db.update_project_alignment(target_uuid, status, dossier)

            return {
                "success": True,
                "project_uuid": target_uuid,
                "alignment_status": status,
                "dossier_markdown": dossier or "No hay dossier disponible."
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def ratify_okf_bootstrap(self, project_uuid: Optional[str] = None, comment: str = "") -> Dict[str, Any]:
        """
        Ratificación formal del Dossier de Alineación Inicial OKF v2 bajo firma Ed25519.
        Materializa N ADRs, RFs, RNFs, convenciones, mapa, y crea las tarjetas Kanban iniciales.
        """
        try:
            target_uuid = project_uuid or self.active_project_uuid
            if not target_uuid:
                self._ensure_active_project()
                target_uuid = self.active_project_uuid

            proj = self.db.get_project_by_uuid(target_uuid)
            if not proj:
                return {"success": False, "error": "Proyecto no encontrado"}

            code_path = proj.get("code_repo_path")
            gov_path = proj.get("gov_repo_path")
            if not code_path or not gov_path:
                return {"success": False, "error": "Rutas de repositorio no configuradas"}

            from src.storage.code_scanner import CodebaseAnalyzer
            analyzer = CodebaseAnalyzer(code_path)
            analysis = analyzer.analyze_full_repository()
            dossier_md = analyzer.generate_alignment_dossier(proj.get("name", "Proyecto"), target_uuid)
            analysis["dossier_markdown"] = dossier_md

            local_node = os.environ.get("MCP2P_NODE_UUID", f"node_{platform.node().lower()[:12]}")
            dev_name = os.environ.get("USER", os.environ.get("USERNAME", "developer"))

            # Firmar con Ed25519
            sig_block = self.crypto.sign_human_approval(
                artifact_id=f"BOOTSTRAP_OKF_{target_uuid}",
                content_text=dossier_md,
                node_uuid=local_node,
                developer_name=dev_name,
                decision="RATIFIED"
            )

            # Materializar archivos en la gobernanza
            mat_res = self.okf.materialize_bootstrap_dossier(
                gov_base_path=gov_path,
                analysis_result=analysis,
                signature_block=sig_block,
                project_uuid=target_uuid
            )

            # Insertar tarjetas sugeridas en kanban_cards si no existen
            suggested_cards = analysis.get("backlog_suggestions", [])
            existing_cards = {c["card_id"] for c in self.db.get_cards(target_uuid)}
            created_cards_count = 0
            for card in suggested_cards:
                cid = card["card_id"]
                if cid not in existing_cards:
                    self.db.create_card(
                        project_uuid=target_uuid,
                        card_id=cid,
                        title=card["title"],
                        description=card.get("description", "")
                    )
                    created_cards_count += 1

            # Transicionar estado del proyecto a aligned_okf
            self.db.update_project_alignment(
                project_uuid=target_uuid,
                status="aligned_okf",
                dossier_markdown=dossier_md
            )

            return {
                "success": True,
                "project_uuid": target_uuid,
                "new_status": "aligned_okf",
                "materialization": mat_res,
                "backlog_cards_created": created_cards_count,
                "signature": sig_block
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def audit_okf_drift(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        """
        Modo 2: Auditoría Continua de Deriva (Drift) entre el código real y el OKF ratificado.
        """
        try:
            target_uuid = project_uuid or self.active_project_uuid
            if not target_uuid:
                self._ensure_active_project()
                target_uuid = self.active_project_uuid

            proj = self.db.get_project_by_uuid(target_uuid)
            if not proj:
                return {"success": False, "error": "Proyecto no encontrado"}

            status = proj.get("okf_alignment_status", "aligned_okf")
            if status == "pending_bootstrap":
                return {
                    "success": True,
                    "is_aligned": False,
                    "status": "pending_bootstrap",
                    "message": "El proyecto está en estado pending_bootstrap. Requiere ratificación HITL antes de auditar derivas."
                }

            drift = self.okf.audit_okf_drift(
                code_repo_path=proj.get("code_repo_path", ""),
                gov_base_path=proj.get("gov_repo_path", "")
            )
            return {
                "success": True,
                "project_uuid": target_uuid,
                "drift": drift
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
