"""
Compilador Automatizado PyInstaller para MCP2P Desktop.
Genera un ejecutable autocontenido (.exe en Windows / binario nativo en Linux)
utilizando el motor web nativo del sistema (WebView2 / WebKitGTK) sin sobrecarga de Chromium.
"""
import os
import sys
import subprocess
import argparse
import shutil


def ensure_frontend_built(base_dir):
    """Recompila SIEMPRE `frontend/dist` antes de empaquetar (EGM-84).

    Antes solo se compilaba si faltaba `dist/index.html`: una compilacion local con un `dist` viejo
    (p. ej. anterior a xterm) empaquetaba esa interfaz sin avisar. Sin npm en el PATH se usa el `dist`
    existente con un aviso; sin npm ni `dist`, la compilacion falla.
    """
    frontend_dir = os.path.join(base_dir, "frontend")
    index_html = os.path.join(frontend_dir, "dist", "index.html")
    npm_cmd = shutil.which("npm.cmd" if sys.platform == "win32" else "npm") or shutil.which("npm")
    if npm_cmd is None:
        if os.path.exists(index_html):
            print("[MCP2P] AVISO: npm no esta en el PATH; se empaqueta el frontend/dist existente (puede estar desactualizado).")
            return
        raise SystemExit("[MCP2P] npm no esta en el PATH y no hay frontend/dist: no se puede empaquetar la interfaz.")
    if not os.path.isdir(os.path.join(frontend_dir, "node_modules")):
        print("[MCP2P] Instalando dependencias del frontend (npm ci)...")
        subprocess.check_call([npm_cmd, "ci", "--no-audit"], cwd=frontend_dir)
    print("[MCP2P] Compilando frontend en React + Vite...")
    subprocess.check_call([npm_cmd, "run", "build"], cwd=frontend_dir)


def winpty_pyinstaller_args():
    """
    Argumentos de PyInstaller para empaquetar ConPTY (pywinpty) en Windows. `winpty` se importa de forma perezosa,
    asi que se recoge entero; `conpty.dll` + `OpenConsole.exe` deben quedar juntos y junto a `_winpty*.pyd` (si no,
    ConPTY cae en silencio al conhost del sistema). Falla visiblemente si pywinpty no esta instalado.
    """
    import importlib.util

    spec = importlib.util.find_spec("winpty")
    if spec is None or not spec.submodule_search_locations:
        raise SystemExit("pywinpty no esta instalado: no se puede empaquetar ConPTY (pip install 'pywinpty>=3.1.2').")
    package_dir = list(spec.submodule_search_locations)[0]
    args = ["--collect-all=winpty", "--hidden-import=winpty", "--hidden-import=winpty.ptyprocess"]
    for name in ("conpty.dll", "OpenConsole.exe", "winpty.dll", "winpty-agent.exe"):
        path = os.path.join(package_dir, name)
        if os.path.exists(path):
            args.append(f"--add-binary={path}{os.pathsep}winpty")
    return args


def build():
    parser = argparse.ArgumentParser(description="Compilador MCP2P Desktop — PyWebView + React 18")
    parser.add_argument("--onefile", action="store_true", help="Genera un único archivo ejecutable autocontenido")
    args = parser.parse_args()

    print("=" * 65)
    print(" Compilador MCP2P Desktop — PyWebView + React 18")
    print(f" Modo: {'Ejecutable Único (--onefile)' if args.onefile else 'Carpeta Portable (--onedir)'}")
    print("=" * 65)

    base_dir = os.path.dirname(os.path.abspath(__file__))
    entry_point = os.path.join(base_dir, "src", "app", "main.py")
    frontend_dist = os.path.join(base_dir, "frontend", "dist")
    ui_fallback_dir = os.path.join(base_dir, "src", "ui")

    ensure_frontend_built(base_dir)

    is_windows = sys.platform == "win32"
    platform_dir = "windows" if is_windows else "linux"
    dist_dir = os.path.join(base_dir, "ejecutables", platform_dir)
    build_dir = os.path.join(base_dir, "build", platform_dir)

    sep = os.pathsep
    add_data_args = [f"--add-data={frontend_dist}{sep}frontend/dist"]

    if os.path.exists(ui_fallback_dir):
        add_data_args.append(f"--add-data={ui_fallback_dir}{sep}src/ui")

    # Catalogo versionado de CLIs de agente: src.adapters.terminal.cli_catalog lo lee en runtime.
    cli_catalog_json = os.path.join(base_dir, "src", "adapters", "terminal", "cli_catalog.json")
    add_data_args.append(f"--add-data={cli_catalog_json}{sep}src/adapters/terminal")

    for asset_file in ["mcp2p.png", "mcp2p.ico"]:
        asset_path = os.path.join(base_dir, asset_file)
        if os.path.exists(asset_path):
            add_data_args.append(f"--add-data={asset_path}{sep}.")

    icon_name = "mcp2p.ico" if is_windows else "mcp2p.png"
    icon_path = os.path.join(base_dir, icon_name)
    mode_arg = "--onefile" if args.onefile else "--onedir"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconsole", mode_arg,
        "--name=Mcp2p",
        f"--icon={icon_path}",
        f"--distpath={dist_dir}",
        f"--workpath={build_dir}",
        f"--paths={base_dir}",
        "--clean", "--noconfirm",
    ] + add_data_args + [
        "--collect-all=pywebview",
        "--collect-all=webview",
        "--collect-all=cryptography",
        "--collect-all=mcp",
        "--collect-all=yaml",
        "--collect-all=certifi",
        "--collect-all=aiortc",
        "--collect-all=websockets",
        # av is a mandatory transitive dependency of aiortc (not an optional
        # media extra) and bundles its own ffmpeg shared libraries, so it needs
        # the same --collect-all treatment as aiortc itself or the frozen
        # binary risks failing to import aiortc at all (see Mcp2p.spec).
        "--collect-all=av",
        "--hidden-import=yaml",
        "--hidden-import=msgpack",
        "--hidden-import=cbor2",
        "--hidden-import=src",
        "--hidden-import=src.app",
        "--hidden-import=src.app.api_bridge",
        "--hidden-import=src.app.auto_updater",
        "--hidden-import=src.app.paths",
        "--hidden-import=src.runtime.paths",
        "--hidden-import=src.runtime.identity",
        "--hidden-import=src.adapters.provisioning.defaults",
        "--hidden-import=src.mcp.transport_options",
        "--hidden-import=src.storage",
        "--hidden-import=src.storage.db",
        "--hidden-import=src.storage.okf_adapter",
        "--hidden-import=src.storage.github_sync",
        "--hidden-import=src.storage.code_scanner",
        "--hidden-import=src.p2p",
        "--hidden-import=src.p2p.network",
        "--hidden-import=src.p2p.crypto",
        "--hidden-import=src.a2a",
        "--hidden-import=src.a2a.bus",
        "--hidden-import=src.a2a.jury_engine",
        "--hidden-import=src.adapters.terminal",
        "--hidden-import=src.adapters.terminal.cli_catalog",
        "--hidden-import=src.adapters.terminal.cli_install",
        "--hidden-import=src.adapters.terminal.pty_host",
        "--hidden-import=src.adapters.terminal.pty_common",
        "--hidden-import=src.adapters.terminal.winpty_host",
        "--hidden-import=src.adapters.terminal.win_job",
        "--hidden-import=src.adapters.terminal.win_launch",
        "--hidden-import=src.runtime.terminal_platform",
        "--hidden-import=src.core.services.project_portability_service",
        entry_point,
    ]

    if is_windows:
        cmd[cmd.index(entry_point):cmd.index(entry_point)] = winpty_pyinstaller_args()

    print("\nEjecutando PyInstaller:\n" + " ".join(cmd))

    try:
        subprocess.check_call(cmd)
        print(f"\n[Éxito] Binario generado en: {dist_dir}")

        if not args.onefile:
            archive_dir = os.path.join(dist_dir, "Mcp2p")
            if os.path.exists(archive_dir):
                if not is_windows:
                    launcher_target = os.path.join(archive_dir, "mcp2p.sh")
                    with open(launcher_target, "w", encoding="utf-8", newline="\n") as f:
                        f.write(
                            "#!/bin/bash\n"
                            "# MCP2P — Wrapper de Lanzamiento Portable para Linux\n"
                            "export WEBKIT_DISABLE_DMABUF_RENDERER=1\n"
                            "export WEBKIT_DISABLE_COMPOSITING_MODE=1\n"
                            "SCRIPT_DIR=\"$(cd \"$(dirname \"${BASH_SOURCE[0]}\")\" && pwd)\"\n"
                            "cd \"$SCRIPT_DIR\" || exit 1\n"
                            "exec \"$SCRIPT_DIR/Mcp2p\" \"$@\"\n"
                        )
                    os.chmod(launcher_target, 0o755)
                    mcp2p_bin = os.path.join(archive_dir, "Mcp2p")
                    if os.path.exists(mcp2p_bin):
                        os.chmod(mcp2p_bin, 0o755)

                zip_name = "Mcp2p-Windows-Portable" if is_windows else "Mcp2p-Linux-Portable"
                archive_format = "zip" if is_windows else "gztar"
                archive_output = os.path.join(dist_dir, zip_name)
                shutil.make_archive(archive_output, archive_format, root_dir=dist_dir, base_dir="Mcp2p")
                print(f"[MCP2P] Paquete portable listo en: {archive_output}")

    except subprocess.CalledProcessError as e:
        print(f"\n[Error] La compilación falló con código {e.returncode}")
        sys.exit(e.returncode)


if __name__ == "__main__":
    build()
