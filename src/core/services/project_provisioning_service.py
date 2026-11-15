"""Casos de uso de los efectos en disco de la persistencia (EGM-55).

- `ProjectProvisioningService`: andamiaje de un proyecto recién creado (OKF estándar en el repo
  de gobernanza vía `OkfGovernancePort.initialize_project_okf` y, si se pide, doble repo git vía
  `GitRepositoryScaffoldPort`).
- `OkfCardProjector`: proyección de una tarjeta Kanban a su YAML OKF y commit de gobernanza vía
  `OkfGovernancePort.write_card_yaml` / `commit_gov_file`.

Antes vivían dentro de `DatabaseManager.create_project` y `_sync_card_to_okf_yaml`. El
comportamiento es idéntico; lo que cambia es el dueño y la dirección de la dependencia.

Ambos reciben `okf_port` (instancia) u `okf_factory` (callable sin argumentos): el cableado por
defecto usa la fábrica para crear el adaptador OKF en cada operación, como hacía `db.py`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, Optional


class _OkfHolder:
    def __init__(self, okf_port: Optional[Any], okf_factory: Optional[Callable[[], Any]]) -> None:
        if (okf_port is None) == (okf_factory is None):
            raise ValueError("Indica exactamente uno de okf_port u okf_factory")
        self._okf_port = okf_port
        self._okf_factory = okf_factory

    def _okf(self) -> Any:
        return self._okf_port if self._okf_port is not None else self._okf_factory()  # type: ignore[misc]


def build_card_commit_message(card: Dict[str, Any]) -> str:
    """Mensaje de commit de gobernanza de una tarjeta: id, estado y título; descripción en el cuerpo."""
    card_id = card.get("card_id") or card.get("id") or "UNKNOWN"
    status = card.get("status", "")
    title = (card.get("title") or "").strip()
    description = (card.get("description") or "").strip()
    message = f"sync(kanban): {card_id} [{status}] {title}"
    if description:
        message += f"\n\n{description}"
    return message


class OkfCardProjector(_OkfHolder):
    """Implementa `CardProjectionPort` sobre `OkfGovernancePort`."""

    def __init__(self, okf_port: Optional[Any] = None, *, okf_factory: Optional[Callable[[], Any]] = None) -> None:
        super().__init__(okf_port, okf_factory)

    def project_card(self, gov_repo_path: Path, card: Dict[str, Any]) -> None:
        okf = self._okf()
        yaml_path = okf.write_card_yaml(gov_repo_path, card)
        # Una tarjeta en revisión no se empuja todavía: el push llega con la decisión HITL.
        should_push = card.get("status", "") != "in_review"
        okf.commit_gov_file(gov_repo_path, yaml_path, build_card_commit_message(card), push_now=should_push)


class ProjectProvisioningService(_OkfHolder):
    """Implementa `ProjectProvisioningPort`: OKF estándar y, con `init_git`, el doble repo git."""

    def __init__(
        self,
        okf_port: Optional[Any] = None,
        repo_scaffolder: Optional[Any] = None,
        *,
        okf_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        super().__init__(okf_port, okf_factory)
        self._scaffolder = repo_scaffolder

    def provision_project(
        self,
        *,
        name: str,
        project_uuid: str,
        clean_name: str,
        app_dir: Path,
        gov_dir: Path,
        init_git: bool,
    ) -> None:
        self._okf().initialize_project_okf(gov_dir, name, project_uuid)
        if init_git and self._scaffolder is not None:
            self._scaffolder.scaffold_project_repositories(
                name=name,
                project_uuid=project_uuid,
                clean_name=clean_name,
                app_dir=app_dir,
                gov_dir=gov_dir,
            )


class NullWorkspaceEffects:
    """Proyector y aprovisionador sin efectos, para quien quiera persistencia SQL pura."""

    def project_card(self, gov_repo_path: Path, card: Dict[str, Any]) -> None:
        return None

    def provision_project(self, **_kwargs: Any) -> None:
        return None
