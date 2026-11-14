"""
Módulo de Actualización Automática en Segundo Plano (Auto-Updater Protocol) para MCP2P.
Descarga binarios o instaladores en segundo plano sin congelar la interfaz ni cerrar
abruptamente el programa, ofreciendo progreso continuo (RPC) y reinicio silencioso
seguro con cero pérdida de datos (mcp2p_local.db y workspace/ residen en user_data_dir
y nunca se sobreescriben al actualizar).
"""
import os
import re
import sys
import time
import shlex
import shutil
import hashlib
import hmac
import tempfile
import threading
import subprocess
import urllib.request
from typing import Optional, Dict, Any

from src.proc_utils import run_hidden
from src.app.paths import is_system_installed

OFFICIAL_RELEASE_PREFIX = "https://github.com/NMullerSM/Mcp2p/releases/download/"


def _safe_version_component(version: str) -> str:
    """
    `version` es el `tag_name` de la release (dato remoto de la API de GitHub) y se
    interpola en el nombre del archivo temporal: se reduce a un único componente de ruta
    sin separadores ni '..' para que nunca pueda escribir fuera del directorio temporal.
    """
    cleaned = re.sub(r"[^0-9A-Za-z._+-]", "_", str(version or ""))
    cleaned = cleaned.replace("..", "_").strip(".")
    return cleaned[:64] or "unknown"


class AutoUpdateManager:
    """Gestor de descargas asíncronas y aplicación de actualizaciones en caliente."""

    def __init__(self, base_dir: str, user_data_dir: str):
        self._base_dir = base_dir
        self._user_data_dir = user_data_dir
        self._window = None
        self._lock = threading.Lock()

        self._status: str = "idle"  # "idle" | "downloading" | "ready_to_restart" | "error"
        self._percent: int = 0
        self._downloaded_bytes: int = 0
        self._total_bytes: int = 0
        self._downloaded_mb: float = 0.0
        self._total_mb: float = 0.0
        self._target_file: Optional[str] = None
        self._temp_dir: Optional[str] = None
        self._target_version: Optional[str] = None
        self._error_msg: Optional[str] = None
        self._worker_thread: Optional[threading.Thread] = None

    def set_window(self, window):
        self._window = window

    def get_progress(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "status": self._status,
                "percent": self._percent,
                "downloaded_mb": self._downloaded_mb,
                "total_mb": self._total_mb,
                "error": self._error_msg,
                "version": self._target_version,
            }

    def start_download(self, download_url: str, version: str) -> Dict[str, Any]:
        if not download_url or not download_url.startswith(OFFICIAL_RELEASE_PREFIX):
            return {
                "success": False,
                "error": f"URL no permitida por directiva de seguridad: {download_url}"
            }

        with self._lock:
            if self._status == "downloading":
                return {"success": True, "message": "Ya hay una descarga en curso"}

            self._status = "downloading"
            self._percent = 0
            self._downloaded_bytes = 0
            self._total_bytes = 0
            self._downloaded_mb = 0.0
            self._total_mb = 0.0
            self._target_version = version
            self._error_msg = None

            ext = ".exe" if sys.platform == "win32" else (".deb" if download_url.endswith(".deb") else ".tar.gz")
            if download_url.endswith(".exe"):
                ext = ".exe"
            elif download_url.endswith(".deb"):
                ext = ".deb"
            elif download_url.endswith(".zip"):
                ext = ".zip"

            self._temp_dir = tempfile.mkdtemp(prefix="mcp2p_update_")
            filename = f"Mcp2p-Setup-{_safe_version_component(version)}{ext}"
            self._target_file = os.path.join(self._temp_dir, filename)

        self._worker_thread = threading.Thread(
            target=self._download_worker,
            args=(download_url, self._target_file),
            daemon=True,
        )
        self._worker_thread.start()
        return {"success": True, "message": "Descarga iniciada en segundo plano"}

    def _download_worker(self, url: str, destination_path: str):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "MCP2P-AutoUpdater/1.0"},
            )
            hasher = hashlib.sha256()
            with urllib.request.urlopen(req, timeout=45) as response, open(destination_path, "wb") as out_file:
                content_len = response.headers.get("Content-Length")
                total_len = int(content_len) if content_len and content_len.isdigit() else 0

                with self._lock:
                    self._total_bytes = total_len
                    self._total_mb = round(total_len / (1024 * 1024), 2) if total_len > 0 else 0.0

                downloaded = 0
                chunk_size = 65536

                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    out_file.write(chunk)
                    hasher.update(chunk)
                    downloaded += len(chunk)

                    with self._lock:
                        self._downloaded_bytes = downloaded
                        self._downloaded_mb = round(downloaded / (1024 * 1024), 2)
                        if total_len > 0:
                            self._percent = min(95, int((downloaded / total_len) * 95))
                        else:
                            self._percent = 50

            if not os.path.exists(destination_path) or os.path.getsize(destination_path) == 0:
                raise RuntimeError("El archivo descargado está vacío o corrupto.")

            asset_filename = url.rsplit("/", 1)[-1]
            sha_url = f"{url.rsplit('/', 1)[0]}/SHA256SUMS"
            req_sha = urllib.request.Request(sha_url, headers={"User-Agent": "MCP2P-AutoUpdater/1.0"})

            try:
                with urllib.request.urlopen(req_sha, timeout=25) as sha_resp:
                    sha_content = sha_resp.read().decode("utf-8", errors="ignore")
            except Exception as e:
                raise RuntimeError(f"No se pudo obtener el manifiesto de integridad SHA256SUMS: {e}")

            expected_hash = None
            for line in sha_content.splitlines():
                parts = line.strip().split()
                if len(parts) >= 2:
                    h, fname = parts[0], parts[1].lstrip("*")
                    if fname == asset_filename:
                        expected_hash = h.lower()
                        break

            if not expected_hash:
                raise RuntimeError(f"El archivo {asset_filename} no está registrado en el manifiesto oficial SHA256SUMS.")

            calculated_hash = hasher.hexdigest().lower()
            if not hmac.compare_digest(calculated_hash, expected_hash):
                if os.path.exists(destination_path):
                    os.remove(destination_path)
                raise RuntimeError("Fallo crítico de integridad: el hash SHA256 no coincide con el manifiesto oficial.")

            with self._lock:
                self._percent = 100
                self._status = "ready_to_restart"

        except Exception as e:
            if destination_path and os.path.exists(destination_path):
                try:
                    os.remove(destination_path)
                except Exception:
                    pass
            with self._lock:
                self._status = "error"
                self._error_msg = str(e)

    def _validate_portable_app_dir(self, app_dir: str) -> bool:
        """Guarda de seguridad: solo autoriza reemplazar en caliente una carpeta portable
        genuina de MCP2P (nunca una ruta de sistema, vacía, relativa o ajena)."""
        if not app_dir or not os.path.isabs(app_dir):
            return False
        if os.path.basename(app_dir.rstrip(os.sep)) != "Mcp2p":
            return False
        if is_system_installed():
            return False
        return os.path.isfile(os.path.join(app_dir, "Mcp2p"))

    def _build_linux_relaunch_script(self, new_archive_path: str, app_dir: str) -> str:
        """Genera un script bash que espera a que MCP2P cierre, reemplaza la carpeta
        portable con el contenido del nuevo tarball ya verificado (SHA256) y relanza
        la app automáticamente, sin intervención manual del usuario."""
        extract_dir = tempfile.mkdtemp(prefix="mcp2p_update_extract_")
        script_dir = self._temp_dir or tempfile.gettempdir()
        script_path = os.path.join(script_dir, "mcp2p_apply_update.sh")
        pid = os.getpid()
        q_extract = shlex.quote(extract_dir)
        q_archive = shlex.quote(new_archive_path)
        q_app_dir = shlex.quote(app_dir)
        script = f"""#!/bin/bash
set -e
for i in $(seq 1 150); do
    kill -0 {pid} 2>/dev/null || break
    sleep 0.1
done
mkdir -p {q_extract}
tar -xzf {q_archive} -C {q_extract}
if [ ! -f {q_extract}/Mcp2p/Mcp2p ]; then
    exit 1
fi
# Modo portable: mcp2p_local.db y workspace/ pueden vivir DENTRO de {q_app_dir}
# (ver src/app/paths.py::get_user_data_dir). El reemplazo en caliente de más abajo
# hace rm -rf sobre esa misma carpeta, así que primero se sacan a un directorio
# temporal fuera de {q_app_dir} y se restauran después de copiar el build nuevo —
# sin esto, cada actualización portable borraría la base de datos del usuario y
# todos los proyectos clonados. No-op seguro si no existen (instalación nueva, o
# datos ya redirigidos a un directorio de usuario separado).
PRESERVE_DIR=$(mktemp -d)
[ -f {q_app_dir}/mcp2p_local.db ] && mv {q_app_dir}/mcp2p_local.db "$PRESERVE_DIR/"
[ -f {q_app_dir}/mcp2p_local.db-wal ] && mv {q_app_dir}/mcp2p_local.db-wal "$PRESERVE_DIR/"
[ -f {q_app_dir}/mcp2p_local.db-shm ] && mv {q_app_dir}/mcp2p_local.db-shm "$PRESERVE_DIR/"
[ -d {q_app_dir}/workspace ] && mv {q_app_dir}/workspace "$PRESERVE_DIR/"
rm -rf {q_app_dir}
mkdir -p {q_app_dir}
cp -a {q_extract}/Mcp2p/. {q_app_dir}/
[ -f "$PRESERVE_DIR/mcp2p_local.db" ] && mv "$PRESERVE_DIR/mcp2p_local.db" {q_app_dir}/
[ -f "$PRESERVE_DIR/mcp2p_local.db-wal" ] && mv "$PRESERVE_DIR/mcp2p_local.db-wal" {q_app_dir}/
[ -f "$PRESERVE_DIR/mcp2p_local.db-shm" ] && mv "$PRESERVE_DIR/mcp2p_local.db-shm" {q_app_dir}/
[ -d "$PRESERVE_DIR/workspace" ] && mv "$PRESERVE_DIR/workspace" {q_app_dir}/
rm -rf "$PRESERVE_DIR"
rm -rf {q_extract}
chmod 755 {q_app_dir}/Mcp2p {q_app_dir}/mcp2p.sh 2>/dev/null || true
nohup {q_app_dir}/mcp2p.sh >/dev/null 2>&1 &
"""
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(script)
        os.chmod(script_path, 0o755)
        return script_path

    def apply_update_and_restart(self) -> Dict[str, Any]:
        with self._lock:
            status = self._status
            target_path = self._target_file

        git_dir = os.path.join(self._base_dir, ".git")
        if not getattr(sys, "frozen", False) and os.path.exists(git_dir):
            try:
                res = run_hidden(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    cwd=self._base_dir, capture_output=True, text=True, check=True, timeout=10,
                )
                current_branch = res.stdout.strip() or "main"
                run_hidden(["git", "pull", "origin", current_branch], cwd=self._base_dir, check=True, timeout=30)
                if self._window:
                    self._window.destroy()
                return {"success": True, "mode": "git_pull", "branch": current_branch}
            except Exception as e:
                return {"success": False, "error": f"Error ejecutando git pull: {e}"}

        if status != "ready_to_restart" or not target_path or not os.path.exists(target_path):
            return {"success": False, "error": "No hay ningún instalador listo para aplicar."}

        try:
            if sys.platform == "win32":
                cmd = [target_path, "/SILENT", "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS"]
                subprocess.Popen(cmd)
            elif sys.platform.startswith("linux"):
                if target_path.endswith(".deb"):
                    # Instalación de sistema (.deb en /opt): requiere un único prompt de
                    # autenticación (polkit) inevitable para escribir en rutas de root.
                    subprocess.Popen(["pkexec", "dpkg", "-i", target_path])

                    def _relaunch_after_deb_install():
                        time.sleep(6.0)
                        try:
                            subprocess.Popen(["mcp2p"])
                        except Exception:
                            pass

                    threading.Thread(target=_relaunch_after_deb_install, daemon=True).start()
                else:
                    # Instalación portable (tarball descomprimido, carpeta de usuario):
                    # reemplazo en caliente sin prompts, sin descarga manual.
                    app_dir = os.path.dirname(sys.executable)
                    if not self._validate_portable_app_dir(app_dir):
                        return {
                            "success": False,
                            "error": "No se pudo verificar de forma segura la carpeta de instalación portable."
                        }
                    script_path = self._build_linux_relaunch_script(target_path, app_dir)
                    subprocess.Popen(["bash", script_path], start_new_session=True)
            elif sys.platform.startswith("darwin"):
                subprocess.Popen(["open", target_path])

            def _close_app():
                time.sleep(1.0)
                if self._window:
                    self._window.destroy()
                else:
                    sys.exit(0)

            threading.Thread(target=_close_app, daemon=True).start()
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}
