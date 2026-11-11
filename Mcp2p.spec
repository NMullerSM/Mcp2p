# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_all

ROOT_DIR = os.path.dirname(os.path.abspath(SPEC)) if 'SPEC' in locals() else os.path.abspath('.')

datas = [
    (os.path.join(ROOT_DIR, 'frontend', 'dist'), 'frontend/dist'),
    (os.path.join(ROOT_DIR, 'src', 'ui'), 'src/ui'),
    (os.path.join(ROOT_DIR, 'mcp2p.ico'), '.'),
    (os.path.join(ROOT_DIR, 'mcp2p.png'), '.'),
]
binaries = []
hiddenimports = [
    'yaml', 'msgpack', 'cbor2', 'src', 'src.app', 'src.app.api_bridge', 'src.app.auto_updater',
    'src.app.paths', 'src.storage', 'src.storage.db', 'src.storage.okf_adapter',
    'src.storage.github_sync', 'src.storage.code_scanner', 'src.p2p', 'src.p2p.network',
    'src.p2p.crypto', 'src.a2a', 'src.a2a.bus', 'src.a2a.jury_engine',
    'src.mcp', 'src.mcp.server', 'src.mcp.tools'
]
for pkg in ('pywebview', 'webview', 'cryptography', 'mcp', 'yaml'):
    tmp_ret = collect_all(pkg)
    datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

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
