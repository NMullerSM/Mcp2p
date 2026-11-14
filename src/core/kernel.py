"""
CoreKernel tipo "Hub USB" para la arquitectura hexagonal de MCP2P.

Centraliza repositorios, bus de eventos de dominio, servicios de aplicación y puertos
enchufables (conectores OKF, malla P2P multi-seed, puerta de seguridad de extensiones,
REST saliente, terminal de agentes, sincronización Git y criptografía) sin imponer
singletons globales.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.a2a.bus import A2ABus
from src.core.events import DomainEventBus
from src.core.ports.repositories import (
    CollaborationRepositoryPort,
    ExtensionRegistryRepositoryPort,
    KanbanRepositoryPort,
    OkfMetadataRepositoryPort,
    ProjectRepositoryPort,
    TribunalRepositoryPort,
)
from src.core.services.kanban_service import KanbanGovernanceService
from src.core.services.tribunal_service import DialecticTribunalService
from src.core.services.view_synthesizer import WorkspaceViewSynthesizer
from src.storage.db import DatabaseManager

logger = logging.getLogger("mcp2p.core.kernel")


class CoreKernel:
    """
    Núcleo hexagonal del workspace MCP2P.

    Actúa como un "Hub USB" donde los adaptadores salientes se enchufan por nombre
    de puerto y pueden ser consultados, sustituidos en pruebas o inspeccionados
    de forma uniforme desde CLI, MCP, API Bridge o pruebas de integración.
    """

    PORT_OKF = "okf"
    PORT_MESH = "p2p_mesh"
    PORT_EXTENSION_GATE = "extension_gate"
    PORT_OUTBOUND_REST = "outbound_rest"
    PORT_AGENT_TERMINAL = "agent_terminal_matrix"
    PORT_GIT_SYNC = "git_sync"
    PORT_GIT_WORKSPACE = "git_workspace"
    PORT_CRYPTO = "crypto"

    STANDARD_PORTS: Tuple[str, ...] = (
        PORT_OKF,
        PORT_MESH,
        PORT_EXTENSION_GATE,
        PORT_OUTBOUND_REST,
        PORT_AGENT_TERMINAL,
        PORT_GIT_SYNC,
        PORT_CRYPTO,
    )

    def __init__(
        self,
        *,
        db: DatabaseManager,
        node_uuid: str,
        project_repo: ProjectRepositoryPort,
        kanban_repo: KanbanRepositoryPort,
        tribunal_repo: TribunalRepositoryPort,
        collab_repo: CollaborationRepositoryPort,
        okf_meta_repo: OkfMetadataRepositoryPort,
        extension_repo: ExtensionRegistryRepositoryPort,
        event_bus: DomainEventBus,
        bus: A2ABus,
        kanban_service: KanbanGovernanceService,
        tribunal_service: DialecticTribunalService,
        view_synthesizer: WorkspaceViewSynthesizer,
        profile: str = "custom",
        connectors: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._closed = False

        self.db = db
        self.node_uuid = node_uuid
        self.profile = profile

        # Repositorios hexagonales
        self.project_repo = project_repo
        self.kanban_repo = kanban_repo
        self.tribunal_repo = tribunal_repo
        self.collab_repo = collab_repo
        self.okf_meta_repo = okf_meta_repo
        self.extension_repo = extension_repo

        # Mensajería y eventos
        self.event_bus = event_bus
        self.bus = bus

        # Servicios de aplicación (con alias cortos usados por adaptadores CLI/MCP)
        self.kanban_service = kanban_service
        self.kanban = kanban_service
        self.tribunal_service = tribunal_service
        self.tribunal = tribunal_service
        self.view_synthesizer = view_synthesizer
        self.views = view_synthesizer

        self._connectors: Dict[str, Any] = {}
        if connectors:
            for name, connector in connectors.items():
                if connector is not None:
                    self.register_connector(name, connector, replace=True)

    # ------------------------------------------------------------------
    # Propiedades de conveniencia para conectores estándar
    # ------------------------------------------------------------------

    @property
    def okf_adapter(self) -> Any:
        return self.get_connector(self.PORT_OKF)

    @property
    def mesh_connector(self) -> Any:
        return self.get_connector(self.PORT_MESH)

    @property
    def extension_gate(self) -> Any:
        return self.get_connector(self.PORT_EXTENSION_GATE)

    @property
    def rest_connector(self) -> Any:
        return self.get_connector(self.PORT_OUTBOUND_REST)

    @property
    def terminal_connector(self) -> Any:
        return self.get_connector(self.PORT_AGENT_TERMINAL)

    @property
    def git_sync(self) -> Any:
        return self.get_connector(self.PORT_GIT_SYNC)

    @property
    def crypto(self) -> Any:
        return self.get_connector(self.PORT_CRYPTO)

    # ------------------------------------------------------------------
    # Hub USB: registro, consulta e introspección de conectores
    # ------------------------------------------------------------------

    def register_connector(self, name: str, connector: Any, *, replace: bool = True) -> Any:
        """Enchufa un conector en un puerto del Hub USB y sincroniza referencias de servicio."""
        port_name = str(name or "").strip()
        if not port_name:
            raise ValueError("El nombre del puerto/conector no puede estar vacío.")
        if connector is None:
            raise ValueError(f"El conector para '{port_name}' no puede ser None.")

        with self._lock:
            if not replace and port_name in self._connectors:
                raise ValueError(f"El puerto '{port_name}' ya tiene un conector registrado.")
            self._connectors[port_name] = connector

            if port_name == self.PORT_OKF:
                self.kanban_service.okf_port = connector
                self.view_synthesizer.okf_port = connector
            elif port_name in (self.PORT_GIT_WORKSPACE, self.PORT_GIT_SYNC):
                if hasattr(connector, "get_git_branches_and_log"):
                    self.view_synthesizer.git_port = connector

            return connector

    def unregister_connector(self, name: str, *, close_if_supported: bool = False) -> bool:
        """Desenchufa un conector del Hub USB; opcionalmente invoca su método close()."""
        port_name = str(name or "").strip()
        if not port_name:
            return False

        with self._lock:
            conn = self._connectors.pop(port_name, None)
            if conn is None:
                return False

            if port_name == self.PORT_OKF:
                self.kanban_service.okf_port = None
                self.view_synthesizer.okf_port = None
            elif port_name in (self.PORT_GIT_WORKSPACE, self.PORT_GIT_SYNC):
                if self.view_synthesizer.git_port is conn:
                    self.view_synthesizer.git_port = None

        if close_if_supported and callable(getattr(conn, "close", None)):
            try:
                conn.close()
            except Exception as exc:
                logger.warning("Error al cerrar conector '%s': %s", port_name, exc)
        return True

    def get_connector(self, name: str, default: Any = None) -> Any:
        """Obtiene el conector enchufado en `name`, o `default` si el puerto está vacío."""
        port_name = str(name or "").strip()
        if not port_name:
            return default
        with self._lock:
            return self._connectors.get(port_name, default)

    def _inspect_connector(self, name: str, conn: Any) -> Dict[str, Any]:
        """Construye un resumen no bloqueante y serializable del estado de un conector."""
        entry: Dict[str, Any] = {
            "name": name,
            "plugged": conn is not None,
            "standard_port": name in self.STANDARD_PORTS,
            "type": f"{type(conn).__module__}.{type(conn).__name__}" if conn is not None else None,
            "status": "unplugged" if conn is None else "plugged",
            "details": {},
        }
        if conn is None:
            return entry

        details: Dict[str, Any] = {}
        try:
            if callable(getattr(conn, "is_available", None)):
                available = bool(conn.is_available())
                details["available"] = available
                if not available:
                    entry["status"] = "stub_unavailable"
            if callable(getattr(conn, "get_circuit_state", None)):
                details["circuit_state"] = conn.get_circuit_state()
            if callable(getattr(conn, "get_gates_status", None)):
                gates = conn.get_gates_status()
                details["gates_count"] = len(gates)
                details["unlocked_gates"] = [g.get("connector_id") for g in gates if g.get("unlocked")]
            if callable(getattr(conn, "list_seed_nodes", None)):
                seeds = conn.list_seed_nodes()
                details["seed_count"] = len(seeds)
            if callable(getattr(conn, "get_peers", None)):
                peers = conn.get_peers()
                details["peer_count"] = len(peers)
            if callable(getattr(conn, "get_status", None)) and name == self.PORT_AGENT_TERMINAL:
                term_status = conn.get_status()
                if isinstance(term_status, dict):
                    details.update(term_status)
            if hasattr(conn, "public_key_base64") and name == self.PORT_CRYPTO:
                pk = getattr(conn, "public_key_base64", "")
                details["has_public_key"] = bool(pk)
            if hasattr(conn, "transport_mode"):
                details["transport_mode"] = getattr(conn, "transport_mode", None)
        except Exception as exc:
            entry["status"] = "error"
            details["inspection_error"] = str(exc)

        entry["details"] = details
        return entry

    def list_connectors_status(self) -> List[Dict[str, Any]]:
        """Lista el estado de todos los puertos estándar y conectores adicionales enchufados."""
        with self._lock:
            snapshot = dict(self._connectors)

        ordered_names: List[str] = list(self.STANDARD_PORTS)
        for custom_name in sorted(snapshot.keys()):
            if custom_name not in self.STANDARD_PORTS:
                ordered_names.append(custom_name)

        return [self._inspect_connector(name, snapshot.get(name)) for name in ordered_names]

    def run_kernel_self_test(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        """
        Ejecuta un autodiagnóstico determinista del núcleo, persistencia SQLite,
        servicios y estado de conectores enchufados (sin llamadas de red externas).
        """
        errors: List[str] = []
        checks: Dict[str, Any] = {
            "kernel_open": not self._closed,
            "db_ok": False,
            "repositories_ok": False,
            "services_ok": False,
            "event_bus_ok": self.event_bus is not None,
            "a2a_bus_ok": self.bus is not None,
        }

        if self._closed:
            errors.append("CoreKernel ya fue cerrado.")
        else:
            try:
                projects = self.project_repo.list_projects()
                checks["db_ok"] = True
                checks["projects_count"] = len(projects)
            except Exception as exc:
                errors.append(f"Error consultando SQLite/ProjectRepository: {exc}")

            checks["repositories_ok"] = all(
                repo is not None
                for repo in (
                    self.project_repo,
                    self.kanban_repo,
                    self.tribunal_repo,
                    self.collab_repo,
                    self.okf_meta_repo,
                    self.extension_repo,
                )
            )
            checks["services_ok"] = all(
                svc is not None
                for svc in (self.kanban_service, self.tribunal_service, self.view_synthesizer)
            )

            if project_uuid:
                try:
                    fp = self.project_repo.get_project_sync_fingerprint(project_uuid)
                    checks["project_fingerprint_ok"] = True
                    checks["project_sync_fingerprint"] = fp
                    proj = self.project_repo.get_project_by_uuid(project_uuid)
                    if proj and proj.get("gov_repo_path") and self.okf_adapter is not None:
                        gov_path = Path(str(proj["gov_repo_path"]))
                        checks["project_gov_path_exists"] = gov_path.exists()
                except Exception as exc:
                    checks["project_fingerprint_ok"] = False
                    errors.append(f"Error inspeccionando proyecto '{project_uuid}': {exc}")

        connectors = self.list_connectors_status()
        for c in connectors:
            if c.get("status") == "error":
                errors.append(
                    f"Conector '{c['name']}' reportó error de inspección: {c.get('details', {}).get('inspection_error')}"
                )

        ok = (
            not errors
            and checks["kernel_open"]
            and checks["db_ok"]
            and checks["repositories_ok"]
            and checks["services_ok"]
            and checks["event_bus_ok"]
            and checks["a2a_bus_ok"]
        )
        return {
            "success": ok,
            "ok": ok,
            "profile": self.profile,
            "node_uuid": self.node_uuid,
            "db_path": str(getattr(self.db, "db_path", "")),
            "checks": checks,
            "connectors": connectors,
            "errors": errors,
        }

    def close(self, *, close_db: bool = True) -> None:
        """Cierra de forma idempotente los conectores enchufados y el pool SQLite."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            connectors_to_close = list(self._connectors.items())
            self._connectors.clear()

        seen_ids: set[int] = set()
        for name, conn in connectors_to_close:
            if conn is None or id(conn) in seen_ids:
                continue
            seen_ids.add(id(conn))
            close_fn = getattr(conn, "close", None)
            if callable(close_fn):
                try:
                    close_fn()
                except Exception as exc:
                    logger.warning("Error cerrando conector '%s': %s", name, exc)

        if close_db and self.db is not None:
            try:
                self.db.close()
            except Exception as exc:
                logger.warning("Error cerrando DatabaseManager en CoreKernel.close(): %s", exc)
