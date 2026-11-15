"""
Fábrica de Composición (ApplicationFactory) sin dependencia de singletons.

Construye instancias independientes de `CoreKernel` para cada perfil de ejecución:
- `create_kernel`: ensamblado configurable completo.
- `create_desktop_kernel`: perfil desktop con conectores estándar enchufados.
- `create_headless_kernel`: perfil daemon / MCP server con malla P2P multi-seed.
- `create_mcp_kernel`: perfil servidor MCP (agentes) con OKF y sin crypto/P2P.
- `create_fast_cli_kernel`: perfil CLI rápido (<50ms) sin carga de UI, crypto ni P2P.
- `create_test_kernel`: perfil aislado para pruebas unitarias e integración con inyección de dobles.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union

from src.a2a.bus import A2ABus
from src.adapters.persistence.sqlite_repositories import (
    SqliteCollaborationRepository,
    SqliteExtensionRegistryRepository,
    SqliteKanbanRepository,
    SqliteOkfMetadataRepository,
    SqliteProjectRepository,
    SqliteTribunalRepository,
)
from src.runtime.identity import local_node_uuid
from src.core.events import DomainEventBus
from src.core.kernel import CoreKernel
from src.core.services.kanban_service import KanbanGovernanceService
from src.core.services.tribunal_service import DialecticTribunalService
from src.core.services.view_synthesizer import WorkspaceViewSynthesizer
from src.storage.db import DatabaseManager, resolve_db_path

logger = logging.getLogger("mcp2p.core.factory")


class ApplicationFactory:
    """
    Composition Root hexagonal sin singletons obligatorios.

    Cada invocación crea su propio grafo de repositorios, servicios, buses y
    conectores (salvo cuando el llamador inyecta explícitamente una instancia).
    """

    @staticmethod
    def _build_default_okf_adapter() -> Any:
        from src.storage.okf_adapter import OKFAdapter

        return OKFAdapter()

    @staticmethod
    def _build_default_crypto(key_path: Optional[Union[str, Path]] = None) -> Any:
        from src.p2p.crypto import NodeCryptoManager

        return NodeCryptoManager(key_path=key_path)

    @staticmethod
    def _build_default_extension_bundle() -> tuple[Any, Any, Any]:
        from src.adapters.outbound.extension_gate import (
            ExtensionSecurityGate,
            NullAgentTerminalConnector,
            OutboundRestConnector,
        )

        gate = ExtensionSecurityGate()
        rest = OutboundRestConnector(gate=gate)
        terminal = NullAgentTerminalConnector(gate=gate)
        return gate, rest, terminal

    @staticmethod
    def _build_default_mesh_connector(
        *,
        node_uuid: str,
        crypto: Any,
        transport_mode: str,
        seed_nodes: Optional[Iterable[str]],
        extension_repo: SqliteExtensionRegistryRepository,
    ) -> Any:
        try:
            from src.p2p.mesh_adapter import MultiPeerMeshConnector
        except ImportError:
            return None

        resolved_seeds: List[str] = []
        if seed_nodes is not None:
            resolved_seeds = [str(s).strip() for s in seed_nodes if str(s).strip()]
        else:
            try:
                for item in extension_repo.list_seed_nodes():
                    if isinstance(item, dict):
                        url = str(item.get("seed_url") or item.get("url") or "").strip()
                    else:
                        url = str(item).strip()
                    if url:
                        resolved_seeds.append(url)
            except Exception as exc:
                logger.debug("No se pudieron cargar seed_nodes persistidos: %s", exc)

        return MultiPeerMeshConnector(
            node_uuid=node_uuid,
            crypto_manager=crypto,
            transport_mode=transport_mode,
            seed_nodes=resolved_seeds or None,
        )

    @staticmethod
    def _build_default_git_sync(db: DatabaseManager) -> Any:
        from src.storage.github_sync import GitHubSyncManager

        return GitHubSyncManager(db_manager=db)

    @classmethod
    def create_kernel(
        cls,
        db_path: Optional[Union[str, Path]] = None,
        db_manager: Optional[DatabaseManager] = None,
        node_uuid: Optional[str] = None,
        *,
        profile: str = "custom",
        load_default_connectors: bool = True,
        okf_adapter: Optional[Any] = None,
        mesh_connector: Optional[Any] = None,
        extension_gate: Optional[Any] = None,
        rest_connector: Optional[Any] = None,
        terminal_connector: Optional[Any] = None,
        git_sync: Optional[Any] = None,
        git_port: Optional[Any] = None,
        crypto: Optional[Any] = None,
        event_bus: Optional[DomainEventBus] = None,
        bus: Optional[A2ABus] = None,
        key_path: Optional[Union[str, Path]] = None,
        transport_mode: str = "lan_mesh",
        seed_nodes: Optional[Iterable[str]] = None,
        extra_connectors: Optional[Dict[str, Any]] = None,
    ) -> CoreKernel:
        """
        Ensambla un `CoreKernel` independiente inyectando dependencias reales u opcionales.
        """
        if db_manager is not None:
            db = db_manager
        else:
            resolved_path = resolve_db_path(db_path)
            # EGM-55: la fábrica es dueña del cableado de los efectos en disco de la persistencia.
            from src.adapters.provisioning.defaults import build_default_workspace_effects

            card_projector, project_provisioner = build_default_workspace_effects()
            db = DatabaseManager(
                db_path=resolved_path,
                card_projector=card_projector,
                project_provisioner=project_provisioner,
            )

        resolved_node_uuid = (node_uuid or "").strip() or local_node_uuid()

        # Repositorios modulares SQLite
        project_repo = SqliteProjectRepository(db)
        kanban_repo = SqliteKanbanRepository(db)
        tribunal_repo = SqliteTribunalRepository(db)
        collab_repo = SqliteCollaborationRepository(db)
        okf_meta_repo = SqliteOkfMetadataRepository(db)
        extension_repo = SqliteExtensionRegistryRepository(db)

        # Buses
        resolved_event_bus = event_bus if event_bus is not None else DomainEventBus()
        resolved_bus = bus if bus is not None else A2ABus(db=db)

        # Conectores enchufables
        resolved_okf = okf_adapter
        resolved_crypto = crypto
        resolved_gate = extension_gate
        resolved_rest = rest_connector
        resolved_terminal = terminal_connector
        resolved_mesh = mesh_connector
        resolved_git_sync = git_sync

        if load_default_connectors:
            if resolved_okf is None:
                resolved_okf = cls._build_default_okf_adapter()
            if resolved_crypto is None:
                resolved_crypto = cls._build_default_crypto(key_path=key_path)
            if resolved_gate is None or resolved_rest is None or resolved_terminal is None:
                default_gate, default_rest, default_terminal = cls._build_default_extension_bundle()
                if resolved_gate is None:
                    resolved_gate = default_gate
                if resolved_rest is None:
                    resolved_rest = default_rest
                if resolved_terminal is None:
                    resolved_terminal = default_terminal
            if resolved_mesh is None:
                resolved_mesh = cls._build_default_mesh_connector(
                    node_uuid=resolved_node_uuid,
                    crypto=resolved_crypto,
                    transport_mode=transport_mode,
                    seed_nodes=seed_nodes,
                    extension_repo=extension_repo,
                )
            if resolved_git_sync is None:
                resolved_git_sync = cls._build_default_git_sync(db)

        resolved_git_port = git_port
        if resolved_git_port is None and hasattr(resolved_git_sync, "get_git_branches_and_log"):
            resolved_git_port = resolved_git_sync

        # Servicios de dominio / aplicación
        # EGM-56: el efecto OKF/git del laudo vive en el núcleo y sale por OkfGovernancePort.
        ruling_publisher = None
        if resolved_okf is not None:
            from src.core.services.tribunal_ruling_publisher import OkfRulingPublisher

            ruling_publisher = OkfRulingPublisher(
                okf_port=resolved_okf,
                project_lookup=project_repo.get_project_by_uuid,
                jury_engine=resolved_bus.jury_engine,
            )
        tribunal_service = DialecticTribunalService(
            tribunal_repo=tribunal_repo,
            bus=resolved_bus,
            event_bus=resolved_event_bus,
            ruling_publisher=ruling_publisher,
        )
        if ruling_publisher is not None and getattr(resolved_bus, "ruling_publisher", None) is None:
            resolved_bus.ruling_publisher = ruling_publisher.publish
        kanban_service = KanbanGovernanceService(
            kanban_repo=kanban_repo,
            project_repo=project_repo,
            okf_port=resolved_okf,
            event_bus=resolved_event_bus,
            tribunal_service=tribunal_service,
        )
        view_synthesizer = WorkspaceViewSynthesizer(
            project_repo=project_repo,
            kanban_repo=kanban_repo,
            tribunal_repo=tribunal_repo,
            collab_repo=collab_repo,
            okf_port=resolved_okf,
            git_port=resolved_git_port,
        )

        connectors: Dict[str, Any] = {}
        if resolved_okf is not None:
            connectors[CoreKernel.PORT_OKF] = resolved_okf
        if resolved_mesh is not None:
            connectors[CoreKernel.PORT_MESH] = resolved_mesh
        if resolved_gate is not None:
            connectors[CoreKernel.PORT_EXTENSION_GATE] = resolved_gate
        if resolved_rest is not None:
            connectors[CoreKernel.PORT_OUTBOUND_REST] = resolved_rest
        if resolved_terminal is not None:
            connectors[CoreKernel.PORT_AGENT_TERMINAL] = resolved_terminal
        if resolved_git_sync is not None:
            connectors[CoreKernel.PORT_GIT_SYNC] = resolved_git_sync
        if resolved_git_port is not None and resolved_git_port is not resolved_git_sync:
            connectors[CoreKernel.PORT_GIT_WORKSPACE] = resolved_git_port
        if resolved_crypto is not None:
            connectors[CoreKernel.PORT_CRYPTO] = resolved_crypto
        if extra_connectors:
            for k, v in extra_connectors.items():
                if v is not None:
                    connectors[k] = v

        return CoreKernel(
            db=db,
            node_uuid=resolved_node_uuid,
            project_repo=project_repo,
            kanban_repo=kanban_repo,
            tribunal_repo=tribunal_repo,
            collab_repo=collab_repo,
            okf_meta_repo=okf_meta_repo,
            extension_repo=extension_repo,
            event_bus=resolved_event_bus,
            bus=resolved_bus,
            kanban_service=kanban_service,
            tribunal_service=tribunal_service,
            view_synthesizer=view_synthesizer,
            profile=profile,
            connectors=connectors,
        )

    @staticmethod
    def create_peer_network(
        *,
        node_uuid: str,
        crypto: Any,
        db: Optional[DatabaseManager] = None,
        signaling_url: str = "",
        lan_port: int = 42424,
        bind_host: Optional[str] = None,
        transport_builder: Optional[Any] = None,
        lan_service: Optional[Any] = None,
    ) -> Any:
        """
        Compone el `PeerNetworkPort` de producción (EGM-48): `SafePeerNetworkSupervisor`
        con el canal 1:1 (`direct`, WebRTC/Null según `signaling_url`) y el listener LAN
        firmado (`lan`, envuelve `P2PNotificationService`). Única composición de red del
        proceso; `main.py` ya no construye las dos pilas por separado.
        """
        from src.adapters.network import (
            DirectPeerNetworkConnector,
            LanWebhookNetworkConnector,
            SafePeerNetworkSupervisor,
            build_p2p_transport,
        )

        builder = transport_builder or (lambda url, c: build_p2p_transport(url, c))

        def _endpoint(node: str) -> Optional[str]:
            if db is None:
                return None
            row = db.get_node_by_uuid(node)
            return (row or {}).get("p2p_endpoint")

        def _project_peers(project_uuid: str) -> List[str]:
            if db is None:
                return []
            return [
                c["node_uuid"]
                for c in db.get_project_collaborators(project_uuid)
                if c.get("status") == "approved" and c.get("node_uuid")
            ]

        direct = DirectPeerNetworkConnector(
            transport_factory=lambda url: builder(url, crypto),
            signaling_url=signaling_url,
        )
        lan = LanWebhookNetworkConnector(
            node_uuid=node_uuid,
            crypto_manager=crypto,
            is_trusted_pubkey=getattr(db, "is_trusted_public_key", None),
            endpoint_resolver=_endpoint,
            project_peers_resolver=_project_peers,
            lan_port=lan_port,
            bind_host=bind_host,
            service=lan_service,
        )
        return SafePeerNetworkSupervisor({"direct": direct, "lan": lan})

    @classmethod
    def create_desktop_kernel(
        cls,
        db_path: Optional[Union[str, Path]] = None,
        **kwargs: Any,
    ) -> CoreKernel:
        """Crea el núcleo completo para el entorno Desktop (UI + OKF + P2P + ExtensionGate)."""
        kwargs.setdefault("profile", "desktop")
        kwargs.setdefault("load_default_connectors", True)
        return cls.create_kernel(db_path=db_path, **kwargs)

    @classmethod
    def create_headless_kernel(
        cls,
        db_path: Optional[Union[str, Path]] = None,
        **kwargs: Any,
    ) -> CoreKernel:
        """Crea el núcleo para ejecución headless / daemon / MCP server sin PyWebView."""
        kwargs.setdefault("profile", "headless")
        kwargs.setdefault("load_default_connectors", True)
        return cls.create_kernel(db_path=db_path, **kwargs)

    @classmethod
    def create_mcp_kernel(
        cls,
        db_path: Optional[Union[str, Path]] = None,
        **kwargs: Any,
    ) -> CoreKernel:
        """
        Crea el núcleo del servidor MCP (`MCPToolsHandler`): SQLite + repositorios + servicios
        + conector OKF. NO carga `NodeCryptoManager` (un proceso de agente nunca debe tener
        a mano la clave Ed25519 del humano), ni malla P2P, ni GitHubSync, ni ExtensionGate.
        """
        kwargs.setdefault("profile", "mcp_server")
        kwargs.setdefault("load_default_connectors", False)
        if kwargs.get("okf_adapter") is None:
            kwargs["okf_adapter"] = cls._build_default_okf_adapter()
        return cls.create_kernel(db_path=db_path, **kwargs)

    @classmethod
    def create_fast_cli_kernel(
        cls,
        db_path: Optional[Union[str, Path]] = None,
        **kwargs: Any,
    ) -> CoreKernel:
        """
        Crea el núcleo ligero para Fast CLI: solo SQLite + repositorios + servicios,
        sin cargar crypto, malla P2P ni adaptadores pesados.
        """
        kwargs.setdefault("profile", "fast_cli")
        kwargs.setdefault("load_default_connectors", False)
        return cls.create_kernel(db_path=db_path, **kwargs)

    @classmethod
    def create_test_kernel(
        cls,
        db_path: Optional[Union[str, Path]] = None,
        db_manager: Optional[DatabaseManager] = None,
        **kwargs: Any,
    ) -> CoreKernel:
        """
        Crea un núcleo aislado y determinista para pruebas, sin conectores por defecto
        salvo los que el test inyecte explícitamente o solicite con `load_default_connectors=True`.
        """
        kwargs.setdefault("profile", "test")
        kwargs.setdefault("load_default_connectors", False)
        return cls.create_kernel(db_path=db_path, db_manager=db_manager, **kwargs)
