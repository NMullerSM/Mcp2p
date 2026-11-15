"""Puertos hexagonales (Protocolos) para repositorios y conectores externos."""

from src.core.ports.connectors import (
    AgentTerminalPort,
    ExtensionGatePort,
    GitWorkspacePort,
    OkfGovernancePort,
    PeerMeshPort,
    RestConnectorPort,
)
from src.core.ports.workspace_effects import (
    CardProjectionPort,
    GitRepositoryScaffoldPort,
    ProjectProvisioningPort,
)
from src.core.ports.repositories import (
    CollaborationRepositoryPort,
    ExtensionRegistryRepositoryPort,
    KanbanRepositoryPort,
    OkfMetadataRepositoryPort,
    ProjectRepositoryPort,
    TribunalRepositoryPort,
)

__all__ = [
    "ProjectRepositoryPort",
    "KanbanRepositoryPort",
    "TribunalRepositoryPort",
    "CollaborationRepositoryPort",
    "OkfMetadataRepositoryPort",
    "ExtensionRegistryRepositoryPort",
    "OkfGovernancePort",
    "PeerMeshPort",
    "ExtensionGatePort",
    "RestConnectorPort",
    "AgentTerminalPort",
    "GitWorkspacePort",
    "CardProjectionPort",
    "ProjectProvisioningPort",
    "GitRepositoryScaffoldPort",
]
