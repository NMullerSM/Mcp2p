"""Cableado por defecto de los efectos en disco de `DatabaseManager` (EGM-55).

Es el único sitio que une el adaptador OKF real y el andamiaje git con los casos de uso del
núcleo. Lo usan `ApplicationFactory` (explícitamente) y `DatabaseManager` (como respaldo cuando
alguien lo construye a secas), de modo que toda mutación de tarjeta sigue escribiendo su YAML y
todo `create_project` sigue andamiando los repos, entre por donde entre.
"""

from __future__ import annotations

import importlib
from typing import Any, Tuple

from src.adapters.provisioning.git_scaffolder import SubprocessGitRepoScaffolder
from src.core.services.project_provisioning_service import OkfCardProjector, ProjectProvisioningService


def _new_okf_adapter() -> Any:
    # Resolución en cada operación (como hacía db.py): respeta parches de `okf_adapter.OKFAdapter`.
    return importlib.import_module("src.storage.okf_adapter").OKFAdapter()


def build_default_workspace_effects() -> Tuple[OkfCardProjector, ProjectProvisioningService]:
    """Devuelve `(card_projector, project_provisioner)` con OKF real y git por subprocess."""
    return (
        OkfCardProjector(okf_factory=_new_okf_adapter),
        ProjectProvisioningService(repo_scaffolder=SubprocessGitRepoScaffolder(), okf_factory=_new_okf_adapter),
    )
