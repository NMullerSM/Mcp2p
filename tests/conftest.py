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

import pytest

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


@pytest.fixture(scope="session", autouse=True)
def _hermetic_home(tmp_path_factory):
    """La suite nunca debe leer ni escribir el HOME real del desarrollador.

    `NodeCryptoManager()` resuelve `~/.mcp2p/node_identity.key` con `Path.home()`: si el dueño cifra su
    clave real (Gate 0c) o ya hay una, 18 pruebas de firma fallaban con KEY_LOCKED. Un HOME temporal
    de sesion aisla tambien `~/.gemini`, `~/.local/bin` y demas rutas de usuario.
    """
    home = tmp_path_factory.mktemp("home")
    # Sin un ~/.gitconfig propio, git no puede deducir una identidad en un runner de CI y los
    # `git commit` de las pruebas fallan con "Author identity unknown" (codigo 128).
    (home / ".gitconfig").write_text(
        "[user]\n\tname = MCP2P Test\n\temail = test@mcp2p.local\n"
        "[init]\n\tdefaultBranch = main\n",
        encoding="utf-8",
    )
    mp = pytest.MonkeyPatch()
    mp.setenv("HOME", str(home))
    mp.setenv("USERPROFILE", str(home))
    # La clave del dispositivo (Ola 3) nunca debe ir al llavero real del desarrollador: sin backend utilizable cae
    # al archivo cifrado dentro de este HOME temporal.
    mp.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")
    try:
        yield home
    finally:
        mp.undo()


@pytest.fixture(autouse=True)
def _auto_confirm_native_dialogs(monkeypatch):
    """Sin ventana no hay dialogo nativo (EGM-74): las pruebas que no lo ejercitan lo dan por aceptado.

    Las que SI prueban la confirmacion (`tests/test_egm74_native_confirmation.py`) quitan esta variable.
    """
    monkeypatch.setenv("MCP2P_TEST_AUTO_CONFIRM", "1")
