"""Proxy stdio del operador (Ola 4, F2.1 del dossier 37): `mcp2p --operator-proxy` o, desde fuentes,
`python -m src.mcp.operator_proxy`.

Lo lanza el *harness* del dueño (Claude Code, Claude Desktop u otro cliente MCP) como servidor MCP por stdio. Es un
TUBO de bytes entre su stdin/stdout y el canal local del Hub de la GUI de MCP2P: no interpreta MCP, no abre la base de
datos y no lleva ningun secreto. La identidad la pone el sistema operativo (el Hub mira que proceso conecto y quien lo
lanzo) y la aprueba el humano en un dialogo nativo la primera vez.

Antes de empezar envia una linea de saludo con los proyectos pedidos (`--project`, solo restringen el alcance) y una
etiqueta del cliente (dato no verificado, solo para mostrar).

Nunca escribe en stdout nada que no venga del Hub: los avisos van a stderr.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
from pathlib import Path
from typing import List, Optional

from src.mcp.operator_endpoint import (
    HELLO_KEY,
    PROTOCOL_VERSION,
    default_endpoint_file,
    default_socket_path,
    default_transport,
    read_endpoint,
)

_CONNECT_TIMEOUT_S = 3.0
_CHUNK = 65536


def _eprint(message: str) -> None:
    try:
        print(f"MCP2P operador: {message}", file=sys.stderr, flush=True)
    except Exception:
        pass


def _verify_tcp_listener(host: str, port: int, hub_pid: Optional[int]) -> bool:
    """Comprueba que quien escucha en `host:port` es el proceso del Hub anunciado (contra un puerto ocupado por otro
    programa tras cerrar MCP2P). Sin psutil o sin permiso para verlo, no se bloquea."""
    if not hub_pid:
        return True
    try:
        import psutil

        for conn in psutil.net_connections(kind="tcp"):
            laddr = conn.laddr
            if laddr and int(laddr.port) == int(port) and conn.status == psutil.CONN_LISTEN:
                return conn.pid in (None, int(hub_pid))
    except Exception:
        return True
    return False


def connect(args: argparse.Namespace) -> socket.socket:
    transport = args.transport or default_transport()
    if transport == "unix":
        path = Path(args.socket) if args.socket else default_socket_path()
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(_CONNECT_TIMEOUT_S)
        try:
            sock.connect(str(path))
        except OSError as exc:
            sock.close()
            raise ConnectionError(f"no se pudo conectar con MCP2P en {path} ({exc}). Abre la ventana de MCP2P.") from exc
    else:
        endpoint_file = Path(args.endpoint_file) if args.endpoint_file else default_endpoint_file()
        try:
            data = read_endpoint(endpoint_file)
        except (OSError, ValueError) as exc:
            raise ConnectionError(f"MCP2P no esta abierto (sin {endpoint_file}: {exc}).") from exc
        host, port = str(data["host"]), int(data["port"])
        if not _verify_tcp_listener(host, port, data.get("hub_pid")):
            raise ConnectionError("el puerto anunciado ya no pertenece a MCP2P; vuelve a abrir la ventana de MCP2P.")
        try:
            sock = socket.create_connection((host, port), timeout=_CONNECT_TIMEOUT_S)
        except OSError as exc:
            raise ConnectionError(f"no se pudo conectar con MCP2P en {host}:{port} ({exc}).") from exc
    sock.settimeout(None)
    return sock


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


def _pump_stdin(sock: socket.socket) -> None:
    # Descriptores 0/1 del SO, no `sys.stdin`/`sys.stdout`: en el binario de ventana de Windows (sin consola) Python
    # puede dejar `sys.stdout = None` aunque el harness le haya pasado tuberias.
    fd = 0
    try:
        while True:
            data = os.read(fd, _CHUNK)
            if not data:
                break
            sock.sendall(data)
    except OSError:
        pass
    finally:
        try:
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def run(args: argparse.Namespace) -> int:
    try:
        sock = connect(args)
    except ConnectionError as exc:
        _eprint(str(exc))
        return 2
    hello = {HELLO_KEY: PROTOCOL_VERSION, "projects": list(args.project or []), "client": args.client_name or ""}
    try:
        sock.sendall(json.dumps(hello).encode("utf-8") + b"\n")
    except OSError as exc:
        _eprint(f"conexion cerrada por MCP2P ({exc}).")
        return 2
    threading.Thread(target=_pump_stdin, args=(sock,), name="mcp2p-operator-stdin", daemon=True).start()
    try:
        while True:
            data = sock.recv(_CHUNK)
            if not data:
                break
            _write_all(1, data)
    except (OSError, ValueError):
        pass
    finally:
        try:
            sock.close()
        except OSError:
            pass
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcp2p --operator-proxy",
        description="Proxy stdio del operador de MCP2P: conecta tu cliente MCP con la ventana de MCP2P abierta.",
    )
    parser.add_argument("--project", action="append", default=[], metavar="PROJECT_UUID",
                        help="Limita la sesion a este proyecto (se puede repetir).")
    parser.add_argument("--client-name", default="", help="Nombre que se muestra en el dialogo (no verificado).")
    parser.add_argument("--transport", choices=("unix", "tcp"), default=None, help=argparse.SUPPRESS)
    parser.add_argument("--socket", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--endpoint-file", default=None, help=argparse.SUPPRESS)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
