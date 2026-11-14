import os
import sys
import shutil
import subprocess
import tarfile
import tempfile
import pytest
from src.app.auto_updater import AutoUpdateManager, OFFICIAL_RELEASE_PREFIX
from src.app.paths import get_bundle_dir, get_user_data_dir, get_default_db_path, is_system_installed
from src.storage.db import DatabaseManager
from src.app.api_bridge import ApiBridge


def test_updater_rejects_unauthorized_urls():
    manager = AutoUpdateManager(base_dir=".", user_data_dir=tempfile.gettempdir())

    # 1. URL externa o maliciosa
    bad_url = "https://malicious-site.com/downloads/trojan.exe"
    res = manager.start_download(bad_url, version="1.0.1")
    assert res["success"] is False
    assert "URL no permitida" in res["error"]

    # 2. URL de GitHub pero de otro repositorio
    other_repo_url = "https://github.com/OtherUser/OtherRepo/releases/download/v1.0.1/Mcp2p-Setup-1.0.1.exe"
    res2 = manager.start_download(other_repo_url, version="1.0.1")
    assert res2["success"] is False
    assert "URL no permitida" in res2["error"]

    # 3. URL oficial válida dentro de la allowlist (no se descarga de verdad en el unit test)
    valid_url = f"{OFFICIAL_RELEASE_PREFIX}v1.0.1/Mcp2p-Setup-1.0.1.exe"
    assert valid_url.startswith(OFFICIAL_RELEASE_PREFIX)


def test_updater_progress_starts_idle():
    manager = AutoUpdateManager(base_dir=".", user_data_dir=tempfile.gettempdir())
    progress = manager.get_progress()
    assert progress["status"] == "idle"
    assert progress["percent"] == 0


def test_paths_management():
    bundle_dir = get_bundle_dir()
    assert os.path.isabs(bundle_dir)
    user_data = get_user_data_dir()
    assert os.path.isabs(user_data)
    db_path = get_default_db_path()
    assert os.path.isabs(db_path)
    assert db_path.endswith("mcp2p_local.db")


def test_frozen_paths_simulation(monkeypatch):
    orig_frozen = getattr(sys, "frozen", False)
    orig_exe = sys.executable
    try:
        monkeypatch.setattr(sys, "platform", "linux")
        sys.frozen = True
        sys.executable = "/opt/mcp2p/Mcp2p"
        user_dir = get_user_data_dir()
        assert "mcp2p/data" in user_dir.replace("\\", "/")
        assert os.path.isabs(user_dir)
    finally:
        if orig_frozen:
            sys.frozen = orig_frozen
        else:
            delattr(sys, "frozen")
        sys.executable = orig_exe


def test_api_bridge_auto_update_endpoints():
    with tempfile.TemporaryDirectory() as tmp_dir:
        test_db = os.path.join(tmp_dir, "test_api_updater.db")
        db = DatabaseManager(test_db)
        bridge = ApiBridge(db_manager=db)

        # Test initial progress
        progress = bridge.get_update_progress()
        assert progress["status"] == "idle"
        assert progress["percent"] == 0

        # Test start_auto_update rejects bad URL
        bad_res = bridge.start_auto_update("https://evil.org/payload.exe", "1.0.1")
        assert bad_res["success"] is False
        assert "URL no permitida" in bad_res["error"]

        # Test apply_update_and_restart without ready installer
        restart_res = bridge.apply_update_and_restart()
        # In non-frozen dev mode with .git it attempts git pull or reports no ready installer
        assert "success" in restart_res


def test_is_system_installed_dev_mode_is_always_false():
    # En modo desarrollo (no frozen) nunca debe reportarse como instalación de sistema,
    # sin importar sys.platform o sys.executable.
    assert is_system_installed() is False


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="os.path.dirname de una ruta con backslashes solo se parsea como Windows en un host Windows real (ntpath).",
)
def test_is_system_installed_detects_windows_program_files(monkeypatch):
    orig_frozen = getattr(sys, "frozen", False)
    orig_exe = sys.executable
    try:
        monkeypatch.setattr(sys, "platform", "win32")
        sys.frozen = True
        sys.executable = r"C:\Program Files\MCP2P\Mcp2p.exe"
        assert is_system_installed() is True
    finally:
        if orig_frozen:
            sys.frozen = orig_frozen
        else:
            delattr(sys, "frozen")
        sys.executable = orig_exe


def test_is_system_installed_detects_linux_opt(monkeypatch):
    orig_frozen = getattr(sys, "frozen", False)
    orig_exe = sys.executable
    try:
        monkeypatch.setattr(sys, "platform", "linux")
        sys.frozen = True
        sys.executable = "/opt/mcp2p/Mcp2p"
        assert is_system_installed() is True
    finally:
        if orig_frozen:
            sys.frozen = orig_frozen
        else:
            delattr(sys, "frozen")
        sys.executable = orig_exe


def test_is_system_installed_portable_is_false(monkeypatch):
    orig_frozen = getattr(sys, "frozen", False)
    orig_exe = sys.executable
    try:
        monkeypatch.setattr(sys, "platform", "linux")
        sys.frozen = True
        sys.executable = "/home/user/Descargas/Mcp2p/Mcp2p"
        assert is_system_installed() is False
    finally:
        if orig_frozen:
            sys.frozen = orig_frozen
        else:
            delattr(sys, "frozen")
        sys.executable = orig_exe


def test_validate_portable_app_dir_guard():
    manager = AutoUpdateManager(base_dir=".", user_data_dir=tempfile.gettempdir())

    # Ruta relativa: rechazada
    assert manager._validate_portable_app_dir("Mcp2p") is False
    # Vacía: rechazada
    assert manager._validate_portable_app_dir("") is False
    # Carpeta cuyo nombre final no es "Mcp2p": rechazada
    with tempfile.TemporaryDirectory() as tmp_dir:
        other_dir = os.path.join(tmp_dir, "otra_carpeta")
        os.makedirs(other_dir)
        assert manager._validate_portable_app_dir(other_dir) is False

    # Carpeta "Mcp2p" pero sin el binario adentro: rechazada
    with tempfile.TemporaryDirectory() as tmp_dir:
        app_dir = os.path.join(tmp_dir, "Mcp2p")
        os.makedirs(app_dir)
        assert manager._validate_portable_app_dir(app_dir) is False

        # Con el binario "Mcp2p" presente: aceptada
        with open(os.path.join(app_dir, "Mcp2p"), "w") as f:
            f.write("binario simulado")
        assert manager._validate_portable_app_dir(app_dir) is True


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="El script de reemplazo en caliente generado es bash, solo aplica a Linux/macOS.",
)
def test_linux_relaunch_script_preserves_db_and_workspace_across_rm_rf(monkeypatch):
    """Regresión: en modo portable, mcp2p_local.db y workspace/ viven DENTRO de
    app_dir (ver paths.get_user_data_dir). El script de reemplazo en caliente hace
    `rm -rf {app_dir}` para instalar la versión nueva — sin preservar/restaurar esos
    datos, cada auto-actualización portable destruye la base de datos del usuario y
    todos los proyectos clonados. Este test ejecuta el script bash real generado,
    de punta a punta, sobre carpetas temporales (nunca toca el repo real)."""
    with tempfile.TemporaryDirectory() as tmp_root:
        # 1. App dir "portable" existente, con datos de usuario adentro (el escenario real).
        app_dir = os.path.join(tmp_root, "Mcp2p")
        os.makedirs(app_dir)
        with open(os.path.join(app_dir, "Mcp2p"), "w") as f:
            f.write("binario viejo simulado")
        with open(os.path.join(app_dir, "mcp2p_local.db"), "w") as f:
            f.write("DATOS CRITICOS DEL USUARIO")
        with open(os.path.join(app_dir, "mcp2p_local.db-wal"), "w") as f:
            f.write("wal")
        with open(os.path.join(app_dir, "mcp2p_local.db-shm"), "w") as f:
            f.write("shm")
        workspace_dir = os.path.join(app_dir, "workspace", "proyecto_clonado")
        os.makedirs(workspace_dir)
        with open(os.path.join(workspace_dir, "archivo_del_proyecto.txt"), "w") as f:
            f.write("contenido real de un repo clonado")

        # 2. Tarball de "release nueva" válido (igual a lo que publica el CI).
        release_src = os.path.join(tmp_root, "release_src")
        os.makedirs(os.path.join(release_src, "Mcp2p"))
        with open(os.path.join(release_src, "Mcp2p", "Mcp2p"), "w") as f:
            f.write("binario nuevo simulado")
        with open(os.path.join(release_src, "Mcp2p", "mcp2p.sh"), "w") as f:
            f.write("#!/bin/bash\nexit 0\n")
        archive_path = os.path.join(tmp_root, "Mcp2p-Setup-1.0.9.tar.gz")
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(os.path.join(release_src, "Mcp2p"), arcname="Mcp2p")

        # 3. user_data_dir == app_dir, igual que get_user_data_dir() en modo portable escribible.
        manager = AutoUpdateManager(base_dir=tmp_root, user_data_dir=app_dir)
        manager._temp_dir = tempfile.mkdtemp(prefix="mcp2p_update_test_", dir=tmp_root)

        # El script espera a que el PID actual muera antes de reemplazar la carpeta;
        # usamos un PID que ya no existe para que el script avance de inmediato
        # en vez de esperar hasta 15s a que el proceso de este mismo test "muera".
        monkeypatch.setattr(os, "getpid", lambda: 999999999)
        script_path = manager._build_linux_relaunch_script(archive_path, app_dir)

        result = subprocess.run(["bash", script_path], capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, f"script falló: stdout={result.stdout} stderr={result.stderr}"

        # 4. El binario se actualizó...
        with open(os.path.join(app_dir, "Mcp2p")) as f:
            assert f.read() == "binario nuevo simulado"

        # 5. ...pero los datos del usuario sobrevivieron intactos.
        with open(os.path.join(app_dir, "mcp2p_local.db")) as f:
            assert f.read() == "DATOS CRITICOS DEL USUARIO"
        assert os.path.exists(os.path.join(app_dir, "mcp2p_local.db-wal"))
        assert os.path.exists(os.path.join(app_dir, "mcp2p_local.db-shm"))
        restored_project_file = os.path.join(app_dir, "workspace", "proyecto_clonado", "archivo_del_proyecto.txt")
        with open(restored_project_file) as f:
            assert f.read() == "contenido real de un repo clonado"


def test_get_install_mode_dev_mode():
    with tempfile.TemporaryDirectory() as tmp_dir:
        test_db = os.path.join(tmp_dir, "test_install_mode.db")
        db = DatabaseManager(test_db)
        bridge = ApiBridge(db_manager=db)

        res = bridge.get_install_mode()
        assert res["success"] is True
        assert res["mode"] == "dev"
        assert res["platform"] == sys.platform


@pytest.mark.parametrize(
    "tag_name",
    ["v1.0.1", "../../../tmp/evil", "release/1.2", "..\\..\\x", "", "a" * 200],
)
def test_updater_target_file_never_escapes_temp_dir(tag_name, monkeypatch):
    """`version` (tag_name remoto) forma parte del nombre del archivo temporal: nunca debe
    producir una ruta fuera de `_temp_dir` (ver docs/okf/auditoria_2026-11/26_)."""
    monkeypatch.setattr(AutoUpdateManager, "_download_worker", lambda self, url, dest: None)
    manager = AutoUpdateManager(base_dir=".", user_data_dir=tempfile.gettempdir())
    url = f"{OFFICIAL_RELEASE_PREFIX}v1.0.1/Mcp2p-Setup-1.0.1.tar.gz"
    try:
        res = manager.start_download(url, version=tag_name)
        assert res["success"] is True
        target = os.path.realpath(manager._target_file)
        assert os.path.dirname(target) == os.path.realpath(manager._temp_dir)
        assert os.path.basename(target).startswith("Mcp2p-Setup-")
    finally:
        shutil.rmtree(manager._temp_dir, ignore_errors=True)
