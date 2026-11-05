"""
Generador Automatizado de Paquetes Debian (.deb) para MCP2P.
Empaqueta el binario compilado por PyInstaller en un paquete .deb instalable
con un clic en Pop!_OS, Ubuntu, Debian y derivadas.
"""
import os
import sys
import shutil
import subprocess


def build_deb(version: str = "1.0.0"):
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    linux_dist = os.path.join(base_dir, "ejecutables", "linux")
    source_app_dir = os.path.join(linux_dist, "Mcp2p")
    deb_dir = os.path.join(base_dir, "ejecutables", "linux", "deb")
    staging_dir = os.path.join(base_dir, "build", "deb_staging")
    output_deb = os.path.join(linux_dist, f"mcp2p_{version}_amd64.deb")

    print("=" * 65)
    print(f" Empaquetador MCP2P .deb (Linux amd64) — v{version}")
    print("=" * 65)

    if not os.path.exists(source_app_dir) or not os.path.isfile(os.path.join(source_app_dir, "Mcp2p")):
        print(f"[Error] No se encontró la carpeta compilada en: {source_app_dir}")
        print("Compila primero la aplicación ejecutando: python build_desktop.py")
        sys.exit(1)

    if os.path.exists(staging_dir):
        shutil.rmtree(staging_dir)
    os.makedirs(staging_dir, exist_ok=True)

    opt_mcp2p = os.path.join(staging_dir, "opt", "mcp2p")
    usr_bin = os.path.join(staging_dir, "usr", "bin")
    usr_apps = os.path.join(staging_dir, "usr", "share", "applications")
    usr_icons_256 = os.path.join(staging_dir, "usr", "share", "icons", "hicolor", "256x256", "apps")
    debian_dir = os.path.join(staging_dir, "DEBIAN")

    for d in [opt_mcp2p, usr_bin, usr_apps, usr_icons_256, debian_dir]:
        os.makedirs(d, exist_ok=True)

    print("[MCP2P .deb] Copiando binarios a /opt/mcp2p...")
    for item in os.listdir(source_app_dir):
        s = os.path.join(source_app_dir, item)
        d = os.path.join(opt_mcp2p, item)
        if os.path.isdir(s):
            shutil.copytree(s, d)
        else:
            shutil.copy2(s, d)

    launcher_src = os.path.join(deb_dir, "mcp2p-launcher.sh")
    launcher_dst = os.path.join(opt_mcp2p, "mcp2p-launcher.sh")
    shutil.copy2(launcher_src, launcher_dst)
    os.chmod(launcher_dst, 0o755)

    print("[MCP2P .deb] Configurando acceso directo en /usr/bin/mcp2p...")
    symlink_target = "/opt/mcp2p/mcp2p-launcher.sh"
    symlink_path = os.path.join(usr_bin, "mcp2p")
    try:
        os.symlink(symlink_target, symlink_path)
    except OSError:
        with open(symlink_path, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\nexec /opt/mcp2p/mcp2p-launcher.sh \"$@\"\n")
        os.chmod(symlink_path, 0o755)

    desktop_src = os.path.join(deb_dir, "mcp2p.desktop")
    desktop_dst = os.path.join(usr_apps, "mcp2p.desktop")
    shutil.copy2(desktop_src, desktop_dst)
    os.chmod(desktop_dst, 0o644)

    icon_png = os.path.join(base_dir, "mcp2p.png")
    if os.path.exists(icon_png):
        shutil.copy2(icon_png, os.path.join(usr_icons_256, "mcp2p.png"))

    control_src = os.path.join(deb_dir, "DEBIAN", "control")
    control_dst = os.path.join(debian_dir, "control")
    with open(control_src, "r", encoding="utf-8") as f:
        content = f.read()
    import re
    if "Version:" in content:
        content = re.sub(r'Version:\s*[^\n]+', f'Version: {version}', content)
    else:
        content = f"Version: {version}\n" + content
    content = content.strip() + "\n"
    with open(control_dst, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    os.chmod(control_dst, 0o644)

    postinst_src = os.path.join(deb_dir, "DEBIAN", "postinst")
    postinst_dst = os.path.join(debian_dir, "postinst")
    shutil.copy2(postinst_src, postinst_dst)
    os.chmod(postinst_dst, 0o755)

    mcp2p_bin = os.path.join(opt_mcp2p, "Mcp2p")
    if os.path.exists(mcp2p_bin):
        os.chmod(mcp2p_bin, 0o755)

    os.chmod(staging_dir, 0o755)
    for root, dirs, _ in os.walk(staging_dir):
        os.chmod(root, 0o755)
        for d in dirs:
            os.chmod(os.path.join(root, d), 0o755)

    print("[MCP2P .deb] Ejecutando dpkg-deb...")
    if not shutil.which("dpkg-deb"):
        print("[Error] dpkg-deb no está instalado. Instálalo con: sudo apt install dpkg")
        sys.exit(1)

    cmd = ["dpkg-deb", "--build", "--root-owner-group", staging_dir, output_deb]
    subprocess.check_call(cmd)

    print(f"\n[Éxito] Paquete .deb generado en: {output_deb}")
    print(f"Para instalarlo: sudo apt install ./{os.path.basename(output_deb)}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        ver = sys.argv[1]
    else:
        try:
            import re
            v_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "src", "app", "version.py")
            m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', open(v_file, encoding="utf-8").read())
            ver = m.group(1) if m else "1.0.0"
        except Exception:
            ver = "1.0.0"
    build_deb(ver)
