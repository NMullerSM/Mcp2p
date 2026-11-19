"""Dispositivo remoto simulado para pruebas de ingestión y cuarentena (v2.0.2, WP-7)."""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple, Union

from src.adapters.identity.ed25519_identity import DeviceIdentity, DeviceKeyStore
from src.core.domain.envelope import build_header
from src.p2p.crypto import NodeCryptoManager


class SimulatedDevice:
    """Simula otro dispositivo MCP2P con su propio almacén de claves Ed25519 y certificado opcional."""

    def __init__(
        self,
        base_dir: Union[str, Path],
        display_name: str,
        *,
        human_signer: Any = None,
        include_cert: bool = True,
        device_id: Optional[str] = None,
        sender_node_uuid: Optional[str] = None,
        clock_ms: Optional[Callable[[], int]] = None,
    ) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.display_name = str(display_name)
        self.device_id = device_id or f"sim-{uuid.uuid4().hex[:12]}"
        self.sender_node_uuid = sender_node_uuid or f"remote-pane-{self.device_id}"
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._include_cert = bool(include_cert)
        if self._include_cert:
            self._human_signer = (
                human_signer
                if human_signer is not None
                else NodeCryptoManager(key_path=self.base_dir / "human" / "node_identity.key")
            )
        else:
            self._human_signer = None
        self._build_identity()

    def _build_identity(self) -> None:
        self.key_store = DeviceKeyStore(
            self.device_id,
            base_dir=self.base_dir,
            use_keyring=False,
        )
        self.identity = DeviceIdentity(
            self.device_id,
            key_store=self.key_store,
            human_signer=self._human_signer,
            clock_ms=self._clock_ms,
        )

    def fingerprint(self) -> str:
        return self.identity.fingerprint()

    def public_key_b64(self) -> str:
        return self.identity.public_key_b64()

    def rename(self, new_name: str) -> str:
        self.display_name = str(new_name)
        return self.display_name

    def rotate_key(self) -> str:
        self.device_id = f"sim-{uuid.uuid4().hex[:12]}"
        self.sender_node_uuid = f"remote-pane-{self.device_id}"
        self._build_identity()
        return self.fingerprint()

    def envelope_to(
        self,
        target_node_uuid: str,
        project_uuid: str,
        text: str,
        *,
        kind: str = "note",
        chain_id: Optional[str] = None,
        hop: int = 0,
        ttl_ms: int = 7 * 24 * 60 * 60 * 1000,
        created_at: Optional[int] = None,
        parent_msg_id: Optional[str] = None,
        trust: str = "unverified",
    ) -> Tuple[Dict[str, Any], str]:
        now = int(created_at if created_at is not None else self._clock_ms())
        cert = self.identity.device_certificate() if self._include_cert else None
        header = build_header(
            msg_id=str(uuid.uuid4()),
            kind=kind,
            from_uri=f"pane:{self.sender_node_uuid}",
            from_device=self.identity.device_uri(),
            to_uri=f"pane:{target_node_uuid}",
            project_uuid=project_uuid,
            chain_id=chain_id or str(uuid.uuid4()),
            hop=int(hop),
            parent_msg_id=parent_msg_id,
            content=text,
            created_at=now,
            expires_at=now + int(ttl_ms),
            trust=trust,
            device_cert=cert,
        )
        envelope = self.identity.sign_header(header)
        return envelope, text
