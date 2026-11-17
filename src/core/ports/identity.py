"""Puertos de identidad del relevo (Ola 3, F1.2/F1.3 del dossier 37).

- `PeerProcessResolver`: dado el extremo TCP loopback de una peticion al Hub, averigua que proceso la hizo y si
  pertenece al arbol de procesos del panel cuyo token presenta (cierra EGM-105: un token robado de
  `/proc/<pid>/environ` no sirve desde otro arbol).
- `DeviceIdentityPort`: clave Ed25519 del dispositivo que firma y verifica los sobres del Hub.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Protocol, Tuple, runtime_checkable

Endpoint = Tuple[str, int]

PEER_VERIFIED = "verified"
PEER_MISMATCH = "mismatch"
PEER_UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ProcessIdentity:
    """Un proceso concreto: el pid solo no basta (se reutiliza); `create_time` lo fija."""

    pid: int
    create_time: float

    def same_as(self, other: "ProcessIdentity") -> bool:
        return self.pid == other.pid and abs(self.create_time - other.create_time) < 0.01


@dataclass(frozen=True)
class PeerCheck:
    """Resultado interno. `reason` es solo para el registro local: nunca se devuelve a un agente."""

    status: str
    peer: Optional[ProcessIdentity] = None
    reason: str = ""

    @property
    def verified(self) -> bool:
        return self.status == PEER_VERIFIED


@runtime_checkable
class PeerProcessResolver(Protocol):
    def process_identity(self, pid: int) -> Optional[ProcessIdentity]:
        """`(pid, create_time)` de un proceso vivo, o None."""
        ...

    def check(self, client: Endpoint, server: Endpoint, root: ProcessIdentity) -> PeerCheck:
        """¿La conexion `client -> server` es de un proceso que es `root` o desciende de el?"""
        ...

    def still_bound(self, peer: ProcessIdentity, client: Endpoint, server: Endpoint, root: ProcessIdentity) -> bool:
        """Revalidacion barata de un resultado en cache: el mismo proceso sigue siendo dueno de la conexion y sigue
        dentro del arbol."""
        ...


@runtime_checkable
class DeviceIdentityPort(Protocol):
    def device_uri(self) -> str: ...

    def public_key_b64(self) -> str: ...

    def fingerprint(self) -> str: ...

    def sign_header(self, header: Mapping[str, Any]) -> Dict[str, Any]: ...

    def verify(self, envelope: Mapping[str, Any], content: Optional[str], *, now_ms: Optional[int] = None) -> Any: ...

    def device_certificate(self) -> Optional[Dict[str, Any]]: ...
