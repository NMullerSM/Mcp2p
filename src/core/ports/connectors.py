"""
Puertos hexagonales (Protocolos) para conectores enchufables del CoreKernel ("Hub USB").
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class OkfGovernancePort(Protocol):
    """Puerto para gestión de gobernanza OKF, carpetas, tags, freshness e integridad."""

    def validate_okf_integrity(self, gov_base_path: str | Path) -> Dict[str, Any]: ...

    # EGM-56: efectos del laudo ratificado del Tribunal (ruling + precedentes + commit).
    def export_precedents_index(self, gov_base_path: str | Path) -> Any: ...

    def commit_gov_file(
        self,
        gov_base_path: str | Path,
        file_path: Any,
        commit_message: str,
        push_now: bool = False,
    ) -> bool: ...

    def get_okf_adrs(self, gov_base_path: str | Path) -> List[Dict[str, Any]]: ...

    def get_okf_skills(self, gov_base_path: str | Path) -> List[Dict[str, Any]]: ...

    def move_decision_lifecycle(
        self,
        gov_base_path: str | Path,
        dec_id: str,
        target_state: str,
        reason_or_consequences: str,
        signature_block: Optional[Dict[str, Any]] = None,
    ) -> Path: ...

    def reconcile_kanban_from_okf(
        self,
        gov_base_path: str | Path,
        project_uuid: str,
        db_manager: Optional[Any] = None,
    ) -> Dict[str, int]: ...

    def list_okf_folders(self, gov_base_path: str | Path) -> List[Dict[str, Any]]: ...

    def add_okf_folder(
        self,
        gov_base_path: str | Path,
        folder_path: str,
        category: str = "custom",
        description: str = "",
    ) -> Dict[str, Any]: ...

    def remove_okf_folder(
        self,
        gov_base_path: str | Path,
        folder_path: str,
        archive_if_not_empty: bool = True,
    ) -> Dict[str, Any]: ...

    def rename_okf_folder(
        self, gov_base_path: str | Path, old_path: str, new_path: str
    ) -> Dict[str, Any]: ...

    def get_okf_freshness_report(
        self, code_repo_path: str | Path, gov_base_path: str | Path, sla_days: int = 14
    ) -> Dict[str, Any]: ...

    def get_tags_index(self, gov_base_path: str | Path) -> Dict[str, Any]: ...

    def update_artifact_tags(
        self, gov_base_path: str | Path, rel_path: str, tags: List[str]
    ) -> Dict[str, Any]: ...

    def filter_by_tags(
        self, gov_base_path: str | Path, tags: List[str], match_all: bool = False
    ) -> List[Dict[str, Any]]: ...


@runtime_checkable
class PeerNetworkPort(Protocol):
    """
    Puerto secundario ÚNICO de red entre pares (EGM-48, `22_ §3.5`, `23_` Tarea 3.1, ver `25_`).

    Contrato multi-parte direccionado por `node_uuid`/`project_uuid` que unifica el
    listener LAN firmado (`LanWebhookNetworkConnector`, envuelve `P2PNotificationService`),
    el canal 1:1 (`DirectPeerNetworkConnector`, envuelve un `P2PTransport` WebRTC/Null) y
    el supervisor con *circuit breaker* (`SafePeerNetworkSupervisor`).

    Un "envelope" es un `dict` de aplicación con clave `type` (`join_request`,
    `join_decision`, `sync_pulse`, ...). La firma/verificación Ed25519 es responsabilidad
    de cada conector según su medio. Ningún método lanza por fallos de red: degradan a
    `False` / resultados con `failed`.
    """

    connector_mode: str

    def start_listening(
        self, local_node_uuid: str, on_envelope: Callable[[Dict[str, Any]], None]
    ) -> bool: ...

    def send_to(self, target_node_uuid: str, envelope: Dict[str, Any]) -> bool: ...

    def broadcast(self, project_uuid: str, envelope: Dict[str, Any]) -> Dict[str, Any]: ...

    def get_presence(self, project_uuid: Optional[str] = None) -> List[Dict[str, Any]]: ...

    def reconfigure(self, settings: Dict[str, Any]) -> Dict[str, Any]: ...

    def get_status(self) -> Dict[str, Any]: ...

    def close(self) -> None: ...


@runtime_checkable
class PeerMeshPort(Protocol):
    """
    Puerto de registro de malla multi-seed (seeds, pares confiables y seguridad de sobres).

    Alcance tras `25_` (EGM-48): NO es el transporte de producción. `MultiPeerMeshConnector`
    se conserva como registro de seeds/pares y motor de sobres firmados con anti-replay,
    anti-flood y circuit breaker por par, que hoy solo consume la UI del Laboratorio. Su
    formato de sobre es incompatible con el listener LAN (`/webhook/p2p` verifica la firma
    sobre `payload`, el mesh firma el núcleo del sobre), y nada en producción llama a
    `send_to_peer`/`ingest_envelope`. Todo transporte nuevo debe implementar
    `PeerNetworkPort`; este Protocolo queda congelado (sin métodos nuevos) hasta que su
    verificación de sobres se reutilice como capa de `PeerNetworkPort`.
    """

    def add_seed_node(self, seed_url: str) -> List[str]: ...

    def remove_seed_node(self, seed_url: str) -> List[str]: ...

    def list_seed_nodes(self) -> List[str]: ...

    def register_peer(self, node_uuid: str, endpoint: str = "", public_key: str = "") -> Dict[str, Any]: ...

    def unregister_peer(self, node_uuid: str) -> bool: ...

    def get_peers(self) -> List[Dict[str, Any]]: ...

    def send_to_peer(self, target_node_uuid: str, payload: Dict[str, Any]) -> Dict[str, Any]: ...

    def broadcast_envelope(self, payload: Dict[str, Any]) -> Dict[str, Any]: ...

    def ingest_envelope(self, envelope: Dict[str, Any]) -> Dict[str, Any]: ...

    def close(self) -> None: ...


@runtime_checkable
class ExtensionGatePort(Protocol):
    """Puerto de seguridad Default-Deny ("Puerta con Llave") para conectores externos."""

    def unlock_connector(
        self,
        manifest: Any,
        trusted_owner_pubkey: str,
        actor_kind: str = "human_ui",
    ) -> Dict[str, Any]: ...

    def lock_connector(self, connector_id: str) -> Dict[str, Any]: ...

    def is_unlocked(self, connector_id: str) -> bool: ...

    def require_unlocked(self, connector_id: str) -> Any: ...

    def get_gates_status(self) -> List[Dict[str, Any]]: ...


@runtime_checkable
class RestConnectorPort(Protocol):
    """Puerto saliente HTTP/REST controlado por ExtensionSecurityGate y anti-SSRF."""

    def validate_url(self, url: str) -> Dict[str, Any]: ...

    def request(
        self,
        url: str,
        method: str = "GET",
        headers: Optional[Dict[str, str]] = None,
        body: Optional[bytes | str] = None,
    ) -> Dict[str, Any]: ...

    def fetch_json(
        self,
        url: str,
        method: str = "GET",
        headers: Optional[Dict[str, str]] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]: ...


@runtime_checkable
class AgentTerminalPort(Protocol):
    """Puerto para la futura malla de terminales interactivas de agentes (o NullAgentTerminalConnector)."""

    def get_status(self) -> Dict[str, Any]: ...

    def list_sessions(self) -> List[Dict[str, Any]]: ...

    def spawn_session(self, *args: Any, **kwargs: Any) -> Dict[str, Any]: ...

    def send_input(self, *args: Any, **kwargs: Any) -> Dict[str, Any]: ...

    def close_session(self, *args: Any, **kwargs: Any) -> Dict[str, Any]: ...


@runtime_checkable
class GitWorkspacePort(Protocol):
    """Puerto para operaciones de repositorios Git locales y remotos."""

    def get_git_branches_and_log(self, repo_path: str | Path) -> Dict[str, Any]: ...

    def has_remote(self, repo_path: str | Path, remote: str = "origin") -> bool: ...

    def git_fetch(
        self, repo_path: str | Path, remote: str = "origin", pat: Optional[str] = None
    ) -> Dict[str, Any]: ...

    def git_pull(
        self,
        repo_path: str | Path,
        remote: str = "origin",
        branch: Optional[str] = None,
        rebase: bool = False,
        pat: Optional[str] = None,
    ) -> Dict[str, Any]: ...

    def git_push_branch(
        self,
        repo_path: str | Path,
        branch: str = "main",
        remote: str = "origin",
        pat: Optional[str] = None,
    ) -> Dict[str, Any]: ...

    def git_merge(
        self, repo_path: str | Path, source_branch: str, target_branch: str = "main"
    ) -> Dict[str, Any]: ...
