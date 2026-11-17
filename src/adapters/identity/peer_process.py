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

