"""
Puerta con Llave Criptográfica (`ExtensionSecurityGate`), conector REST anti-SSRF
(`OutboundRestConnector`) y puerto seguro degradado para terminales (`NullAgentTerminalConnector`).

Principio DEFAULT_DENY: ningún conector externo puede operar hasta ser desbloqueado
explícitamente por el humano (`actor_kind == 'human_ui'`) con un `SignedConnectorManifest`
firmado en Ed25519 por la clave pública confiable del propietario.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import ssl
import threading
import time
import urllib.parse
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional

from src.p2p.crypto import NodeCryptoManager, verify_connector_manifest
from src.storage.validators import validate_safe_id


KNOWN_CONNECTORS = ("outbound_rest", "agent_terminal_matrix", "custom_webhook")
_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")


@dataclass
class SignedConnectorManifest:
    """Manifiesto declarativo firmado en Ed25519 que autoriza capacidades de un conector."""

    connector_id: str
    allowed_domains: List[str] = field(default_factory=list)
    allow_private_ips: bool = False
    db_scopes: List[str] = field(default_factory=list)
    can_invoke_subprocess: bool = False
    issued_at: str = ""
    owner_node_uuid: str = ""
    owner_public_key: str = ""
    signature_ed25519: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SignedConnectorManifest":
        if not isinstance(data, dict):
            raise ValueError("El manifiesto debe ser un diccionario.")
        return cls(
            connector_id=str(data.get("connector_id") or ""),
            allowed_domains=list(data.get("allowed_domains") or []),
            allow_private_ips=bool(data.get("allow_private_ips", False)),
            db_scopes=list(data.get("db_scopes") or []),
            can_invoke_subprocess=bool(data.get("can_invoke_subprocess", False)),
            issued_at=str(data.get("issued_at") or ""),
            owner_node_uuid=str(data.get("owner_node_uuid") or ""),
            owner_public_key=str(data.get("owner_public_key") or ""),
            signature_ed25519=str(data.get("signature_ed25519") or ""),
        )

    @classmethod
    def create_signed(
        cls,
        crypto_manager: NodeCryptoManager,
        connector_id: str,
        owner_node_uuid: str,
        allowed_domains: Optional[List[str]] = None,
        allow_private_ips: bool = False,
        db_scopes: Optional[List[str]] = None,
        can_invoke_subprocess: bool = False,
        issued_at: Optional[str] = None,
    ) -> "SignedConnectorManifest":
        unsigned = {
            "connector_id": connector_id,
            "allowed_domains": list(allowed_domains or []),
            "allow_private_ips": bool(allow_private_ips),
            "db_scopes": list(db_scopes or []),
            "can_invoke_subprocess": bool(can_invoke_subprocess),
            "issued_at": issued_at or datetime.now(timezone.utc).isoformat(),
            "owner_node_uuid": owner_node_uuid,
            "owner_public_key": crypto_manager.public_key_base64,
        }
        signed_dict = crypto_manager.sign_connector_manifest(unsigned)
        return cls.from_dict(signed_dict)


class ExtensionSecurityGate:
    """
    Puerta de seguridad DEFAULT_DENY para conectores externos.
    Todos los conectores conocidos arrancan LOCKED hasta desbloqueo HITL firmado.
    """

    def __init__(self, known_connectors: Optional[Iterable[str]] = None) -> None:
        self._lock = threading.RLock()
        self._known_connectors: List[str] = list(known_connectors or KNOWN_CONNECTORS)
        self._unlocked: Dict[str, SignedConnectorManifest] = {}
        self._circuit_states: Dict[str, str] = {}

    def _gate_entry(self, connector_id: str) -> Dict[str, Any]:
        manifest = self._unlocked.get(connector_id)
        return {
            "connector_id": connector_id,
            "unlocked": manifest is not None,
            "allowed_domains": list(manifest.allowed_domains) if manifest else [],
            "allow_private_ips": bool(manifest.allow_private_ips) if manifest else False,
            "db_scopes": list(manifest.db_scopes) if manifest else [],
            "can_invoke_subprocess": bool(manifest.can_invoke_subprocess) if manifest else False,
            "issued_at": manifest.issued_at if manifest else None,
            "owner_node_uuid": manifest.owner_node_uuid if manifest else None,
            "circuit_state": self._circuit_states.get(connector_id, "closed"),
        }

    def unlock_connector(
        self,
        manifest: SignedConnectorManifest | Dict[str, Any],
        trusted_owner_pubkey: str,
        actor_kind: str = "human_ui",
    ) -> Dict[str, Any]:
        """
        Desbloquea un conector únicamente si actor_kind == 'human_ui' y la firma Ed25519
        del manifiesto es válida contra `trusted_owner_pubkey`.
        """
        if actor_kind != "human_ui":
            raise PermissionError("EXTENSION_GATE_LOCKED")

        manifest_obj = (
            manifest
            if isinstance(manifest, SignedConnectorManifest)
            else SignedConnectorManifest.from_dict(manifest)
        )
        try:
            validate_safe_id(manifest_obj.connector_id, "connector_id")
        except Exception as exc:
            raise PermissionError("EXTENSION_GATE_LOCKED") from exc

        if not trusted_owner_pubkey or not verify_connector_manifest(
            manifest_obj.to_dict(), trusted_owner_pubkey
        ):
            raise PermissionError("EXTENSION_GATE_LOCKED")

        with self._lock:
            if manifest_obj.connector_id not in self._known_connectors:
                self._known_connectors.append(manifest_obj.connector_id)
            self._unlocked[manifest_obj.connector_id] = manifest_obj
            return self._gate_entry(manifest_obj.connector_id)

    def lock_connector(self, connector_id: str) -> Dict[str, Any]:
        """Cierra la puerta de seguridad para `connector_id`."""
        with self._lock:
            self._unlocked.pop(connector_id, None)
            return self._gate_entry(connector_id)

    def is_unlocked(self, connector_id: str) -> bool:
        with self._lock:
            return connector_id in self._unlocked

    def require_unlocked(self, connector_id: str) -> SignedConnectorManifest:
        with self._lock:
            manifest = self._unlocked.get(connector_id)
            if manifest is None:
                raise PermissionError("EXTENSION_GATE_LOCKED")
            return manifest

    def set_circuit_state(self, connector_id: str, state: str) -> None:
        with self._lock:
            self._circuit_states[connector_id] = state

    def get_gates_status(self) -> List[Dict[str, Any]]:
        with self._lock:
            ordered = list(self._known_connectors)
            for cid in self._unlocked:
                if cid not in ordered:
                    ordered.append(cid)
            return [self._gate_entry(cid) for cid in ordered]


class _PinnedIPHTTPSConnection(http.client.HTTPSConnection):
    """HTTPSConnection que conecta directamente a la IP validada conservando SNI."""

    def __init__(self, target_ip: str, sni_host: str, port: int, timeout: float, context: ssl.SSLContext):
        super().__init__(host=sni_host, port=port, timeout=timeout, context=context)
        self._target_ip = target_ip
        self._sni_host = sni_host

    def connect(self) -> None:
        raw_sock = socket.create_connection((self._target_ip, self.port), self.timeout, self.source_address)
        self.sock = self._context.wrap_socket(raw_sock, server_hostname=self._sni_host)


class _PinnedIPHTTPConnection(http.client.HTTPConnection):
    """HTTPConnection que conecta directamente a la IP validada evitando DNS rebinding."""

    def __init__(self, target_ip: str, host_header: str, port: int, timeout: float):
        super().__init__(host=host_header, port=port, timeout=timeout)
        self._target_ip = target_ip

    def connect(self) -> None:
        self.sock = socket.create_connection((self._target_ip, self.port), self.timeout, self.source_address)


class OutboundRestConnector:
    """
    Conector HTTP/HTTPS saliente con validación anti-SSRF, lista blanca de dominios
    del manifiesto firmado, límite de bytes/timeout y Circuit Breaker.
    """

    def __init__(
        self,
        gate: ExtensionSecurityGate,
        connector_id: str = "outbound_rest",
        timeout_s: float = 5.0,
        max_bytes: int = 1_048_576,
        failure_threshold: int = 3,
        recovery_timeout_s: float = 30.0,
        allow_http: bool = True,
        dns_resolver: Optional[Callable[[str], List[str]]] = None,
        transport_fn: Optional[Callable[..., Dict[str, Any]]] = None,
    ) -> None:
        self.gate = gate
        self.connector_id = connector_id
        self.timeout_s = float(timeout_s)
        self.max_bytes = int(max_bytes)
        self.failure_threshold = max(1, int(failure_threshold))
        self.recovery_timeout_s = float(recovery_timeout_s)
        self.allow_http = bool(allow_http)
        self._dns_resolver = dns_resolver
        self._transport_fn = transport_fn

        self._lock = threading.RLock()
        self._failure_count = 0
        self._circuit_state = "closed"
        self._opened_at = 0.0

    @staticmethod
    def _domain_allowed(hostname: str, allowed_domains: List[str]) -> bool:
        host = hostname.lower().rstrip(".")
        for raw_dom in allowed_domains:
            dom = str(raw_dom or "").strip().lower().rstrip(".")
            if not dom:
                continue
            if dom.startswith("*."):
                suffix = dom[2:]
                if suffix and host.endswith("." + suffix) and len(host) > len(suffix) + 1:
                    return True
            elif host == dom:
                return True
        return False

    @staticmethod
    def _is_blocked_ip(ip_str: str) -> bool:
        ip_obj = ipaddress.ip_address(ip_str)
        if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped is not None:
            ip_obj = ip_obj.ipv4_mapped
        if (
            ip_obj.is_loopback
            or ip_obj.is_private
            or ip_obj.is_link_local
            or ip_obj.is_multicast
            or ip_obj.is_reserved
            or ip_obj.is_unspecified
        ):
            return True
        if ip_obj.version == 4 and ip_obj in _CGNAT_NETWORK:
            return True
        return False

    def _resolve_host_ips(self, hostname: str, port: int) -> List[str]:
        try:
            ip_lit = ipaddress.ip_address(hostname)
            return [str(ip_lit)]
        except ValueError:
            pass

        if self._dns_resolver is not None:
            resolved = self._dns_resolver(hostname)
            return [str(ip) for ip in (resolved or []) if ip]

        infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        ips: List[str] = []
        for info in infos:
            sockaddr = info[4]
            if sockaddr and sockaddr[0]:
                ip_s = str(sockaddr[0]).split("%", 1)[0]
                if ip_s not in ips:
                    ips.append(ip_s)
        return ips

    def validate_url(self, url: str) -> Dict[str, Any]:
        """
        Valida puerta desbloqueada, esquema permitido, dominio en lista blanca y
        resolución DNS/IP anti-SSRF.
        """
        manifest = self.gate.require_unlocked(self.connector_id)

        if not isinstance(url, str) or not url.strip() or any(ord(c) < 32 for c in url):
            raise PermissionError("INVALID_URL")

        parsed = urllib.parse.urlparse(url.strip())
        scheme = (parsed.scheme or "").lower()
        allowed_schemes = ("https", "http") if self.allow_http else ("https",)
        if scheme not in allowed_schemes:
            raise PermissionError(f"SCHEME_NOT_ALLOWED: {scheme}")

        if parsed.username or parsed.password:
            raise PermissionError("URL_USERINFO_NOT_ALLOWED")

        hostname = (parsed.hostname or "").strip().lower().rstrip(".")
        if not hostname:
            raise PermissionError("INVALID_URL_HOST")

        if not self._domain_allowed(hostname, manifest.allowed_domains):
            raise PermissionError(f"DOMAIN_NOT_ALLOWED: {hostname}")

        port = parsed.port or (443 if scheme == "https" else 80)
        try:
            resolved_ips = self._resolve_host_ips(hostname, port)
        except Exception as exc:
            raise PermissionError(f"DNS_RESOLUTION_FAILED: {hostname}") from exc

        if not resolved_ips:
            raise PermissionError(f"DNS_RESOLUTION_FAILED: {hostname}")

        if not manifest.allow_private_ips:
            for ip_s in resolved_ips:
                try:
                    if self._is_blocked_ip(ip_s):
                        raise PermissionError(f"SSRF_BLOCKED_IP: {ip_s}")
                except ValueError as exc:
                    raise PermissionError(f"SSRF_INVALID_IP: {ip_s}") from exc

        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        return {
            "url": url.strip(),
            "scheme": scheme,
            "host": hostname,
            "netloc": parsed.netloc,
            "port": port,
            "path": path,
            "resolved_ips": resolved_ips,
        }

    def get_circuit_state(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "connector_id": self.connector_id,
                "circuit_state": self._circuit_state,
                "failure_count": self._failure_count,
            }

    def _check_circuit_before_call(self) -> None:
        with self._lock:
            if self._circuit_state == "open":
                if (time.monotonic() - self._opened_at) >= self.recovery_timeout_s:
                    self._circuit_state = "half_open"
                    self.gate.set_circuit_state(self.connector_id, "half_open")
                else:
                    raise RuntimeError("CIRCUIT_OPEN")

    def _record_success(self) -> None:
        with self._lock:
            self._failure_count = 0
            self._circuit_state = "closed"
            self.gate.set_circuit_state(self.connector_id, "closed")

    def _record_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            if self._failure_count >= self.failure_threshold:
                self._circuit_state = "open"
                self._opened_at = time.monotonic()
                self.gate.set_circuit_state(self.connector_id, "open")

    def request(
        self,
        url: str,
        method: str = "GET",
        headers: Optional[Dict[str, str]] = None,
        body: Optional[bytes | str] = None,
        timeout_s: Optional[float] = None,
        max_bytes: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Ejecuta una petición HTTP/HTTPS conectando a la IP validada con Circuit Breaker."""
        info = self.validate_url(url)
        self._check_circuit_before_call()

        effective_timeout = float(timeout_s if timeout_s is not None else self.timeout_s)
        effective_max_bytes = int(max_bytes if max_bytes is not None else self.max_bytes)
        req_headers = dict(headers or {})
        req_headers.setdefault("Host", info["netloc"])
        req_headers.setdefault("User-Agent", "MCP2P-OutboundRestConnector/2.0")

        payload_bytes: Optional[bytes]
        if isinstance(body, str):
            payload_bytes = body.encode("utf-8")
        else:
            payload_bytes = body

        target_ip = info["resolved_ips"][0]

        try:
            if self._transport_fn is not None:
                resp_data = self._transport_fn(
                    url=info["url"],
                    method=method.upper(),
                    headers=req_headers,
                    body=payload_bytes,
                    timeout_s=effective_timeout,
                    resolved_ip=target_ip,
                )
                status = int(resp_data.get("status_code", 200))
                raw_body = resp_data.get("body", b"")
                if isinstance(raw_body, str):
                    raw_body = raw_body.encode("utf-8")
                if len(raw_body) > effective_max_bytes:
                    raise ValueError("RESPONSE_TOO_LARGE")
                resp_headers = dict(resp_data.get("headers") or {})
            else:
                conn: http.client.HTTPConnection
                if info["scheme"] == "https":
                    ctx = ssl.create_default_context()
                    conn = _PinnedIPHTTPSConnection(
                        target_ip=target_ip,
                        sni_host=info["host"],
                        port=info["port"],
                        timeout=effective_timeout,
                        context=ctx,
                    )
                else:
                    conn = _PinnedIPHTTPConnection(
                        target_ip=target_ip,
                        host_header=info["netloc"],
                        port=info["port"],
                        timeout=effective_timeout,
                    )
                try:
                    conn.request(method.upper(), info["path"], body=payload_bytes, headers=req_headers)
                    resp = conn.getresponse()
                    status = resp.status
                    resp_headers = {k.lower(): v for k, v in resp.getheaders()}
                    raw_body = resp.read(effective_max_bytes + 1)
                    if len(raw_body) > effective_max_bytes:
                        raise ValueError("RESPONSE_TOO_LARGE")
                finally:
                    conn.close()
        except Exception:
            self._record_failure()
            raise

        if status >= 500:
            self._record_failure()
        else:
            self._record_success()

        decoded_text = raw_body.decode("utf-8", errors="replace")
        return {
            "status_code": status,
            "headers": resp_headers,
            "body": raw_body,
            "text": decoded_text,
            "resolved_ip": target_ip,
        }

    def fetch_json(
        self,
        url: str,
        method: str = "GET",
        headers: Optional[Dict[str, str]] = None,
        body: Optional[Any] = None,
        timeout_s: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Ejecuta una petición REST esperando respuesta JSON."""
        req_headers = dict(headers or {})
        req_headers.setdefault("Accept", "application/json")
        encoded_body: Optional[bytes | str]
        if isinstance(body, (dict, list)):
            req_headers.setdefault("Content-Type", "application/json")
            encoded_body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        else:
            encoded_body = body

        res = self.request(url=url, method=method, headers=req_headers, body=encoded_body, timeout_s=timeout_s)
        parsed_json = json.loads(res["text"]) if res["text"].strip() else None
        return {
            "status_code": res["status_code"],
            "data": parsed_json,
            "headers": res["headers"],
            "resolved_ip": res["resolved_ip"],
        }


class NullAgentTerminalConnector:
    """
    Adaptador nulo plug-and-play para el futuro puerto de terminales (`agent_terminal_matrix`)
    detrás de `ExtensionSecurityGate`. Exige puerta desbloqueada y degrada de forma segura.
    """

    def __init__(self, gate: ExtensionSecurityGate, connector_id: str = "agent_terminal_matrix") -> None:
        self.gate = gate
        self.connector_id = connector_id

    @staticmethod
    def is_available() -> bool:
        return False

    def get_status(self) -> Dict[str, Any]:
        return {
            "connector_id": self.connector_id,
            "unlocked": self.gate.is_unlocked(self.connector_id),
            "available": False,
            "mode": "null_degraded",
        }

    def list_sessions(self) -> List[Dict[str, Any]]:
        self.gate.require_unlocked(self.connector_id)
        return []

    def _degraded_response(self) -> Dict[str, Any]:
        self.gate.require_unlocked(self.connector_id)
        return {
            "success": False,
            "degraded": True,
            "code": "TERMINAL_ADAPTER_NOT_INSTALLED",
            "message": "NullAgentTerminalConnector activo: adaptador de terminales pendiente de hito V2.",
        }

    def spawn_session(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return self._degraded_response()

    def send_input(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return self._degraded_response()

    def close_session(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return self._degraded_response()
