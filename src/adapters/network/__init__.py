"""
Conectores de red entre pares que implementan `PeerNetworkPort` (EGM-48, ver `25_`).
"""

from src.adapters.network.direct_peer import DirectPeerNetworkConnector, build_p2p_transport
from src.adapters.network.lan_webhook import LanWebhookNetworkConnector
from src.adapters.network.null_connector import NullNetworkConnector
from src.adapters.network.supervisor import SafePeerNetworkSupervisor

__all__ = [
    "DirectPeerNetworkConnector",
    "LanWebhookNetworkConnector",
    "NullNetworkConnector",
    "SafePeerNetworkSupervisor",
    "build_p2p_transport",
]
