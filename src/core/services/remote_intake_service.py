"""Servicio de ingestión remota, política por nodo y cuarentena (v2.0.2, WP-7).

En 2.0.2 ningún camino de red o MCP llama a `accept_remote_envelope`: solo el dispositivo
simulado de las pruebas y `scripts/simulate_remote_node.py`.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Mapping, Optional

from src.core.domain.envelope import header_of
from src.core.domain.identity import TrustLevel
from src.core.domain.node_policy import (
    CHAIN_APPROVAL_TTL_MS,
    DEFAULT_POLICY,
    QUARANTINE_TTL_MS,
    NodePolicy,
    NodeRelation,
    decide_intake,
    sanitize_node_name,
)
from src.core.ports.peer_keys import PeerEnvelopeVerifierPort


def _now_ms() -> int:
    return int(time.time() * 1000)


def _err(code: str, error: str, **extra: Any) -> Dict[str, Any]:
    return {"success": False, "code": code, "error": error, **extra}


class RemoteIntakeService:
    """Verifica sobres de otros equipos, aplica política/TOFU/cuarentena y gestiona la confianza de nodos."""

    def __init__(
        self,
        *,
        relay: Any,
        node_store: Any,
        verifier: PeerEnvelopeVerifierPort,
        local_human_pub: Optional[Callable[[], Optional[str]] | str] = None,
        local_device_fingerprint: Optional[Callable[[], Optional[str]] | str] = None,
        clock_ms: Optional[Callable[[], int]] = None,
    ) -> None:
        self._relay = relay
        self._store = relay.store
        self._node_store = node_store
        self._verifier = verifier
        self._local_human_pub_src = local_human_pub
        self._local_fp_src = local_device_fingerprint
        self._clock_ms = clock_ms or _now_ms

    def _local_human_pub(self) -> Optional[str]:
        if callable(self._local_human_pub_src):
            return self._local_human_pub_src()
        return self._local_human_pub_src

    def _local_fingerprint(self) -> Optional[str]:
        if callable(self._local_fp_src):
            return self._local_fp_src()
        return self._local_fp_src

    def _resolve_local_pane(self, to_uri: str, project_uuid: str) -> Optional[Any]:
        if not to_uri.startswith("pane:") or not project_uuid:
            return None
        target_node = to_uri[5:]
        pane_dir = getattr(self._relay, "pane_directory", None)
        finder = getattr(pane_dir, "find_active_pane_by_node", None) if pane_dir is not None else None
        if not callable(finder):
            return None
        rec = finder(target_node, project_uuid=project_uuid)
        if rec is None:
            return None
        if getattr(rec, "status", "active") == "closed":
            return None
        if str(getattr(rec, "project_uuid", "") or "") != project_uuid:
            return None
        return rec

    def accept_remote_envelope(
        self,
        envelope: Mapping[str, Any],
        content: str,
        *,
        transport: str,
        declared_node_name: str,
    ) -> Dict[str, Any]:
        now = int(self._clock_ms())
        header = header_of(envelope) if isinstance(envelope, Mapping) else {}
        base_audit: Dict[str, Any] = {
            "msg_id": header.get("msg_id"),
            "chain_id": header.get("chain_id"),
            "hop": header.get("hop"),
            "project_uuid": header.get("project_uuid"),
            "from_principal": header.get("from"),
            "to_principal": header.get("to"),
            "trust": TrustLevel.UNVERIFIED.value,
            "content_hash": header.get("content_hash"),
            "actor": f"remote:{transport}",
            "ts_ms": now,
        }

        verdict = self._verifier.verify(envelope, content, now_ms=now)
        if not verdict.ok or not verdict.fingerprint or not verdict.public_key_b64:
            self._store.append_audit({**base_audit, "event": "rejected", "reason_code": f"ENVELOPE_{verdict.code}"})
            return _err("ENVELOPE_REJECTED", f"Sobre remoto rechazado ({verdict.code}).", reason=verdict.code)

        local_fp = self._local_fingerprint()
        if local_fp and verdict.fingerprint == local_fp:
            self._store.append_audit({**base_audit, "event": "rejected", "reason_code": "LOCAL_DEVICE_ENVELOPE"})
            return _err(
                "ENVELOPE_REJECTED",
                "Un sobre del dispositivo local no puede entrar por ingestión remota.",
                reason="LOCAL_DEVICE_ENVELOPE",
            )

        project_uuid = str(header.get("project_uuid") or "")
        to_uri = str(header.get("to") or "")
        target_rec = self._resolve_local_pane(to_uri, project_uuid)
        if target_rec is None:
            self._store.append_audit({**base_audit, "event": "rejected", "reason_code": "TARGET_NOT_FOUND"})
            return _err("TARGET_NOT_FOUND", "No hay ningún panel activo con ese destino en este proyecto.")

        local_hpub = self._local_human_pub()
        is_same_owner = bool(verdict.human_pub and local_hpub and verdict.human_pub == local_hpub)
        relation = NodeRelation.SAME_OWNER if is_same_owner else NodeRelation.FOREIGN
        default_policy = DEFAULT_POLICY[relation]
        clean_name = sanitize_node_name(declared_node_name) or "Nodo remoto"
        from_device = str(header.get("from_device") or "")
        device_id = from_device[7:] if from_device.startswith("device:") else from_device

        node = self._node_store.upsert_on_contact(
            device_fingerprint=verdict.fingerprint,
            device_public_key=verdict.public_key_b64,
            device_id=device_id,
            display_name=clean_name,
            relation=relation.value,
            human_pub=verdict.human_pub,
            default_policy=default_policy.value,
            now_ms=now,
        )

        policy = NodePolicy(node["policy"])
        chain_id = str(header["chain_id"])
        chain_approved = self._node_store.is_chain_approved(chain_id, verdict.fingerprint, now)
        pending_from_node = self._node_store.count_parked_for_node(verdict.fingerprint)
        decision = decide_intake(
            policy=policy,
            chain_approved=chain_approved,
            pending_from_node=pending_from_node,
        )

        computed_trust = (
            TrustLevel.DEVICE_CERTIFIED.value
            if is_same_owner
            else TrustLevel.UNVERIFIED.value
        )

        if decision.action == "drop":
            self._store.append_audit(
                {
                    **base_audit,
                    "trust": computed_trust,
                    "event": "remote_blocked",
                    "decision": "drop",
                    "reason_code": decision.reason,
                }
            )
            return _err(
                "NODE_BLOCKED",
                "El nodo remitente está bloqueado.",
                decision="drop",
                device_fingerprint=verdict.fingerprint,
            )

        state = "queued" if decision.action == "deliver" else "parked"
        state_reason = decision.reason
        quarantine_expires_at_ms = (now + QUARANTINE_TTL_MS) if state == "parked" else None

        row = {
            "msg_id": header["msg_id"],
            "project_uuid": project_uuid,
            "from_principal": header["from"],
            "to_principal": to_uri,
            "kind": header["kind"],
            "chain_id": chain_id,
            "hop": int(header["hop"]),
            "parent_msg_id": header.get("parent_msg_id"),
            "trust": computed_trust,
            "content_hash": header["content_hash"],
            "content": content,
            "envelope_json": json.dumps(envelope, sort_keys=True, ensure_ascii=False),
            "state": state,
            "state_reason": state_reason,
            "card_id": None,
            "created_at_ms": int(header["created_at"]),
            "expires_at_ms": int(header["expires_at"]),
            "updated_at_ms": now,
        }
        audit_entries: List[Dict[str, Any]] = [
            {
                **base_audit,
                "trust": computed_trust,
                "event": "remote_received",
                "decision": state,
                "reason_code": state_reason,
            }
        ]
        if state == "parked":
            audit_entries.append(
                {
                    **base_audit,
                    "trust": computed_trust,
                    "event": "quarantined",
                    "decision": "park",
                    "reason_code": state_reason,
                }
            )

        if not self._store.insert_message(row, audit_entries):
            self._store.append_audit(
                {
                    **base_audit,
                    "trust": computed_trust,
                    "event": "rejected",
                    "reason_code": "REPLAY_DETECTED",
                }
            )
            return _err("REPLAY_DETECTED", "Ese sobre remoto ya se recibió.")

        self._node_store.record_origin(
            msg_id=header["msg_id"],
            device_fingerprint=verdict.fingerprint,
            transport=str(transport),
            received_at_ms=now,
            quarantine_expires_at_ms=quarantine_expires_at_ms,
        )

        if state == "queued":
            self._relay.notify_recipient(str(target_rec.node_uuid))

        return {
            "success": True,
            "msg_id": header["msg_id"],
            "state": state,
            "state_reason": state_reason,
            "decision": decision.action,
            "device_fingerprint": verdict.fingerprint,
            "relation": node["relation"],
            "policy": node["policy"],
            "trust": computed_trust,
            "name_collision": node["name_collision"],
        }

    def get_node(self, device_fingerprint: str) -> Optional[Dict[str, Any]]:
        now = int(self._clock_ms())
        return self._node_store.get_node(device_fingerprint, now_ms=now)

    def list_nodes(self) -> List[Dict[str, Any]]:
        now = int(self._clock_ms())
        self.expire_quarantine(now)
        return self._node_store.list_nodes(now_ms=now)

    def set_policy(
        self,
        device_fingerprint: str,
        policy: str,
        *,
        actor: str = "human_ui",
    ) -> Dict[str, Any]:
        try:
            policy_enum = NodePolicy(str(policy))
        except ValueError:
            allowed = ", ".join(p.value for p in NodePolicy)
            return _err("INVALID_ARGUMENT", f"Política inválida: {policy!r}. Permitidas: {allowed}.")

        now = int(self._clock_ms())
        updated = self._node_store.set_policy(
            device_fingerprint,
            policy_enum.value,
            actor=actor,
            now_ms=now,
        )
        if updated is None:
            return _err("NODE_NOT_FOUND", "No existe ningún nodo registrado con esa huella.")

        self._store.append_audit(
            {
                "event": "node_policy_changed",
                "from_principal": "human:local",
                "to_principal": f"device:{updated['device_id']}",
                "decision": policy_enum.value,
                "reason_code": device_fingerprint,
                "actor": actor,
                "ts_ms": now,
            }
        )
        discarded = 0
        if policy_enum is NodePolicy.BLOCKED:
            discarded = self._node_store.discard_parked_for_node(
                device_fingerprint,
                self._store,
                now_ms=now,
                actor=actor,
            )
        return {"success": True, "node": updated, "discarded_count": discarded}

    def on_release(self, row: Mapping[str, Any]) -> None:
        msg_id = str(row.get("msg_id") or "")
        if not msg_id:
            return
        now = int(self._clock_ms())
        origin = self._node_store.get_origin(msg_id, now_ms=now)
        if origin is None:
            return
        fp = origin["device_fingerprint"]
        policy = origin["policy"]
        if policy == NodePolicy.ASK_EACH.value:
            chain_id = str(row.get("chain_id") or "")
            if chain_id:
                self._node_store.approve_chain(
                    chain_id,
                    fp,
                    approved_at_ms=now,
                    expires_at_ms=now + CHAIN_APPROVAL_TTL_MS,
                    approved_by="human_ui",
                )
        elif policy == NodePolicy.ASK_ONCE.value:
            self._node_store.reveal_next_queued_for_node(fp, now_ms=now)

    def expire_quarantine(self, now_ms: int) -> int:
        return int(self._node_store.expire_quarantine(self._store, int(now_ms)))

    def allows_injection(self, msg_row: Mapping[str, Any]) -> bool:
        msg_id = str(msg_row.get("msg_id") or "")
        if not msg_id:
            return False
        origin = self._node_store.get_origin(msg_id, now_ms=int(self._clock_ms()))
        if origin is None:
            return True
        return origin["policy"] == NodePolicy.DIRECT.value

    def remote_info(self, msg_id: str) -> Optional[Dict[str, Any]]:
        if not msg_id:
            return None
        origin = self._node_store.get_origin(str(msg_id), now_ms=int(self._clock_ms()))
        if origin is None:
            return None
        return {
            "display_name": origin["display_name"],
            "fingerprint_short": origin["fingerprint_short"],
            "device_fingerprint": origin["device_fingerprint"],
            "relation": origin["relation"],
            "transport": origin["transport"],
            "expires_at": origin["expires_at"],
            "name_collision": origin["name_collision"],
            "previous_name": origin["previous_name"],
            "previous_names": origin["previous_names"],
            "policy": origin["policy"],
        }
