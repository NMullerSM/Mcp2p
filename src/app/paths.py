"""
Módulo de Gestión Centralizada de Rutas para MCP2P.
Distingue entre:
1. Bundle Dir (solo lectura): código y assets empaquetados por PyInstaller (frontend/dist, src/ui).
2. User Data Dir (lectura y escritura): mcp2p_local.db, workspace/ (proyectos clonados),
   descargas del auto-actualizador.
   - Windows instalado: %LOCALAPPDATA%\\MCP2P\\data
   - Linux instalado (/opt/mcp2p): ~/.local/share/mcp2p/data
   - Modo desarrollo (no frozen): <repo_root>/ (comportamiento actual, sin cambios)
"""
import os
import sys
from pathlib import Path


def get_bundle_dir() -> str:
    """Raíz de recursos empaquetados de solo lectura (sys._MEIPASS en modo frozen)."""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return str(Path(__file__).resolve().parent.parent.parent)


def get_user_data_dir() -> str:
    """Directorio con permisos de escritura garantizados para la base de datos y workspace/."""
    if not getattr(sys, "frozen", False):
        # Modo desarrollo: comportamiento idéntico al actual (raíz del repo).
        return get_bundle_dir()

    exe_dir = os.path.dirname(sys.executable)
    is_installed = False

    if sys.platform == "win32":
        prog_files = os.environ.get("ProgramFiles", "C:\\Program Files").lower()
        prog_files_x86 = os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)").lower()
        exe_lower = exe_dir.lower()
        if exe_lower.startswith(prog_files) or exe_lower.startswith(prog_files_x86) or "program files" in exe_lower:
            is_installed = True
    elif sys.platform.startswith("linux"):
        if exe_dir.startswith("/opt") or exe_dir.startswith("/usr"):
            is_installed = True

    if not is_installed:
        # Modo portable (tarball / zip descomprimido): probar si la carpeta del ejecutable
        # acepta escritura antes de forzar un directorio de usuario.
        test_file = os.path.join(exe_dir, ".write_test")
        try:
            with open(test_file, "w") as f:
                f.write("ok")
            os.remove(test_file)
            return exe_dir
        except (OSError, PermissionError):
            pass

    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA", os.path.expanduser(r"~\AppData\Local"))
        user_dir = os.path.join(local_app_data, "MCP2P", "data")
    elif sys.platform.startswith("darwin"):
        user_dir = os.path.expanduser("~/Library/Application Support/MCP2P/data")
    else:
        xdg_data = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
        user_dir = os.path.join(xdg_data, "mcp2p", "data")

    os.makedirs(user_dir, exist_ok=True)
    return user_dir


def get_default_db_path() -> str:
    """Ruta absoluta a mcp2p_local.db en el directorio escribible correcto."""
    return os.path.join(get_user_data_dir(), "mcp2p_local.db")
