"""
Conectores de terminal interactiva (POSIX PTY, Windows ConPTY y supervisor con circuit breaker)
que implementan `AgentTerminalPort`.
"""

from src.adapters.terminal.cli_catalog import CliCatalogService, load_catalog, load_repo_catalog
from src.adapters.terminal.pty_host import (
    CLI_PROFILES,
    DEFAULT_MAX_PANES,
    HARD_MAX_PANES,
    PaneLimitError,
    PosixPtyHostConnector,
    PtyOutputRingBuffer,
    clamp_max_panes,
    load_configured_max_panes,
    normalize_pane_id,
)
from src.adapters.terminal.supervisor import SafeTerminalHostSupervisor
from src.adapters.terminal.winpty_host import WinPtyHostConnector

__all__ = [
    "CLI_PROFILES",
    "CliCatalogService",
    "DEFAULT_MAX_PANES",
    "HARD_MAX_PANES",
    "PaneLimitError",
    "clamp_max_panes",
    "load_catalog",
    "load_configured_max_panes",
    "load_repo_catalog",
    "PosixPtyHostConnector",
    "PtyOutputRingBuffer",
    "SafeTerminalHostSupervisor",
    "WinPtyHostConnector",
    "normalize_pane_id",
]
