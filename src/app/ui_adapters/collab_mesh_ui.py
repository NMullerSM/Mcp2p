"""
Sub-adaptador UI para colaboración P2P, señalización WebRTC en caliente (EGM-47)
y flujo de invitación/unión HITL.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import platform
import re
import secrets
import subprocess
import threading
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from src.app.identity import local_node_uuid
from src.app.ui_adapters import _BaseUIAdapter
from src.p2p.transport import NullTransportAdapter, P2PTransport
from src.storage.validators import InvalidIdentifierError, validate_safe_id

logger = logging.getLogger("mcp2p.api_bridge.collab")

DEFAULT_SIGNALING_URL = ""


class CollabMeshUIAdapter(_BaseUIAdapter):
    """Gestiona invitaciones P2P, transporte en vivo, colaboradores y SLA."""

    def get_retry_lock(self, project_uuid: str) -> threading.Lock:
        with self._bridge._retry_locks_guard:
            lock = self._bridge._retry_locks.get(project_uuid)
            if lock is None:
                lock = threading.Lock()
                self._bridge._retry_locks[project_uuid] = lock
            return lock

    def join_stub_repo_dirs(
        self, p_name: str, p_uuid: str, base_path: Optional[str] = None
    ) -> Tuple[Path, Path, Path]:
        clean_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in (p_name or "proyecto").lower())
        short_id = p_uuid.replace("proj_", "")[:8]
        base_dir = Path(base_path) if base_path else (self.db.db_path.parent / "workspace" / f"{clean_name}_{short_id}")
        app_dir = base_dir / f"app_{clean_name}"
        gov_dir = base_dir / f"okf_{clean_name}_{short_id}"
        return base_dir, app_dir, gov_dir

    def generate_p2p_invite(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        try:
            _target_uuid, proj, err = self._get_project_or_error(project_uuid)
            if err or proj is None:
                return err or {"success": False, "error": "Proyecto no encontrado"}
            if self._bridge._is_pending_join_stub(proj):
                return self._bridge._pending_join_refusal(proj, "generar invitaciones para este proyecto")

            local_node = local_node_uuid()
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
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_signaling_server_url(self) -> str:
        return self.db.get_setting("p2p_signaling_url", default=DEFAULT_SIGNALING_URL)

    def set_signaling_server_url(self, url: str) -> Dict[str, Any]:
        """Configura la URL del servidor de señalización y reconstruye el transporte en caliente (EGM-47)."""
        if url != "" and not (url.startswith("wss://") or url.startswith("ws://")):
            return {"success": False, "error": "La URL debe estar vacía o comenzar con ws:// o wss://"}
        self.db.set_setting("p2p_signaling_url", url)

        old_transport = self._bridge.transport
        if old_transport is not None and hasattr(old_transport, "close"):
            try:
                old_transport.close()
            except Exception as close_err:
                logger.debug(f"Error cerrando transporte previo: {close_err}")

        if url:
            try:
                from src.p2p.webrtc_adapter import WebRTCTransportAdapter
                new_transport: P2PTransport = WebRTCTransportAdapter(url, self.crypto)
            except Exception as exc:
                logger.warning(f"No se pudo inicializar WebRTCTransportAdapter para {url!r}: {exc}")
                new_transport = NullTransportAdapter()
        else:
            new_transport = NullTransportAdapter()

        self._bridge.set_transport(new_transport)
        return {"success": True, "url": url, "transport": type(new_transport).__name__}

    def set_transport(self, transport: P2PTransport) -> None:
        self._bridge.transport = transport
        self._bridge.transport.on_message(self._bridge._handle_transport_message)

    def handle_transport_message(self, message: dict) -> None:
        """Procesa mensajes P2P en vivo (join_request y join_decision approved/rejected, EGM-47)."""
        try:
            msg_type = message["type"]
            if msg_type == "join_request":
                self._bridge.ingest_join_request(message["request_code"])
            elif msg_type == "join_decision":
                decision = message.get("decision")
                p_uuid = message.get("project_uuid")
                if decision == "approved" and p_uuid:
                    self._bridge.retry_p2p_join(p_uuid)
                elif decision == "rejected":
                    node_id = message.get("node_uuid") or local_node_uuid()
                    logger.info(
                        "Solicitud de unión rechazada por el dueño (project=%s, node=%s)",
                        p_uuid,
                        node_id,
                    )
                    if p_uuid and node_id == local_node_uuid():
                        try:
                            self.db.record_rejection(p_uuid, node_id)
                        except Exception:
                            pass
            else:
                logger.debug(f"Mensaje de transporte P2P con tipo desconocido ignorado: {msg_type!r}")
        except Exception as exc:
            logger.warning(f"Mensaje de transporte P2P malformado o inválido, ignorado: {exc}")

    def join_p2p_project(self, invite_str: str) -> Dict[str, Any]:
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

            rate_state = self.db.get_join_rate_limit_state(p_uuid, self.crypto.public_key_base64)
            if not rate_state.get("allowed", True):
                return {
                    "success": False,
                    "status": "locked",
                    "error": "Solicitud de unión bloqueada temporalmente por rechazos previos.",
                    "locked_until": rate_state.get("locked_until"),
                }

            existing = self.db.get_project_by_uuid(p_uuid)
            if existing and existing.get("join_status") != "pending_approval":
                self._bridge.active_project_uuid = p_uuid
                return {"success": True, "project": existing, "message": "Proyecto ya registrado. Se activó el contexto."}

            if existing is None:
                p_sla = int(payload.get("reviewer_sla_hours") or 24)
                base_dir, future_app_dir, future_gov_dir = self.join_stub_repo_dirs(p_name, p_uuid)
                code_url = payload.get("code_repo_url", "").strip()
                gov_url = payload.get("gov_repo_url", "").strip()
                self.db.upsert_pending_join_stub(
                    project_uuid=p_uuid,
                    name=p_name,
                    base_path=str(base_dir),
                    code_repo_path=str(future_app_dir),
                    gov_repo_path=str(future_gov_dir),
                    code_repo_url=code_url,
                    gov_repo_url=gov_url,
                    default_sla_hours=p_sla,
                )
                existing = self.db.get_project_by_uuid(p_uuid)

            local_node = local_node_uuid()
            gh_status = self.github.get_auth_status()

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
                self.db.register_or_update_node(
                    node_uuid=effective_sender_node,
                    developer_name=payload.get("sender_github") or f"Peer {sender_node[:8]}",
                    agent_id=f"agent_{sender_node[:8]}",
                    reviewer_sla_hours=24,
                    status="online",
                    node_kind="human",
                )
                self.db.add_or_request_collaborator(
                    project_uuid=p_uuid,
                    node_uuid=effective_sender_node,
                    github_username=payload.get("sender_github", ""),
                    public_key=sender_pubkey,
                    role="owner",
                    status="approved",
                )

            self.db.add_or_request_collaborator(
                project_uuid=p_uuid,
                node_uuid=local_node,
                github_username=gh_status.get("username", ""),
                public_key=self.crypto.public_key_base64,
                role="collaborator",
                status="pending_approval",
            )

            try:
                self.db.record_join_attempt(p_uuid, self.crypto.public_key_base64)
            except Exception as rae:
                logger.debug(f"No se pudo registrar el intento de unión en la ventana horaria: {rae}")

            request_code = self._bridge._build_join_request_code(
                p_uuid, payload.get("rendezvous_nonce", ""), gh_status.get("username", ""), local_node
            )

            try:
                rendezvous_id = f"{p_uuid}_{payload.get('rendezvous_nonce', '')}"
                if self.transport.try_connect(rendezvous_id):
                    self.transport.send({"type": "join_request", "request_code": request_code})
            except Exception as te:
                logger.debug(f"Intento de transporte P2P en vivo (best-effort) falló: {te}")

            proj = self.db.get_project_by_uuid(p_uuid)
            return {
                "success": True,
                "status": "pending_approval",
                "project": proj,
                "request_code": request_code,
            }
        except Exception as e:
            return {"success": False, "error": f"Error al unirse al proyecto P2P: {str(e)}"}

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

    def get_pending_join_request_code(self, project_uuid: str) -> Dict[str, Any]:
        try:
            proj = self.db.get_project_by_uuid(project_uuid)
            if not proj:
                return {"success": False, "error": "Proyecto no encontrado"}
            if not self._bridge._is_pending_join_stub(proj):
                return {"success": False, "error": "El proyecto no tiene una solicitud de unión pendiente"}
            local_node = local_node_uuid()
            gh_status = self.github.get_auth_status()
            request_code = self._bridge._build_join_request_code(
                project_uuid, "", gh_status.get("username", ""), local_node
            )
            return {"success": True, "project": proj, "request_code": request_code}
        except Exception as e:
            return {"success": False, "error": f"No se pudo regenerar el código de solicitud: {str(e)}"}

    def retry_p2p_join(self, project_uuid: str) -> Dict[str, Any]:
        lock = self._bridge._get_retry_lock(project_uuid)
        with lock:
            return self._bridge._do_retry_p2p_join(project_uuid)

    def do_retry_p2p_join(self, project_uuid: str) -> Dict[str, Any]:
        try:
            proj = self.db.get_project_by_uuid(project_uuid)
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

            _base_dir, app_dir, gov_dir = self.join_stub_repo_dirs(p_name, project_uuid, proj.get("base_path"))

            repo_full_names = set()
            for raw_url in (code_url, gov_url):
                owner_repo = self._bridge._parse_github_owner_repo(raw_url)
                if owner_repo:
                    repo_full_names.add(f"{owner_repo[0]}/{owner_repo[1]}")
            if repo_full_names:
                try:
                    self.github.accept_pending_invitations(repo_full_names=repo_full_names)
                except Exception as acc_e:
                    logger.debug(f"Auto-accept invitations omitido: {acc_e}")

            if gov_url and not (gov_dir / ".git").exists():
                res_gov = self.github.git_clone(gov_url, gov_dir)
                if res_gov.get("status") == "pending_access":
                    return still_pending_result
                if not res_gov.get("success"):
                    return {"success": False, "error": f"Fallo al clonar repositorio de gobernanza: {res_gov.get('error')}"}

            if code_url and not (app_dir / ".git").exists():
                res_app = self.github.git_clone(code_url, app_dir)
                if res_app.get("status") == "pending_access":
                    return still_pending_result
                if not res_app.get("success"):
                    return {"success": False, "error": f"Fallo al clonar repositorio de código: {res_app.get('error')}"}

            if gov_dir.exists() and (gov_dir / ".git").exists():
                try:
                    self._bridge.ensure_orphan_worktree(gov_dir, project_uuid)
                except Exception as oe:
                    logger.warning(f"No se pudo inicializar orphan worktree tras clonar: {oe}")

                try:
                    self.okf.reconcile_kanban_from_okf(gov_dir, project_uuid, self.db)
                except Exception as re_e:
                    logger.warning(f"Error al reconciliar OKF tras clonar: {re_e}")

            local_node = local_node_uuid()
            proj = self.db.activate_joined_project(
                project_uuid=project_uuid,
                code_repo_path=str(app_dir),
                gov_repo_path=str(gov_dir),
                local_node=local_node,
            )
            self._bridge.active_project_uuid = project_uuid

            try:
                self._bridge.sync_three_tiers(project_uuid)
            except Exception as se:
                logger.debug(f"Sync three tiers inicial pospuesta: {se}")

            proj = self.db.get_project_by_uuid(project_uuid) or proj
            return {"success": True, "status": "active", "project": proj}
        except Exception as e:
            return {"success": False, "error": f"Error al reintentar la unión P2P: {str(e)}"}

    def check_join_request_status(self, project_uuid: str) -> Dict[str, Any]:
        try:
            return self._bridge.retry_p2p_join(project_uuid)
        except Exception as e:
            return {"success": False, "error": f"Error al consultar el estado de la solicitud de unión: {str(e)}"}

    def ingest_join_request(self, request_code: str, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        try:
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

            project_uuid = project_uuid or self._bridge.active_project_uuid
            if not project_uuid:
                return {"success": False, "error": "No hay un proyecto activo para procesar la solicitud de unión"}
            target_proj = self.db.get_project_by_uuid(project_uuid)
            if not target_proj:
                return {"success": False, "error": "Proyecto no encontrado"}
            if self._bridge._is_pending_join_stub(target_proj):
                return self._bridge._pending_join_refusal(target_proj, "registrar solicitudes de unión en este proyecto")

            if payload.get("project_uuid") != project_uuid:
                return {
                    "success": False,
                    "error": "El código de solicitud corresponde a un proyecto distinto a este",
                }

            existing = self.db.get_unresolved_or_approved_collaborator_by_pubkey(project_uuid, public_key)
            if existing is not None:
                return {"success": True, "status": existing.get("status"), "collaborator": existing}

            rate_state = self.db.get_join_rate_limit_state(project_uuid, public_key)
            if not rate_state.get("allowed", True):
                return {
                    "success": False,
                    "status": "locked",
                    "error": "Solicitud de unión bloqueada temporalmente por rechazos previos.",
                    "locked_until": rate_state.get("locked_until"),
                }

            effective_node_uuid = node_uuid
            existing_by_node = self.db.get_collaborator(project_uuid, node_uuid)
            if existing_by_node is not None and existing_by_node.get("public_key", "") != public_key:
                suffix = hashlib.sha256(public_key.encode("utf-8")).hexdigest()[:8]
                effective_node_uuid = f"{node_uuid}_{suffix}"

            try:
                collaborator = self.db.add_or_request_collaborator(
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
                self.db.record_join_attempt(project_uuid, public_key)
            except Exception as rae:
                logger.debug(f"No se pudo registrar el intento de unión en la ventana horaria: {rae}")

            collaborator = self.db.get_collaborator(project_uuid, effective_node_uuid) or collaborator
            return {"success": True, "status": "pending_approval", "collaborator": collaborator}
        except Exception as e:
            return {"success": False, "error": f"Error al ingerir la solicitud de unión: {str(e)}"}

    def get_project_collaborators(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        try:
            target_uuid = self._resolve_project_uuid(project_uuid)
            collaborators = self.db.get_project_collaborators(target_uuid)
            for c in collaborators:
                c["locked_until"] = None
                if c.get("status") != "approved" and c.get("public_key"):
                    rate_state = self.db.get_join_rate_limit_state(target_uuid, c["public_key"])
                    c["locked_until"] = rate_state.get("locked_until")
            return {"success": True, "collaborators": collaborators}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def approve_collaborator(self, project_uuid: str, node_uuid: str) -> Dict[str, Any]:
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

                if pub_key and gov_path.exists():
                    try:
                        self.okf.add_trusted_node_pubkey(gov_path, node_uuid, pub_key, gh_user or "peer")
                    except Exception as okf_e:
                        logger.warning(f"No se pudo escribir en trusted_nodes.yaml: {okf_e}")

                if gh_user:
                    for repo_field in ("code_repo_url", "gov_repo_url"):
                        raw_url = proj.get(repo_field, "")
                        m = re.search(r"github\.com[/:]([^/]+)/([^/.]+)", raw_url)
                        if m:
                            owner, repo_name = m.group(1), m.group(2)
                            inv_res = self.github.add_repo_collaborator(owner, repo_name, gh_user)
                            invitations_sent.append({"repo": f"{owner}/{repo_name}", "result": inv_res})

                if gov_path.exists() and (gov_path / ".git").exists():
                    try:
                        creationflags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
                        self._subprocess_run(["git", "add", "."], cwd=gov_path, capture_output=True, creationflags=creationflags)
                        staged = self._subprocess_run(
                            ["git", "diff", "--cached", "--quiet"],
                            cwd=gov_path,
                            capture_output=True,
                            creationflags=creationflags,
                        )
                        commit_ok = True
                        if staged.returncode != 0:
                            commit_res = self._subprocess_run(
                                [
                                    "git",
                                    "commit",
                                    "-m",
                                    f"chore(gov): authorize peer {node_uuid[:8]} (@{gh_user or 'node'}) in trusted_nodes.yaml",
                                ],
                                cwd=gov_path,
                                capture_output=True,
                                encoding="utf-8",
                                errors="replace",
                                creationflags=creationflags,
                            )
                            commit_ok = commit_res.returncode == 0
                            if not commit_ok:
                                gov_commit_error = (commit_res.stderr or commit_res.stdout or "").strip()[:300]
                                logger.warning(
                                    f"approve_collaborator: commit de trusted_nodes.yaml falló: {gov_commit_error}"
                                )
                        if commit_ok and self.github.has_remote(gov_path, "origin"):
                            self.github.git_push_branch(gov_path, branch="main", remote="origin")
                    except Exception as push_err:
                        logger.debug(f"Push de trusted_nodes omitido: {push_err}")

            result: Dict[str, Any] = {
                "success": True,
                "invitations": invitations_sent,
                **({"gov_commit_error": gov_commit_error} if gov_commit_error else {}),
            }

            try:
                self.transport.send({
                    "type": "join_decision",
                    "decision": "approved",
                    "project_uuid": project_uuid,
                    "node_uuid": node_uuid,
                })
            except Exception as notify_err:
                logger.debug(f"No se pudo enviar join_decision en vivo: {notify_err}")

            return result
        except Exception as e:
            return {"success": False, "error": str(e)}

    def reject_collaborator(self, project_uuid: str, node_uuid: str) -> Dict[str, Any]:
        """Rechaza colaborador y emite join_decision(decision='rejected') best-effort (EGM-47)."""
        try:
            res = self.db.record_rejection(project_uuid, node_uuid)
            try:
                if res.get("success", True):
                    self.transport.send({
                        "type": "join_decision",
                        "decision": "rejected",
                        "project_uuid": project_uuid,
                        "node_uuid": node_uuid,
                    })
            except Exception as notify_err:
                logger.debug(f"No se pudo enviar join_decision(rejected) en vivo: {notify_err}")
            return res
        except Exception as e:
            return {"success": False, "error": str(e)}

    def remove_collaborator(self, project_uuid: str, node_uuid: str) -> Dict[str, Any]:
        try:
            res = self.db.remove_collaborator(project_uuid, node_uuid)
            return {"success": res}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def update_project_sla(self, project_uuid: str, sla_hours: int) -> Dict[str, Any]:
        try:
            res = self.db.update_project_sla(project_uuid, sla_hours)
            return {"success": res, "sla_hours": sla_hours}
        except Exception as e:
            return {"success": False, "error": str(e)}
