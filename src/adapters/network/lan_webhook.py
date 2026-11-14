"""
Conector LAN por defecto (`LanWebhookNetworkConnector`) del `PeerNetworkPort` (EGM-48).

Envuelve `P2PNotificationService` (`src/p2p/network.py`) sin cambiar su contrato:
- Entrada: listener HTTP en `MCP2P_LAN_BIND_HOST` (por defecto `127.0.0.1`) puerto 42424,
  que solo entrega al callback sobres con firma Ed25519 válida de un colaborador aprobado
  (`is_trusted_pubkey`). La verificación sigue en `P2PWebhookHandler.do_POST`.
- Salida: `broadcast_lan_notification` firmado, resolviendo el endpoint de cada par con
  `endpoint_resolver(node_uuid)` (en producción: `swarm_nodes.p2p_endpoint`).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("mcp2p.adapters.network.lan")


class LanWebhookNetworkConnector:
    connector_mode = "lan_webhook"

    def __init__(
        self,
        node_uuid: str,
        crypto_manager: Optional[Any] = None,
        is_trusted_pubkey: Optional[Callable[[str], bool]] = None,
        endpoint_resolver: Optional[Callable[[str], Optional[str]]] = None,
        project_peers_resolver: Optional[Callable[[str], List[str]]] = None,
        lan_port: int = 42424,
        bind_host: Optional[str] = None,
        service: Optional[Any] = None,
    ) -> None:
        self.node_uuid = node_uuid
        self._endpoint_resolver = endpoint_resolver
        self._project_peers_resolver = project_peers_resolver
        self._on_envelope: Optional[Callable[[Dict[str, Any]], None]] = None
        if service is None:
            from src.p2p.network import P2PNotificationService

            service = P2PNotificationService(
                node_uuid=node_uuid,
                lan_port=lan_port,
                on_event_callback=self._dispatch,
                crypto_manager=crypto_manager,
                is_trusted_pubkey=is_trusted_pubkey,
                bind_host=bind_host,
            )
        else:
            service.on_event = self._dispatch
        self.service = service

    # ------------------------------------------------------------------
    @property
    def lan_port(self) -> int:
        return int(getattr(self.service, "lan_port", 0) or 0)

    def _dispatch(self, payload: Dict[str, Any]) -> None:
        callback = self._on_envelope
        if callback is not None:
            callback(payload)

    def _resolve_endpoint(self, node_uuid: str) -> Optional[str]:
        if self._endpoint_resolver is None or not node_uuid:
            return None
        try:
            endpoint = self._endpoint_resolver(node_uuid)
        except Exception as exc:
            logger.debug("endpoint_resolver falló para %s: %s", node_uuid, exc)
            return None
        endpoint = str(endpoint or "").strip()
        return endpoint or None

    # ------------------------------------------------------------------
    # PeerNetworkPort
    # ------------------------------------------------------------------

    def start_listening(self, local_node_uuid: str, on_envelope: Callable[[Dict[str, Any]], None]) -> bool:
        self._on_envelope = on_envelope
        return bool(self.service.start_lan_server())

    def send_to(self, target_node_uuid: str, envelope: Dict[str, Any]) -> bool:
        endpoint = self._resolve_endpoint(target_node_uuid)
        if endpoint is None:
            return False
        payload = dict(envelope)
        payload.setdefault("target_node_uuid", target_node_uuid)
        res = self.service.broadcast_lan_notification([endpoint], payload)
        return int(res.get("delivered", 0)) == 1

    def broadcast(self, project_uuid: str, envelope: Dict[str, Any]) -> Dict[str, Any]:
        nodes: List[str] = []
        if self._project_peers_resolver is not None:
            try:
                nodes = [n for n in self._project_peers_resolver(project_uuid) if n and n != self.node_uuid]
            except Exception as exc:
                logger.debug("project_peers_resolver falló para %s: %s", project_uuid, exc)
        endpoints: List[str] = []
        without_endpoint: List[str] = []
        for node in nodes:
            ep = self._resolve_endpoint(node)
            if ep:
                endpoints.append(ep)
            else:
                without_endpoint.append(node)
        if not endpoints:
            return {"delivered": 0, "failed": 0, "failures": [], "skipped_without_endpoint": without_endpoint}
        payload = dict(envelope)
        payload.setdefault("project_uuid", project_uuid)
        res = self.service.broadcast_lan_notification(endpoints, payload)
        return {
            "delivered": int(res.get("delivered", 0)),
            "failed": int(res.get("failed", 0)),
            "failures": [{"endpoint": e, "error": str(err)} for e, err in res.get("failures", [])],
            "skipped_without_endpoint": without_endpoint,
        }

    def get_presence(self, project_uuid: Optional[str] = None) -> List[Dict[str, Any]]:
        if project_uuid is None or self._project_peers_resolver is None:
            return []
        try:
            nodes = self._project_peers_resolver(project_uuid)
        except Exception:
            return []
        return [
            {
                "node_uuid": n,
                "connector_mode": self.connector_mode,
                "endpoint": self._resolve_endpoint(n),
                "reachable": self._resolve_endpoint(n) is not None,
            }
            for n in nodes
            if n and n != self.node_uuid
        ]

    def reconfigure(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        # El listener LAN no tiene ajustes en caliente hoy (puerto/bind se fijan al arrancar).
        return {"success": True, "applied": []}

    def get_status(self) -> Dict[str, Any]:
        return {
            "connector_mode": self.connector_mode,
            "listening": bool(getattr(self.service, "_running", False)),
            "lan_port": self.lan_port,
            "bind_host": getattr(self.service, "bind_host", None),
        }

    def check_wan_remote_changes(self, repo_path: str | Path, remote_url: Optional[str] = None) -> Dict[str, Any]:
        """Sondeo WAN `git ls-remote` del servicio envuelto (sin cambios de contrato)."""
        return self.service.check_wan_remote_changes(repo_path, remote_url)

    def close(self) -> None:
        try:
            self.service.stop_lan_server()
        except Exception as exc:
            logger.debug("stop_lan_server falló: %s", exc)
