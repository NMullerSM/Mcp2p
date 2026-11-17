"""Canal local del operador en el Hub (Ola 4, F2.1 del dossier 37).

Escucha en un canal solo accesible por el usuario (ver `operator_endpoint.py`) y, por cada conexion:

1. Obtiene el proceso par por el SISTEMA OPERATIVO, antes de leer nada: `SO_PEERCRED` (Linux) / `LOCAL_PEERPID` (macOS,
   sin probar) en el socket Unix, o la tabla de conexiones TCP (Windows, via psutil). Una conexion de OTRO usuario se
   cierra sin hablar. Un pid creado despues de aceptar la conexion no puede ser quien conecto (pid reutilizado): se
   cierra.
2. Lee la primera linea (saludo opcional del proxy con los proyectos pedidos). Una sonda que conecta y cierra no abre
   ningun dialogo.
3. Identifica al harness (padre del proxy) con `OperatorChannelService.identify` y, si no esta emparejado, lanza el
   dialogo nativo en segundo plano.
4. Sirve MCP (JSON-RPC por lineas, como stdio) con un servidor que SOLO tiene las herramientas `operator_*`.

El proxy (`operator_proxy.py`) es un tubo de bytes: no interpreta MCP ni lleva secretos.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import stat
import struct
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

import anyio
import anyio.abc
from mcp import types
from anyio.streams.buffered import BufferedByteReceiveStream

from src.core.ports.identity import ProcessIdentity
from src.mcp.operator_endpoint import (
    HELLO_KEY,
    MAX_LINE_BYTES,
    default_endpoint_file,
    default_socket_path,
    default_transport,
    ensure_private_dir,
    write_endpoint,
)

logger = logging.getLogger("mcp2p.mcp.operator_channel")

_FIRST_LINE_TIMEOUT_S = 15.0
_CREATE_TIME_SLACK_S = 0.05


def unix_peer_credentials(sock: socket.socket) -> Tuple[int, int]:
    """(pid, uid) del proceso que conecto al socket Unix, segun el nucleo."""
    if hasattr(socket, "SO_PEERCRED"):  # Linux
        raw = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        pid, uid, _gid = struct.unpack("3i", raw)
        return int(pid), int(uid)
    if sys.platform == "darwin":  # pragma: no cover - sin probar en macOS real
        sol_local, local_peercred, local_peerpid = 0, 0x001, 0x002
        pid = struct.unpack("i", sock.getsockopt(sol_local, local_peerpid, 4))[0]
        xucred = sock.getsockopt(sol_local, local_peercred, 76)
        _version, uid = struct.unpack_from("II", xucred)
        return int(pid), int(uid)
    raise OSError("este sistema no permite identificar el proceso par de un socket Unix")


class OperatorChannel:
    """Servidor del canal del operador en un hilo propio (portal de anyio). `start()`/`stop()` idempotentes."""

    def __init__(
        self,
        service: Any,
        *,
        transport: Optional[str] = None,
        socket_path: Optional[Path] = None,
        endpoint_file: Optional[Path] = None,
        server_factory: Optional[Callable[[Any, Any], Any]] = None,
    ) -> None:
        self.service = service
        self.transport = transport or default_transport()
        self.socket_path = Path(socket_path) if socket_path is not None else None
        self.endpoint_file = Path(endpoint_file) if endpoint_file is not None else None
        if server_factory is None:
            from src.mcp.operator_tools import build_operator_server

            server_factory = build_operator_server
        self._server_factory = server_factory
        self._lock = threading.RLock()
        self._portal_cm: Any = None
        self._portal: Any = None
        self._listener: Any = None
        self._scope: Optional[anyio.CancelScope] = None
        self._future: Any = None
        self._info: Dict[str, Any] = {"listening": False, "transport": self.transport}

    # -- ciclo de vida -------------------------------------------------------------------------------------------

    @property
    def info(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._info)

    def start(self) -> Dict[str, Any]:
        with self._lock:
            if self._portal is not None:
                return dict(self._info)
            cm = anyio.from_thread.start_blocking_portal()
            portal = cm.__enter__()
            try:
                info = portal.call(self._open_listener)
                self._future = portal.start_task_soon(self._serve)
            except BaseException:
                cm.__exit__(*sys.exc_info())
                raise
            self._portal_cm, self._portal = cm, portal
            self._info = {"listening": True, **info}
            self.service.channel_info = dict(self._info)
            return dict(self._info)

    def stop(self) -> None:
        with self._lock:
            portal, cm = self._portal, self._portal_cm
            self._portal = self._portal_cm = None
            if portal is None:
                return
            try:
                if self._scope is not None:
                    portal.call(self._scope.cancel)
                if self._future is not None:
                    try:
                        self._future.result(timeout=5)
                    except BaseException:
                        pass
                if self._listener is not None:
                    portal.call(self._listener.aclose)
            finally:
                cm.__exit__(None, None, None)
                self._cleanup_files()
                self._listener = None
                self._info = {"listening": False, "transport": self.transport}
                self.service.channel_info = dict(self._info)

    def _cleanup_files(self) -> None:
        target = self._info.get("path") if self.transport == "unix" else self._info.get("endpoint_file")
        if not target:
            return
        try:
            os.unlink(target)
        except OSError:
            pass

    async def _open_listener(self) -> Dict[str, Any]:
        if self.transport == "unix":
            path = self.socket_path or default_socket_path()
            ensure_private_dir(path.parent)
            self._remove_stale_socket(path)
            self._listener = await anyio.create_unix_listener(path)
            os.chmod(path, 0o600)
            return {"transport": "unix", "path": str(path)}
        listener = await anyio.create_tcp_listener(local_host="127.0.0.1", local_port=0)
        self._listener = listener
        port = int(listener.extra(anyio.abc.SocketAttribute.local_port))
        endpoint = self.endpoint_file or default_endpoint_file()
        ident = self.service.processes.process_identity(os.getpid())
        write_endpoint(endpoint, host="127.0.0.1", port=port, hub_pid=os.getpid(),
                       hub_create_time=getattr(ident, "create_time", None))
        return {"transport": "tcp", "host": "127.0.0.1", "port": port, "endpoint_file": str(endpoint)}

    @staticmethod
    def _remove_stale_socket(path: Path) -> None:
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            return
        if not stat.S_ISSOCK(info.st_mode):
            raise RuntimeError(f"{path} existe y no es un socket; no se toca")
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(0.5)
        try:
            probe.connect(str(path))
        except (ConnectionRefusedError, FileNotFoundError, socket.timeout, OSError):
            os.unlink(path)  # socket huerfano de un Hub anterior
            return
        finally:
            probe.close()
        raise RuntimeError("Ya hay otro MCP2P escuchando en el canal del operador")

    async def _serve(self) -> None:
        with anyio.CancelScope() as scope:
            self._scope = scope
            await self._listener.serve(self._handle)

    # -- conexiones ------------------------------------------------------------------------------------------------

    def _peer_of(self, stream: Any, accepted_at: float) -> Tuple[Optional[ProcessIdentity], bool]:
        """(proceso par, se_puede_hablar). False -> se cierra sin responder (otro usuario o no identificable)."""
        processes = self.service.processes
        try:
            if self.transport == "unix":
                raw = stream.extra(anyio.abc.SocketAttribute.raw_socket)
                pid, uid = unix_peer_credentials(raw)
                if hasattr(os, "getuid") and uid != os.getuid():
                    logger.warning("Conexion al canal del operador de otro usuario (uid %s): se cierra.", uid)
                    return None, False
                ident = processes.process_identity(pid)
            else:
                remote = stream.extra(anyio.abc.SocketAttribute.remote_address)
                local = stream.extra(anyio.abc.SocketAttribute.local_address)
                ident = processes.connection_owner((str(remote[0]), int(remote[1])), (str(local[0]), int(local[1])))
                if ident is None:
                    return None, False
                info = processes.describe(ident)
                if info is None or not info.get("username") or info.get("username") != processes.current_username():
                    logger.warning("Conexion al canal del operador de otro usuario: se cierra.")
                    return None, False
        except Exception:
            logger.warning("No se pudo identificar el par del canal del operador", exc_info=True)
            return None, False
        if ident is not None and float(ident.create_time) > accepted_at + _CREATE_TIME_SLACK_S:
            return None, False  # nacio despues de conectar: el pid se reutilizo
        return ident, True

    async def _handle(self, stream: Any) -> None:
        accepted_at = time.time()
        async with stream:
            peer, talk = await anyio.to_thread.run_sync(self._peer_of, stream, accepted_at)
            if not talk:
                return
            receiver = BufferedByteReceiveStream(stream)
            try:
                with anyio.fail_after(_FIRST_LINE_TIMEOUT_S):
                    first = await receiver.receive_until(b"\n", MAX_LINE_BYTES)
            except Exception:
                return  # sonda o cliente mudo: no se identifica ni se abre ningun dialogo
            hello: Dict[str, Any] = {}
            pending: Optional[bytes] = first
            try:
                parsed = json.loads(first.decode("utf-8"))
                if isinstance(parsed, dict) and parsed.get(HELLO_KEY):
                    hello, pending = parsed, None
            except (UnicodeDecodeError, ValueError):
                pass
            op = await anyio.to_thread.run_sync(
                lambda: self.service.identify(peer, transport=self.transport, hello=hello)
            )
            with anyio.CancelScope() as conn_scope:
                portal = self._portal

                def _close() -> None:
                    try:
                        if portal is not None:
                            portal.start_task_soon(self._cancel_scope, conn_scope)
                    except RuntimeError:
                        conn_scope.cancel()

                await anyio.to_thread.run_sync(self.service.connection_opened, op, _close)
                try:
                    await self._run_mcp(op, receiver, stream, pending)
                finally:
                    self.service.connection_closed(op)

    @staticmethod
    async def _cancel_scope(scope: anyio.CancelScope) -> None:
        scope.cancel()

    async def _run_mcp(self, op: Any, receiver: Any, stream: Any, pending: Optional[bytes]) -> None:
        from mcp.shared._context_streams import create_context_streams
        from mcp.shared.message import SessionMessage

        server = self._server_factory(self.service, op)
        lowlevel = server._lowlevel_server
        read_writer, read_stream = create_context_streams[SessionMessage | Exception](0)
        write_stream, write_reader = create_context_streams[SessionMessage](0)

        async def _feed(line: bytes) -> None:
            if not line.strip():
                return
            try:
                message = types.jsonrpc_message_adapter.validate_json(line, by_name=False)
            except Exception as exc:
                await read_writer.send(exc)
                return
            await read_writer.send(SessionMessage(message))

        async def _reader() -> None:
            async with read_writer:
                if pending is not None:
                    await _feed(pending)
                while True:
                    try:
                        line = await receiver.receive_until(b"\n", MAX_LINE_BYTES)
                    except Exception:
                        return  # fin de la conexion, linea demasiado larga o conexion rota
                    await _feed(line)

        async def _writer() -> None:
            async with write_reader:
                async for session_message in write_reader:
                    data = session_message.message.model_dump_json(by_alias=True, exclude_unset=True)
                    try:
                        await stream.send(data.encode("utf-8") + b"\n")
                    except Exception:
                        return

        async with anyio.create_task_group() as tg:
            tg.start_soon(_reader)
            tg.start_soon(_writer)
            try:
                await lowlevel.run(read_stream, write_stream, lowlevel.create_initialization_options())
            finally:
                tg.cancel_scope.cancel()
