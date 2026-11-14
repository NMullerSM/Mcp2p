"""
Adaptadores salientes (Outbound Adapters) protegidos por ExtensionSecurityGate.
"""

from src.adapters.outbound.extension_gate import (
    KNOWN_CONNECTORS,
    ExtensionSecurityGate,
    NullAgentTerminalConnector,
    OutboundRestConnector,
    SignedConnectorManifest,
)

__all__ = [
    "KNOWN_CONNECTORS",
    "SignedConnectorManifest",
    "ExtensionSecurityGate",
    "OutboundRestConnector",
    "NullAgentTerminalConnector",
]
