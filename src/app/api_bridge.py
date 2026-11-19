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
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.a2a.bus import A2ABus
from src.adapters.outbound.extension_gate import ExtensionSecurityGate
from src.app.auto_updater import AutoUpdateManager
from src.app.identity import local_node_uuid
from src.app.native_confirm import NativeConfirmationService
from src.app.paths import get_bundle_dir, get_user_data_dir
from src.app.ui_adapters import (
    CollabMeshUIAdapter,
    ExtensionsLabUIAdapter,
    GitBranchesUIAdapter,
    KanbanOkfUIAdapter,
    RelayUIAdapter,
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

_FACADE_INTERNAL_METHODS: frozenset[str] = frozenset({
    "public_js_functions",
    "set_window",
    "set_transport",
    "ensure_local_node_registered",
})


class InertJsApi:
    """Valor para `create_window(js_api=...)` que no expone NADA (EGM-73).

    `js_api=None` no basta: `webview.util.get_nested_attribute(None, "__class__.__init__")` sigue resolviendo
    (`getattr(None, "__class__")` no es None) y deja invocar con argumentos JSON cualquier invocable alcanzable
    por `getattr` desde `NoneType`. Aqui todo `getattr` da `None`, asi que PyWebView corta el recorrido y
    responde "Function ... does not exist"; `dir()` esta vacio, asi que tampoco anuncia funciones.
    """

    __slots__ = ()

    def __getattribute__(self, name: str) -> Any:
        return None

    def __dir__(self) -> List[str]:
        return []


def _make_public_rpc(method_name: str, bound: Callable[..., Any]) -> Callable[..., Any]:
    """Funcion plana que PyWebView registra con `window.expose` bajo `method_name` (EGM-58/EGM-73).

    Es una funcion de nivel de modulo (sus `__globals__` son los de este modulo y no los de los
    sub-adaptadores) y PyWebView la resuelve por NOMBRE EXACTO en `window._functions`: con `js_api=None`
    una ruta como `pty_x.__globals__.__setitem__` ya no recorre ningun objeto.
    """

    def _rpc(*args: Any, **kwargs: Any) -> Any:
        return bound(*args, **kwargs)

    _rpc.__name__ = method_name
    _rpc.__qualname__ = method_name
    _rpc.__doc__ = getattr(bound, "__doc__", None)
    # `expose` calcula los nombres de parametros con `inspect.getfullargspec`, que respeta `__signature__`.
    _rpc.__signature__ = inspect.signature(bound)  # type: ignore[attr-defined]
    return _rpc


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
        # Dialogo nativo de confirmacion (EGM-74): el JS no puede pulsarlo.
        self.confirmations = NativeConfirmationService(lambda: self._window)
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
        self.relay_ui = RelayUIAdapter(self)  # Ola 3: bandeja del humano, auditoria y parada del relevo
        from src.app.ui_adapters.operator_ui import OperatorUIAdapter

        self.operator_ui = OperatorUIAdapter(self)  # Ola 4: sesiones del operador (el canal lo abre main.py)

        self.transport.on_message(self._handle_transport_message)
        self._ensure_local_node()
        self.ensure_local_node_registered()
        self._ensure_active_project()
        # --- v2.0.2 WP-3a: roles ---
        from src.app.ui_adapters.roles_ui import RolesUIAdapter

        self.roles_ui = RolesUIAdapter(self)
        # --- fin WP-3a ---
        # --- v2.0.2 WP-4a: sesiones ---
        from src.app.ui_adapters.sessions_ui import SessionsUIAdapter, wire_sessions

        self.sessions_ui = SessionsUIAdapter(self)
        wire_sessions(self)
        # --- fin WP-4a ---
        # --- v2.0.2 WP-6a: actividad por hooks ---
        from src.app.ui_adapters.activity_ui import wire_activity

        wire_activity(self)
        # --- fin WP-6a ---
        # --- v2.0.2 WP-6b: entrega directa ---
        # --- fin WP-6b ---
        # --- v2.0.2 WP-7: politica de nodos ---
        # --- fin WP-7 ---
        # --- v2.0.2 WP-8: chat del humano ---
        # --- fin WP-8 ---
        # --- v2.0.2 WP-9: confirmaciones recordables ---
        from src.app.app_confirmations import ConfirmationGate
        from src.app.ui_adapters.confirmations_ui import ConfirmationsUIAdapter

        self.confirm_gate = ConfirmationGate(self.confirmations, self.db)
        self.confirmations_ui = ConfirmationsUIAdapter(self)
        # --- fin WP-9 ---

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

    def open_release_page(self, url: Optional[str] = None) -> Dict[str, Any]:
        return self.system_ui.open_release_page(url)

    def get_update_log(self, max_lines: int = 200) -> Dict[str, Any]:
        return self.system_ui.get_update_log(max_lines)

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
        resume: bool = False,
        project_uuid: Optional[str] = None,
        resume_mode: Optional[str] = None,
        cli_session_id: Optional[str] = None,
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
            resume=resume,
            project_uuid=project_uuid,
            resume_mode=resume_mode,
            cli_session_id=cli_session_id,
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

    def reset_loop_breaker(
        self, project_uuid: str = "", card_id: str = "", session_id: str = ""
    ) -> Dict[str, Any]:
        return self.extensions_ui.reset_loop_breaker(
            project_uuid=project_uuid, card_id=card_id, session_id=session_id
        )

    def pty_set_auto_relay(self, pane_id: Any, enabled: Any = None) -> Dict[str, Any]:
        return self.extensions_ui.pty_set_auto_relay(pane_id=pane_id, enabled=enabled)

    def pty_set_worker_mode(self, pane_id: Any, enabled: Any = None) -> Dict[str, Any]:
        return self.extensions_ui.pty_set_worker_mode(pane_id=pane_id, enabled=enabled)

    # Ola 3: relevo persistente (bandeja del humano, auditoria, parada). Solo backend; la UI llega despues.
    def relay_send_as_human(
        self, project_uuid: Any = None, target_pane_id: Any = None, text: Any = "", kind: Any = "note"
    ) -> Dict[str, Any]:
        return self.relay_ui.relay_send_as_human(
            project_uuid=project_uuid, target_pane_id=target_pane_id, text=text, kind=kind
        )

    def relay_list_human_inbox(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        return self.relay_ui.relay_list_human_inbox(project_uuid=project_uuid)

    def relay_resolve_parked(self, msg_id: Any, decision: Any) -> Dict[str, Any]:
        return self.relay_ui.relay_resolve_parked(msg_id=msg_id, decision=decision)

    def relay_audit_status(self) -> Dict[str, Any]:
        return self.relay_ui.relay_audit_status()

    def relay_global_pause(self, enabled: Any) -> Dict[str, Any]:
        return self.relay_ui.relay_global_pause(enabled=enabled)

    def relay_pause_state(self) -> Dict[str, Any]:
        return self.relay_ui.relay_pause_state()

    def relay_resume_pane(self, node_uuid: Any) -> Dict[str, Any]:
        return self.relay_ui.relay_resume_pane(node_uuid=node_uuid)

    def operator_list_sessions(self, include_ended: Any = False) -> Dict[str, Any]:
        return self.operator_ui.operator_list_sessions(include_ended=include_ended)

    def operator_revoke_session(self, session_id: Any) -> Dict[str, Any]:
        return self.operator_ui.operator_revoke_session(session_id=session_id)

    def operator_pairing_status(self) -> Dict[str, Any]:
        return self.operator_ui.operator_pairing_status()

    def pty_register_agy_mcp(self) -> Dict[str, Any]:
        return self.extensions_ui.pty_register_agy_mcp()

    def pty_unregister_agy_mcp(self) -> Dict[str, Any]:
        return self.extensions_ui.pty_unregister_agy_mcp()

    def pty_get_agy_mcp_status(self) -> Dict[str, Any]:
        return self.extensions_ui.pty_get_agy_mcp_status()

    def pty_get_kanban_swarm_snapshot(
        self, project_uuid: Optional[str] = None, all_projects: bool = False
    ) -> Dict[str, Any]:
        return self.extensions_ui.pty_get_kanban_swarm_snapshot(project_uuid=project_uuid, all_projects=all_projects)

    # --- v2.0.2 WP-3a: roles ---
    def pty_set_pane_role(self, pane_id: Any, role: Any, mandate: Optional[str] = None) -> Dict[str, Any]:
        return self.roles_ui.pty_set_pane_role(pane_id=pane_id, role=role, mandate=mandate)

    def pty_get_pane_role(self, pane_id: Any) -> Dict[str, Any]:
        return self.roles_ui.pty_get_pane_role(pane_id=pane_id)
    # --- fin WP-3a ---
    # --- v2.0.2 WP-4a: sesiones ---
    def pty_relaunch_pane(self, pane_id: Any) -> Dict[str, Any]:
        return self.sessions_ui.pty_relaunch_pane(pane_id=pane_id)

    def pty_get_restorable_session(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        return self.sessions_ui.pty_get_restorable_session(project_uuid=project_uuid)

    def pty_restore_session(self, project_uuid: Any, pane_slots: Any = None) -> Dict[str, Any]:
        return self.sessions_ui.pty_restore_session(project_uuid=project_uuid, pane_slots=pane_slots)

    def pty_dismiss_restore(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        return self.sessions_ui.pty_dismiss_restore(project_uuid=project_uuid)

    def pty_get_restore_policy(self) -> Dict[str, Any]:
        return self.sessions_ui.pty_get_restore_policy()

    def pty_set_restore_policy(self, policy: Any) -> Dict[str, Any]:
        return self.sessions_ui.pty_set_restore_policy(policy=policy)
    # --- fin WP-4a ---
    # --- v2.0.2 WP-6b: entrega directa ---
    # --- fin WP-6b ---
    # --- v2.0.2 WP-7: politica de nodos ---
    # --- fin WP-7 ---
    # --- v2.0.2 WP-8: chat del humano ---
    # --- fin WP-8 ---
    # --- v2.0.2 WP-9: confirmaciones recordables ---
    def ui_confirm_action(self, token: Any, accepted: Any, remember: Any = False) -> Dict[str, Any]:
        return self.confirmations_ui.ui_confirm_action(token=token, accepted=accepted, remember=remember)

    def ui_list_confirmation_prefs(self) -> Dict[str, Any]:
        return self.confirmations_ui.ui_list_confirmation_prefs()

    def ui_forget_confirmation(self, action_id: Any) -> Dict[str, Any]:
        return self.confirmations_ui.ui_forget_confirmation(action_id=action_id)

    def ui_forget_all_confirmations(self) -> Dict[str, Any]:
        return self.confirmations_ui.ui_forget_all_confirmations()
    # --- fin WP-9 ---

    def public_js_functions(self) -> List[Callable[..., Any]]:
        """Funciones planas para `window.expose(...)`; la ventana se crea con `js_api=None` (EGM-73).

        Solo los metodos publicos de primer nivel de `ApiBridge`: nada privado, ningun objeto anidado y
        ningun `__self__`/`__globals__` alcanzable por nombre desde el JS.
        """
        functions: List[Callable[..., Any]] = []
        for name, _ in inspect.getmembers(type(self)):
            if name.startswith("_") or name in _FACADE_INTERNAL_METHODS:
                continue
            member = getattr(self, name, None)
            if member is None or not callable(member):
                continue
            functions.append(_make_public_rpc(name, member))
        return functions
