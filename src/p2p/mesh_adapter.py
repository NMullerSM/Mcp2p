"""
Conector de malla P2P multi-nodo y multi-seed (`MultiPeerMeshConnector`).

Proporciona:
- Gestión de múltiples `seed_nodes` y N pares simultáneos.
- Firma y verificación Ed25519 de sobres (`envelope`) contra pares confiables registrados.
- Caché LRU anti-replay por `(envelope_id, nonce)` con ventana temporal (0 tormentas de broadcast).
- Rate-limiter por `sender_node_uuid` (anti-flood).
- Circuit Breaker por par y por seed.
- Modos de transporte seleccionables: `lan_mesh`, `direct_proxy`, `realtime_relay_stub`
  e inyección de `transport_sender` para pruebas de carga en memoria o HTTP loopback.
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
import time
import urllib.request
import uuid
from collections import OrderedDict, deque
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
import base64

from src.p2p.crypto import NodeCryptoManager

logger = logging.getLogger("mcp2p.p2p.mesh")

VALID_TRANSPORT_MODES = ("lan_mesh", "direct_proxy", "realtime_relay_stub")


class _EphemeralSigner:
    """Firmante Ed25519 en memoria cuando no se inyecta NodeCryptoManager."""

    def __init__(self) -> None:
        self._priv = ed25519.Ed25519PrivateKey.generate()
        raw_pub = self._priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.public_key_base64 = base64.urlsafe_b64encode(raw_pub).decode("ascii")

    def sign_payload(self, payload: Dict[str, Any]) -> str:
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return base64.urlsafe_b64encode(self._priv.sign(canonical)).decode("ascii")


class MultiPeerMeshConnector:
    """Malla P2P multi-nodo / multi-seed con seguridad Ed25519, anti-replay, anti-flood y Circuit Breaker."""

    def __init__(
        self,
        node_uuid: str,
        crypto_manager: Optional[Any] = None,
        transport_mode: str = "lan_mesh",
        transport_sender: Optional[Callable[[str, Dict[str, Any]], bool]] = None,
        replay_window_s: float = 300.0,
        replay_cache_size: int = 4096,
        rate_limit_max: int = 120,
        rate_limit_window_s: float = 10.0,
        cb_failure_threshold: int = 3,
        cb_recovery_s: float = 30.0,
        http_timeout_s: float = 4.0,
        on_message: Optional[Callable[[Dict[str, Any]], None]] = None,
        seed_nodes: Optional[List[str]] = None,
    ) -> None:
        if transport_mode not in VALID_TRANSPORT_MODES:
            raise ValueError(
                f"Modo de transporte inválido: {transport_mode!r}. "
                f"Use uno de {VALID_TRANSPORT_MODES}."
            )

        self.node_uuid = node_uuid
        self.crypto = crypto_manager if crypto_manager is not None else _EphemeralSigner()
        self.transport_mode = transport_mode
        self._transport_sender = transport_sender
        self.replay_window_s = float(replay_window_s)
        self.replay_cache_size = max(16, int(replay_cache_size))
        self.rate_limit_max = max(1, int(rate_limit_max))
        self.rate_limit_window_s = max(0.1, float(rate_limit_window_s))
        self.cb_failure_threshold = max(1, int(cb_failure_threshold))
        self.cb_recovery_s = max(0.5, float(cb_recovery_s))
        self.http_timeout_s = float(http_timeout_s)

        self._lock = threading.RLock()
        self._closed = False
        self._callbacks: List[Callable[[Dict[str, Any]], None]] = []
        if on_message is not None:
            self._callbacks.append(on_message)

        # seed_url -> circuit state dict
        self._seeds: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        for s in (seed_nodes or []):
            self.add_seed_node(s)

        # node_uuid -> peer dict
        self._peers: Dict[str, Dict[str, Any]] = {}

        # LRU anti-replay cache: (envelope_id, nonce) -> timestamp
        self._replay_cache: OrderedDict[Tuple[str, str], float] = OrderedDict()

        # Per-sender sliding window timestamps
        self._rate_windows: Dict[str, Deque[float]] = {}

    @property
    def public_key_base64(self) -> str:
        return self.crypto.public_key_base64

    def set_transport_mode(self, mode: str) -> str:
        if mode not in VALID_TRANSPORT_MODES:
            raise ValueError(f"Modo de transporte inválido: {mode!r}.")
        with self._lock:
            self.transport_mode = mode
            return self.transport_mode

    def on_message(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        with self._lock:
            if callback not in self._callbacks:
                self._callbacks.append(callback)

    # ==========================================
    # Gestión de Seeds y Pares
    # ==========================================

    def add_seed_node(self, seed_url: str) -> List[Dict[str, Any]]:
        clean = str(seed_url or "").strip()
        if not clean:
            raise ValueError("seed_url no puede estar vacío.")
        with self._lock:
            if clean not in self._seeds:
                self._seeds[clean] = {
                    "seed_url": clean,
                    "url": clean,
                    "circuit_state": "closed",
                    "failure_count": 0,
                    "opened_at": 0.0,
                    "healthy": True,
                }
            return self.list_seed_nodes()

    def remove_seed_node(self, seed_url: str) -> List[Dict[str, Any]]:
        clean = str(seed_url or "").strip()
        with self._lock:
            self._seeds.pop(clean, None)
            return self.list_seed_nodes()

    def list_seed_nodes(self) -> List[Dict[str, Any]]:
        with self._lock:
            now = time.monotonic()
            out: List[Dict[str, Any]] = []
            for entry in self._seeds.values():
                state = entry["circuit_state"]
                if state == "open" and (now - entry["opened_at"]) >= self.cb_recovery_s:
                    state = "half_open"
                    entry["circuit_state"] = state
                out.append({
                    "seed_url": entry["seed_url"],
                    "url": entry["url"],
                    "healthy": state != "open",
                    "circuit_state": state,
                    "failure_count": entry["failure_count"],
                })
            return out

    def register_peer(
        self,
        node_uuid: str,
        public_key: str,
        endpoint: str = "",
        role: str = "collaborator",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not node_uuid or not public_key:
            raise ValueError("node_uuid y public_key son obligatorios para registrar un par.")
        with self._lock:
            existing = self._peers.get(node_uuid)
            peer = {
                "node_uuid": node_uuid,
                "public_key": public_key,
                "endpoint": str(endpoint or "").strip(),
                "role": str(role or "collaborator"),
                "metadata": dict(metadata or {}),
                "circuit_state": existing["circuit_state"] if existing else "closed",
                "failure_count": existing["failure_count"] if existing else 0,
                "opened_at": existing["opened_at"] if existing else 0.0,
                "last_seen_at": existing["last_seen_at"] if existing else None,
            }
            self._peers[node_uuid] = peer
            return self._public_peer_view(peer)

    def unregister_peer(self, node_uuid: str) -> bool:
        with self._lock:
            removed = self._peers.pop(node_uuid, None) is not None
            self._rate_windows.pop(node_uuid, None)
            return removed

    @staticmethod
    def _public_peer_view(peer: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "node_uuid": peer["node_uuid"],
            "public_key": peer["public_key"],
            "endpoint": peer["endpoint"],
            "role": peer["role"],
            "metadata": dict(peer["metadata"]),
            "circuit_state": peer["circuit_state"],
            "failure_count": peer["failure_count"],
            "last_seen_at": peer["last_seen_at"],
        }

    def get_peers(self) -> List[Dict[str, Any]]:
        with self._lock:
            now = time.monotonic()
            result: List[Dict[str, Any]] = []
            for peer in self._peers.values():
                if peer["circuit_state"] == "open" and (now - peer["opened_at"]) >= self.cb_recovery_s:
                    peer["circuit_state"] = "half_open"
                result.append(self._public_peer_view(peer))
            return result

    # ==========================================
    # Sobres Criptográficos, Circuit Breaker y Envío
    # ==========================================

    @staticmethod
    def _envelope_core(envelope: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "envelope_id": envelope.get("envelope_id"),
            "nonce": envelope.get("nonce"),
            "timestamp": envelope.get("timestamp"),
            "sender_node_uuid": envelope.get("sender_node_uuid"),
            "target_node_uuid": envelope.get("target_node_uuid"),
            "payload": envelope.get("payload"),
        }

    def build_signed_envelope(
        self,
        payload: Dict[str, Any],
        target_node_uuid: str = "*",
        ttl: int = 2,
    ) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("El payload del sobre debe ser un diccionario.")
        envelope: Dict[str, Any] = {
            "envelope_id": str(uuid.uuid4()),
            "nonce": secrets.token_hex(16),
            "timestamp": time.time(),
            "sender_node_uuid": self.node_uuid,
            "sender_pubkey": self.public_key_base64,
            "target_node_uuid": target_node_uuid,
            "ttl": int(ttl),
            "payload": payload,
        }
        envelope["sig"] = self.crypto.sign_payload(self._envelope_core(envelope))
        return envelope

    def _is_circuit_open(self, entry: Dict[str, Any]) -> bool:
        if entry["circuit_state"] != "open":
            return False
        if (time.monotonic() - entry["opened_at"]) >= self.cb_recovery_s:
            entry["circuit_state"] = "half_open"
            return False
        return True

    def _record_cb_success(self, entry: Dict[str, Any]) -> None:
        entry["failure_count"] = 0
        entry["circuit_state"] = "closed"
        if "healthy" in entry:
            entry["healthy"] = True

    def _record_cb_failure(self, entry: Dict[str, Any]) -> None:
        entry["failure_count"] += 1
        if entry["failure_count"] >= self.cb_failure_threshold:
            entry["circuit_state"] = "open"
            entry["opened_at"] = time.monotonic()
            if "healthy" in entry:
                entry["healthy"] = False

    def _deliver_envelope(self, target: str, envelope: Dict[str, Any]) -> bool:
        if self._closed:
            return False
        if self._transport_sender is not None:
            try:
                return bool(self._transport_sender(target, envelope))
            except Exception as exc:
                logger.debug(f"transport_sender falló hacia {target}: {exc}")
                return False

        if self.transport_mode == "realtime_relay_stub":
            return False

        if target.startswith(("http://", "https://")):
            url = target.rstrip("/") + "/webhook/p2p" if not target.endswith("/webhook/p2p") else target
            body = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.http_timeout_s) as resp:
                    return 200 <= resp.status < 300
            except Exception as exc:
                logger.debug(f"Envío HTTP mesh falló hacia {url}: {exc}")
                return False
        return False

    def _deliver_via_seeds(self, envelope: Dict[str, Any]) -> Optional[str]:
        with self._lock:
            seed_entries = list(self._seeds.values())

        for seed in seed_entries:
            with self._lock:
                if self._is_circuit_open(seed):
                    continue
            if self._deliver_envelope(seed["seed_url"], envelope):
                with self._lock:
                    self._record_cb_success(seed)
                return seed["seed_url"]
            with self._lock:
                self._record_cb_failure(seed)
        return None

    def send_to_peer(self, target_node_uuid: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Envía un sobre firmado Ed25519 a un par registrado, con Circuit Breaker y fallback a seeds."""
        with self._lock:
            if self._closed:
                return {"success": False, "reason": "CONNECTOR_CLOSED"}
            peer = self._peers.get(target_node_uuid)
            if peer is None:
                return {"success": False, "reason": "UNKNOWN_PEER"}
            peer_open = self._is_circuit_open(peer)
            endpoint = peer["endpoint"] or target_node_uuid
            mode = self.transport_mode

        envelope = self.build_signed_envelope(payload, target_node_uuid=target_node_uuid)

        if mode != "direct_proxy" and not peer_open:
            if self._deliver_envelope(endpoint, envelope):
                with self._lock:
                    if target_node_uuid in self._peers:
                        self._record_cb_success(self._peers[target_node_uuid])
                return {"success": True, "envelope_id": envelope["envelope_id"], "via": "direct"}
            with self._lock:
                if target_node_uuid in self._peers:
                    self._record_cb_failure(self._peers[target_node_uuid])

        seed_used = self._deliver_via_seeds(envelope)
        if seed_used is not None:
            return {
                "success": True,
                "envelope_id": envelope["envelope_id"],
                "via": "seed",
                "seed_url": seed_used,
            }

        reason = "CIRCUIT_OPEN" if peer_open else "DELIVERY_FAILED"
        return {"success": False, "reason": reason, "envelope_id": envelope["envelope_id"]}

    def broadcast_envelope(
        self,
        payload: Dict[str, Any],
        target_peers: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Difunde un único sobre firmado a múltiples pares sin tormentas de broadcast.
        Si la entrega directa falla o el modo es `direct_proxy`, conmuta automáticamente a seeds saludables.
        """
        with self._lock:
            if self._closed:
                return {"success": False, "reason": "CONNECTOR_CLOSED", "delivered": 0, "failed": 0}
            if target_peers is not None:
                peer_ids = [pid for pid in target_peers if pid != self.node_uuid and pid in self._peers]
            else:
                peer_ids = [pid for pid in self._peers if pid != self.node_uuid]
            mode = self.transport_mode
            has_seeds = len(self._seeds) > 0

        envelope = self.build_signed_envelope(payload, target_node_uuid="*")
        delivered_peers: List[str] = []
        failed_peers: List[str] = []

        if mode == "direct_proxy" and has_seeds:
            seed_used = self._deliver_via_seeds(envelope)
            if seed_used is not None:
                return {
                    "success": True,
                    "envelope_id": envelope["envelope_id"],
                    "delivered": len(peer_ids),
                    "failed": 0,
                    "via": "seed",
                    "seed_url": seed_used,
                }

        need_seed_fallback = False
        for pid in peer_ids:
            with self._lock:
                peer = self._peers.get(pid)
                if peer is None or self._is_circuit_open(peer):
                    failed_peers.append(pid)
                    need_seed_fallback = True
                    continue
                endpoint = peer["endpoint"] or pid

            if self._deliver_envelope(endpoint, envelope):
                delivered_peers.append(pid)
                with self._lock:
                    if pid in self._peers:
                        self._record_cb_success(self._peers[pid])
            else:
                failed_peers.append(pid)
                need_seed_fallback = True
                with self._lock:
                    if pid in self._peers:
                        self._record_cb_failure(self._peers[pid])

        seed_used: Optional[str] = None
        if need_seed_fallback and has_seeds:
            seed_used = self._deliver_via_seeds(envelope)
            if seed_used is not None:
                delivered_peers = list(peer_ids)
                failed_peers = []

        return {
            "success": len(delivered_peers) > 0 or len(peer_ids) == 0,
            "envelope_id": envelope["envelope_id"],
            "delivered": len(delivered_peers),
            "failed": len(failed_peers),
            "delivered_peers": delivered_peers,
            "failed_peers": failed_peers,
            "seed_url": seed_used,
        }

    # ==========================================
    # Ingesta, Verificación Ed25519, Anti-Replay y Anti-Flood
    # ==========================================

    def ingest_envelope(self, envelope: Dict[str, Any]) -> Dict[str, Any]:
        """
        Valida un sobre entrante:
        1. Emisor registrado y clave pública coincidente (trusted peers).
        2. Firma Ed25519 válida sobre el núcleo canónico del sobre.
        3. Ventana temporal y caché LRU anti-replay `(envelope_id, nonce)`.
        4. Rate-limiter por `sender_node_uuid` (anti-flood).
        """
        if not isinstance(envelope, dict):
            return {"accepted": False, "reason": "INVALID_ENVELOPE"}

        envelope_id = envelope.get("envelope_id")
        nonce = envelope.get("nonce")
        ts_raw = envelope.get("timestamp")
        sender_uuid = envelope.get("sender_node_uuid")
        sender_pubkey = envelope.get("sender_pubkey")
        sig = envelope.get("sig")
        payload = envelope.get("payload")

        if not (
            isinstance(envelope_id, str)
            and envelope_id
            and isinstance(nonce, str)
            and nonce
            and isinstance(sender_uuid, str)
            and sender_uuid
            and isinstance(sender_pubkey, str)
            and sender_pubkey
            and isinstance(sig, str)
            and sig
            and isinstance(payload, dict)
        ):
            return {"accepted": False, "reason": "MALFORMED_ENVELOPE"}

        if sender_uuid == self.node_uuid:
            return {"accepted": False, "reason": "SELF_ECHO"}

        try:
            ts_val = float(ts_raw)
        except (TypeError, ValueError):
            return {"accepted": False, "reason": "INVALID_TIMESTAMP"}

        with self._lock:
            if self._closed:
                return {"accepted": False, "reason": "CONNECTOR_CLOSED"}
            peer = self._peers.get(sender_uuid)
            if peer is None:
                return {"accepted": False, "reason": "UNTRUSTED_PEER"}
            registered_pubkey = peer["public_key"]

        if sender_pubkey != registered_pubkey:
            return {"accepted": False, "reason": "PUBLIC_KEY_MISMATCH"}

        if not NodeCryptoManager.verify_signature(self._envelope_core(envelope), sig, registered_pubkey):
            return {"accepted": False, "reason": "INVALID_SIGNATURE"}

        now_wall = time.time()
        if abs(now_wall - ts_val) > self.replay_window_s:
            return {"accepted": False, "reason": "EXPIRED_ENVELOPE"}

        cache_key = (envelope_id, nonce)
        now_mono = time.monotonic()

        with self._lock:
            if cache_key in self._replay_cache:
                self._replay_cache.move_to_end(cache_key)
                return {"accepted": False, "reason": "REPLAY_DETECTED"}

            win = self._rate_windows.setdefault(sender_uuid, deque())
            cutoff = now_mono - self.rate_limit_window_s
            while win and win[0] < cutoff:
                win.popleft()
            if len(win) >= self.rate_limit_max:
                return {"accepted": False, "reason": "RATE_LIMITED"}

            win.append(now_mono)
            self._replay_cache[cache_key] = now_wall
            while len(self._replay_cache) > self.replay_cache_size:
                self._replay_cache.popitem(last=False)

            if sender_uuid in self._peers:
                self._peers[sender_uuid]["last_seen_at"] = now_wall

            callbacks = list(self._callbacks)

        target_uuid = envelope.get("target_node_uuid")
        if target_uuid not in ("*", self.node_uuid, "", None):
            return {"accepted": False, "reason": "WRONG_TARGET"}

        delivered_msg = {
            "envelope_id": envelope_id,
            "sender_node_uuid": sender_uuid,
            "target_node_uuid": target_uuid,
            "timestamp": ts_val,
            "payload": payload,
        }
        for cb in callbacks:
            try:
                cb(delivered_msg)
            except Exception as exc:
                logger.debug(f"Callback de mensaje mesh lanzó excepción: {exc}")

        return {
            "accepted": True,
            "envelope_id": envelope_id,
            "sender_node_uuid": sender_uuid,
        }

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._callbacks.clear()
