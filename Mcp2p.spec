# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('<repo>/frontend/dist', 'frontend/dist'), ('<repo>/src/ui', 'src/ui'), ('<repo>/mcp2p.png', '.'), ('<repo>/mcp2p.ico', '.')]
binaries = []
hiddenimports = ['yaml', 'msgpack', 'cbor2', 'src', 'src.app', 'src.app.api_bridge', 'src.app.auto_updater', 'src.app.paths', 'src.storage', 'src.storage.db', 'src.storage.okf_adapter', 'src.storage.github_sync', 'src.storage.code_scanner', 'src.p2p', 'src.p2p.network', 'src.p2p.crypto', 'src.a2a', 'src.a2a.bus', 'src.a2a.jury_engine']
tmp_ret = collect_all('pywebview')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('cryptography')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('mcp')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['<repo>/src/app/main.py'],
    pathex=['<repo>'],
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
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['<repo>/mcp2p.png'],
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
