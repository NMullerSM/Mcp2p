"""
Módulo de Actualización Automática en Segundo Plano (Auto-Updater Protocol) para MCP2P.
Descarga binarios o instaladores en segundo plano sin congelar la interfaz ni cerrar
abruptamente el programa, ofreciendo progreso continuo (RPC) y reinicio silencioso
seguro con cero pérdida de datos (mcp2p_local.db y workspace/ no se sobreescriben al actualizar).

Estados de `get_progress()["status"]`:
    idle -> downloading -> verifying -> ready_to_restart -> installing
                      \\-> error (con `error` = causa legible; reintentable)

Cada paso queda registrado en `<user_data_dir>/logs/updater.log` (rotación simple, sin
secretos) para poder diagnosticar un fallo posterior.
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
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import zipfile
from datetime import datetime
from typing import Optional, Dict, Any

from src.proc_utils import run_hidden
from src.app.paths import is_system_installed

OFFICIAL_RELEASE_PREFIX = "https://github.com/NMullerSM/Mcp2p/releases/download/"
OFFICIAL_RELEASES_PAGE = "https://github.com/NMullerSM/Mcp2p/releases"

_USER_AGENT = "MCP2P-AutoUpdater/1.1"
_LOG_MAX_BYTES = 256 * 1024
_LOG_BACKUPS = 2

# Banderas de creación de procesos de Windows (no existen en `subprocess` de otras plataformas).
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_NO_WINDOW = 0x08000000

# Tras pedir el cierre de la ventana, si el proceso sigue vivo pasado este margen se fuerza la
# salida: el instalador no puede reemplazar `Mcp2p.exe` mientras siga abierto.
_EXIT_GRACE_SECONDS = 10.0


def _safe_version_component(version: str) -> str:
    """
    `version` es el `tag_name` de la release (dato remoto de la API de GitHub) y se
    interpola en el nombre del archivo temporal: se reduce a un único componente de ruta
    sin separadores ni '..' para que nunca pueda escribir fuera del directorio temporal.
    """
    cleaned = re.sub(r"[^0-9A-Za-z._+-]", "_", str(version or ""))
    cleaned = cleaned.replace("..", "_").strip(".")
    return cleaned[:64] or "unknown"


def _is_trusted_download_url(url: str) -> bool:
    """URL permitida para descargas: el prefijo oficial (también tras una redirección) o, tras
    redirigir, un host de GitHub servido por https. Impide bajar de nivel a http o saltar a
    un dominio ajeno aunque el servidor de origen lo ordene."""
    if not url:
        return False
    if url.startswith(OFFICIAL_RELEASE_PREFIX):
        return True
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (host == "github.com" or host.endswith(".githubusercontent.com"))


def is_official_release_page(url: str) -> bool:
    """True si `url` es https://github.com/NMullerSM/Mcp2p/releases[/...] (sin credenciales)."""
    try:
        parsed = urllib.parse.urlparse(str(url or ""))
    except ValueError:
        return False
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != "github.com" or parsed.username or parsed.port:
        return False
    base = "/nmullersm/mcp2p/releases"
    path = parsed.path.lower().rstrip("/")
    return path == base or path.startswith(base + "/")


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Revalida cada salto de redirección (GitHub redirige a *.githubusercontent.com)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _is_trusted_download_url(newurl):
            raise urllib.error.URLError(f"Redirección a un destino no permitido: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _describe_error(exc: BaseException) -> str:
    """Causa legible (en español) para mostrar en la UI y registrar en el log."""
    if isinstance(exc, urllib.error.HTTPError):
        return f"El servidor respondió HTTP {exc.code} ({exc.reason}) al descargar {exc.url}"
    if isinstance(exc, urllib.error.URLError):
        return f"No se pudo conectar con el servidor de descargas: {exc.reason}"
    if isinstance(exc, TimeoutError):
        return "Se agotó el tiempo de espera de la descarga."
    return str(exc) or exc.__class__.__name__


def get_install_info() -> Dict[str, Any]:
    """Cómo está instalada ESTA copia, para elegir el asset de la release y el mecanismo de reemplazo.

    `kind`: dev | windows_installer | windows_portable | linux_deb | linux_portable | macos.
    En Windows, el instalador Inno Setup (por usuario, `{autopf}` con PrivilegesRequired=lowest
    instala en %LOCALAPPDATA%\\Programs, no en Program Files) deja `unins000.exe` junto al
    ejecutable; el zip portable no. Es la señal fiable (is_system_installed() solo mira Program Files).
    """
    platform = sys.platform
    if not getattr(sys, "frozen", False):
        return {"success": True, "platform": platform, "mode": "dev", "kind": "dev"}

    exe_dir = os.path.dirname(sys.executable)
    if platform == "win32":
        try:
            has_uninstaller = any(
                n.lower().startswith("unins") and n.lower().endswith(".exe") for n in os.listdir(exe_dir)
            )
        except OSError:
            has_uninstaller = False
        if has_uninstaller or is_system_installed():
            return {"success": True, "platform": platform, "mode": "system", "kind": "windows_installer"}
        return {"success": True, "platform": platform, "mode": "portable", "kind": "windows_portable"}
    if platform.startswith("linux"):
        if is_system_installed():
            return {"success": True, "platform": platform, "mode": "system", "kind": "linux_deb"}
        return {"success": True, "platform": platform, "mode": "portable", "kind": "linux_portable"}
    if platform.startswith("darwin"):
        return {"success": True, "platform": platform, "mode": "portable", "kind": "macos"}
    return {"success": True, "platform": platform, "mode": "portable", "kind": "unknown"}


def _ps_quote(value: str) -> str:
    """Literal de cadena de PowerShell entre comillas simples."""
    return "'" + str(value).replace("'", "''") + "'"


class AutoUpdateManager:
    """Gestor de descargas asíncronas y aplicación de actualizaciones en caliente."""

    def __init__(self, base_dir: str, user_data_dir: str):
        self._base_dir = base_dir
        self._user_data_dir = user_data_dir
        self._window = None
        self._lock = threading.Lock()
        self._log_lock = threading.Lock()

        # "idle" | "downloading" | "verifying" | "ready_to_restart" | "installing" | "error"
        self._status: str = "idle"
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

    # ------------------------------------------------------------------ registro

    @property
    def log_path(self) -> str:
        return os.path.join(self._user_data_dir, "logs", "updater.log")

    def _log(self, message: str) -> None:
        """Añade una línea a updater.log (rotación simple). Nunca lanza: el log no debe romper el flujo."""
        line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [{sys.platform}] {message}\n"
        try:
            with self._log_lock:
                path = self.log_path
                os.makedirs(os.path.dirname(path), exist_ok=True)
                if os.path.exists(path) and os.path.getsize(path) > _LOG_MAX_BYTES:
                    for i in range(_LOG_BACKUPS, 0, -1):
                        src = path if i == 1 else f"{path}.{i - 1}"
                        if os.path.exists(src):
                            os.replace(src, f"{path}.{i}")
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(line)
        except Exception:
            pass

    def get_log(self, max_lines: int = 200) -> Dict[str, Any]:
        try:
            max_lines = max(1, min(int(max_lines), 2000))
        except (TypeError, ValueError):
            max_lines = 200
        path = self.log_path
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
        except FileNotFoundError:
            return {"success": True, "path": path, "text": ""}
        except OSError as e:
            return {"success": False, "path": path, "error": f"No se pudo leer el registro: {e}"}
        return {"success": True, "path": path, "text": "\n".join(lines[-max_lines:])}

    def open_release_page(self, url: Optional[str] = None) -> Dict[str, Any]:
        """Respaldo cuando la descarga automática falla: abre la página de la release en el
        navegador del sistema (`window.open` no hace nada dentro de PyWebView)."""
        target = url or OFFICIAL_RELEASES_PAGE
        if not is_official_release_page(target):
            self._log(f"open_release_page rechazada: {target}")
            return {"success": False, "error": f"URL no permitida por directiva de seguridad: {target}"}
        try:
            opened = bool(webbrowser.open(target))
            if not opened and sys.platform == "win32" and hasattr(os, "startfile"):
                os.startfile(target)  # type: ignore[attr-defined]
                opened = True
        except Exception as e:
            self._log(f"open_release_page falló: {e}")
            return {"success": False, "error": f"No se pudo abrir el navegador: {e}", "url": target}
        self._log(f"open_release_page({target}) -> {opened}")
        if not opened:
            return {"success": False, "error": "El sistema no pudo abrir el navegador.", "url": target}
        return {"success": True, "url": target}

    def get_install_info(self) -> Dict[str, Any]:
        return get_install_info()

    # ------------------------------------------------------------------ progreso

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

    def _fail(self, message: str) -> None:
        self._log(f"ERROR: {message}")
        with self._lock:
            self._status = "error"
            self._error_msg = message

    # ------------------------------------------------------------------ descarga

    def start_download(self, download_url: str, version: str) -> Dict[str, Any]:
        if not download_url or not download_url.startswith(OFFICIAL_RELEASE_PREFIX):
            self._log(f"start_download rechazada (URL no oficial): {download_url}")
            return {
                "success": False,
                "error": f"URL no permitida por directiva de seguridad: {download_url}"
            }

        with self._lock:
            if self._status in ("downloading", "verifying"):
                return {"success": True, "message": "Ya hay una descarga en curso"}
            if self._status == "installing":
                return {"success": True, "message": "La instalación ya está en curso"}

            previous_dir = self._temp_dir
            self._status = "downloading"
            self._percent = 0
            self._downloaded_bytes = 0
            self._total_bytes = 0
            self._downloaded_mb = 0.0
            self._total_mb = 0.0
            self._target_version = version
            self._error_msg = None

            ext = ".tar.gz"
            if download_url.endswith(".exe"):
                ext = ".exe"
            elif download_url.endswith(".deb"):
                ext = ".deb"
            elif download_url.endswith(".zip"):
                ext = ".zip"

            self._temp_dir = tempfile.mkdtemp(prefix="mcp2p_update_")
            filename = f"Mcp2p-Setup-{_safe_version_component(version)}{ext}"
            self._target_file = os.path.join(self._temp_dir, filename)
            target_file = self._target_file

        if previous_dir and previous_dir != self._temp_dir:
            shutil.rmtree(previous_dir, ignore_errors=True)

        self._log(f"start_download version={version} url={download_url} destino={target_file}")
        self._worker_thread = threading.Thread(
            target=self._download_worker,
            args=(download_url, target_file),
            daemon=True,
        )
        self._worker_thread.start()
        return {"success": True, "message": "Descarga iniciada en segundo plano"}

    def _open(self, url: str, timeout: float):
        opener = urllib.request.build_opener(_SafeRedirectHandler())
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        return opener.open(req, timeout=timeout)

    def _download_worker(self, url: str, destination_path: str):
        try:
            hasher = hashlib.sha256()
            with self._open(url, 45) as response, open(destination_path, "wb") as out_file:
                content_len = response.headers.get("Content-Length")
                total_len = int(content_len) if content_len and content_len.isdigit() else 0
                self._log(f"descarga: respuesta HTTP {getattr(response, 'status', '?')} url_final={response.geturl()} bytes={total_len}")

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
            if total_len > 0 and downloaded != total_len:
                raise RuntimeError(
                    f"Descarga incompleta: se recibieron {downloaded} de {total_len} bytes. Revisa tu conexión y reintenta."
                )

            with self._lock:
                self._status = "verifying"
                self._percent = 96
            self._log(f"descarga completa ({downloaded} bytes); verificando SHA256SUMS")

            asset_filename = urllib.parse.unquote(url.rsplit("/", 1)[-1].split("?", 1)[0])
            sha_url = f"{url.rsplit('/', 1)[0]}/SHA256SUMS"

            try:
                with self._open(sha_url, 25) as sha_resp:
                    sha_content = sha_resp.read().decode("utf-8", errors="ignore")
            except Exception as e:
                raise RuntimeError(f"No se pudo obtener el manifiesto de integridad SHA256SUMS: {_describe_error(e)}")

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
                raise RuntimeError("Fallo crítico de integridad: el hash SHA256 no coincide con el manifiesto oficial.")

            with self._lock:
                self._percent = 100
                self._status = "ready_to_restart"
            self._log(f"verificación SHA256 correcta ({asset_filename}); listo para reiniciar")

        except Exception as e:
            if destination_path and os.path.exists(destination_path):
                try:
                    os.remove(destination_path)
                except Exception:
                    pass
            self._fail(_describe_error(e))

    # ------------------------------------------------------------------ Linux

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

    # ------------------------------------------------------------------ Windows

    def _windows_detached_flags(self) -> int:
        return _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP

    def _shell_execute(self, path: str, params: str, workdir: Optional[str] = None) -> None:
        """Lanza `path` con ShellExecuteW (verbo "open"). A diferencia de `subprocess.Popen`
        (CreateProcess), ShellExecute pide elevación por UAC cuando el manifiesto del
        ejecutable la exige en vez de fallar con WinError 740."""
        import ctypes

        result = ctypes.windll.shell32.ShellExecuteW(None, "open", path, params, workdir, 1)  # type: ignore[attr-defined]
        if int(result) <= 32:
            raise OSError(f"ShellExecuteW devolvió {int(result)} al lanzar {path}")

    def _launch_windows_installer(self, installer_path: str) -> Dict[str, Any]:
        """Instalador Inno Setup: silencioso, cierra la app en uso y la reabre al terminar
        (`/RELAUNCH=1` lo atiende el [Run] de mcp2p_setup.iss). Se intenta ShellExecuteW y, si
        no está disponible, un Popen desacoplado de este proceso."""
        installer_log = os.path.join(os.path.dirname(installer_path), "installer.log")
        params = f'/SILENT /CLOSEAPPLICATIONS /NORESTART /RELAUNCH=1 /LOG="{installer_log}"'
        workdir = os.path.dirname(installer_path)
        self._log(f"lanzando instalador: {installer_path} {params}")
        try:
            self._shell_execute(installer_path, params, workdir)
            self._log("instalador lanzado con ShellExecuteW")
            return {"success": True}
        except Exception as first_error:
            self._log(f"ShellExecuteW falló ({first_error}); reintentando con CreateProcess desacoplado")
        try:
            subprocess.Popen(
                [installer_path, "/SILENT", "/CLOSEAPPLICATIONS", "/NORESTART", "/RELAUNCH=1", f"/LOG={installer_log}"],
                creationflags=self._windows_detached_flags(),
                close_fds=True,
            )
            self._log("instalador lanzado con Popen (DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP)")
            return {"success": True}
        except OSError as e:
            hint = " (el instalador requiere permisos de administrador)" if getattr(e, "winerror", None) == 740 else ""
            return {"success": False, "error": f"No se pudo iniciar el instalador{hint}: {e}"}

    def _validate_windows_portable_dir(self, app_dir: str) -> bool:
        if not app_dir or not os.path.isabs(app_dir):
            return False
        if is_system_installed():
            return False
        return os.path.isfile(os.path.join(app_dir, "Mcp2p.exe"))

    @staticmethod
    def _safe_extract_zip(zip_path: str, dest_dir: str) -> None:
        """Extrae sin permitir rutas absolutas ni '..' (zip-slip)."""
        dest_real = os.path.realpath(dest_dir)
        with zipfile.ZipFile(zip_path) as zf:
            for member in zf.infolist():
                name = member.filename
                if name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", name):
                    raise RuntimeError(f"El paquete contiene una ruta absoluta no permitida: {name}")
                target = os.path.realpath(os.path.join(dest_real, name))
                if target != dest_real and not target.startswith(dest_real + os.sep):
                    raise RuntimeError(f"El paquete contiene una ruta fuera del destino: {name}")
            zf.extractall(dest_real)

    def _build_windows_portable_script(self, source_dir: str, app_dir: str, pid: int, log_path: str) -> str:
        """Script de PowerShell que espera a que esta app cierre, copia el build nuevo encima de la
        carpeta portable (sin purgar: mcp2p_local.db y workspace\\ quedan intactos) y la reabre."""
        return f"""$ErrorActionPreference = 'Continue'
$log = {_ps_quote(log_path)}
function Log($m) {{ try {{ Add-Content -Path $log -Value ("{{0}} [updater.ps1] {{1}}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m) -Encoding UTF8 }} catch {{}} }}
$src = {_ps_quote(source_dir)}
$dst = {_ps_quote(app_dir)}
Log "esperando a que termine el proceso {pid}"
try {{ Wait-Process -Id {pid} -Timeout 60 -ErrorAction SilentlyContinue }} catch {{}}
Start-Sleep -Seconds 1
Log "copiando $src -> $dst"
& robocopy $src $dst /E /R:10 /W:1 /NFL /NDL /NJH /NJS /NP /XF mcp2p_local.db mcp2p_local.db-wal mcp2p_local.db-shm /XD workspace | Out-Null
$rc = $LASTEXITCODE
if ($rc -ge 8) {{ Log "robocopy falló con código $rc; no se reabre la app"; exit 1 }}
Log "copia completada (robocopy $rc); relanzando"
Start-Process -FilePath (Join-Path $dst 'Mcp2p.exe') -WorkingDirectory $dst
Remove-Item -LiteralPath $src -Recurse -Force -ErrorAction SilentlyContinue
"""

    def _apply_windows_portable(self, zip_path: str) -> Dict[str, Any]:
        app_dir = os.path.dirname(sys.executable)
        if not self._validate_windows_portable_dir(app_dir):
            return {
                "success": False,
                "error": "No se pudo verificar de forma segura la carpeta de la instalación portable.",
            }
        staging = os.path.join(self._temp_dir or tempfile.gettempdir(), "portable_staging")
        shutil.rmtree(staging, ignore_errors=True)
        os.makedirs(staging, exist_ok=True)
        try:
            self._safe_extract_zip(zip_path, staging)
        except Exception as e:
            self._log(f"extracción del zip falló: {e}")
            return {"success": False, "error": f"No se pudo descomprimir el paquete portable: {e}"}

        source_dir = os.path.join(staging, "Mcp2p")
        if not os.path.isfile(os.path.join(source_dir, "Mcp2p.exe")):
            source_dir = staging
        if not os.path.isfile(os.path.join(source_dir, "Mcp2p.exe")):
            return {"success": False, "error": "El paquete portable no contiene Mcp2p.exe."}

        script_path = os.path.join(self._temp_dir or staging, "mcp2p_apply_update.ps1")
        script = self._build_windows_portable_script(source_dir, app_dir, os.getpid(), self.log_path)
        with open(script_path, "w", encoding="utf-8-sig") as fh:
            fh.write(script)
        cmd = [
            "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-WindowStyle", "Hidden", "-File", script_path,
        ]
        self._log(f"lanzando reemplazo portable: {cmd}")
        try:
            subprocess.Popen(
                cmd,
                creationflags=self._windows_detached_flags() | _CREATE_NO_WINDOW,
                close_fds=True,
            )
        except OSError as e:
            return {"success": False, "error": f"No se pudo iniciar PowerShell para reemplazar los archivos: {e}"}
        return {"success": True}

    # ------------------------------------------------------------------ aplicar

    def _request_app_exit(self, delay: float = 1.0) -> None:
        """Cierra la ventana y, si el proceso sigue vivo tras el margen, fuerza la salida para
        liberar los archivos que el instalador necesita reemplazar."""

        def _close_app():
            time.sleep(delay)
            if self._window:
                try:
                    self._window.destroy()
                except Exception as e:
                    self._log(f"window.destroy() falló: {e}")
            time.sleep(_EXIT_GRACE_SECONDS)
            self._log("el proceso seguía vivo tras el cierre de la ventana; salida forzada")
            os._exit(0)

        threading.Thread(target=_close_app, daemon=True).start()

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
                self._log(f"modo desarrollo: git pull origin {current_branch}")
                if self._window:
                    self._window.destroy()
                return {"success": True, "mode": "git_pull", "branch": current_branch}
            except Exception as e:
                self._log(f"git pull falló: {e}")
                return {"success": False, "error": f"Error ejecutando git pull: {e}"}

        if status != "ready_to_restart" or not target_path or not os.path.exists(target_path):
            self._log(f"apply_update_and_restart sin instalador listo (status={status})")
            return {"success": False, "error": "No hay ningún instalador listo para aplicar."}

        self._log(f"apply_update_and_restart: {target_path}")
        try:
            mode = "installer"
            if sys.platform == "win32":
                if target_path.endswith(".zip"):
                    mode = "windows_portable"
                    res = self._apply_windows_portable(target_path)
                else:
                    mode = "windows_installer"
                    res = self._launch_windows_installer(target_path)
                if not res.get("success"):
                    self._log(f"aplicar falló: {res.get('error')}")
                    return res
            elif sys.platform.startswith("linux"):
                if target_path.endswith(".deb"):
                    mode = "linux_deb"
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
                    mode = "linux_portable"
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

            with self._lock:
                self._status = "installing"
            self._log(f"aplicación en curso (modo={mode}); cerrando la app")
            self._request_app_exit()
            return {"success": True, "mode": mode}
        except Exception as e:
            self._log(f"apply_update_and_restart falló: {e}")
            return {"success": False, "error": str(e)}
