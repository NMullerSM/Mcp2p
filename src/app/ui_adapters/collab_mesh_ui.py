"""
Sub-adaptador UI para colaboración P2P, señalización WebRTC en caliente (EGM-47)
y flujo de invitación/unión HITL.

EGM-57: la lógica de invitar / unirse / reintentar / ingerir solicitudes / listar / decidir vive
en `ProjectCollaborationService` (`src/core/services/collaboration_service.py`). Aquí solo
quedan la resolución del proyecto, los envoltorios de error de la API JS, el lock por proyecto
del reintento y el enrutado del transporte en vivo.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from src.app.identity import local_node_uuid
from src.app.ui_adapters import _BaseUIAdapter
from src.p2p.transport import P2PTransport

logger = logging.getLogger("mcp2p.api_bridge.collab")

DEFAULT_SIGNALING_URL = ""


class CollabMeshUIAdapter(_BaseUIAdapter):
    """Gestiona invitaciones P2P, transporte en vivo, colaboradores y SLA."""

    def get_retry_lock(self, project_uuid: str) -> threading.Lock:
        with self._bridge._retry_locks_guard:
            lock = self._bridge._retry_locks.get(project_uuid)
            if lock is None:
                lock = threading.Lock()
                self._bridge._retry_locks[project_uuid] = lock
            return lock

    def join_stub_repo_dirs(
        self, p_name: str, p_uuid: str, base_path: Optional[str] = None
    ) -> Tuple[Path, Path, Path]:
        return self._collaboration_service().stub_repo_dirs(p_name, p_uuid, base_path)

    def generate_p2p_invite(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        try:
            _target_uuid, proj, err = self._get_project_or_error(project_uuid)
            if err or proj is None:
                return err or {"success": False, "error": "Proyecto no encontrado"}
            return self._collaboration_service().create_invite(proj)
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_signaling_server_url(self) -> str:
        return self.db.get_setting("p2p_signaling_url", default=DEFAULT_SIGNALING_URL)

    def set_signaling_server_url(self, url: str) -> Dict[str, Any]:
        """
        Configura la URL del servidor de señalización y reconstruye el transporte 1:1 en
        caliente (EGM-47): cierra el anterior y construye el nuevo con la única fábrica
        `build_p2p_transport`. Con `PeerNetworkPort` enchufado delega en
        `DirectPeerNetworkConnector.reconfigure` (ver `25_`).
        """
        if url != "" and not (url.startswith("wss://") or url.startswith("ws://")):
            return {"success": False, "error": "La URL debe estar vacía o comenzar con ws:// o wss://"}
        self.db.set_setting("p2p_signaling_url", url)

        direct = self._direct_peer_connector()
        if direct is not None:
            direct.reconfigure({"signaling_url": url})
            new_transport: P2PTransport = direct.transport
        else:
            old_transport = self._bridge.transport
            if old_transport is not None and hasattr(old_transport, "close"):
                try:
                    old_transport.close()
                except Exception as close_err:
                    logger.debug(f"Error cerrando transporte previo: {close_err}")
            from src.adapters.network.direct_peer import build_p2p_transport
            new_transport = build_p2p_transport(url, self.crypto)

        self._bridge.set_transport(new_transport)
        return {"success": True, "url": url, "transport": type(new_transport).__name__}

    def set_transport(self, transport: P2PTransport) -> None:
        self._bridge.transport = transport
        self._bridge.transport.on_message(self._bridge._handle_transport_message)
        direct = self._direct_peer_connector()
        if direct is not None:
            direct.adopt_transport(transport)

    def _notify_peer(self, node_uuid: str, envelope: Dict[str, Any]) -> bool:
        """Envío best-effort al par: por el `PeerNetworkPort` (1:1 y luego LAN) o, sin él, por el transporte."""
        network = self._peer_network()
        if network is not None:
            return bool(network.send_to(node_uuid, envelope))
        return bool(self.transport.send(envelope))

    def handle_peer_envelope(self, envelope: Dict[str, Any]) -> None:
        """
        Enrutador de sobres entrantes del listener LAN (antes `main.py` llamaba a
        `api.sync_three_tiers()` para CUALQUIER evento). `join_*` va al flujo de
        colaboradores; el resto conserva el comportamiento previo (sincronización).
        """
        if not isinstance(envelope, dict):
            return
        if envelope.get("type") in ("join_request", "join_decision"):
            self._bridge._handle_transport_message(envelope)
            return
        self._bridge.sync_three_tiers()

    def handle_transport_message(self, message: dict) -> None:
        """Procesa mensajes P2P en vivo (join_request y join_decision approved/rejected, EGM-47)."""
        try:
            msg_type = message["type"]
            if msg_type == "join_request":
                self._bridge.ingest_join_request(message["request_code"])
            elif msg_type == "join_decision":
                decision = message.get("decision")
                p_uuid = message.get("project_uuid")
                if decision == "approved" and p_uuid:
                    self._bridge.retry_p2p_join(p_uuid)
                elif decision == "rejected":
                    node_id = message.get("node_uuid") or local_node_uuid()
                    logger.info(
                        "Solicitud de unión rechazada por el dueño (project=%s, node=%s)",
                        p_uuid,
                        node_id,
                    )
                    self._collaboration_service().record_local_rejection(p_uuid, node_id)
            else:
                logger.debug(f"Mensaje de transporte P2P con tipo desconocido ignorado: {msg_type!r}")
        except Exception as exc:
            logger.warning(f"Mensaje de transporte P2P malformado o inválido, ignorado: {exc}")

    def join_p2p_project(self, invite_str: str) -> Dict[str, Any]:
        try:
            return self._collaboration_service().join_project(invite_str)
        except Exception as e:
            return {"success": False, "error": f"Error al unirse al proyecto P2P: {str(e)}"}

    def build_join_request_code(
        self, p_uuid: str, rendezvous_nonce: str, github_username: str, node_uuid: str
    ) -> str:
        return self._collaboration_service().build_join_request_code(
            p_uuid, rendezvous_nonce, github_username, node_uuid
        )

    def get_pending_join_request_code(self, project_uuid: str) -> Dict[str, Any]:
        try:
            return self._collaboration_service().pending_join_request_code(project_uuid)
        except Exception as e:
            return {"success": False, "error": f"No se pudo regenerar el código de solicitud: {str(e)}"}

    def retry_p2p_join(self, project_uuid: str) -> Dict[str, Any]:
        lock = self._bridge._get_retry_lock(project_uuid)
        with lock:
            return self._bridge._do_retry_p2p_join(project_uuid)

    def do_retry_p2p_join(self, project_uuid: str) -> Dict[str, Any]:
        try:
            return self._collaboration_service().retry_join(project_uuid)
        except Exception as e:
            return {"success": False, "error": f"Error al reintentar la unión P2P: {str(e)}"}

    def check_join_request_status(self, project_uuid: str) -> Dict[str, Any]:
        try:
            return self._bridge.retry_p2p_join(project_uuid)
        except Exception as e:
            return {"success": False, "error": f"Error al consultar el estado de la solicitud de unión: {str(e)}"}

    def ingest_join_request(self, request_code: str, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        try:
            return self._collaboration_service().ingest_join_request(request_code, project_uuid)
        except Exception as e:
            return {"success": False, "error": f"Error al ingerir la solicitud de unión: {str(e)}"}

    def get_project_collaborators(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        try:
            target_uuid = self._resolve_project_uuid(project_uuid)
            return {"success": True, "collaborators": self._collaboration_service().list_collaborators(target_uuid)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _collaboration_service(self) -> Any:
        """`ProjectCollaborationService` con los puertos y ganchos de esta GUI (EGM-57).

        Se obtiene por llamada a partir de los atributos actuales del puente (BD, crypto, github,
        transporte), para que sustituirlos en caliente siga surtiendo efecto, y los ganchos se
        resuelven en el puente al usarse. Si el kernel trae su servicio registrado se reutiliza
        con `rebind`; los repositorios del kernel solo se usan si apuntan a la misma BD."""
        from src.core.services.collaboration_service import (
            CollaborationHostHooks,
            ProjectCollaborationService,
        )

        bridge = self._bridge
        kernel = self.core_kernel
        same_db = kernel is not None and getattr(kernel, "db", None) is self.db
        collaborators: Dict[str, Any] = dict(
            collab_repo=(getattr(kernel, "collab_repo", None) if same_db else None) or self.db,
            project_lookup=self.db.get_project_by_uuid,
            okf_port=self.okf,
            git_port=self.github,
            repo_host=self.github,
            peer_notifier=self._notify_peer,
            project_repo=(getattr(kernel, "project_repo", None) if same_db else None) or self.db,
            crypto=self.crypto,
            live_transport=self.transport,
            hooks=CollaborationHostHooks(
                set_active_project=lambda p_uuid: setattr(bridge, "active_project_uuid", p_uuid),
                get_active_project=lambda: bridge.active_project_uuid,
                build_join_request_code=lambda *args: bridge._build_join_request_code(*args),
                ensure_orphan_worktree=lambda gov_dir, p_uuid: bridge.ensure_orphan_worktree(gov_dir, p_uuid),
                sync_project=lambda p_uuid: bridge.sync_three_tiers(p_uuid),
            ),
            workspace_root=lambda: self.db.db_path.parent / "workspace",
            reconcile_target=self.db,
            local_node=local_node_uuid,
        )
        base = getattr(kernel, "collaboration_service", None) if kernel is not None else None
        if isinstance(base, ProjectCollaborationService):
            return base.rebind(**collaborators)
        return ProjectCollaborationService(**collaborators)

    def approve_collaborator(self, project_uuid: str, node_uuid: str) -> Dict[str, Any]:
        try:
            return self._collaboration_service().approve_collaborator(project_uuid, node_uuid)
        except Exception as e:
            return {"success": False, "error": str(e)}

    def reject_collaborator(self, project_uuid: str, node_uuid: str) -> Dict[str, Any]:
        """Rechaza colaborador y emite join_decision(decision='rejected') best-effort (EGM-47)."""
        try:
            return self._collaboration_service().reject_collaborator(project_uuid, node_uuid)
        except Exception as e:
            return {"success": False, "error": str(e)}

    def remove_collaborator(self, project_uuid: str, node_uuid: str) -> Dict[str, Any]:
        try:
            return self._collaboration_service().remove_collaborator(project_uuid, node_uuid)
        except Exception as e:
            return {"success": False, "error": str(e)}

    def update_project_sla(self, project_uuid: str, sla_hours: int) -> Dict[str, Any]:
        try:
            res = self.db.update_project_sla(project_uuid, sla_hours)
            return {"success": res, "sla_hours": sla_hours}
        except Exception as e:
            return {"success": False, "error": str(e)}
