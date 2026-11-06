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

    if not os.path.exists(os.path.join(frontend_dist, "index.html")):
        print("[MCP2P] Compilando frontend en React + Vite...")
        npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
        subprocess.check_call([npm_cmd, "run", "build"], cwd=os.path.join(base_dir, "frontend"))

    is_windows = sys.platform == "win32"
    platform_dir = "windows" if is_windows else "linux"
    dist_dir = os.path.join(base_dir, "ejecutables", platform_dir)
    build_dir = os.path.join(base_dir, "build", platform_dir)

    sep = os.pathsep
    add_data_args = [f"--add-data={frontend_dist}{sep}frontend/dist"]

    if os.path.exists(ui_fallback_dir):
        add_data_args.append(f"--add-data={ui_fallback_dir}{sep}src/ui")

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
        "--hidden-import=yaml",
        "--hidden-import=msgpack",
        "--hidden-import=cbor2",
        "--hidden-import=src",
        "--hidden-import=src.app",
        "--hidden-import=src.app.api_bridge",
        "--hidden-import=src.app.auto_updater",
        "--hidden-import=src.app.paths",
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
        entry_point,
    ]

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
