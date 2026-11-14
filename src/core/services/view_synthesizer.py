"""
Sintetizador de Vistas del Espacio de Trabajo (WorkspaceViewSynthesizer).

Consolida en una lectura coherente el estado Kanban, metadatos/tags/freshness OKF,
ramas/commits Git, colaboradores y bandeja HITL sin exponer SQL crudo.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.ports.connectors import GitWorkspacePort, OkfGovernancePort
from src.core.ports.repositories import (
    CollaborationRepositoryPort,
    KanbanRepositoryPort,
    ProjectRepositoryPort,
    TribunalRepositoryPort,
)


class WorkspaceViewSynthesizer:
    """Sintetiza el estado unificado del workspace de un proyecto para UI/CLI/MCP."""

    def __init__(
        self,
        project_repo: ProjectRepositoryPort,
        kanban_repo: KanbanRepositoryPort,
        tribunal_repo: TribunalRepositoryPort,
        collab_repo: CollaborationRepositoryPort,
        okf_port: Optional[OkfGovernancePort] = None,
        git_port: Optional[GitWorkspacePort] = None,
    ) -> None:
        self.project_repo = project_repo
        self.kanban_repo = kanban_repo
        self.tribunal_repo = tribunal_repo
        self.collab_repo = collab_repo
        self.okf_port = okf_port
        self.git_port = git_port

    def synthesize_sync_fingerprint(self, project_uuid: str) -> Dict[str, Any]:
        """Devuelve la huella reactiva de sincronización del proyecto."""
        return self.project_repo.get_project_sync_fingerprint(project_uuid)

    def synthesize_workspace(
        self,
        project_uuid: str,
        viewer_node_uuid: Optional[str] = None,
        include_git: bool = True,
        include_okf: bool = True,
    ) -> Dict[str, Any]:
        """Sintetiza tablero Kanban (+tags OKF), gobernanza OKF, ramas Git y resumen HITL."""
        proj = self.project_repo.get_project_by_uuid(project_uuid)
        if not proj:
            return {"success": False, "error": f"Proyecto no encontrado: {project_uuid}"}

        summary = self.kanban_repo.get_project_cards_and_disputes_summary(project_uuid)
        cards: List[Dict[str, Any]] = summary.get("cards", [])
        hitl_bundle = self.tribunal_repo.get_pending_hitl_bundle(project_uuid, local_node=viewer_node_uuid)
        collaborators = self.collab_repo.get_project_collaborators(project_uuid)

        gov_raw = (proj.get("gov_repo_path") or "").strip()
        code_raw = (proj.get("code_repo_path") or "").strip()
        gov_path = Path(gov_raw) if gov_raw else None
        code_path = Path(code_raw) if code_raw else None

        okf_summary: Dict[str, Any] = {"folders": [], "tags_index": {"tags": {}, "items": []}}
        if include_okf and self.okf_port is not None and gov_path and gov_path.exists():
            try:
                folders = self.okf_port.list_okf_folders(gov_path)
                tags_idx = self.okf_port.get_tags_index(gov_path)
                okf_summary["folders"] = folders
                okf_summary["tags_index"] = tags_idx

                # Enriquecer tarjetas con tags YAML de OKF si existen
                tags_by_card: Dict[str, List[str]] = {}
                for item in tags_idx.get("items", []):
                    p = str(item.get("path", ""))
                    if "kanban/cards/" in p:
                        stem = Path(p).stem
                        tags_by_card[stem] = list(item.get("tags", []))
                for card in cards:
                    c_id = card.get("card_id", "")
                    if c_id in tags_by_card:
                        card["tags"] = tags_by_card[c_id]
                    else:
                        card.setdefault("tags", [])
            except Exception as exc:
                okf_summary["error"] = str(exc)
        else:
            for card in cards:
                card.setdefault("tags", [])

        git_summary: Dict[str, Any] = {"app": {"branches": [], "commits": []}, "gov": {"branches": [], "commits": []}}
        if include_git and self.git_port is not None:
            if code_path and code_path.exists():
                try:
                    git_summary["app"] = self.git_port.get_git_branches_and_log(code_path)
                except Exception:
                    pass
            if gov_path and gov_path.exists():
                try:
                    git_summary["gov"] = self.git_port.get_git_branches_and_log(gov_path)
                except Exception:
                    pass

        open_disputes = hitl_bundle.get("open_disputes", [])
        cards_in_review = hitl_bundle.get("cards_in_review", [])

        return {
            "success": True,
            "project": proj,
            "project_uuid": project_uuid,
            "cards": cards,
            "dispute_tickets": summary.get("dispute_tickets", []),
            "a2a_messages_count": len(summary.get("a2a_messages", [])),
            "hitl": {
                "open_disputes_count": len(open_disputes),
                "cards_in_review_count": len(cards_in_review),
                "open_disputes": open_disputes,
                "cards_in_review": cards_in_review,
            },
            "collaborators": collaborators,
            "okf": okf_summary,
            "git": git_summary,
            "sync_fingerprint": self.project_repo.get_project_sync_fingerprint(project_uuid),
        }
