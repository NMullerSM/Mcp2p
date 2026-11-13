"""
Configuración y utilidades compartidas para la suite de pruebas de MCP2P.

Centraliza helpers usados por múltiples módulos de test (p.ej. make_test_db())
y se encarga de limpiar los directorios temporales que esos helpers crean,
evitando que queden huérfanos en /tmp tras ejecutar la suite completa.
"""

import shutil
import tempfile
from pathlib import Path
from typing import List

from src.storage.db import DatabaseManager

# Directorios temporales creados por los helpers de este módulo durante la
# sesión de pruebas. Se eliminan todos en pytest_sessionfinish.
_TMP_DIRS: List[str] = []


def register_tmp_dir(path) -> None:
    """Registra un directorio temporal para su limpieza automática al finalizar la sesión."""
    _TMP_DIRS.append(str(path))


def make_test_db() -> DatabaseManager:
    """Crea una base de datos SQLite temporal y aislada para pruebas puntuales."""
    tmpdir = tempfile.mkdtemp()
    register_tmp_dir(tmpdir)
    db_path = Path(tmpdir) / "test_bridge_helper.db"
    return DatabaseManager(str(db_path))


def pytest_sessionfinish(session, exitstatus):
    """Elimina todos los directorios temporales registrados por los helpers de este
    módulo (make_test_db() y otros) una vez finalizada la sesión de pruebas."""
    for d in _TMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)
