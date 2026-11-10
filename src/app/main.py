"""
MCP2P Desktop Application Entry Point
Punto de entrada principal para la aplicación de escritorio construida con PyWebView.
"""

from __future__ import annotations

import argparse
import os
import platform
import sys
from pathlib import Path

# Asegurar que la raíz del proyecto esté en sys.path
_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.app.api_bridge import ApiBridge
from src.app.paths import get_bundle_dir
from src.p2p.network import P2PNotificationService


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MCP2P: Plataforma de Colaboración Agéntica Local-First")
    parser.add_argument("--db-path", type=str, default=None, help="Ruta a la base de datos SQLite local")
    parser.add_argument("--headless", action="store_true", help="Ejecutar en modo sin cabeza (CI/CD o tests)")
    parser.add_argument("--debug", action="store_true", help="Habilitar herramientas de desarrollador en PyWebView")
    return parser.parse_args()


def get_ui_target() -> str:
    """Resuelve la URL o ruta local al archivo index.html."""
    root_dir = Path(get_bundle_dir())

    # 1. Comprobar build compilado de React (Vite)
    react_dist = root_dir / "frontend" / "dist" / "index.html"
    if react_dist.exists():
        return react_dist.as_uri()

    # 2. Fallback a la UI clásica en src/ui
    vanilla_ui = root_dir / "src" / "ui" / "index.html"
    if vanilla_ui.exists():
        return vanilla_ui.as_uri()

    raise FileNotFoundError("No se encontró ningún archivo de interfaz válido (frontend/dist ni src/ui).")


def main() -> int:
    args = parse_arguments()

    if args.db_path:
        os.environ["MCP2P_DB_PATH"] = args.db_path

    api = ApiBridge()

    # Iniciar servicio de red P2P en segundo plano (daemon)
    local_node = os.environ.get("MCP2P_NODE_UUID", f"node_{platform.node().lower()[:12]}")
    p2p_network = P2PNotificationService(
        node_uuid=local_node,
        lan_port=int(os.environ.get("MCP2P_LAN_PORT", 42424)),
        on_event_callback=lambda evt: api.sync_three_tiers() if hasattr(api, "sync_three_tiers") else None,
        crypto_manager=api.crypto,
        is_trusted_pubkey=api.db.is_trusted_public_key,
    )
    p2p_network.start_lan_server()

    if args.headless:
        print("[MCP2P] Modo Headless activo. Base de datos inicializada correctamente.")
        info = api.get_system_info()
        print(f"[MCP2P] Nodo Local: {info['active_node']} | SQLite WAL: {info['wal_mode']} | LAN Port: {p2p_network.lan_port}")
        p2p_network.stop_lan_server()
        return 0

    try:
        import webview
    except ImportError as e:
        print(f"[MCP2P Error] PyWebView no está instalado en este entorno: {e}")
        p2p_network.stop_lan_server()
        return 1

    target_url = get_ui_target()

    # Determinar color de fondo nativo coherente con el tema persistido (FLK-05)
    try:
        ui_theme = api.db.get_setting("ui_theme", "dark") or "dark"
    except Exception:
        ui_theme = "dark"
    bg_color = "#f8fafc" if ui_theme == "light" else "#0f1013"

    window = webview.create_window(
        title="MCP²P — Agent Consensus & Governance",
        url=target_url,
        js_api=api,
        width=1280,
        height=840,
        min_size=(960, 600),
        background_color=bg_color,
        text_select=True
    )
    if hasattr(api, "set_window"):
        api.set_window(window)
    api._updater.set_window(window)

    try:
        print(f"[MCP2P] Iniciando ventana de escritorio con PyWebView ({target_url})...")
        start_kwargs = {"debug": args.debug}
        icon_path = Path(get_bundle_dir()) / "mcp2p.png"
        if icon_path.exists():
            start_kwargs["icon"] = str(icon_path)
        webview.start(**start_kwargs)
    finally:
        p2p_network.stop_lan_server()
        print("[MCP2P] Ventana cerrada. Red P2P y sesión finalizadas limpiamente.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
