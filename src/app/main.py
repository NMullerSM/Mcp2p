"""
MCP2P Desktop Application Entry Point
Punto de entrada principal para la aplicación de escritorio construida con PyWebView,
con despacho temprano (< 30 ms, sin imports pesados) para `fast` CLI y `--pty-exec`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, List, Optional

# Asegurar que la raíz del proyecto esté en sys.path antes de cualquier despacho temprano
_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))


def _sanitize_pyinstaller_env() -> None:
    """Limpia variables heredadas del bootloader PyInstaller antes de `os.execvp` (EGM-59)."""
    for key in [k for k in os.environ if k.startswith("_PYI_")]:
        os.environ.pop(key, None)

    env_meipass = os.environ.pop("_MEIPASS", None)
    sys_meipass = getattr(sys, "_MEIPASS", None)

    if "LD_LIBRARY_PATH_ORIG" in os.environ:
        orig_ld = os.environ.pop("LD_LIBRARY_PATH_ORIG")
        if orig_ld:
            os.environ["LD_LIBRARY_PATH"] = orig_ld
        else:
            os.environ.pop("LD_LIBRARY_PATH", None)
        return

    meipass_dirs = [str(p) for p in (env_meipass, sys_meipass) if p]
    if meipass_dirs and "LD_LIBRARY_PATH" in os.environ:
        meipass_norm = {os.path.normpath(p) for p in meipass_dirs}
        raw_parts = os.environ["LD_LIBRARY_PATH"].split(os.pathsep)
        if any(entry and os.path.normpath(entry) in meipass_norm for entry in raw_parts):
            filtered = [
                entry for entry in raw_parts
                if entry and os.path.normpath(entry) not in meipass_norm
            ]
            if filtered:
                os.environ["LD_LIBRARY_PATH"] = os.pathsep.join(filtered)
            else:
                os.environ.pop("LD_LIBRARY_PATH", None)


def _dispatch_pty_exec(argv: List[str]) -> int:
    """Trampolín POSIX (EGM-24/EGM-59): asigna terminal controladora (TIOCSCTTY) y reemplaza el proceso."""
    if sys.platform == "win32":
        if sys.stderr is not None:
            sys.stderr.write("[mcp2p --pty-exec] No soportado en win32 (use ConPTY/winpty).\n")
        return 2

    cmd = argv[1:] if argv and argv[0] == "--" else argv
    if not cmd:
        if sys.stderr is not None:
            sys.stderr.write("[mcp2p --pty-exec] Comando objetivo requerido.\n")
        return 2

    import fcntl
    import termios

    # En entornos interactivos con TTY en stdin, TIOCSCTTY debe fallar cerrado (EGM-59).
    if os.isatty(0):
        try:
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)
        except OSError as exc:
            if sys.stderr is not None:
                sys.stderr.write(
                    f"[mcp2p --pty-exec] Error asignando terminal controladora (TIOCSCTTY): {exc}\n"
                )
            return 1

    _sanitize_pyinstaller_env()
    _restore_default_signals()
    os.execvp(cmd[0], cmd)
    return 0


def _restore_default_signals() -> None:
    """Disposiciones por defecto antes de `execvp`, como hace un emulador de terminal (EGM-81).

    Un `SIG_IGN` sobrevive a `exec`: sin esto el CLI del panel heredaba `SIGPIPE`/`SIGXFSZ` ignorados
    (los ignora el propio Python al arrancar) y, si la GUI se lanzo con `SIGINT`/`SIGQUIT` ignorados
    (p. ej. `./run.sh &` desde un script), Ctrl+C no cortaba nada dentro de la terminal.
    """
    import signal

    for name in ("SIGINT", "SIGQUIT", "SIGPIPE", "SIGXFSZ", "SIGTSTP", "SIGTTIN", "SIGTTOU", "SIGHUP", "SIGTERM"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, signal.SIG_DFL)
        except (OSError, ValueError):
            pass


# Despacho temprano antes de importar ApiBridge / PyWebView / aiortc
if __name__ == "__main__" and len(sys.argv) > 1:
    if sys.argv[1] == "--pty-exec":
        sys.exit(_dispatch_pty_exec(sys.argv[2:]))
    if sys.argv[1] == "fast":
        from src.app.fast_cli import run_fast_cli
        sys.exit(run_fast_cli(sys.argv[2:]))

import argparse
import logging
import platform

logger = logging.getLogger("mcp2p.app.main")

# Símbolos perezosos (evita importar aiortc/webview al importar main.py)
WebRTCTransportAdapter: Any = None


def _resolve_local_node_uuid() -> str:
    try:
        from src.app.identity import local_node_uuid
        return local_node_uuid()
    except ImportError:
        return os.environ.get("MCP2P_NODE_UUID", f"node_{platform.node().lower()[:12]}")


def _build_p2p_transport(signaling_url: str, node_crypto: Any) -> Any:
    """Construye el transporte P2P 1:1 (WebRTC/aiortc) o degrada a NullTransportAdapter
    ante CUALQUIER fallo de construcción -- incluido un ImportError si el wheel de aiortc
    no está instalado o está roto en esta plataforma. Delega en la fábrica única
    `src.adapters.network.direct_peer.build_p2p_transport` (EGM-48)."""
    from src.adapters.network.direct_peer import build_p2p_transport

    return build_p2p_transport(signaling_url, node_crypto, adapter_cls=WebRTCTransportAdapter)


def _build_peer_network(api: Any, db: Any, local_node: str) -> Any:
    """Compone el `PeerNetworkPort` de producción vía `ApplicationFactory.create_peer_network`."""
    from src.core.factory import ApplicationFactory

    return ApplicationFactory.create_peer_network(
        node_uuid=local_node,
        crypto=api.crypto,
        db=db,
        signaling_url=api.get_signaling_server_url(),
        lan_port=int(os.environ.get("MCP2P_LAN_PORT", 42424)),
        transport_builder=_build_p2p_transport,
    )


def parse_arguments(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MCP2P: Plataforma de Colaboración Agéntica Local-First")
    parser.add_argument("--db-path", type=str, default=None, help="Ruta a la base de datos SQLite local")
    parser.add_argument("--headless", action="store_true", help="Ejecutar en modo sin cabeza (CI/CD o tests)")
    parser.add_argument("--debug", action="store_true", help="Habilitar herramientas de desarrollador en PyWebView")
    parser.add_argument(
        "--mcp-server", action="store_true",
        help="Ejecutar este mismo binario como servidor MCP (sin abrir la GUI de escritorio)",
    )
    from src.mcp.transport_options import add_transport_arguments

    add_transport_arguments(parser)
    return parser.parse_args(argv)


def _run_as_mcp_server(args: argparse.Namespace) -> int:
    """Arranca el servidor MCP en el mismo proceso del binario empaquetado.

    El transporte stdio reserva stdout exclusivamente para los mensajes JSON-RPC,
    así que nada en esta ruta debe hacer print() ni tocar PyWebView/P2P.
    """
    if getattr(args, "hub_proxy_only", False):
        # Solo proxy al Hub: nunca abre la BD (EGM-98). Es lo que registra Antigravity con el binario empaquetado.
        if args.transport != "stdio":
            print("MCP2P: --hub-proxy-only solo admite el transporte stdio.", file=sys.stderr, flush=True)
            return 2
        from src.mcp.stdio_proxy import run_hub_proxy_only

        return run_hub_proxy_only()

    from src.mcp.server import create_mcp_server, run_mcp_server

    if args.transport == "stdio":
        from src.mcp.stdio_proxy import hub_proxy_misconfiguration, run_stdio_hub_proxy, should_use_hub_proxy

        proxy_cfg = should_use_hub_proxy()
        if proxy_cfg is not None:
            # El binario empaquetado tambien hace proxy real al Hub vivo cuando el panel le pasa su entorno.
            run_stdio_hub_proxy(proxy_cfg[0], proxy_cfg[1])
            return 0
        problem = hub_proxy_misconfiguration()
        if problem is not None:
            # Fallo cerrado (EGM-98): la intencion era el proxy al Hub; no se degrada a un servidor independiente.
            print(problem, file=sys.stderr, flush=True)
            return 2

    server = create_mcp_server(db_path=args.db_path, transport=args.transport)
    run_mcp_server(server, transport=args.transport, host=args.host, port=args.port)
    return 0


def get_ui_target() -> str:
    """Resuelve la URL o ruta local al archivo index.html."""
    from src.app.paths import get_bundle_dir

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


def _build_kernel_and_bridge(db_path: Optional[str], headless: bool = False) -> tuple[Any, Any, Any]:
    """Construye DatabaseManager, CoreKernel (vía ApplicationFactory si está disponible) y ApiBridge
    respetando dinámicamente --db-path en runtime (EGM-45)."""
    from src.app.api_bridge import ApiBridge
    from src.storage.db import DatabaseManager

    db = DatabaseManager.get_instance(db_path) if db_path else DatabaseManager.get_instance()
    resolved_db_path = str(db.db_path)

    core_kernel = None
    try:
        from src.core.factory import ApplicationFactory
        if headless and hasattr(ApplicationFactory, "create_headless_kernel"):
            core_kernel = ApplicationFactory.create_headless_kernel(db_path=resolved_db_path, db_manager=db)
        elif hasattr(ApplicationFactory, "create_desktop_kernel"):
            core_kernel = ApplicationFactory.create_desktop_kernel(db_path=resolved_db_path, db_manager=db)
        elif hasattr(ApplicationFactory, "create_kernel"):
            core_kernel = ApplicationFactory.create_kernel(db_path=resolved_db_path, db_manager=db)
    except ImportError:
        core_kernel = None

    # Conectar ApiBridge al CoreKernel si su constructor lo acepta; compatibilidad previa a Hito 3.2
    import inspect
    sig = inspect.signature(ApiBridge.__init__)
    if "core_kernel" in sig.parameters:
        api = ApiBridge(db_manager=db, core_kernel=core_kernel)
    else:
        api = ApiBridge(db_manager=db)
        if core_kernel is not None:
            api.core_kernel = core_kernel
    return db, core_kernel, api


def main(argv: Optional[List[str]] = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if raw_argv:
        if raw_argv[0] == "--pty-exec":
            return _dispatch_pty_exec(raw_argv[1:])
        if raw_argv[0] == "fast":
            from src.app.fast_cli import run_fast_cli
            return run_fast_cli(raw_argv[1:])

    args = parse_arguments(raw_argv)

    if args.mcp_server:
        return _run_as_mcp_server(args)

    if args.db_path:
        os.environ["MCP2P_DB_PATH"] = args.db_path

    from src.app.paths import get_bundle_dir

    db, core_kernel, api = _build_kernel_and_bridge(args.db_path, headless=args.headless)

    # EGM-48: una sola composición de red (PeerNetworkPort) con el canal 1:1 y el listener
    # LAN firmado, enchufada en el CoreKernel en vez de dos pilas sueltas.
    local_node = _resolve_local_node_uuid()
    p2p_network = _build_peer_network(api, db, local_node)
    if core_kernel is not None and hasattr(core_kernel, "register_connector"):
        from src.core.kernel import CoreKernel
        core_kernel.register_connector(CoreKernel.PORT_NETWORK, p2p_network, replace=True)
    p2p_network.start_listening(
        local_node,
        api._handle_peer_envelope,
        handlers={"direct": api._handle_transport_message},
    )
    api.set_transport(p2p_network.get_connector("direct").transport)
    lan_connector = p2p_network.get_connector("lan")

    if args.headless:
        print("[MCP2P] Modo Headless activo. Base de datos inicializada correctamente.")
        info = api.get_system_info()
        print(f"[MCP2P] Nodo Local: {info['active_node']} | SQLite WAL: {info['wal_mode']} | LAN Port: {lan_connector.lan_port}")
        p2p_network.close()
        if core_kernel is not None and hasattr(core_kernel, "close"):
            core_kernel.close()
        return 0

    try:
        import webview
    except ImportError as e:
        print(f"[MCP2P Error] PyWebView no está instalado en este entorno: {e}")
        p2p_network.close()
        if core_kernel is not None and hasattr(core_kernel, "close"):
            core_kernel.close()
        return 1

    target_url = get_ui_target()

    # Determinar color de fondo nativo coherente con el tema persistido (FLK-05)
    try:
        ui_theme = api.db.get_setting("ui_theme", "dark") or "dark"
    except Exception:
        ui_theme = "dark"
    bg_color = "#f8fafc" if ui_theme == "light" else "#0f1013"

    from src.app.api_bridge import InertJsApi

    window = webview.create_window(
        title="MCP²P — Agent Consensus & Governance",
        url=target_url,
        js_api=InertJsApi(),  # EGM-73: sin objeto js_api util; las funciones se registran por nombre con window.expose
        width=1280,
        height=840,
        min_size=(960, 600),
        background_color=bg_color,
        text_select=True
    )
    window.expose(*api.public_js_functions())
    if hasattr(api, "set_window"):
        api.set_window(window)
    api._updater.set_window(window)

    try:
        print(f"[MCP2P] Iniciando ventana de escritorio con PyWebView ({target_url})...")
        start_kwargs: dict[str, Any] = {"debug": args.debug}
        bundle_dir = Path(get_bundle_dir())
        icon_name = "mcp2p.ico" if sys.platform == "win32" else "mcp2p.png"
        icon_cand = bundle_dir / icon_name
        if not icon_cand.exists() and sys.platform != "win32":
            icon_cand = bundle_dir / "mcp2p.ico"
        if icon_cand.exists():
            start_kwargs["icon"] = str(icon_cand)
        webview.start(**start_kwargs)
    finally:
        if hasattr(api, "_close_terminal_matrix"):
            try:
                api._close_terminal_matrix()
            except Exception as exc:
                logger.warning("Error cerrando matriz de terminales al salir: %s", exc)
        p2p_network.close()
        if core_kernel is not None and hasattr(core_kernel, "close"):
            core_kernel.close()
        print("[MCP2P] Ventana cerrada. Red P2P y sesión finalizadas limpiamente.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
