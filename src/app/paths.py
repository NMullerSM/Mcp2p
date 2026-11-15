"""Compatibilidad: la gestión de rutas vive en `src.runtime.paths` (EGM-55)."""

from src.runtime.paths import (
    get_bundle_dir,
    get_default_db_path,
    get_user_data_dir,
    is_system_installed,
)

__all__ = ["get_bundle_dir", "get_default_db_path", "get_user_data_dir", "is_system_installed"]
