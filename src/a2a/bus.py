"""
Bus de Comunicación Agente a Agente (A2A) Híbrido para MCP2P.

Combina dos canales complementarios:
1. Canal INFORMATIVO (0 tokens): Mensajes binarios MessagePack ultrarrápidos para
   latidos, leases, cambios de estado en Kanban y telemetría de sincronización.
2. Canal de COORDINACIÓN (RFC dialéctico denso): Protocolo estructurado de 3 turnos
   (PROPOSAL -> COUNTER_PROPOSAL -> RESOLUTION) para decisiones de arquitectura y PRs.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

import msgpack

from src.storage.db import DatabaseManager

logger = logging.getLogger("mcp2p.a2a.bus")


class A2AIntent(str, Enum):
    INFORMATIVE = "INFORMATIVE"
    COORDINATION = "COORDINATION"
    SEMANTIC_COLLISION = "SEMANTIC_COLLISION"
    SUMMARY_SYNTHESIS = "SUMMARY_SYNTHESIS"


class CoordinationTurn(int, Enum):
    PROPOSAL = 1
    COUNTER_PROPOSAL = 2
    RESOLUTION = 3


class A2ABus:
    """
    Bus de mensajería híbrido A2A con persistencia SQLite WAL y cola Outbox.
    """

    def __init__(self, db: Optional[DatabaseManager] = None) -> None:
        self.db = db or DatabaseManager.get_instance()
        self._subscribers: Dict[str, List[Callable[[Dict[str, Any]], None]]] = {}

    # ==========================================
    # Suscripciones y Despacho en Memoria
    # ==========================================

    def subscribe(self, topic: str, callback: Callable[[Dict[str, Any]], None]) -> None:
        """Registra un suscriptor a un tópico (ej. 'INFORMATIVE', 'COORDINATION', '*' o project_uuid)."""
        if topic not in self._subscribers:
            self._subscribers[topic] = []
        self._subscribers[topic].append(callback)

    def unsubscribe(self, topic: str, callback: Callable[[Dict[str, Any]], None]) -> None:
        """Elimina un suscriptor registrado."""
        if topic in self._subscribers and callback in self._subscribers[topic]:
            self._subscribers[topic].remove(callback)

    def _dispatch_local(self, event: Dict[str, Any]) -> None:
        """Despacha un evento a los suscriptores locales en memoria."""
        topics = ["*", event.get("intent", ""), event.get("project_uuid", "")]
        for t in topics:
            if t in self._subscribers:
                for cb in self._subscribers[t]:
                    try:
                        cb(event)
                    except Exception as exc:
                        logger.warning(f"Error en callback de suscriptor A2A para {t}: {exc}")

    # ==========================================
    # Canal INFORMATIVO (0 Tokens con MessagePack)
    # ==========================================

    @staticmethod
    def pack_binary(data: Dict[str, Any]) -> bytes:
        """Serializa un diccionario a binario MessagePack a costo cero de tokens."""
        return msgpack.packb(data, use_bin_type=True)

    @staticmethod
    def unpack_binary(raw_bytes: bytes) -> Dict[str, Any]:
        """Deserializa un paquete binario MessagePack a diccionario Python."""
        return msgpack.unpackb(raw_bytes, raw=False)

    def send_informative(
        self,
        project_uuid: str,
        sender_node_uuid: str,
        signal_type: str,
        data: Dict[str, Any],
        card_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Envía una señal operativa (heartbeat, lease, cambio de estado) a 0 tokens.
        Registra el mensaje en SQLite y lo encola en outbox_events.
        """
        message_uuid = str(uuid.uuid4())
        now_str = datetime.now(timezone.utc).isoformat()

        packet = {
            "message_uuid": message_uuid,
            "project_uuid": project_uuid,
            "sender_node_uuid": sender_node_uuid,
            "intent": A2AIntent.INFORMATIVE.value,
            "signal_type": signal_type,
            "data": data,
            "card_id": card_id,
            "timestamp": now_str
        }

        # Serialización binaria MessagePack para transporte P2P
        binary_payload = self.pack_binary(packet)

        # Registro persistente en SQLite
        payload_str = json.dumps({
            "signal_type": signal_type,
            "data": data,
            "timestamp": now_str,
            "binary_size_bytes": len(binary_payload)
        })

        self.db.log_a2a_message(
            project_uuid=project_uuid,
            message_uuid=message_uuid,
            sender_node_uuid=sender_node_uuid,
            intent=A2AIntent.INFORMATIVE.value,
            payload_rfc_yaml=payload_str,
            card_id=card_id
        )

        # Encolado en outbox para sincronización P2P
        self.db.enqueue_outbox_event(
            project_uuid=project_uuid,
            node_uuid=sender_node_uuid,
            event_type=f"A2A_INFORMATIVE_{signal_type.upper()}",
            payload_raw=payload_str,
            event_uuid=message_uuid
        )

        self._dispatch_local(packet)
        return packet

    # ==========================================
    # Canal de COORDINACIÓN (RFC Dialéctico 3 Turnos)
    # ==========================================

    def send_proposal(
        self,
        project_uuid: str,
        sender_node_uuid: str,
        session_id: str,
        component: str,
        intent: str,
        rationale: str,
        diff_ref: str,
        impact: str,
        card_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Turno 1 del RFC Dialéctico: Propuesta formal de cambio o diseño.
        """
        message_uuid = str(uuid.uuid4())
        now_str = datetime.now(timezone.utc).isoformat()

        proposal_payload = {
            "session_id": session_id,
            "turn": CoordinationTurn.PROPOSAL.value,
            "stage": "PROPOSAL",
            "component": component,
            "intent": intent,
            "rationale": rationale,
            "diff_ref": diff_ref,
            "impact": impact,
            "timestamp": now_str
        }

        payload_str = json.dumps(proposal_payload)

        self.db.log_a2a_message(
            project_uuid=project_uuid,
            message_uuid=message_uuid,
            sender_node_uuid=sender_node_uuid,
            intent=A2AIntent.COORDINATION.value,
            payload_rfc_yaml=payload_str,
            card_id=card_id
        )

        self.db.enqueue_outbox_event(
            project_uuid=project_uuid,
            node_uuid=sender_node_uuid,
            event_type="A2A_COORDINATION_PROPOSAL",
            payload_raw=payload_str,
            event_uuid=message_uuid
        )

        packet = {
            "message_uuid": message_uuid,
            "project_uuid": project_uuid,
            "sender_node_uuid": sender_node_uuid,
            "intent": A2AIntent.COORDINATION.value,
            "card_id": card_id,
            "payload": proposal_payload
        }
        self._dispatch_local(packet)
        return packet

    def send_counter_proposal(
        self,
        project_uuid: str,
        sender_node_uuid: str,
        session_id: str,
        critique: str,
        alternative: Dict[str, Any],
        verdict_signal: str,
        card_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Turno 2 del RFC Dialéctico: Contra-propuesta, evaluación o refactorización.
        verdict_signal debe ser 'ACCEPT', 'REJECT' o 'COMPROMISE'.
        """
        if verdict_signal not in ("ACCEPT", "REJECT", "COMPROMISE"):
            raise ValueError(f"verdict_signal inválido: {verdict_signal}. Use ACCEPT, REJECT o COMPROMISE.")

        # Validar existencia de Turno 1 previo
        history = self.get_session_turns(project_uuid, session_id)
        if not any(h.get("turn") == CoordinationTurn.PROPOSAL.value for h in history):
            raise ValueError(f"No existe un Turno 1 (PROPOSAL) previo para la sesión {session_id}.")

        message_uuid = str(uuid.uuid4())
        now_str = datetime.now(timezone.utc).isoformat()

        counter_payload = {
            "session_id": session_id,
            "turn": CoordinationTurn.COUNTER_PROPOSAL.value,
            "stage": "COUNTER_PROPOSAL",
            "critique": critique,
            "alternative": alternative,
            "verdict_signal": verdict_signal,
            "timestamp": now_str
        }

        payload_str = json.dumps(counter_payload)

        self.db.log_a2a_message(
            project_uuid=project_uuid,
            message_uuid=message_uuid,
            sender_node_uuid=sender_node_uuid,
            intent=A2AIntent.COORDINATION.value,
            payload_rfc_yaml=payload_str,
            card_id=card_id
        )

        self.db.enqueue_outbox_event(
            project_uuid=project_uuid,
            node_uuid=sender_node_uuid,
            event_type="A2A_COORDINATION_COUNTER_PROPOSAL",
            payload_raw=payload_str,
            event_uuid=message_uuid
        )

        packet = {
            "message_uuid": message_uuid,
            "project_uuid": project_uuid,
            "sender_node_uuid": sender_node_uuid,
            "intent": A2AIntent.COORDINATION.value,
            "card_id": card_id,
            "payload": counter_payload
        }
        self._dispatch_local(packet)
        return packet

    def send_resolution(
        self,
        project_uuid: str,
        sender_node_uuid: str,
        session_id: str,
        final_decision: str,
        synthesis_notes: str,
        agreed_branch: Optional[str] = None,
        card_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Turno 3 del RFC Dialéctico: Resolución final ('AGREED' o 'ESCALATE_TO_JURY').
        Si es ESCALATE_TO_JURY, emite un aviso para convocar al tribunal Borda.
        """
        if final_decision not in ("AGREED", "ESCALATE_TO_JURY"):
            raise ValueError(f"final_decision inválido: {final_decision}. Use AGREED o ESCALATE_TO_JURY.")

        # Validar existencia de Turnos 1 y 2
        history = self.get_session_turns(project_uuid, session_id)
        has_turn_1 = any(h.get("turn") == CoordinationTurn.PROPOSAL.value for h in history)
        has_turn_2 = any(h.get("turn") == CoordinationTurn.COUNTER_PROPOSAL.value for h in history)

        if not (has_turn_1 and has_turn_2):
            raise ValueError(
                f"La resolución requiere haber completado Turno 1 y Turno 2 (Sesión: {session_id})."
            )

        message_uuid = str(uuid.uuid4())
        now_str = datetime.now(timezone.utc).isoformat()

        resolution_payload = {
            "session_id": session_id,
            "turn": CoordinationTurn.RESOLUTION.value,
            "stage": "RESOLUTION",
            "final_decision": final_decision,
            "synthesis_notes": synthesis_notes,
            "agreed_branch": agreed_branch,
            "timestamp": now_str
        }

        payload_str = json.dumps(resolution_payload)

        self.db.log_a2a_message(
            project_uuid=project_uuid,
            message_uuid=message_uuid,
            sender_node_uuid=sender_node_uuid,
            intent=A2AIntent.COORDINATION.value,
            payload_rfc_yaml=payload_str,
            card_id=card_id
        )

        self.db.enqueue_outbox_event(
            project_uuid=project_uuid,
            node_uuid=sender_node_uuid,
            event_type="A2A_COORDINATION_RESOLUTION",
            payload_raw=payload_str,
            event_uuid=message_uuid
        )

        packet = {
            "message_uuid": message_uuid,
            "project_uuid": project_uuid,
            "sender_node_uuid": sender_node_uuid,
            "intent": A2AIntent.COORDINATION.value,
            "card_id": card_id,
            "payload": resolution_payload
        }
        self._dispatch_local(packet)
        return packet

    def get_session_turns(self, project_uuid: str, session_id: str) -> List[Dict[str, Any]]:
        """Recupera la secuencia de turnos de una sesión dialéctica específica en orden cronológico y de turno."""
        messages = self.db.get_recent_a2a_messages(project_uuid=project_uuid, limit=200)
        turns = []
        for msg in reversed(messages):  # Invertir el DESC para orden cronológico ascendente
            try:
                payload = json.loads(msg["payload_rfc_yaml"])
                if isinstance(payload, dict) and payload.get("session_id") == session_id:
                    turns.append({
                        "message_uuid": msg["message_uuid"],
                        "sender_node_uuid": msg["sender_node_uuid"],
                        "developer_name": msg.get("developer_name"),
                        "turn": payload.get("turn", 0),
                        "stage": payload.get("stage"),
                        "payload": payload,
                        "created_at": msg["created_at"]
                    })
            except Exception:
                continue
        # Ordenar garantizado por número de turno
        turns.sort(key=lambda x: x["turn"])
        return turns
