"""Reglas puras del flujo de unión a proyectos P2P (EGM-57).

Antes eran `staticmethod` de `SystemProjectsUIAdapter`/`CollabMeshUIAdapter`; viven en el
dominio para que `ProjectCollaborationService` las use sin depender de la capa `app`. Los
adaptadores conservan sus nombres delegando aquí.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def is_pending_join_stub(proj: Optional[Dict[str, Any]]) -> bool:
    """Un proyecto es *stub* de unión mientras su `join_status` no sea `active`."""
    if not proj:
        return False
    return (proj.get("join_status") or "active") != "active"


def pending_join_refusal(proj: Dict[str, Any], action: str) -> Dict[str, Any]:
    return {
        "success": False,
        "status": "join_pending",
        "join_status": proj.get("join_status"),
        "error": (
            f"No se puede {action}: la unión a '{proj.get('name', proj.get('project_uuid'))}' "
            "todavía está pendiente de aprobación por el dueño del proyecto."
        ),
    }


def parse_github_owner_repo(raw_url: str) -> Optional[Tuple[str, str]]:
    m = re.search(r"github\.com[/:]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", (raw_url or "").strip())
    if not m:
        return None
    return m.group(1), m.group(2)


def join_stub_repo_dirs(
    p_name: str, p_uuid: str, workspace_root: Path, base_path: Optional[str] = None
) -> Tuple[Path, Path, Path]:
    """`(base_dir, app_dir, gov_dir)` del doble repositorio de un proyecto al que se une el nodo."""
    clean_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in (p_name or "proyecto").lower())
    short_id = p_uuid.replace("proj_", "")[:8]
    base_dir = Path(base_path) if base_path else (Path(workspace_root) / f"{clean_name}_{short_id}")
    return base_dir, base_dir / f"app_{clean_name}", base_dir / f"okf_{clean_name}_{short_id}"
