"""
Conectores de terminal interactiva (POSIX PTY y supervisor con circuit breaker)
que implementan `AgentTerminalPort`.
"""

from src.adapters.terminal.pty_host import (
    CLI_PROFILES,
    PosixPtyHostConnector,
    PtyOutputRingBuffer,
    normalize_pane_id,
)
from src.adapters.terminal.supervisor import SafeTerminalHostSupervisor

__all__ = [
    "CLI_PROFILES",
    "PosixPtyHostConnector",
    "PtyOutputRingBuffer",
    "SafeTerminalHostSupervisor",
    "normalize_pane_id",
]
