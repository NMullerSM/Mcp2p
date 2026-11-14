"""
Servicio de colaboración de proyecto del núcleo hexagonal (EGM-57, diseño `22_ §3.3`).

Primer corte: aprobar / rechazar / retirar colaboradores. La persistencia sale por
`CollaborationRepositoryPort`, la autorización en `trusted_nodes.yaml` por el puerto OKF, el
commit + push de la gobernanza por `GitWorkspacePort` (antes: `git add/commit` por
`subprocess` dentro de `collab_mesh_ui.py`) y la notificación al par por el notificador
inyectado (en la GUI, `PeerNetworkPort` con caída al transporte heredado).
Invitar / unirse / reintentar / ingerir solicitudes siguen en el adaptador (pendiente, ver `27_`).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.core.ports.repositories import CollaborationRepositoryPort

logger = logging.getLogger("mcp2p.core.collaboration")

PeerNotifier = Callable[[str, Dict[str, Any]], Any]


class ProjectCollaborationService:
    def __init__(
        self,
        collab_repo: CollaborationRepositoryPort,
        project_lookup: Callable[[str], Optional[Dict[str, Any]]],
        okf_port: Any = None,
        git_port: Any = None,
        repo_host: Any = None,
        peer_notifier: Optional[PeerNotifier] = None,
    ) -> None:
        self.collab_repo = collab_repo
        self.project_lookup = project_lookup
        self.okf_port = okf_port
        self.git_port = git_port
        self.repo_host = repo_host
        self.peer_notifier = peer_notifier

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
