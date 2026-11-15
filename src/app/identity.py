"""Compatibilidad: la identidad del nodo local vive en `src.runtime.identity` (EGM-55)."""

from src.runtime.identity import local_node_uuid

__all__ = ["local_node_uuid"]
