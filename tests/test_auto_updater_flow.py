"""
Flujo completo del actualizador (src/app/auto_updater.py) contra un servidor HTTP local y con
el lanzado del instalador simulado por plataforma. Nada de aquí ejecuta un instalador real
ni toca la red: `OFFICIAL_RELEASE_PREFIX` apunta a 127.0.0.1.

Contexto: docs/okf/auditoria_2026-11/39_ACTUALIZACION_AUTOMATICA_WINDOWS.md
"""
import hashlib
import http.server
import os
import socket
import subprocess
import sys
import threading
import time
import zipfile

import pytest

import src.app.auto_updater as au
from src.app.auto_updater import AutoUpdateManager, get_install_info, is_official_release_page

PAYLOAD = b"MZ" + b"installer-bytes-" * 5000
ASSET = "Mcp2p-Setup-v2.0.1.exe"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _Server:
    def __init__(self):
        self.routes = {}  # path -> (status, headers, body)
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                status, headers, body = outer.routes.get(self.path, (404, {}, b"no existe"))
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                if "Content-Length" not in headers and status not in (301, 302):
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                self.wfile.flush()
                if headers.get("X-Truncate"):
                    self.close_connection = True

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def prefix(self):
        return f"http://127.0.0.1:{self.port}/"

    def publish(self, version="v2.0.1", asset=ASSET, payload=PAYLOAD, sums=None):
        base = f"/{version}/"
        self.routes[base + asset] = (200, {}, payload)
        if sums is None:
            sums = f"{_sha(payload)}  {asset}\n".encode()
        self.routes[base + "SHA256SUMS"] = (200, {}, sums)
        return f"{self.prefix}{version}/{asset}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server(monkeypatch):
    srv = _Server()
    monkeypatch.setattr(au, "OFFICIAL_RELEASE_PREFIX", srv.prefix)
    yield srv
    srv.close()


@pytest.fixture
def manager(tmp_path):
    m = AutoUpdateManager(base_dir=str(tmp_path), user_data_dir=str(tmp_path / "userdata"))
    yield m
    if m._temp_dir:
        import shutil
        shutil.rmtree(m._temp_dir, ignore_errors=True)


def _wait_final(m, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        p = m.get_progress()
        if p["status"] in ("ready_to_restart", "error"):
            return p
        time.sleep(0.05)
    raise AssertionError(f"la descarga no terminó: {m.get_progress()}")


# ---------------------------------------------------------------- descarga


def test_download_ok_with_correct_sha256(server, manager):
    url = server.publish()
    assert manager.start_download(url, "v2.0.1")["success"] is True
    p = _wait_final(manager)
    assert p["status"] == "ready_to_restart", p
    assert p["percent"] == 100 and p["error"] is None
    with open(manager._target_file, "rb") as fh:
        assert fh.read() == PAYLOAD
    log = manager.get_log()["text"]
    assert "start_download" in log and "verificación SHA256 correcta" in log


def test_download_wrong_hash_is_rejected_and_file_deleted(server, manager):
    url = server.publish(sums=f"{'0' * 64}  {ASSET}\n".encode())
    manager.start_download(url, "v2.0.1")
    p = _wait_final(manager)
    assert p["status"] == "error"
    assert "no coincide" in p["error"]
    assert not os.path.exists(manager._target_file)
    assert "ERROR" in manager.get_log()["text"]


def test_download_asset_missing_from_manifest(server, manager):
    url = server.publish(sums=f"{_sha(PAYLOAD)}  Otro-Archivo.exe\n".encode())
    manager.start_download(url, "v2.0.1")
    p = _wait_final(manager)
    assert p["status"] == "error"
    assert "no está registrado" in p["error"] and ASSET in p["error"]


def test_download_manifest_with_binary_marker_star_is_accepted(server, manager):
    url = server.publish(sums=f"{_sha(PAYLOAD)} *{ASSET}\n".encode())
    manager.start_download(url, "v2.0.1")
    assert _wait_final(manager)["status"] == "ready_to_restart"


def test_download_manifest_unavailable_is_visible_error(server, manager):
    url = server.publish()
    del server.routes["/v2.0.1/SHA256SUMS"]
    manager.start_download(url, "v2.0.1")
    p = _wait_final(manager)
    assert p["status"] == "error" and "SHA256SUMS" in p["error"] and "404" in p["error"]


def test_download_follows_redirect_within_trusted_prefix(server, manager):
    url = server.publish()
    server.routes["/v2.0.1/redirigido.exe"] = (302, {"Location": f"{server.prefix}v2.0.1/{ASSET}"}, b"")
    # La verificación usa el nombre del último segmento de la URL pedida: el manifiesto lo declara.
    server.routes["/v2.0.1/SHA256SUMS"] = (200, {}, f"{_sha(PAYLOAD)}  redirigido.exe\n".encode())
    manager.start_download(f"{server.prefix}v2.0.1/redirigido.exe", "v2.0.1")
    p = _wait_final(manager)
    assert p["status"] == "ready_to_restart", p
    assert "url_final=" in manager.get_log()["text"]


def test_redirect_to_untrusted_host_is_blocked(server, manager):
    server.routes["/v2.0.1/" + ASSET] = (302, {"Location": "http://evil.example/payload.exe"}, b"")
    manager.start_download(f"{server.prefix}v2.0.1/{ASSET}", "v2.0.1")
    p = _wait_final(manager)
    assert p["status"] == "error" and "no permitido" in p["error"]


def test_server_down_reports_connection_error(monkeypatch, manager):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # puerto libre sin nadie escuchando
    prefix = f"http://127.0.0.1:{port}/"
    monkeypatch.setattr(au, "OFFICIAL_RELEASE_PREFIX", prefix)
    manager.start_download(f"{prefix}v2.0.1/{ASSET}", "v2.0.1")
    p = _wait_final(manager)
    assert p["status"] == "error"
    assert "No se pudo conectar" in p["error"]


def test_truncated_download_is_detected(server, manager):
    url = server.publish()
    server.routes["/v2.0.1/" + ASSET] = (200, {"Content-Length": str(len(PAYLOAD) + 5000), "X-Truncate": "1"}, PAYLOAD)
    manager.start_download(url, "v2.0.1")
    p = _wait_final(manager)
    assert p["status"] == "error" and "incompleta" in p["error"]


def test_non_official_url_is_rejected_and_logged(manager):
    res = manager.start_download("https://evil.example/Mcp2p-Setup-v2.0.1.exe", "v2.0.1")
    assert res["success"] is False and "URL no permitida" in res["error"]
    assert manager.get_progress()["status"] == "idle"
    assert "rechazada" in manager.get_log()["text"]


def test_retry_after_error_resets_state(server, manager):
    bad = server.publish(sums=f"{'1' * 64}  {ASSET}\n".encode())
    manager.start_download(bad, "v2.0.1")
    assert _wait_final(manager)["status"] == "error"
    good = server.publish()
    assert manager.start_download(good, "v2.0.1")["success"] is True
    p = _wait_final(manager)
    assert p["status"] == "ready_to_restart" and p["error"] is None


def test_cross_version_confusion_hash_of_other_asset_is_not_used(server, manager):
    other = b"otro contenido"
    url = server.publish(sums=f"{_sha(other)}  {ASSET}\n".encode())
    manager.start_download(url, "v2.0.1")
    assert _wait_final(manager)["status"] == "error"


# ---------------------------------------------------------------- registro


def test_log_rotation_and_tail(manager, monkeypatch):
    monkeypatch.setattr(au, "_LOG_MAX_BYTES", 200)
    for i in range(30):
        manager._log(f"linea numero {i} " + "x" * 20)
    assert os.path.exists(manager.log_path + ".1")
    res = manager.get_log(max_lines=5)
    assert res["success"] and len(res["text"].splitlines()) <= 5
    assert manager.get_log()["path"] == manager.log_path


def test_get_log_without_file_is_empty_not_error(manager):
    res = manager.get_log()
    assert res == {"success": True, "path": manager.log_path, "text": ""}


# ---------------------------------------------------------------- página de descarga


def test_open_release_page_validates_url(manager, monkeypatch):
    opened = []
    monkeypatch.setattr(au.webbrowser, "open", lambda u: opened.append(u) or True)
    ok = "https://github.com/NMullerSM/Mcp2p/releases/tag/v2.0.1"
    assert manager.open_release_page(ok) == {"success": True, "url": ok}
    assert manager.open_release_page(None)["url"] == au.OFFICIAL_RELEASES_PAGE
    for bad in (
        "https://github.com/NMullerSM/Mcp2p-evil/releases",
        "https://github.com.evil.example/NMullerSM/Mcp2p/releases",
        "http://github.com/NMullerSM/Mcp2p/releases",
        "https://github.com/Otro/Mcp2p/releases",
        "https://user@github.com/NMullerSM/Mcp2p/releases",
        "javascript:alert(1)",
        "file:///etc/passwd",
    ):
        assert manager.open_release_page(bad)["success"] is False, bad
        assert not is_official_release_page(bad)
    assert opened == [ok, au.OFFICIAL_RELEASES_PAGE]


def test_open_release_page_reports_browser_failure(manager, monkeypatch):
    monkeypatch.setattr(au.webbrowser, "open", lambda u: False)
    monkeypatch.setattr(sys, "platform", "linux")
    res = manager.open_release_page("https://github.com/NMullerSM/Mcp2p/releases")
    assert res["success"] is False and "navegador" in res["error"]


# ---------------------------------------------------------------- modo de instalación


def _frozen(monkeypatch, platform, exe):
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", exe)


def test_install_info_dev_mode():
    info = get_install_info()
    assert info["kind"] == "dev" and info["mode"] == "dev"


def test_install_info_windows_installer_detected_by_uninstaller(tmp_path, monkeypatch):
    app = tmp_path / "Programs" / "MCP2P"
    app.mkdir(parents=True)
    (app / "Mcp2p.exe").write_bytes(b"x")
    (app / "unins000.exe").write_bytes(b"x")
    _frozen(monkeypatch, "win32", str(app / "Mcp2p.exe"))
    info = get_install_info()
    assert info["kind"] == "windows_installer" and info["platform"] == "win32"


def test_install_info_windows_portable_without_uninstaller(tmp_path, monkeypatch):
    app = tmp_path / "Mcp2p"
    app.mkdir()
    (app / "Mcp2p.exe").write_bytes(b"x")
    _frozen(monkeypatch, "win32", str(app / "Mcp2p.exe"))
    monkeypatch.setattr(au, "is_system_installed", lambda: False)
    info = get_install_info()
    assert info["kind"] == "windows_portable" and info["mode"] == "portable"


def test_install_info_linux_kinds(monkeypatch):
    _frozen(monkeypatch, "linux", "/opt/mcp2p/Mcp2p")
    monkeypatch.setattr(au, "is_system_installed", lambda: True)
    assert get_install_info()["kind"] == "linux_deb"
    monkeypatch.setattr(au, "is_system_installed", lambda: False)
    assert get_install_info()["kind"] == "linux_portable"


# ---------------------------------------------------------------- aplicar (por plataforma)


def _ready(manager, tmp_path, name):
    """Deja al gestor con un paquete 'verificado' sin descargar nada."""
    d = tmp_path / "pkg"
    d.mkdir(exist_ok=True)
    f = d / name
    f.write_bytes(b"paquete")
    manager._temp_dir = str(d)
    manager._target_file = str(f)
    manager._status = "ready_to_restart"
    exits = []
    manager._request_app_exit = lambda *a, **k: exits.append(True)
    return str(f), exits


def test_apply_without_ready_package_fails_visibly(manager):
    res = manager.apply_update_and_restart()
    assert res["success"] is False and "No hay ningún instalador listo" in res["error"]


def test_apply_windows_installer_uses_shell_execute_silent_and_closes_app(manager, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    path, exits = _ready(manager, tmp_path, "Mcp2p-Setup-v2.0.1.exe")
    calls = []
    manager._shell_execute = lambda p, params, workdir=None: calls.append((p, params, workdir))
    popen = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: popen.append((a, k)))

    res = manager.apply_update_and_restart()

    assert res == {"success": True, "mode": "windows_installer"}
    assert len(calls) == 1 and not popen
    assert calls[0][0] == path
    for flag in ("/SILENT", "/CLOSEAPPLICATIONS", "/NORESTART", "/RELAUNCH=1", "/LOG="):
        assert flag in calls[0][1]
    assert exits == [True]
    assert manager.get_progress()["status"] == "installing"
    assert "ShellExecuteW" in manager.get_log()["text"]


def test_apply_windows_installer_falls_back_to_detached_popen(manager, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    path, exits = _ready(manager, tmp_path, "Mcp2p-Setup-v2.0.1.exe")

    def boom(*a, **k):
        raise OSError("ShellExecute no disponible")

    manager._shell_execute = boom
    popen = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: popen.append((a, k)))

    res = manager.apply_update_and_restart()

    assert res["success"] is True
    (args, kwargs), = popen
    assert args[0][0] == path and "/SILENT" in args[0] and "/RELAUNCH=1" in args[0]
    flags = kwargs["creationflags"]
    assert flags & au._DETACHED_PROCESS and flags & au._CREATE_NEW_PROCESS_GROUP
    assert exits == [True]


def test_apply_windows_installer_requiring_elevation_is_a_visible_error(manager, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    _, exits = _ready(manager, tmp_path, "Mcp2p-Setup-v2.0.1.exe")

    def denied(*a, **k):
        raise OSError("denegado")

    manager._shell_execute = denied

    def popen_740(*a, **k):
        e = OSError("La operación solicitada requiere elevación")
        e.winerror = 740
        raise e

    monkeypatch.setattr(subprocess, "Popen", popen_740)

    res = manager.apply_update_and_restart()

    assert res["success"] is False
    assert "administrador" in res["error"]
    assert exits == []  # la app NO se cierra si el instalador no arrancó
    assert manager.get_progress()["status"] == "ready_to_restart"  # se puede reintentar


def test_apply_windows_portable_extracts_and_launches_powershell(manager, tmp_path, monkeypatch):
    app = tmp_path / "portable"
    app.mkdir()
    (app / "Mcp2p.exe").write_bytes(b"old")
    _frozen(monkeypatch, "win32", str(app / "Mcp2p.exe"))
    monkeypatch.setattr(au, "is_system_installed", lambda: False)

    pkg = tmp_path / "pkg"
    pkg.mkdir()
    zpath = pkg / "Mcp2p-Update-v2.0.1.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("Mcp2p/Mcp2p.exe", b"new")
        zf.writestr("Mcp2p/_internal/lib.dll", b"dll")
    manager._temp_dir = str(pkg)
    manager._target_file = str(zpath)
    manager._status = "ready_to_restart"
    exits = []
    manager._request_app_exit = lambda *a, **k: exits.append(True)
    popen = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: popen.append((a, k)))

    res = manager.apply_update_and_restart()

    assert res == {"success": True, "mode": "windows_portable"}
    (args, kwargs), = popen
    cmd = args[0]
    assert cmd[0] == "powershell.exe" and "-File" in cmd and "Bypass" in cmd
    assert kwargs["creationflags"] & au._DETACHED_PROCESS
    script = open(cmd[-1], encoding="utf-8-sig").read()
    assert "robocopy" in script and "mcp2p_local.db" in script and "workspace" in script
    assert f"Wait-Process -Id {os.getpid()}" in script
    assert str(app) in script and "Start-Process" in script
    staged = pkg / "portable_staging" / "Mcp2p"
    assert (staged / "Mcp2p.exe").read_bytes() == b"new"
    assert exits == [True]


def test_apply_windows_portable_rejects_system_dir(manager, tmp_path, monkeypatch):
    app = tmp_path / "x"
    app.mkdir()
    (app / "Mcp2p.exe").write_bytes(b"old")
    _frozen(monkeypatch, "win32", str(app / "Mcp2p.exe"))
    monkeypatch.setattr(au, "is_system_installed", lambda: True)
    _, exits = _ready(manager, tmp_path, "Mcp2p-Update-v2.0.1.zip")
    res = manager.apply_update_and_restart()
    assert res["success"] is False and "portable" in res["error"]
    assert exits == []


def test_zip_slip_is_rejected(tmp_path):
    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("../../escape.txt", b"x")
    with pytest.raises(RuntimeError):
        AutoUpdateManager._safe_extract_zip(str(z), str(tmp_path / "out"))
    assert not (tmp_path / "escape.txt").exists()


def test_portable_script_escapes_single_quotes_in_paths(manager):
    script = manager._build_windows_portable_script("C:\\tmp\\it's\\src", "C:\\Users\\O'Brien\\Mcp2p", 123, "C:\\l.log")
    assert "'C:\\tmp\\it''s\\src'" in script and "'C:\\Users\\O''Brien\\Mcp2p'" in script


def test_apply_linux_deb_uses_pkexec(manager, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    path, exits = _ready(manager, tmp_path, "Mcp2p-Setup-v2.0.1.deb")
    popen = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: popen.append(a[0]))
    monkeypatch.setattr(au.threading, "Thread", lambda *a, **k: type("T", (), {"start": lambda self: None})())
    res = manager.apply_update_and_restart()
    assert res == {"success": True, "mode": "linux_deb"}
    assert popen == [["pkexec", "dpkg", "-i", path]]
    assert exits == [True]


# ---------------------------------------------------------------- puente


def test_bridge_exposes_update_functions_to_pywebview(tmp_path):
    from src.app.api_bridge import ApiBridge
    from tests.conftest import make_test_db

    bridge = ApiBridge(db_manager=make_test_db())
    names = {f.__name__ for f in bridge.public_js_functions()}
    assert {
        "start_auto_update", "get_update_progress", "apply_update_and_restart",
        "get_install_mode", "open_release_page", "get_update_log",
    } <= names
    bridge._updater._user_data_dir = str(tmp_path)
    assert bridge.get_install_mode()["kind"] in ("dev", "windows_installer", "windows_portable", "linux_deb", "linux_portable", "macos", "unknown")
    assert bridge.open_release_page("https://evil.example/")["success"] is False
