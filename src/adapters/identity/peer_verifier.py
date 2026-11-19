"""Verificador criptográfico de sobres remotos sobre `verify_envelope` y `device_cert` (v2.0.2, WP-7)."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Tuple

from cryptography.hazmat.primitives.asymmetric import ed25519

from src.adapters.identity.envelope_crypto import b64url_decode, key_fingerprint, verify_envelope
from src.core.ports.peer_keys import PeerVerdict
from src.p2p.crypto import NodeCryptoManager


def _verify_device_cert(
    cert: Any, declared_device_id: str, now_ms: int
) -> Tuple[bool, Optional[str], Optional[str], str]:
    if cert is None:
        return False, None, None, "MISSING_CERT"
    if not isinstance(cert, Mapping):
        return False, None, None, "BAD_DEVICE_CERT"
    cert_node = cert.get("node_uuid")
    dev_pub = cert.get("device_ed25519_pub")
    human_pub = cert.get("human_pub")
    cert_sig = cert.get("sig")
    not_after = cert.get("not_after")
    if (
        not isinstance(cert_node, str)
        or not cert_node
        or cert_node != declared_device_id
        or not isinstance(dev_pub, str)
        or not dev_pub
        or not isinstance(human_pub, str)
        or not human_pub
        or not isinstance(cert_sig, str)
        or not cert_sig
        or isinstance(not_after, bool)
        or not isinstance(not_after, int)
        or int(not_after) <= int(now_ms)
    ):
        return False, None, None, "BAD_DEVICE_CERT"
    body = {k: v for k, v in cert.items() if k != "sig"}
    if not NodeCryptoManager.verify_signature(body, cert_sig, human_pub):
        return False, None, None, "BAD_DEVICE_CERT"
    return True, dev_pub, human_pub, "OK"


class PeerEnvelopeVerifier:
    """Verifica un sobre remoto usando la clave almacenada del nodo o el `device_cert` en primer contacto."""

    def __init__(self, *, node_store: Any) -> None:
        self._node_store = node_store

    def verify(
        self,
        envelope: Mapping[str, Any],
        content: Optional[str],
        *,
        now_ms: int,
    ) -> PeerVerdict:
        if not isinstance(envelope, Mapping):
            return PeerVerdict(False, "SCHEMA_INVALID", None, None, None)
        sig = envelope.get("sig")
        if not isinstance(sig, Mapping) or set(sig) != {"alg", "key", "value"}:
            return PeerVerdict(False, "SCHEMA_INVALID", None, None, None)
        fp = sig.get("key")
        if not isinstance(fp, str) or not fp.strip():
            return PeerVerdict(False, "SCHEMA_INVALID", None, None, None)
        fp = fp.strip()

        from_device = envelope.get("from_device")
        declared_device_id = (
            from_device[7:]
            if isinstance(from_device, str) and from_device.startswith("device:")
            else ""
        )

        known = self._node_store.get_node(fp, now_ms=int(now_ms))
        cert = envelope.get("device_cert")
        human_pub: Optional[str] = None

        if known is not None:
            public_key_b64 = str(known["device_public_key"])
            if cert is not None:
                cert_ok, cert_dev_pub, cert_human_pub, _ = _verify_device_cert(
                    cert, declared_device_id, int(now_ms)
                )
                if cert_ok and cert_dev_pub == public_key_b64:
                    human_pub = cert_human_pub
        else:
            if cert is None:
                return PeerVerdict(False, "UNKNOWN_KEY", fp, None, None)
            cert_ok, cert_dev_pub, cert_human_pub, cert_code = _verify_device_cert(
                cert, declared_device_id, int(now_ms)
            )
            if not cert_ok or not cert_dev_pub:
                return PeerVerdict(False, cert_code, fp, None, None)
            try:
                raw_pub = b64url_decode(cert_dev_pub)
                ed25519.Ed25519PublicKey.from_public_bytes(raw_pub)
                actual_fp = key_fingerprint(raw_pub)
            except (ValueError, TypeError):
                return PeerVerdict(False, "KEY_MISMATCH", fp, None, None)
            if actual_fp != fp:
                return PeerVerdict(False, "KEY_MISMATCH", fp, None, None)
            public_key_b64 = cert_dev_pub
            human_pub = cert_human_pub

        verdict = verify_envelope(envelope, content, public_key_b64, now_ms=int(now_ms))
        if not verdict.ok:
            return PeerVerdict(False, verdict.code, fp, public_key_b64, None)
        return PeerVerdict(True, "OK", fp, public_key_b64, human_pub)
