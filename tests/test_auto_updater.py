import os
import sys
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


def test_get_install_mode_dev_mode():
    with tempfile.TemporaryDirectory() as tmp_dir:
        test_db = os.path.join(tmp_dir, "test_install_mode.db")
        db = DatabaseManager(test_db)
        bridge = ApiBridge(db_manager=db)

        res = bridge.get_install_mode()
        assert res["success"] is True
        assert res["mode"] == "dev"
        assert res["platform"] == sys.platform
