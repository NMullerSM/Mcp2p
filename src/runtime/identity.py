"""
Identidad local determinista del nodo MCP2P.

Centraliza la resolución de `local_node_uuid()` en tiempo de ejecución para evitar
cadenas duplicadas o congeladas al importar módulos.
"""

from __future__ import annotations

import os
import platform


def local_node_uuid() -> str:
    """
    Devuelve el identificador del nodo local leyendo `MCP2P_NODE_UUID` en runtime
    o derivando `node_{platform.node().lower()[:12]}` como respaldo.
    """
    env_uuid = os.environ.get("MCP2P_NODE_UUID", "").strip()
    if env_uuid:
        return env_uuid
    host_slug = platform.node().lower()[:12] or "local"
    return f"node_{host_slug}"
