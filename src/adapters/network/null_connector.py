"""
Conector de red nulo (`NullNetworkConnector`) del `PeerNetworkPort`.

Nunca abre sockets ni envía nada. Es el estado degradado seguro: lo usa el
supervisor como relleno y las pruebas como doble neutro.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional


class NullNetworkConnector:
    connector_mode = "null"

    def start_listening(self, local_node_uuid: str, on_envelope: Callable[[Dict[str, Any]], None]) -> bool:
        return False

    def send_to(self, target_node_uuid: str, envelope: Dict[str, Any]) -> bool:
        return False

    def broadcast(self, project_uuid: str, envelope: Dict[str, Any]) -> Dict[str, Any]:
        return {"delivered": 0, "failed": 0, "failures": []}

    def get_presence(self, project_uuid: Optional[str] = None) -> List[Dict[str, Any]]:
        return []

    def reconfigure(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        return {"success": True, "applied": []}

    def get_status(self) -> Dict[str, Any]:
        return {"connector_mode": self.connector_mode, "listening": False}

    def close(self) -> None:
        return None
