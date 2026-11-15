"""
MCP2P JavaScript API Bridge (Fachada Hexagonal Delgada).
Expone en PyWebView (`window.pywebview.api`) los sub-adaptadores UI por dominio,
delegando en el CoreKernel hexagonal sin SQL crudo.
"""

from __future__ import annotations

import inspect
import logging
import subprocess
import threading
import types
import weakref
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.a2a.bus import A2ABus
from src.adapters.outbound.extension_gate import ExtensionSecurityGate
from src.app.auto_updater import AutoUpdateManager
from src.app.identity import local_node_uuid
from src.app.paths import get_bundle_dir, get_user_data_dir
from src.app.ui_adapters import (
    CollabMeshUIAdapter,
    ExtensionsLabUIAdapter,
    GitBranchesUIAdapter,
    KanbanOkfUIAdapter,
    SystemProjectsUIAdapter,
    TribunalHitlUIAdapter,
)
from src.core.factory import ApplicationFactory
from src.p2p.crypto import NodeCryptoManager, SigningKeyLockedError, SigningKeyPassphraseError
from src.p2p.mesh_adapter import MultiPeerMeshConnector
from src.p2p.transport import NullTransportAdapter, P2PTransport
from src.storage.db import DatabaseManager
from src.storage.github_sync import GitHubSyncManager
from src.storage.okf_adapter import OKFAdapter

logger = logging.getLogger("mcp2p.api_bridge")

DEFAULT_SIGNALING_URL = ""

_PUBLIC_FACADE_TARGETS: "weakref.WeakKeyDictionary[Any, Any]" = weakref.WeakKeyDictionary()
_FACADE_INTERNAL_METHODS: frozenset[str] = frozenset({
    "as_public_js_api",
    "set_window",
    "set_transport",
    "ensure_local_node_registered",
})


def _make_public_rpc_wrapper(method_name: str, doc: Optional[str]) -> Any:
    def _rpc(facade_self: Any, *args: Any, **kwargs: Any) -> Any:
        target = _PUBLIC_FACADE_TARGETS[facade_self]
        return getattr(target, method_name)(*args, **kwargs)

    _rpc.__name__ = method_name
    _rpc.__qualname__ = f"PublicJsApiFacade.{method_name}"
    _rpc.__doc__ = doc
    return _rpc


class PublicJsApiFacade:
    """Fachada JS de superficie mínima para `window.pywebview.api` (EGM-58).

    Expone únicamente métodos RPC públicos de primer nivel de `ApiBridge`, sin
    guardar atributos de instancia que apunten a `bridge` ni a objetos internos
    (`core_kernel`, `db`, `crypto`, `extension_gate`, sub-adaptadores UI), evitando
    que `inject_pywebview` o `js_bridge_call`/`get_nested_attribute` alcancen
    miembros privados o `metodo.__self__`.
    """

    def __init__(self, bridge: "ApiBridge") -> None:
        _PUBLIC_FACADE_TARGETS[self] = bridge
        bridge_cls = type(bridge)
        for name, _ in inspect.getmembers(bridge_cls):
            if name.startswith("_") or name in _FACADE_INTERNAL_METHODS:
                continue
            member = getattr(bridge, name, None)
            if member is None or not callable(member):
                continue
            fn = _make_public_rpc_wrapper(name, getattr(member, "__doc__", None))
            setattr(self, name, types.MethodType(fn, self))


class ApiBridge:
    """Fachada puente delgada cuyos métodos son expuestos en `window.pywebview.api`."""

    def __init__(
        self,
        db_manager: Optional[DatabaseManager] = None,
        transport: Optional[P2PTransport] = None,
        core_kernel: Optional[Any] = None,
    ) -> None:
        self.db = db_manager or (
            core_kernel.db if core_kernel is not None else DatabaseManager.get_instance()
        )
        if core_kernel is None:
            core_kernel = ApplicationFactory.create_desktop_kernel(
                db_path=str(self.db.db_path),
                db_manager=self.db,
            )
        self.core_kernel = core_kernel
        self.bus = getattr(core_kernel, "bus", None) or A2ABus(db=self.db)
        self.okf = getattr(core_kernel, "okf_adapter", None) or OKFAdapter()
        self.crypto = getattr(core_kernel, "crypto", None) or NodeCryptoManager()
        self.github = getattr(core_kernel, "git_sync", None) or GitHubSyncManager(db_manager=self.db)
        self.extension_gate = getattr(core_kernel, "extension_gate", None) or ExtensionSecurityGate(db_manager=self.db)
        self.mesh = getattr(core_kernel, "mesh_connector", None) or MultiPeerMeshConnector(
            node_uuid=local_node_uuid(),
            crypto_manager=self.crypto,
        )

        self.transport: P2PTransport = transport or NullTransportAdapter()
        self._updater = AutoUpdateManager(get_bundle_dir(), get_user_data_dir())
        self._window: Optional[Any] = None
        self._retry_locks: Dict[str, threading.Lock] = {}
        self._retry_locks_guard = threading.Lock()
        self.active_project_uuid: Optional[str] = None

        # Sub-adaptadores UI por dominio funcional
        self.system_ui = SystemProjectsUIAdapter(self)
        self.kanban_ui = KanbanOkfUIAdapter(self)
        self.git_ui = GitBranchesUIAdapter(self)
        self.tribunal_ui = TribunalHitlUIAdapter(self)
        self.collab_ui = CollabMeshUIAdapter(self)
        self.extensions_ui = ExtensionsLabUIAdapter(self)

        self.transport.on_message(self._handle_transport_message)
        self._ensure_local_node()
        self.ensure_local_node_registered()
        self._ensure_active_project()

    # ==========================
    # Helpers internos preservados para compatibilidad y concurrencia
    # ==========================

    def _get_retry_lock(self, project_uuid: str) -> threading.Lock:
        return self.collab_ui.get_retry_lock(project_uuid)

    def _ensure_local_node(self) -> None:
        self.system_ui.ensure_local_node()

    @staticmethod
    def _is_pending_join_stub(proj: Optional[Dict[str, Any]]) -> bool:
        return SystemProjectsUIAdapter.is_pending_join_stub(proj)

    @staticmethod
    def _pending_join_refusal(proj: Dict[str, Any], action: str) -> Dict[str, Any]:
        return SystemProjectsUIAdapter.pending_join_refusal(proj, action)

    def _join_stub_repo_dirs(
        self, p_name: str, p_uuid: str, base_path: Optional[str] = None
    ) -> Tuple[Path, Path, Path]:
        return self.collab_ui.join_stub_repo_dirs(p_name, p_uuid, base_path=base_path)

    def _ensure_active_project(self) -> None:
        self.system_ui.ensure_active_project()

    @staticmethod
    def _parse_github_owner_repo(raw_url: str) -> Optional[Tuple[str, str]]:
        return SystemProjectsUIAdapter.parse_github_owner_repo(raw_url)

    def _get_blind_options_mapping(
        self, ticket_id: str, viewer_node: str
    ) -> Tuple[Dict[str, str], Dict[str, str]]:
        return self.tribunal_ui.get_blind_options_mapping(ticket_id, viewer_node)

    def _anonymize_text(self, text: str, canonical_to_alias: Dict[str, str]) -> str:
        return self.tribunal_ui.anonymize_text(text, canonical_to_alias)

    # ==========================
    # Sistema, Proyectos y Entorno
    # ==========================

    # ==========================
    # Clave de firma humana cifrada (EGM-53 / Gate 0c)
    # ==========================

    def get_signing_key_status(self) -> Dict[str, Any]:
        """Estado de la clave Ed25519 humana: {encrypted, locked, public_key}."""
        try:
            return {"success": True, **self.crypto.key_status()}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def set_signing_key_passphrase(self, passphrase: str) -> Dict[str, Any]:
        """
        Cifra la clave con una frase que teclea el humano y borra el PEM en claro (migración).
        La frase nunca se persiste ni se pasa a procesos de agente; la clave queda desbloqueada
        en esta sesión del proceso GUI hasta su caducidad.
        """
        try:
            self.crypto.set_passphrase(passphrase)
            self.crypto.unlock(passphrase)
            return {"success": True, **self.crypto.key_status()}
        except (SigningKeyLockedError, SigningKeyPassphraseError) as e:
            return {"success": False, "code": e.code, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def unlock_signing_key(self, passphrase: str) -> Dict[str, Any]:
        """Desbloquea en memoria (solo este proceso) la clave cifrada para firmar durante la sesión."""
        try:
            if not self.crypto.is_encrypted:
                return {"success": False, "code": "KEY_NOT_ENCRYPTED",
                        "error": "La clave no está cifrada; fija primero una frase de paso."}
            self.crypto.unlock(passphrase)
            return {"success": True, **self.crypto.key_status()}
        except SigningKeyPassphraseError as e:
            return {"success": False, "code": e.code, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def lock_signing_key(self) -> Dict[str, Any]:
        """Olvida la clave descifrada de la memoria del proceso GUI."""
        try:
            self.crypto.lock()
            return {"success": True, **self.crypto.key_status()}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def set_window(self, window: Any) -> None:
        self.system_ui.set_window(window)

    def set_ui_theme(self, theme: str) -> Dict[str, Any]:
        return self.system_ui.set_ui_theme(theme)

    def list_projects(self) -> Dict[str, Any]:
        return self.system_ui.list_projects()

    def create_project(
        self,
        name: str,
        code_repo_url: str = "",
        gov_repo_url: str = "",
        auto_publish: bool = False,
        reviewer_sla_hours: int = 24,
    ) -> Dict[str, Any]:
        return self.system_ui.create_project(
            name=name,
            code_repo_url=code_repo_url,
            gov_repo_url=gov_repo_url,
            auto_publish=auto_publish,
            reviewer_sla_hours=reviewer_sla_hours,
        )

    def select_project(self, project_uuid: str) -> Dict[str, Any]:
        return self.system_ui.select_project(project_uuid)

    def delete_project(self, project_uuid: str) -> Dict[str, Any]:
        return self.system_ui.delete_project(project_uuid)

    def get_mcp_config_snippet(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        return self.system_ui.get_mcp_config_snippet(project_uuid=project_uuid)

    def get_github_status(self) -> Dict[str, Any]:
        return self.system_ui.get_github_status()

    def save_github_pat(self, token: str) -> Dict[str, Any]:
        return self.system_ui.save_github_pat(token)

    def clear_github_pat(self) -> Dict[str, Any]:
        return self.system_ui.clear_github_pat()

    def start_auto_update(self, download_url: str, version: str) -> Dict[str, Any]:
        return self.system_ui.start_auto_update(download_url, version)

    def get_update_progress(self) -> Dict[str, Any]:
        return self.system_ui.get_update_progress()

    def apply_update_and_restart(self) -> Dict[str, Any]:
        return self.system_ui.apply_update_and_restart()

    def get_install_mode(self) -> Dict[str, Any]:
        return self.system_ui.get_install_mode()

    def get_active_project(self) -> Dict[str, Any]:
        return self.system_ui.get_active_project()

    def get_project_sync_pulse(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        return self.system_ui.get_project_sync_pulse(project_uuid=project_uuid)

    def ensure_local_node_registered(self) -> None:
        self.system_ui.ensure_local_node_registered()

    def get_mcp_and_agents_status(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        return self.system_ui.get_mcp_and_agents_status(project_uuid=project_uuid)

    def get_system_info(self) -> Dict[str, Any]:
        return self.system_ui.get_system_info()

    # ==========================
    # Colaboración P2P y Señalización
    # ==========================

    def generate_p2p_invite(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        return self.collab_ui.generate_p2p_invite(project_uuid=project_uuid)

    def get_signaling_server_url(self) -> str:
        return self.collab_ui.get_signaling_server_url()

    def set_signaling_server_url(self, url: str) -> Dict[str, Any]:
        return self.collab_ui.set_signaling_server_url(url)

    def set_transport(self, transport: P2PTransport) -> None:
        self.collab_ui.set_transport(transport)

    def _handle_transport_message(self, message: dict) -> None:
        self.collab_ui.handle_transport_message(message)

    def _handle_peer_envelope(self, envelope: dict) -> None:
        """Sobres entrantes del listener LAN del `PeerNetworkPort` (privado: no expuesto a JS)."""
        self.collab_ui.handle_peer_envelope(envelope)

    def join_p2p_project(self, invite_str: str) -> Dict[str, Any]:
        return self.collab_ui.join_p2p_project(invite_str)

    def _build_join_request_code(
        self, p_uuid: str, rendezvous_nonce: str, github_username: str, node_uuid: str
    ) -> str:
        return self.collab_ui.build_join_request_code(p_uuid, rendezvous_nonce, github_username, node_uuid)

    def get_pending_join_request_code(self, project_uuid: str) -> Dict[str, Any]:
        return self.collab_ui.get_pending_join_request_code(project_uuid)

    def retry_p2p_join(self, project_uuid: str) -> Dict[str, Any]:
        return self.collab_ui.retry_p2p_join(project_uuid)

    def _do_retry_p2p_join(self, project_uuid: str) -> Dict[str, Any]:
        return self.collab_ui.do_retry_p2p_join(project_uuid)

    def check_join_request_status(self, project_uuid: str) -> Dict[str, Any]:
        return self.collab_ui.check_join_request_status(project_uuid)

    def ingest_join_request(self, request_code: str, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        return self.collab_ui.ingest_join_request(request_code, project_uuid=project_uuid)

    def get_project_collaborators(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        return self.collab_ui.get_project_collaborators(project_uuid=project_uuid)

    def approve_collaborator(self, project_uuid: str, node_uuid: str) -> Dict[str, Any]:
        return self.collab_ui.approve_collaborator(project_uuid, node_uuid)

    def reject_collaborator(self, project_uuid: str, node_uuid: str) -> Dict[str, Any]:
        return self.collab_ui.reject_collaborator(project_uuid, node_uuid)

    def remove_collaborator(self, project_uuid: str, node_uuid: str) -> Dict[str, Any]:
        return self.collab_ui.remove_collaborator(project_uuid, node_uuid)

    def update_project_sla(self, project_uuid: str, sla_hours: int) -> Dict[str, Any]:
        return self.collab_ui.update_project_sla(project_uuid, sla_hours)

    # ==========================
    # Kanban, A2A y Gobernanza OKF
    # ==========================

    def get_okf_overview(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        return self.kanban_ui.get_okf_overview(project_uuid=project_uuid)

    def repair_project_okf(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        return self.kanban_ui.repair_project_okf(project_uuid=project_uuid)

    def get_kanban_board(self, status: Optional[str] = None) -> Dict[str, Any]:
        return self.kanban_ui.get_kanban_board(status=status)

    def create_kanban_card(
        self,
        card_id: str,
        title: str,
        description: str = "",
        acceptance_criteria: str = "",
        assignee_node_uuid: str = "",
    ) -> Dict[str, Any]:
        return self.kanban_ui.create_kanban_card(
            card_id=card_id,
            title=title,
            description=description,
            acceptance_criteria=acceptance_criteria,
            assignee_node_uuid=assignee_node_uuid,
        )

    def update_kanban_card_details(
        self,
        card_id: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        acceptance_criteria: Optional[str] = None,
        status: Optional[str] = None,
        branch_ref: Optional[str] = None,
        assignee_node_uuid: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.kanban_ui.update_kanban_card_details(
            card_id=card_id,
            title=title,
            description=description,
            acceptance_criteria=acceptance_criteria,
            status=status,
            branch_ref=branch_ref,
            assignee_node_uuid=assignee_node_uuid,
        )

    def claim_task_lease(
        self, card_id: str, node_uuid: str, duration_minutes: int = 30
    ) -> Dict[str, Any]:
        return self.kanban_ui.claim_task_lease(
            card_id=card_id,
            node_uuid=node_uuid,
            duration_minutes=duration_minutes,
        )

    def update_task_status(
        self, card_id: str, new_status: str, branch_ref: Optional[str] = None
    ) -> Dict[str, Any]:
        return self.kanban_ui.update_task_status(card_id=card_id, new_status=new_status, branch_ref=branch_ref)

    def get_swarm_nodes(self) -> Dict[str, Any]:
        return self.kanban_ui.get_swarm_nodes()

    def get_a2a_feed(self, limit: int = 30) -> Dict[str, Any]:
        return self.kanban_ui.get_a2a_feed(limit=limit)

    def send_a2a_signal(
        self, signal_type: str, data: Dict[str, Any], card_id: Optional[str] = None
    ) -> Dict[str, Any]:
        return self.kanban_ui.send_a2a_signal(signal_type=signal_type, data=data, card_id=card_id)

    def get_a2a_session_turns(self, session_id: str) -> Dict[str, Any]:
        return self.kanban_ui.get_a2a_session_turns(session_id)

    def validate_project_okf(self) -> Dict[str, Any]:
        return self.kanban_ui.validate_project_okf()

    def write_memory_snapshot(self, snapshot_data: Dict[str, Any]) -> Dict[str, Any]:
        return self.kanban_ui.write_memory_snapshot(snapshot_data)

    def read_memory_snapshot(self) -> Dict[str, Any]:
        return self.kanban_ui.read_memory_snapshot()

    def get_okf_raw_tree(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        return self.kanban_ui.get_okf_raw_tree(project_uuid=project_uuid)

    def read_okf_file(self, project_uuid: Optional[str] = None, file_path: str = "") -> Dict[str, Any]:
        return self.kanban_ui.read_okf_file(project_uuid=project_uuid, file_path=file_path)

    def scan_and_bootstrap_okf(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        return self.kanban_ui.scan_and_bootstrap_okf(project_uuid=project_uuid)

    def get_alignment_dossier(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        return self.kanban_ui.get_alignment_dossier(project_uuid=project_uuid)

    def ratify_okf_bootstrap(self, project_uuid: Optional[str] = None, comment: str = "") -> Dict[str, Any]:
        return self.kanban_ui.ratify_okf_bootstrap(project_uuid=project_uuid, comment=comment)

    def audit_okf_drift(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        return self.kanban_ui.audit_okf_drift(project_uuid=project_uuid)

    # ==========================
    # Ramas Git y Sincronización 3 Niveles
    # ==========================

    def get_branches_and_consensus(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        return self.git_ui.get_branches_and_consensus(project_uuid=project_uuid)

    def git_fetch_action(self, project_uuid: Optional[str] = None, repo_target: str = "app") -> Dict[str, Any]:
        return self.git_ui.git_fetch_action(project_uuid=project_uuid, repo_target=repo_target)

    def git_pull_action(
        self, project_uuid: Optional[str] = None, branch: Optional[str] = None, repo_target: str = "app"
    ) -> Dict[str, Any]:
        return self.git_ui.git_pull_action(project_uuid=project_uuid, branch=branch, repo_target=repo_target)

    def git_push_action(
        self, project_uuid: Optional[str] = None, branch: Optional[str] = None, repo_target: str = "app"
    ) -> Dict[str, Any]:
        return self.git_ui.git_push_action(project_uuid=project_uuid, branch=branch, repo_target=repo_target)

    def git_merge_action(
        self,
        project_uuid: Optional[str] = None,
        source_branch: str = "",
        target_branch: str = "main",
        repo_target: str = "app",
    ) -> Dict[str, Any]:
        return self.git_ui.git_merge_action(
            project_uuid=project_uuid,
            source_branch=source_branch,
            target_branch=target_branch,
            repo_target=repo_target,
        )

    def git_create_branch_action(
        self, project_uuid: Optional[str] = None, branch_name: str = "", repo_target: str = "app"
    ) -> Dict[str, Any]:
        return self.git_ui.git_create_branch_action(
            project_uuid=project_uuid, branch_name=branch_name, repo_target=repo_target
        )

    def set_project_remote_url(
        self,
        project_uuid: Optional[str] = None,
        remote_url: str = "",
        remote_name: str = "origin",
        repo_target: str = "app",
    ) -> Dict[str, Any]:
        return self.git_ui.set_project_remote_url(
            project_uuid=project_uuid,
            remote_url=remote_url,
            remote_name=remote_name,
            repo_target=repo_target,
        )

    def ensure_orphan_worktree(self, gov_repo_path: str | Path, project_uuid: str) -> Path:
        return self.git_ui.ensure_orphan_worktree(gov_repo_path, project_uuid)

    def publish_project_to_github(
        self,
        project_uuid: Optional[str] = None,
        repo_target: str = "both",
        repo_name: Optional[str] = None,
        is_private: bool = True,
    ) -> Dict[str, Any]:
        return self.git_ui.publish_project_to_github(
            project_uuid=project_uuid,
            repo_target=repo_target,
            repo_name=repo_name,
            is_private=is_private,
        )

    def sync_three_tiers(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        return self.git_ui.sync_three_tiers(project_uuid=project_uuid)

    # ==========================
    # Bandeja HITL y Tribunal Dialéctico
    # ==========================

    def get_pending_human_approvals(
        self, project_uuid: Optional[str] = None, viewer_node: Optional[str] = None
    ) -> Dict[str, Any]:
        return self.tribunal_ui.get_pending_human_approvals(project_uuid=project_uuid, viewer_node=viewer_node)

    def get_hitl_item_detail(
        self, project_uuid: Optional[str] = None, item_type: str = "", item_id: str = ""
    ) -> Dict[str, Any]:
        return self.tribunal_ui.get_hitl_item_detail(
            project_uuid=project_uuid, item_type=item_type, item_id=item_id
        )

    def sign_and_approve_item(
        self,
        project_uuid: str,
        item_type: str,
        item_id: str,
        decision: str = "APPROVED",
        comment: str = "",
        voter_node: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.tribunal_ui.sign_and_approve_item(
            project_uuid=project_uuid,
            item_type=item_type,
            item_id=item_id,
            decision=decision,
            comment=comment,
            voter_node=voter_node,
        )

    # ==========================
    # Laboratorio OKF, Multi-Seed P2P, Puerta con Llave y Diagnóstico
    # ==========================

    def list_okf_folders(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        return self.extensions_ui.list_okf_folders(project_uuid=project_uuid)

    def add_okf_folder(
        self,
        project_uuid: Optional[str],
        folder_path: str,
        category: str = "custom",
        description: str = "",
    ) -> Dict[str, Any]:
        return self.extensions_ui.add_okf_folder(
            project_uuid=project_uuid,
            folder_path=folder_path,
            category=category,
            description=description,
        )

    def remove_okf_folder(
        self,
        project_uuid: Optional[str],
        folder_path: str,
        archive_if_not_empty: bool = True,
    ) -> Dict[str, Any]:
        return self.extensions_ui.remove_okf_folder(
            project_uuid=project_uuid,
            folder_path=folder_path,
            archive_if_not_empty=archive_if_not_empty,
        )

    def rename_okf_folder(
        self, project_uuid: Optional[str], old_path: str, new_path: str
    ) -> Dict[str, Any]:
        return self.extensions_ui.rename_okf_folder(
            project_uuid=project_uuid, old_path=old_path, new_path=new_path
        )

    def get_okf_freshness_report(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        return self.extensions_ui.get_okf_freshness_report(project_uuid=project_uuid)

    def get_okf_tags_index(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        return self.extensions_ui.get_okf_tags_index(project_uuid=project_uuid)

    def update_artifact_tags(
        self, project_uuid: Optional[str], rel_path: str, tags: List[str]
    ) -> Dict[str, Any]:
        return self.extensions_ui.update_artifact_tags(
            project_uuid=project_uuid, rel_path=rel_path, tags=tags
        )

    def filter_okf_by_tags(
        self, project_uuid: Optional[str], tags: List[str], match_all: bool = False
    ) -> Dict[str, Any]:
        return self.extensions_ui.filter_okf_by_tags(
            project_uuid=project_uuid, tags=tags, match_all=match_all
        )

    def list_p2p_seeds(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        return self.extensions_ui.list_p2p_seeds(project_uuid=project_uuid)

    def add_p2p_seed(self, project_uuid: Optional[str], seed_url: str) -> Dict[str, Any]:
        return self.extensions_ui.add_p2p_seed(project_uuid=project_uuid, seed_url=seed_url)

    def remove_p2p_seed(self, project_uuid: Optional[str], seed_url: str) -> Dict[str, Any]:
        return self.extensions_ui.remove_p2p_seed(project_uuid=project_uuid, seed_url=seed_url)

    def get_extension_gates_status(self) -> Dict[str, Any]:
        return self.extensions_ui.get_extension_gates_status()

    def unlock_extension_gate(
        self,
        connector_id: str,
        allowed_domains: List[str],
        allow_private_ips: bool = False,
    ) -> Dict[str, Any]:
        return self.extensions_ui.unlock_extension_gate(
            connector_id=connector_id,
            allowed_domains=allowed_domains,
            allow_private_ips=allow_private_ips,
        )

    def lock_extension_gate(self, connector_id: str) -> Dict[str, Any]:
        return self.extensions_ui.lock_extension_gate(connector_id=connector_id)

    def test_outbound_rest_gate(self, connector_id: str, url: str) -> Dict[str, Any]:
        return self.extensions_ui.test_outbound_rest_gate(connector_id=connector_id, url=url)

    def run_system_diagnostics(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        return self.extensions_ui.run_system_diagnostics(project_uuid=project_uuid)

    # ==========================
    # Swarm Terminal Matrix (PTY + EmbeddedMcpHub)
    # ==========================

    def pty_get_matrix_status(self) -> Dict[str, Any]:
        return self.extensions_ui.pty_get_matrix_status()

    def pty_spawn_pane(
        self,
        pane_id: Any,
        cli_profile: str = "claude",
        role: str = "implementer",
        command: Optional[Any] = None,
        cols: int = 100,
        rows: int = 28,
        start_hub: bool = True,
        cwd: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.extensions_ui.pty_spawn_pane(
            pane_id=pane_id,
            cli_profile=cli_profile,
            role=role,
            command=command,
            cols=cols,
            rows=rows,
            start_hub=start_hub,
            cwd=cwd,
        )

    def pty_list_cli_catalog(self) -> Dict[str, Any]:
        return self.extensions_ui.pty_list_cli_catalog()

    def pty_set_cli_path(self, cli_id: str, path: str) -> Dict[str, Any]:
        return self.extensions_ui.pty_set_cli_path(cli_id=cli_id, path=path)

    def pty_set_max_panes(self, max_panes: int) -> Dict[str, Any]:
        return self.extensions_ui.pty_set_max_panes(max_panes=max_panes)

    def pty_prepare_install(self, cli_id: str) -> Dict[str, Any]:
        return self.extensions_ui.pty_prepare_install(cli_id=cli_id)

    def pty_install_cli(self, cli_id: str, confirmation_token: str, pane_id: Any = None) -> Dict[str, Any]:
        return self.extensions_ui.pty_install_cli(
            cli_id=cli_id, confirmation_token=confirmation_token, pane_id=pane_id
        )

    def pty_write_input(self, pane_id: Any, data: str, is_human: bool = True) -> Dict[str, Any]:
        return self.extensions_ui.pty_write_input(pane_id=pane_id, data=data, is_human=is_human)

    def pty_resize_pane(self, pane_id: Any, cols: int, rows: int) -> Dict[str, Any]:
        return self.extensions_ui.pty_resize_pane(pane_id=pane_id, cols=cols, rows=rows)

    def pty_poll_output(self, pane_id: Any, since_seq: int = 0) -> Dict[str, Any]:
        return self.extensions_ui.pty_poll_output(pane_id=pane_id, since_seq=since_seq)

    def pty_pause_pane(self, pane_id: Any, paused: bool = True) -> Dict[str, Any]:
        return self.extensions_ui.pty_pause_pane(pane_id=pane_id, paused=paused)

    def pty_close_pane(self, pane_id: Any) -> Dict[str, Any]:
        return self.extensions_ui.pty_close_pane(pane_id=pane_id)

    def _close_terminal_matrix(self) -> None:
        self.extensions_ui.close_terminal_matrix()

    def as_public_js_api(self) -> PublicJsApiFacade:
        """Retorna una fachada JS plana y segura para `webview.create_window(..., js_api=...)` (EGM-58)."""
        return PublicJsApiFacade(self)
