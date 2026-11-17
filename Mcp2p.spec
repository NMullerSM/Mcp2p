# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from PyInstaller.utils.hooks import collect_all

ROOT_DIR = os.path.dirname(os.path.abspath(SPEC)) if 'SPEC' in locals() else os.path.abspath('.')

datas = [
    (os.path.join(ROOT_DIR, 'frontend', 'dist'), 'frontend/dist'),
    (os.path.join(ROOT_DIR, 'src', 'ui'), 'src/ui'),
    # Catalogo versionado de CLIs de agente (lo carga src.adapters.terminal.cli_catalog en runtime).
    (os.path.join(ROOT_DIR, 'src', 'adapters', 'terminal', 'cli_catalog.json'), 'src/adapters/terminal'),
    (os.path.join(ROOT_DIR, 'mcp2p.ico'), '.'),
    (os.path.join(ROOT_DIR, 'mcp2p.png'), '.'),
]
binaries = []
hiddenimports = [
    'yaml', 'msgpack', 'cbor2', 'src', 'src.app', 'src.app.api_bridge', 'src.app.auto_updater',
    'src.app.paths', 'src.runtime.paths', 'src.runtime.identity', 'src.adapters.provisioning.defaults',
    'src.mcp.transport_options', 'src.storage', 'src.storage.db', 'src.storage.okf_adapter',
    'src.storage.github_sync', 'src.storage.code_scanner', 'src.p2p', 'src.p2p.network',
    'src.p2p.crypto', 'src.a2a', 'src.a2a.bus', 'src.a2a.jury_engine',
    'src.mcp', 'src.mcp.server', 'src.mcp.tools',
    'src.adapters.terminal', 'src.adapters.terminal.cli_catalog', 'src.adapters.terminal.cli_install', 'src.adapters.terminal.pty_host',
    'src.adapters.terminal.pty_common', 'src.adapters.terminal.winpty_host', 'src.adapters.terminal.win_job',
    'src.adapters.terminal.win_launch',
    'src.runtime.terminal_platform', 'src.core.services.project_portability_service',
    # Ola 3: identidad de proceso, sobre firmado y bandeja persistente del relevo.
    'src.adapters.identity', 'src.adapters.identity.peer_process', 'src.adapters.identity.envelope_crypto',
    'src.adapters.identity.ed25519_identity', 'src.mcp.peer_binding', 'src.adapters.persistence.relay_sqlite',
    'src.storage.relay_schema', 'src.core.services.message_relay_service', 'src.app.ui_adapters.relay_ui',
    # `keyring` se importa de forma perezosa: se declara para que el hook de PyInstaller copie sus metadatos (los
    # backends se descubren por entry points) y se anaden los backends que trae keyring 25 (keyring/backends/).
    'keyring', 'keyring.backends', 'keyring.backends.chainer', 'keyring.backends.fail', 'keyring.backends.null',
    'keyring.backends.SecretService', 'keyring.backends.libsecret', 'keyring.backends.kwallet',
    'keyring.backends.Windows', 'keyring.backends.macOS', 'keyring.backends.macOS.api',
]
# 'av' is collected explicitly even though nothing in this project imports it
# directly: it is a MANDATORY transitive dependency of aiortc (av<20.0.0,>=14.0.0
# in aiortc's own install_requires), not an optional media extra, and it bundles
# its own ffmpeg shared libraries. Without collect_all('av') here, the frozen
# binary can import aiortc fine in dev but fail to import it once packaged —
# this project already hit the same class of bug with certifi/SSL bundling
# (see commits 03ee047 / d95fb7e), so av is collected the same way aiortc itself is.
for pkg in ('pywebview', 'webview', 'cryptography', 'mcp', 'yaml', 'certifi', 'aiortc', 'websockets', 'av'):
    tmp_ret = collect_all(pkg)
    datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# Windows: terminales integradas por ConPTY (pywinpty). `winpty` se importa de forma perezosa (importlib), asi que
# PyInstaller no lo detecta solo: se recoge entero. La wheel trae `conpty.dll` + `OpenConsole.exe` que deben quedar
# JUNTOS y junto a `_winpty*.pyd` (si falta uno, ConPTY cae en silencio al conhost del sistema), ademas de
# `winpty.dll`/`winpty-agent.exe` (respaldo winpty). Se anaden explicitamente por si `collect_all` omite los .exe.
if sys.platform == 'win32':
    tmp_ret = collect_all('winpty')
    datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
    hiddenimports += ['winpty', 'winpty.ptyprocess', 'winpty.enums']
    try:
        import importlib.util
        _winpty_dir = list(importlib.util.find_spec('winpty').submodule_search_locations)[0]
        _already = {os.path.basename(src).lower() for src, _dst in (*datas, *binaries)}
        for _name in ('conpty.dll', 'OpenConsole.exe', 'winpty.dll', 'winpty-agent.exe'):
            _path = os.path.join(_winpty_dir, _name)
            if os.path.exists(_path) and _name.lower() not in _already:
                binaries.append((_path, 'winpty'))
    except Exception as exc:  # el build debe fallar visiblemente si pywinpty no esta instalado
        raise SystemExit('No se pudo localizar pywinpty para empaquetar ConPTY: %s' % exc)

a = Analysis(
    [os.path.join(ROOT_DIR, 'src', 'app', 'main.py')],
    pathex=[ROOT_DIR],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Mcp2p',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # upx=False: UPX es conocido por corromper el recurso de icono embebido en el .exe
    # de Windows (el icono queda como un cuadrado genérico en taskbar/escritorio aunque
    # el .ico pasado en icon= esté bien formado). El COLLECT de abajo sigue usando UPX
    # para las DLLs empaquetadas, donde este problema no aplica.
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[os.path.join(ROOT_DIR, 'mcp2p.ico')],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Mcp2p',
)
