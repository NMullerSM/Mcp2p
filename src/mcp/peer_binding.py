"""Vinculo token <-> proceso de cada peticion HTTP al Hub (Ola 3, F1.2; cierra EGM-105).

El middleware ASGI del Hub autentica el token y, ademas, comprueba con un `PeerProcessResolver` que el proceso que
abrio la conexion pertenece al arbol del panel dueño del token. El resultado viaja por un `ContextVar` hasta las
herramientas:

- Herramientas del relevo (`RELAY_PEER_MODE = "enforce"`, D13): cualquier resultado distinto de `verified` responde
  `PEER_PROCESS_MISMATCH` sin ejecutar nada y sin detalles.
- Las 24 herramientas antiguas (`LEGACY_PEER_MODE = "warn"`, como D8): se ejecutan, se registra el desajuste y la
  respuesta lleva `peer_warning`.

Llamadas en proceso (sin peticion HTTP: pruebas, la propia GUI) no tienen vinculo (`None`): no hay proceso par.

Cache: por conexion TCP (extremos cliente/servidor) + panel + token + raiz. Cada acierto se revalida barato (el mismo
proceso sigue siendo dueño de esa conexion y sigue dentro del arbol); un fallo nunca se guarda.
"""

from __future__ import annotations

import contextlib
import json
import logging
import threading
from collections import OrderedDict
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Dict, Iterator, Optional, Tuple

from src.core.ports.identity import PEER_MISMATCH, PEER_UNAVAILABLE, PEER_VERIFIED, ProcessIdentity

logger = logging.getLogger("mcp2p.mcp.peer_binding")

PEER_PROCESS_MISMATCH = "PEER_PROCESS_MISMATCH"
PEER_UNBOUND = "unbound"
RELAY_PEER_MODE = "enforce"
LEGACY_PEER_MODE = "warn"
_CACHE_MAX = 512


@dataclass(frozen=True)
class PeerBinding:
    status: str
    pane_id: str = ""
    reason: str = ""

    @property
    def verified(self) -> bool:
        return self.status == PEER_VERIFIED


_CURRENT_PEER_BINDING: ContextVar[Optional[PeerBinding]] = ContextVar("mcp2p_current_peer_binding", default=None)


def get_current_peer_binding() -> Optional[PeerBinding]:
    return _CURRENT_PEER_BINDING.get()


@contextlib.contextmanager
def peer_binding_context(binding: Optional[PeerBinding]) -> Iterator[Optional[PeerBinding]]:
    token = _CURRENT_PEER_BINDING.set(binding)
    try:
        yield binding
    finally:
        _CURRENT_PEER_BINDING.reset(token)


def relay_peer_allowed() -> bool:
    """`enforce` para el relevo: sin peticion HTTP (en proceso) o con vinculo verificado."""
    binding = get_current_peer_binding()
    return binding is None or binding.verified


def peer_mismatch_response() -> str:
    """Respuesta al agente: sin pids, sin motivo, sin nada que ayude a afinar un ataque."""
    return json.dumps(
        {
            "success": False,
            "code": PEER_PROCESS_MISMATCH,
            "error": "La llamada no procede del proceso de este panel.",
        },
        indent=2,
        ensure_ascii=False,
    )


def legacy_peer_warning() -> Optional[Dict[str, Any]]:
    """`warn` para las herramientas antiguas: aviso a anotar en la respuesta, o None si no hay desajuste."""
    binding = get_current_peer_binding()
    if binding is None or binding.verified:
        return None
    logger.warning(
        json.dumps(
            {"event": "peer_process_mismatch", "mode": LEGACY_PEER_MODE, "pane_id": binding.pane_id,
             "status": binding.status, "reason": binding.reason},
            ensure_ascii=False,
        )
    )
    return {"code": PEER_PROCESS_MISMATCH, "mode": LEGACY_PEER_MODE}


class PeerBindingVerifier:
    """Calcula el `PeerBinding` de una peticion autenticada (bloqueante: llamarlo fuera del bucle de eventos)."""

    def __init__(self, resolver: Any) -> None:
        self.resolver = resolver
        self._lock = threading.Lock()
        self._cache: "OrderedDict[Tuple[Any, ...], ProcessIdentity]" = OrderedDict()

    def verify(self, record: Any, client: Optional[Tuple[str, int]], server: Optional[Tuple[str, int]]) -> PeerBinding:
        pane_id = str(getattr(record, "pane_id", "") or "")
        pid = getattr(record, "pid", None)
        ctime = getattr(record, "pid_create_time", None)
        if not pid or ctime is None:
            return self._log(PeerBinding(PEER_UNBOUND, pane_id, "pane_without_bound_process"))
        if not client or not server:
            return self._log(PeerBinding(PEER_MISMATCH, pane_id, "missing_endpoints"))
        root = ProcessIdentity(int(pid), float(ctime))
        client_t = (str(client[0]), int(client[1]))
        server_t = (str(server[0]), int(server[1]))
        key = (pane_id, getattr(record, "session_token", ""), client_t, server_t, root.pid, root.create_time)
        with self._lock:
            cached = self._cache.get(key)
        try:
            if cached is not None and self.resolver.still_bound(cached, client_t, server_t, root):
                return PeerBinding(PEER_VERIFIED, pane_id)
            check = self.resolver.check(client_t, server_t, root)
        except Exception:  # el resolvedor nunca debe tumbar el Hub: falla cerrado
            logger.debug("Resolvedor de proceso par con error", exc_info=True)
            with self._lock:
                self._cache.pop(key, None)
            return self._log(PeerBinding(PEER_UNAVAILABLE, pane_id, "resolver_exception"))
        with self._lock:
            if check.verified and check.peer is not None:
                self._cache[key] = check.peer
                self._cache.move_to_end(key)
                while len(self._cache) > _CACHE_MAX:
                    self._cache.popitem(last=False)
            else:
                self._cache.pop(key, None)
        if check.verified:
            return PeerBinding(PEER_VERIFIED, pane_id)
        return self._log(PeerBinding(check.status, pane_id, check.reason))

    @staticmethod
    def _log(binding: PeerBinding) -> PeerBinding:
        logger.info(
            json.dumps(
                {"event": "peer_binding", "status": binding.status, "pane_id": binding.pane_id,
                 "reason": binding.reason},
                ensure_ascii=False,
            )
        )
        return binding
