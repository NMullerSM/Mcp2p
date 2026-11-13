"""
MCP2P Desktop Application Entry Point
Punto de entrada principal para la aplicación de escritorio construida con PyWebView.
"""

from __future__ import annotations

import argparse
import logging
import os
import platform
import sys
from pathlib import Path
from typing import Any

# Asegurar que la raíz del proyecto esté en sys.path
_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.app.api_bridge import ApiBridge
from src.app.paths import get_bundle_dir
from src.p2p.network import P2PNotificationService
from src.p2p.transport import NullTransportAdapter, P2PTransport
from src.p2p.webrtc_adapter import WebRTCTransportAdapter

logger = logging.getLogger("mcp2p.app.main")


def _build_p2p_transport(signaling_url: str, node_crypto: Any) -> P2PTransport:
    """Construye el transporte P2P real (WebRTC/aiortc) o degrada a NullTransportAdapter
    ante CUALQUIER fallo de construcción -- incluido un ImportError si el wheel de aiortc
    no está instalado o está roto en esta plataforma. Un fallo aquí nunca debe tumbar el
    arranque de la aplicación: sin transporte en vivo, el flujo async (manual/polling) de
    las Tareas 6/7 sigue funcionando igual."""
    try:
        return WebRTCTransportAdapter(signaling_url, node_crypto)
    except Exception as exc:
        logger.warning(f"No se pudo construir WebRTCTransportAdapter, degradando a NullTransportAdapter: {exc}")
        return NullTransportAdapter()


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MCP2P: Plataforma de Colaboración Agéntica Local-First")
    parser.add_argument("--db-path", type=str, default=None, help="Ruta a la base de datos SQLite local")
    parser.add_argument("--headless", action="store_true", help="Ejecutar en modo sin cabeza (CI/CD o tests)")
    parser.add_argument("--debug", action="store_true", help="Habilitar herramientas de desarrollador en PyWebView")
    parser.add_argument(
        "--mcp-server", action="store_true",
        help="Ejecutar este mismo binario como servidor MCP (sin abrir la GUI de escritorio)",
    )
    parser.add_argument(
        "--transport", type=str, default="stdio", choices=["stdio", "sse"],
        help="Transporte MCP a usar junto con --mcp-server",
    )
    return parser.parse_args()


def _run_as_mcp_server(args: argparse.Namespace) -> int:
    """Arranca el servidor MCP en el mismo proceso del binario empaquetado.

    El transporte stdio reserva stdout exclusivamente para los mensajes JSON-RPC,
    así que nada en esta ruta debe hacer print() ni tocar PyWebView/P2P.
    """
    from src.mcp.server import create_mcp_server

    server = create_mcp_server(db_path=args.db_path, transport=args.transport)
    server.run(transport=args.transport)
    return 0


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

    if args.mcp_server:
        return _run_as_mcp_server(args)

    if args.db_path:
        os.environ["MCP2P_DB_PATH"] = args.db_path

    # Orden de construcción: ApiBridge primero, sin transporte (toma el NullTransportAdapter
    # por defecto), porque construir el transporte WebRTC real necesita la URL de
    # señalización configurada -- y leerla requiere la propia ApiBridge
    # (api.get_signaling_server_url(), que a su vez necesita self.db ya inicializada).
    # Pedirle el transporte real a ApiBridge.__init__ antes de que exista la propia
    # ApiBridge sería una dependencia circular, así que el transporte real se construye
    # después y se sustituye vía el setter set_transport (que re-registra on_message
    # contra el transporte nuevo).
    api = ApiBridge()
    real_transport = _build_p2p_transport(
        signaling_url=api.get_signaling_server_url(),
        node_crypto=api.crypto,
    )
    api.set_transport(real_transport)

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
        bundle_dir = Path(get_bundle_dir())
        icon_name = "mcp2p.ico" if sys.platform == "win32" else "mcp2p.png"
        icon_cand = bundle_dir / icon_name
        if not icon_cand.exists() and sys.platform != "win32":
            icon_cand = bundle_dir / "mcp2p.ico"
        if icon_cand.exists():
            start_kwargs["icon"] = str(icon_cand)
        webview.start(**start_kwargs)
    finally:
        p2p_network.stop_lan_server()
        print("[MCP2P] Ventana cerrada. Red P2P y sesión finalizadas limpiamente.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
