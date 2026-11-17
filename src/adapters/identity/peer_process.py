"""Vinculo token <-> arbol de procesos del panel (F1.2 del dossier 37, cierra EGM-105).

Problema: el token del panel vive en el entorno del CLI (`MCP2P_SESSION_TOKEN`) y cualquier proceso del mismo usuario
lo lee en `/proc/<pid>/environ`. Con solo el *bearer*, ese proceso actuaba como el panel.

Regla: una llamada se acepta solo si el proceso que abrio la conexion TCP loopback con el Hub es el proceso raiz del
PTY del panel o desciende de el, y la raiz sigue siendo el MISMO proceso (pid + `create_time`, contra la reutilizacion
de pids).

Como se obtiene el proceso par (portable, con `psutil`):

1. El Hub conoce el extremo del cliente (`scope["client"]`) y el suyo (`scope["server"]`).
2. `psutil.net_connections("tcp")` lista los sockets TCP con su pid. El dueño de la conexion es el que tiene
   `laddr == cliente` y `raddr == Hub` (el socket aceptado por el Hub tiene los extremos al reves y no coincide).
   - Linux: lee `/proc/net/tcp{,6}` y asocia inodos a pids recorriendo `/proc/*/fd` (solo se ven los procesos del
     mismo usuario; uno de otro usuario aparece sin pid y se rechaza).
   - Windows: `GetExtendedTcpTable` (no necesita privilegios).
   - macOS: sin root, `net_connections` da AccessDenied; se recorre solo el arbol del panel (`connections()` de cada
     proceso). Si la conexion no aparece en el arbol, se rechaza.
3. Ascendencia con `psutil.Process.parents()`, que ya descarta un padre con `create_time` posterior al hijo.

`SO_PEERCRED` no sirve para TCP (solo para sockets Unix): se reserva para un futuro *socket* local del operador.
"""

from __future__ import annotations

import ipaddress
import logging
from typing import Any, Iterable, List, Optional

from src.core.ports.identity import (
    PEER_MISMATCH,
    PEER_UNAVAILABLE,
    PEER_VERIFIED,
    Endpoint,
    PeerCheck,
    ProcessIdentity,
)

logger = logging.getLogger("mcp2p.adapters.identity.peer_process")

_CREATE_TIME_TOLERANCE = 0.01


def _psutil() -> Any:
    import psutil

    return psutil


def _normalize_ip(raw: Any) -> Optional[ipaddress._BaseAddress]:
    try:
        text = str(raw or "").strip().strip("[]").split("%", 1)[0]
        addr = ipaddress.ip_address(text)
    except ValueError:
        return None
    mapped = getattr(addr, "ipv4_mapped", None)
    return mapped if mapped is not None else addr


def is_loopback_endpoint(endpoint: Optional[Endpoint]) -> bool:
    if not endpoint:
        return False
    addr = _normalize_ip(endpoint[0])
    return bool(addr is not None and addr.is_loopback)


def _same_endpoint(addr: Any, endpoint: Endpoint) -> bool:
    """Compara un `addr` de psutil (`ip`, `port`) con un extremo ASGI `(host, port)`."""
    if not addr:
        return False
    try:
        ip, port = addr.ip, addr.port
    except AttributeError:
        try:
            ip, port = addr[0], addr[1]
        except (TypeError, IndexError):
            return False
    if int(port) != int(endpoint[1]):
        return False
    left, right = _normalize_ip(ip), _normalize_ip(endpoint[0])
    return left is not None and left == right


def _proc_connections(proc: Any) -> List[Any]:
    fn = getattr(proc, "net_connections", None) or getattr(proc, "connections")
    return list(fn(kind="tcp"))


class PsutilPeerProcessResolver:
    """`PeerProcessResolver` portable sobre `psutil` (Linux, Windows; macOS con recorrido del arbol)."""

    def __init__(self, psutil_module: Any = None) -> None:
        self._ps = psutil_module

    @property
    def ps(self) -> Any:
        if self._ps is None:
            self._ps = _psutil()
        return self._ps

    # -- procesos ----------------------------------------------------------------------------------------------

    def process_identity(self, pid: int) -> Optional[ProcessIdentity]:
        ps = self.ps
        try:
            proc = ps.Process(int(pid))
            return ProcessIdentity(proc.pid, float(proc.create_time()))
        except (ps.Error, ValueError, TypeError, OverflowError):
            return None

    def _live(self, ident: ProcessIdentity) -> Optional[Any]:
        """El proceso vivo con ese pid SOLO si su `create_time` coincide (si no, el pid se reutilizo)."""
        ps = self.ps
        try:
            proc = ps.Process(int(ident.pid))
            if abs(float(proc.create_time()) - float(ident.create_time)) >= _CREATE_TIME_TOLERANCE:
                return None
            if proc.status() == getattr(ps, "STATUS_ZOMBIE", "zombie"):
                return None
            return proc
        except (ps.Error, ValueError, TypeError, OverflowError):
            return None

    def _in_tree(self, proc: Any, root: ProcessIdentity) -> bool:
        ps = self.ps
        try:
            if proc.pid == root.pid:
                return abs(float(proc.create_time()) - root.create_time) < _CREATE_TIME_TOLERANCE
            for ancestor in proc.parents():
                if ancestor.pid == root.pid:
                    return abs(float(ancestor.create_time()) - root.create_time) < _CREATE_TIME_TOLERANCE
        except ps.Error:
            return False
        return False

    # -- conexiones --------------------------------------------------------------------------------------------

    def _owners_global(self, client: Endpoint, server: Endpoint) -> Optional[List[Optional[int]]]:
        ps = self.ps
        try:
            conns = ps.net_connections(kind="tcp")
        except ps.AccessDenied:
            return None
        return [c.pid for c in conns if _same_endpoint(c.laddr, client) and _same_endpoint(c.raddr, server)]

    def _owners_in(self, procs: Iterable[Any], client: Endpoint, server: Endpoint) -> List[int]:
        ps = self.ps
        found: List[int] = []
        for proc in procs:
            try:
                for conn in _proc_connections(proc):
                    if _same_endpoint(conn.laddr, client) and _same_endpoint(conn.raddr, server):
                        found.append(proc.pid)
                        break
            except ps.Error:
                continue
        return found

    def check(self, client: Endpoint, server: Endpoint, root: ProcessIdentity) -> PeerCheck:
        if not is_loopback_endpoint(client) or not server:
            return PeerCheck(PEER_MISMATCH, reason="non_loopback_client")
        root_proc = self._live(root)
        if root_proc is None:
            return PeerCheck(PEER_MISMATCH, reason="root_gone_or_pid_reused")
        ps = self.ps
        try:
            owners = self._owners_global(client, server)
            if owners is None:  # macOS sin root: solo se puede buscar dentro del arbol
                tree = [root_proc, *root_proc.children(recursive=True)]
                owners = list(self._owners_in(tree, client, server))
        except ps.Error as exc:
            logger.debug("No se pudo resolver el proceso par: %s", exc)
            return PeerCheck(PEER_UNAVAILABLE, reason="resolver_error")
        if not owners:
            return PeerCheck(PEER_MISMATCH, reason="connection_owner_not_found")
        if any(pid is None for pid in owners):
            return PeerCheck(PEER_MISMATCH, reason="foreign_owner")
        peer: Optional[ProcessIdentity] = None
        for pid in sorted(set(int(p) for p in owners)):
            ident = self.process_identity(pid)
            proc = self._live(ident) if ident is not None else None
            if proc is None or not self._in_tree(proc, root):
                return PeerCheck(PEER_MISMATCH, reason="outside_pane_tree")
            peer = peer or ident
        return PeerCheck(PEER_VERIFIED, peer=peer)

    def still_bound(self, peer: ProcessIdentity, client: Endpoint, server: Endpoint, root: ProcessIdentity) -> bool:
        proc = self._live(peer)
        if proc is None or self._live(root) is None:
            return False
        if not self._owners_in([proc], client, server):
            return False
        return self._in_tree(proc, root)

    # -- canal del operador (Ola 4, F2.1) ------------------------------------------------------------------------

    def connection_owner(self, client: Endpoint, server: Endpoint) -> Optional[ProcessIdentity]:
        """Proceso dueño de la conexion TCP loopback `client -> server` (Windows: canal del operador por TCP).
        None si no se encuentra, si hay varios dueños distintos o si es de otro usuario (sin pid visible)."""
        if not is_loopback_endpoint(client) or not server:
            return None
        ps = self.ps
        try:
            owners = self._owners_global(client, server)
        except ps.Error:
            return None
        if not owners or any(pid is None for pid in owners) or len(set(owners)) != 1:
            return None
        return self.process_identity(int(owners[0]))

    def is_alive(self, ident: ProcessIdentity) -> bool:
        return self._live(ident) is not None

    def descends_from(self, ident: ProcessIdentity, root: ProcessIdentity) -> bool:
        """¿`ident` (vivo) es `root` o desciende de el? (pid + `create_time` en ambos)."""
        proc = self._live(ident)
        return proc is not None and self._in_tree(proc, root)

    def parent_of(self, ident: ProcessIdentity) -> Optional[ProcessIdentity]:
        """Padre vivo de `ident`; None si el padre murio o su pid se reutilizo (padre nacido despues del hijo)."""
        proc = self._live(ident)
        if proc is None:
            return None
        ps = self.ps
        try:
            parent = proc.parent()
            if parent is None:
                return None
            ctime = float(parent.create_time())
        except ps.Error:
            return None
        if ctime > float(ident.create_time) + _CREATE_TIME_TOLERANCE:
            return None
        return ProcessIdentity(int(parent.pid), ctime)

    def describe(self, ident: ProcessIdentity) -> Optional[dict]:
        """Ficha de un proceso VIVO (para el dialogo de emparejamiento): ejecutable, nombre, usuario y cwd."""
        proc = self._live(ident)
        if proc is None:
            return None
        ps = self.ps

        def _safe(fn: Any, default: Any = "") -> Any:
            try:
                value = fn()
            except (ps.Error, OSError):
                return default
            return default if value is None else value

        return {
            "pid": int(ident.pid),
            "create_time": float(ident.create_time),
            "exe": str(_safe(proc.exe)),
            "name": str(_safe(proc.name)),
            "username": str(_safe(proc.username)),
            "cwd": str(_safe(proc.cwd)),
            "cmdline": [str(a) for a in _safe(proc.cmdline, [])][:16],
        }

    def ancestors(self, ident: ProcessIdentity, limit: int = 8) -> List[dict]:
        """`ident` y sus ascendientes (hasta `limit`), cada uno con pid, nombre y ejecutable."""
        out: List[dict] = []
        current: Optional[ProcessIdentity] = ident
        while current is not None and len(out) < max(1, int(limit)):
            info = self.describe(current)
            if info is None:
                break
            out.append({"pid": info["pid"], "name": info["name"], "exe": info["exe"]})
            current = self.parent_of(current)
        return out

    def current_username(self) -> str:
        import os

        ps = self.ps
        try:
            return str(ps.Process(os.getpid()).username())
        except ps.Error:
            return ""

    def tree_cmdlines(self, root: ProcessIdentity) -> Optional[List[List[str]]]:
        """`argv` de la raiz y de todos sus descendientes vivos; None si la raiz no existe o no se puede leer."""
        proc = self._live(root)
        if proc is None:
            return None
        ps = self.ps
        out: List[List[str]] = []
        try:
            procs = [proc, *proc.children(recursive=True)]
        except ps.Error:
            return None
        for item in procs:
            try:
                out.append([str(a) for a in item.cmdline()])
            except ps.NoSuchProcess:
                continue
            except ps.Error:
                return None
        return out
