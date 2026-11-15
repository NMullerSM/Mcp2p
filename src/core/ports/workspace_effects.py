"""Puertos de los efectos de sistema de archivos/git que acompañan a la persistencia (EGM-55).

`DatabaseManager` (adaptador SQLite) ya no instancia `OKFAdapter` ni ejecuta git: delega en estos
colaboradores inyectados. Así la proyección de tarjetas a YAML y el andamiaje de repos de un
proyecto nuevo tienen dueño explícito y pueden sustituirse (dobles de test, modo sin efectos).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Protocol, runtime_checkable


@runtime_checkable
class CardProjectionPort(Protocol):
    """Proyecta una tarjeta Kanban persistida a su YAML OKF versionado en el repo de gobernanza."""

    def project_card(self, gov_repo_path: Path, card: Dict[str, Any]) -> None: ...


@runtime_checkable
class ProjectProvisioningPort(Protocol):
    """Prepara en disco un proyecto recién creado (estructura OKF y, si se pide, repos git)."""

    def provision_project(
        self,
        *,
        name: str,
        project_uuid: str,
        clean_name: str,
        app_dir: Path,
        gov_dir: Path,
        init_git: bool,
    ) -> None: ...


@runtime_checkable
class GitRepositoryScaffoldPort(Protocol):
    """Inicializa el doble repositorio git (código + gobernanza con rama huérfana) y sus hooks.

    Es un puerto distinto de `GitWorkspacePort` porque su adaptador no puede depender de
    `DatabaseManager` (lo invoca el propio `create_project`) y porque es andamiaje local,
    no sincronización con remotos.
    """

    def scaffold_project_repositories(
        self,
        *,
        name: str,
        project_uuid: str,
        clean_name: str,
        app_dir: Path,
        gov_dir: Path,
    ) -> None: ...
