"""
Servicio de colaboración de proyecto del núcleo hexagonal (EGM-57, diseño `22_ §3.3`).

Caso de uso completo de colaboración: invitar, unirse (stub pendiente), regenerar el código de
solicitud, reintentar la unión (aceptar invitaciones, clonar, activar), ingerir solicitudes
entrantes (identidad, rate limit por clave pública, colisiones de `node_uuid`), listar, aprobar,
rechazar y retirar colaboradores.

La persistencia sale por `CollaborationRepositoryPort` y `ProjectRepositoryPort`, la autorización
en `trusted_nodes.yaml` y la reconciliación por el puerto OKF, el commit + push de la gobernanza
por `GitWorkspacePort`, las operaciones del anfitrión de repos (estado de sesión, invitaciones,
clones) por `repo_host`, la firma por la identidad criptográfica y la notificación al par por el
notificador inyectado. Lo que es propio del anfitrión (proyecto activo de la GUI, worktree
huérfano, sincronización de tres niveles) llega como `CollaborationHostHooks`.

Los textos, `success`/`status` y efectos son los de `collab_mesh_ui.py` antes de la extracción
(`tests/test_collab_join_flow_characterization.py`).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import secrets
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.core.domain.join import (
    is_pending_join_stub,
    join_stub_repo_dirs,
    parse_github_owner_repo,
    pending_join_refusal,
)
from src.core.ports.repositories import CollaborationRepositoryPort
from src.runtime.identity import local_node_uuid
from src.storage.validators import InvalidIdentifierError, validate_safe_id

logger = logging.getLogger("mcp2p.core.collaboration")

PeerNotifier = Callable[[str, Dict[str, Any]], Any]


@dataclass
class CollaborationHostHooks:
    """Efectos propios del anfitrión (GUI, CLI...) que el caso de uso dispara pero no posee."""

    set_active_project: Optional[Callable[[str], None]] = None
    get_active_project: Optional[Callable[[], Optional[str]]] = None
    build_join_request_code: Optional[Callable[[str, str, str, str], str]] = None
    ensure_orphan_worktree: Optional[Callable[[Path, str], Any]] = None
    sync_project: Optional[Callable[[str], Any]] = None


class ProjectCollaborationService:
    def __init__(
        self,
        collab_repo: CollaborationRepositoryPort,
        project_lookup: Callable[[str], Optional[Dict[str, Any]]],
        okf_port: Any = None,
        git_port: Any = None,
        repo_host: Any = None,
        peer_notifier: Optional[PeerNotifier] = None,
        *,
        project_repo: Any = None,
        crypto: Any = None,
        live_transport: Any = None,
        hooks: Optional[CollaborationHostHooks] = None,
        workspace_root: Optional[Callable[[], Path]] = None,
        reconcile_target: Any = None,
        local_node: Callable[[], str] = local_node_uuid,
    ) -> None:
        self.collab_repo = collab_repo
        self.project_lookup = project_lookup
        self.okf_port = okf_port
        self.git_port = git_port
        self.repo_host = repo_host
        self.peer_notifier = peer_notifier
        self.project_repo = project_repo
        self.crypto = crypto
        self.live_transport = live_transport
        self.hooks = hooks or CollaborationHostHooks()
        self.workspace_root = workspace_root
        # Objeto que `reconcile_kanban_from_okf` recibe como `db_manager` (en la GUI, su BD).
        self.reconcile_target = reconcile_target
        self.local_node = local_node

    def rebind(self, **changes: Any) -> "ProjectCollaborationService":
        """Copia del servicio con otros colaboradores (p. ej. los de una GUI concreta)."""
        import copy

        clone = copy.copy(self)
        for name, value in changes.items():
            if not hasattr(clone, name):
                raise AttributeError(f"ProjectCollaborationService no tiene el colaborador '{name}'")
            setattr(clone, name, value)
        if clone.hooks is None:
            clone.hooks = CollaborationHostHooks()
        return clone

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------

    def stub_repo_dirs(self, p_name: str, p_uuid: str, base_path: Optional[str] = None) -> Tuple[Path, Path, Path]:
        root = self.workspace_root() if self.workspace_root is not None else Path("workspace")
        return join_stub_repo_dirs(p_name, p_uuid, root, base_path)

    def _set_active(self, project_uuid: str) -> None:
        if self.hooks.set_active_project is not None:
            self.hooks.set_active_project(project_uuid)

    def _request_code(self, p_uuid: str, nonce: str, gh_user: str, node: str) -> str:
        builder = self.hooks.build_join_request_code or self.build_join_request_code
        return builder(p_uuid, nonce, gh_user, node)

    def _gh_status(self) -> Dict[str, Any]:
        return self.repo_host.get_auth_status() if self.repo_host is not None else {}

    # ------------------------------------------------------------------
    # Invitar / unirse (lado del solicitante)
    # ------------------------------------------------------------------

    def create_invite(self, proj: Dict[str, Any]) -> Dict[str, Any]:
        if is_pending_join_stub(proj):
            return pending_join_refusal(proj, "generar invitaciones para este proyecto")

        local_node = self.local_node()
        gh_status = self._gh_status()
        payload = {
            "project_uuid": proj["project_uuid"],
            "name": proj["name"],
            "code_repo_url": proj.get("code_repo_url", ""),
            "gov_repo_url": proj.get("gov_repo_url", ""),
            "sender_node_uuid": local_node,
            "sender_github": gh_status.get("username", ""),
            "sender_pubkey": self.crypto.public_key_base64,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "rendezvous_nonce": secrets.token_hex(8),
        }
        token = self.crypto.create_signed_invite_token(payload)
        return {
            "success": True,
            "project_uuid": proj["project_uuid"],
            "project_name": proj["name"],
            "token": token,
            "invite_url": f"mcp2p://join?token={token}",
            "payload": payload,
        }

    def build_join_request_code(
        self, p_uuid: str, rendezvous_nonce: str, github_username: str, node_uuid: str
    ) -> str:
        request_payload = {
            "project_uuid": p_uuid,
            "rendezvous_nonce": rendezvous_nonce or "",
            "github_username": github_username or "",
            "public_key": self.crypto.public_key_base64,
            "node_uuid": node_uuid,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        return self.crypto.create_signed_invite_token(request_payload)

    def join_project(self, invite_str: str) -> Dict[str, Any]:
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

        rate_state = self.collab_repo.get_join_rate_limit_state(p_uuid, self.crypto.public_key_base64)
        if not rate_state.get("allowed", True):
            return {
                "success": False,
                "status": "locked",
                "error": "Solicitud de unión bloqueada temporalmente por rechazos previos.",
                "locked_until": rate_state.get("locked_until"),
            }

        existing = self.project_lookup(p_uuid)
        if existing and existing.get("join_status") != "pending_approval":
            self._set_active(p_uuid)
            return {"success": True, "project": existing, "message": "Proyecto ya registrado. Se activó el contexto."}

        if existing is None:
            p_sla = int(payload.get("reviewer_sla_hours") or 24)
            base_dir, future_app_dir, future_gov_dir = self.stub_repo_dirs(p_name, p_uuid)
            code_url = payload.get("code_repo_url", "").strip()
            gov_url = payload.get("gov_repo_url", "").strip()
            self.project_repo.upsert_pending_join_stub(
                project_uuid=p_uuid,
                name=p_name,
                base_path=str(base_dir),
                code_repo_path=str(future_app_dir),
                gov_repo_path=str(future_gov_dir),
                code_repo_url=code_url,
                gov_repo_url=gov_url,
                default_sla_hours=p_sla,
            )
            existing = self.project_lookup(p_uuid)

        local_node = self.local_node()
        gh_status = self._gh_status()

        sender_node = payload.get("sender_node_uuid")
        if sender_node:
            sender_pubkey = payload.get("sender_pubkey", "")
            effective_sender_node = sender_node
            if sender_node == local_node and sender_pubkey != self.crypto.public_key_base64:
                suffix = (
                    hashlib.sha256(sender_pubkey.encode("utf-8")).hexdigest()[:8]
                    if sender_pubkey
                    else secrets.token_hex(4)
                )
                effective_sender_node = f"{sender_node}_{suffix}"
            self.collab_repo.register_or_update_node(
                node_uuid=effective_sender_node,
                developer_name=payload.get("sender_github") or f"Peer {sender_node[:8]}",
                agent_id=f"agent_{sender_node[:8]}",
                reviewer_sla_hours=24,
                status="online",
                node_kind="human",
            )
            self.collab_repo.add_or_request_collaborator(
                project_uuid=p_uuid,
                node_uuid=effective_sender_node,
                github_username=payload.get("sender_github", ""),
                public_key=sender_pubkey,
                role="owner",
                status="approved",
            )

        self.collab_repo.add_or_request_collaborator(
            project_uuid=p_uuid,
            node_uuid=local_node,
            github_username=gh_status.get("username", ""),
            public_key=self.crypto.public_key_base64,
            role="collaborator",
            status="pending_approval",
        )

        try:
            self.collab_repo.record_join_attempt(p_uuid, self.crypto.public_key_base64)
        except Exception as rae:
            logger.debug(f"No se pudo registrar el intento de unión en la ventana horaria: {rae}")

        request_code = self._request_code(
            p_uuid, payload.get("rendezvous_nonce", ""), gh_status.get("username", ""), local_node
        )

        try:
            rendezvous_id = f"{p_uuid}_{payload.get('rendezvous_nonce', '')}"
            if self.live_transport is not None and self.live_transport.try_connect(rendezvous_id):
                self.live_transport.send({"type": "join_request", "request_code": request_code})
        except Exception as te:
            logger.debug(f"Intento de transporte P2P en vivo (best-effort) falló: {te}")

        proj = self.project_lookup(p_uuid)
        return {
            "success": True,
            "status": "pending_approval",
            "project": proj,
            "request_code": request_code,
        }

    def pending_join_request_code(self, project_uuid: str) -> Dict[str, Any]:
        proj = self.project_lookup(project_uuid)
        if not proj:
            return {"success": False, "error": "Proyecto no encontrado"}
        if not is_pending_join_stub(proj):
            return {"success": False, "error": "El proyecto no tiene una solicitud de unión pendiente"}
        gh_status = self._gh_status()
        request_code = self._request_code(project_uuid, "", gh_status.get("username", ""), self.local_node())
        return {"success": True, "project": proj, "request_code": request_code}

    def retry_join(self, project_uuid: str) -> Dict[str, Any]:
        """Cuerpo de `retry_p2p_join` (el anfitrión lo serializa por `project_uuid`)."""
        proj = self.project_lookup(project_uuid)
        if not proj:
            return {"success": False, "error": "Proyecto no encontrado"}

        if proj.get("join_status") == "active":
            return {"success": True, "status": "already_active", "project": proj}

        p_name = proj.get("name", "proyecto")
        code_url = (proj.get("code_repo_url") or "").strip()
        gov_url = (proj.get("gov_repo_url") or "").strip()

        still_pending_result = {
            "success": False,
            "status": "still_pending",
            "error": "Todavía no disponible, inténtalo de nuevo en un momento.",
        }

        if not gov_url and not code_url:
            return {
                "success": False,
                "status": "no_remote_repos",
                "error": "El dueño del proyecto todavía no ha publicado sus repositorios remotos. No hay nada que clonar todavía.",
            }

        _base_dir, app_dir, gov_dir = self.stub_repo_dirs(p_name, project_uuid, proj.get("base_path"))

        repo_full_names = set()
        for raw_url in (code_url, gov_url):
            owner_repo = parse_github_owner_repo(raw_url)
            if owner_repo:
                repo_full_names.add(f"{owner_repo[0]}/{owner_repo[1]}")
        if repo_full_names:
            try:
                self.repo_host.accept_pending_invitations(repo_full_names=repo_full_names)
            except Exception as acc_e:
                logger.debug(f"Auto-accept invitations omitido: {acc_e}")

        if gov_url and not (gov_dir / ".git").exists():
            res_gov = self.repo_host.git_clone(gov_url, gov_dir)
            if res_gov.get("status") == "pending_access":
                return still_pending_result
            if not res_gov.get("success"):
                return {"success": False, "error": f"Fallo al clonar repositorio de gobernanza: {res_gov.get('error')}"}

        if code_url and not (app_dir / ".git").exists():
            res_app = self.repo_host.git_clone(code_url, app_dir)
            if res_app.get("status") == "pending_access":
                return still_pending_result
            if not res_app.get("success"):
                return {"success": False, "error": f"Fallo al clonar repositorio de código: {res_app.get('error')}"}

        if gov_dir.exists() and (gov_dir / ".git").exists():
            if self.hooks.ensure_orphan_worktree is not None:
                try:
                    self.hooks.ensure_orphan_worktree(gov_dir, project_uuid)
                except Exception as oe:
                    logger.warning(f"No se pudo inicializar orphan worktree tras clonar: {oe}")

            if self.okf_port is not None:
                try:
                    self.okf_port.reconcile_kanban_from_okf(gov_dir, project_uuid, self.reconcile_target)
                except Exception as re_e:
                    logger.warning(f"Error al reconciliar OKF tras clonar: {re_e}")

        proj = self.project_repo.activate_joined_project(
            project_uuid=project_uuid,
            code_repo_path=str(app_dir),
            gov_repo_path=str(gov_dir),
            local_node=self.local_node(),
        )
        self._set_active(project_uuid)

        if self.hooks.sync_project is not None:
            try:
                self.hooks.sync_project(project_uuid)
            except Exception as se:
                logger.debug(f"Sync three tiers inicial pospuesta: {se}")

        proj = self.project_lookup(project_uuid) or proj
        return {"success": True, "status": "active", "project": proj}

    def record_local_rejection(self, project_uuid: Optional[str], node_uuid: str) -> None:
        """`join_decision(rejected)` recibido en vivo: solo cuenta si va dirigido a este nodo."""
        if project_uuid and node_uuid == self.local_node():
            try:
                self.collab_repo.record_rejection(project_uuid, node_uuid)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Solicitudes entrantes (lado del dueño)
    # ------------------------------------------------------------------

    def ingest_join_request(self, request_code: str, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        is_valid, payload, err = self.crypto.decode_and_verify_invite_token(request_code)
        if not is_valid:
            return {"success": False, "error": err or "Firma criptográfica inválida"}

        public_key = payload.get("public_key", "")
        node_uuid = payload.get("node_uuid", "")
        github_username = payload.get("github_username", "")
        if not public_key or not node_uuid:
            return {"success": False, "error": "El request_code no contiene los datos del solicitante requeridos"}

        try:
            padded = request_code.strip() + "=" * (-len(request_code.strip()) % 4)
            envelope = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
            envelope_signer_pubkey = envelope.get("sender_pubkey", "")
        except Exception:
            envelope_signer_pubkey = ""
        if not envelope_signer_pubkey or public_key != envelope_signer_pubkey:
            return {
                "success": False,
                "error": "La identidad declarada en la solicitud no coincide con la clave que firmó el token",
            }

        if not project_uuid and self.hooks.get_active_project is not None:
            project_uuid = self.hooks.get_active_project()
        if not project_uuid:
            return {"success": False, "error": "No hay un proyecto activo para procesar la solicitud de unión"}
        target_proj = self.project_lookup(project_uuid)
        if not target_proj:
            return {"success": False, "error": "Proyecto no encontrado"}
        if is_pending_join_stub(target_proj):
            return pending_join_refusal(target_proj, "registrar solicitudes de unión en este proyecto")

        if payload.get("project_uuid") != project_uuid:
            return {
                "success": False,
                "error": "El código de solicitud corresponde a un proyecto distinto a este",
            }

        existing = self.collab_repo.get_unresolved_or_approved_collaborator_by_pubkey(project_uuid, public_key)
        if existing is not None:
            return {"success": True, "status": existing.get("status"), "collaborator": existing}

        rate_state = self.collab_repo.get_join_rate_limit_state(project_uuid, public_key)
        if not rate_state.get("allowed", True):
            return {
                "success": False,
                "status": "locked",
                "error": "Solicitud de unión bloqueada temporalmente por rechazos previos.",
                "locked_until": rate_state.get("locked_until"),
            }

        effective_node_uuid = node_uuid
        existing_by_node = self.collab_repo.get_collaborator(project_uuid, node_uuid)
        if existing_by_node is not None and existing_by_node.get("public_key", "") != public_key:
            suffix = hashlib.sha256(public_key.encode("utf-8")).hexdigest()[:8]
            effective_node_uuid = f"{node_uuid}_{suffix}"

        try:
            collaborator = self.collab_repo.add_or_request_collaborator(
                project_uuid=project_uuid,
                node_uuid=effective_node_uuid,
                github_username=github_username,
                public_key=public_key,
                role="collaborator",
                status="pending_approval",
            )
        except ValueError as ve:
            return {"success": False, "error": f"No se pudo registrar la solicitud de unión: {ve}"}

        try:
            self.collab_repo.record_join_attempt(project_uuid, public_key)
        except Exception as rae:
            logger.debug(f"No se pudo registrar el intento de unión en la ventana horaria: {rae}")

        collaborator = self.collab_repo.get_collaborator(project_uuid, effective_node_uuid) or collaborator
        return {"success": True, "status": "pending_approval", "collaborator": collaborator}

    def list_collaborators(self, project_uuid: str) -> List[Dict[str, Any]]:
        collaborators = self.collab_repo.get_project_collaborators(project_uuid)
        for c in collaborators:
            c["locked_until"] = None
            if c.get("status") != "approved" and c.get("public_key"):
                rate_state = self.collab_repo.get_join_rate_limit_state(project_uuid, c["public_key"])
                c["locked_until"] = rate_state.get("locked_until")
        return collaborators

    # ------------------------------------------------------------------
    # Decisiones del dueño
    # ------------------------------------------------------------------

    def _notify_decision(self, project_uuid: str, node_uuid: str, decision: str) -> None:
        if self.peer_notifier is None:
            return
        try:
            self.peer_notifier(node_uuid, {
                "type": "join_decision",
                "decision": decision,
                "project_uuid": project_uuid,
                "node_uuid": node_uuid,
            })
        except Exception as notify_err:
            logger.debug(f"No se pudo enviar join_decision({decision}) en vivo: {notify_err}")

    def _commit_trusted_nodes(self, gov_path: Path, node_uuid: str, gh_user: Optional[str]) -> Optional[str]:
        """Comitea y empuja trusted_nodes.yaml; devuelve el error de commit (o None)."""
        if self.git_port is None or not (gov_path.exists() and (gov_path / ".git").exists()):
            return None
        gov_commit_error = None
        try:
            message = f"chore(gov): authorize peer {node_uuid[:8]} (@{gh_user or 'node'}) in trusted_nodes.yaml"
            commit = self.git_port.commit_all_changes(gov_path, message)
            if not commit.get("success"):
                gov_commit_error = (commit.get("error") or "").strip()[:300]
                logger.warning(f"approve_collaborator: commit de trusted_nodes.yaml falló: {gov_commit_error}")
            elif self.git_port.has_remote(gov_path, "origin"):
                self.git_port.git_push_branch(gov_path, branch="main", remote="origin")
        except Exception as push_err:
            logger.debug(f"Push de trusted_nodes omitido: {push_err}")
        return gov_commit_error

    def approve_collaborator(self, project_uuid: str, node_uuid: str) -> Dict[str, Any]:
        if not self.collab_repo.approve_collaborator(project_uuid, node_uuid):
            return {"success": False, "error": "No se pudo actualizar el colaborador en la base de datos"}

        collabs = self.collab_repo.get_project_collaborators(project_uuid)
        collab = next((c for c in collabs if c.get("node_uuid") == node_uuid), None)
        proj = self.project_lookup(project_uuid)
        invitations_sent: List[Dict[str, Any]] = []
        gov_commit_error = None

        if collab and proj:
            pub_key = collab.get("public_key")
            gh_user = collab.get("github_username")
            gov_path = Path(proj.get("gov_repo_path", ""))

            if pub_key and gov_path.exists() and self.okf_port is not None:
                try:
                    self.okf_port.add_trusted_node_pubkey(gov_path, node_uuid, pub_key, gh_user or "peer")
                except Exception as okf_e:
                    logger.warning(f"No se pudo escribir en trusted_nodes.yaml: {okf_e}")

            if gh_user and self.repo_host is not None:
                for repo_field in ("code_repo_url", "gov_repo_url"):
                    m = re.search(r"github\.com[/:]([^/]+)/([^/.]+)", proj.get(repo_field, ""))
                    if m:
                        owner, repo_name = m.group(1), m.group(2)
                        inv_res = self.repo_host.add_repo_collaborator(owner, repo_name, gh_user)
                        invitations_sent.append({"repo": f"{owner}/{repo_name}", "result": inv_res})

            gov_commit_error = self._commit_trusted_nodes(gov_path, node_uuid, gh_user)

        result: Dict[str, Any] = {
            "success": True,
            "invitations": invitations_sent,
            **({"gov_commit_error": gov_commit_error} if gov_commit_error else {}),
        }
        self._notify_decision(project_uuid, node_uuid, "approved")
        return result

    def reject_collaborator(self, project_uuid: str, node_uuid: str) -> Dict[str, Any]:
        """Rechaza colaborador y emite join_decision(decision='rejected') best-effort (EGM-47)."""
        res = self.collab_repo.record_rejection(project_uuid, node_uuid)
        if res.get("success", True):
            self._notify_decision(project_uuid, node_uuid, "rejected")
        return res

    def remove_collaborator(self, project_uuid: str, node_uuid: str) -> Dict[str, Any]:
        return {"success": self.collab_repo.remove_collaborator(project_uuid, node_uuid)}
