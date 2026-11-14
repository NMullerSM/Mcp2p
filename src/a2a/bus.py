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
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import msgpack

from src.a2a.jury_engine import DialecticJuryEngine
from src.storage.db import DatabaseManager

logger = logging.getLogger("mcp2p.a2a.bus")


class A2AIntent(str, Enum):
    INFORMATIVE = "INFORMATIVE"
    COORDINATION = "COORDINATION"
    SEMANTIC_COLLISION = "SEMANTIC_COLLISION"
    SUMMARY_SYNTHESIS = "SUMMARY_SYNTHESIS"
    DIALOGUE = "DIALOGUE"
    CHECKPOINT_COMPACT = "CHECKPOINT_COMPACT"


class DialogueMessageType(str, Enum):
    PROPOSAL = "PROPOSAL"
    REASONING_REPLY = "REASONING_REPLY"
    TASK_DISTRIBUTION = "TASK_DISTRIBUTION"
    CHECKPOINT_COMPACT = "CHECKPOINT_COMPACT"
    RESOLUTION = "RESOLUTION"
    ESCALATE_TO_JURY = "ESCALATE_TO_JURY"


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
        self.jury_engine = DialecticJuryEngine()
        # EGM-56: el efecto OKF/git del laudo ratificado lo inyecta la raíz de composición
        # (`OkfRulingPublisher.publish` del `DialecticTribunalService`); el bus no hace git.
        self.ruling_publisher: Optional[Callable[[str, Dict[str, Any], Dict[str, Any]], Optional[str]]] = None
        self._subscribers: Dict[str, List[Callable[[Dict[str, Any]], None]]] = {}
        self._subscribers_lock = threading.RLock()

    # ==========================================
    # Suscripciones y Despacho en Memoria
    # ==========================================

    def subscribe(self, topic: str, callback: Callable[[Dict[str, Any]], None]) -> None:
        """Registra un suscriptor a un tópico (ej. 'INFORMATIVE', 'COORDINATION', '*' o project_uuid)."""
        with self._subscribers_lock:
            if topic not in self._subscribers:
                self._subscribers[topic] = []
            if callback not in self._subscribers[topic]:
                self._subscribers[topic].append(callback)

    def unsubscribe(self, topic: str, callback: Callable[[Dict[str, Any]], None]) -> None:
        """Elimina un suscriptor registrado."""
        with self._subscribers_lock:
            if topic in self._subscribers and callback in self._subscribers[topic]:
                self._subscribers[topic].remove(callback)

    def _dispatch_local(self, event: Dict[str, Any]) -> None:
        """Despacha un evento sobre una copia segura de los suscriptores locales en memoria (EGM-28)."""
        topics = ["*", event.get("intent", ""), event.get("project_uuid", "")]
        snapshot: List[Tuple[str, Callable[[Dict[str, Any]], None]]] = []
        with self._subscribers_lock:
            for t in topics:
                if t and t in self._subscribers:
                    for cb in list(self._subscribers[t]):
                        snapshot.append((t, cb))
        for t, cb in snapshot:
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
        """Recupera la secuencia completa de turnos de una sesión dialéctica sin límite de ventana (EGM-34)."""
        messages = self.db.get_session_messages(project_uuid=project_uuid, session_id=session_id)
        turns: List[Dict[str, Any]] = []
        for msg in messages:
            try:
                payload = json.loads(msg["payload_rfc_yaml"])
                if isinstance(payload, dict) and payload.get("session_id") == session_id:
                    turns.append({
                        "message_uuid": msg["message_uuid"],
                        "sender_node_uuid": msg["sender_node_uuid"],
                        "developer_name": msg.get("developer_name"),
                        "turn": payload.get("turn", msg.get("seq", 0)),
                        "stage": payload.get("stage"),
                        "payload": payload,
                        "created_at": msg["created_at"]
                    })
            except Exception:
                continue
        turns.sort(key=lambda x: (x["turn"], x["created_at"]))
        return turns

    # ==========================================
    # Conversación Libre por Razonamiento con Compactación (CHECKPOINT_COMPACT)
    # ==========================================

    def post_dialogue_message(
        self,
        project_uuid: str,
        sender_node_uuid: str,
        session_id: str,
        message_type: str,
        reasoning_text: str,
        structured_proposal: Optional[Dict[str, Any]] = None,
        card_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Publica un mensaje dentro de una conversación dialéctica libre entre N nodos.
        Usa append_session_message en BEGIN IMMEDIATE para seq correlativo atómico (EGM-34).
        """
        valid_types = {t.value for t in DialogueMessageType}
        if message_type.upper() not in valid_types:
            raise ValueError(f"message_type inválido: {message_type}. Use: {sorted(list(valid_types))}")

        message_uuid = str(uuid.uuid4())
        now_str = datetime.now(timezone.utc).isoformat()
        msg_type_upper = message_type.upper()

        payload_dict = {
            "session_id": session_id,
            "message_type": msg_type_upper,
            "reasoning_text": reasoning_text,
            "structured_proposal": structured_proposal or {},
            "card_id": card_id,
            "timestamp": now_str
        }

        appended = self.db.append_session_message(
            project_uuid=project_uuid,
            sender_node_uuid=sender_node_uuid,
            session_id=session_id,
            intent=A2AIntent.DIALOGUE.value,
            payload_dict=payload_dict,
            card_id=card_id,
            event_type=f"A2A_DIALOGUE_{msg_type_upper}",
            message_uuid=message_uuid,
        )
        msg_seq = appended["seq"]
        final_payload = appended["payload"]

        packet = {
            "message_uuid": message_uuid,
            "project_uuid": project_uuid,
            "sender_node_uuid": sender_node_uuid,
            "intent": A2AIntent.DIALOGUE.value,
            "card_id": card_id,
            "payload": final_payload,
            "requires_compaction": (msg_seq % 3 == 0) and (msg_type_upper != "CHECKPOINT_COMPACT")
        }
        self._dispatch_local(packet)
        return packet

    def get_dialogue_session_messages(self, project_uuid: str, session_id: str) -> List[Dict[str, Any]]:
        """Recupera la lista completa de mensajes de diálogo de una sesión sin límite de ventana (EGM-34)."""
        messages = self.db.get_session_messages(project_uuid=project_uuid, session_id=session_id)
        dialogue: List[Dict[str, Any]] = []
        for msg in messages:
            try:
                payload = json.loads(msg["payload_rfc_yaml"])
                if isinstance(payload, dict) and payload.get("session_id") == session_id:
                    dialogue.append({
                        "message_uuid": msg["message_uuid"],
                        "sender_node_uuid": msg["sender_node_uuid"],
                        "developer_name": msg.get("developer_name"),
                        "seq": payload.get("seq", msg.get("seq", 0)),
                        "message_type": payload.get("message_type"),
                        "reasoning_text": payload.get("reasoning_text", ""),
                        "structured_proposal": payload.get("structured_proposal", {}),
                        "card_id": payload.get("card_id"),
                        "timestamp": payload.get("timestamp") or msg["created_at"]
                    })
            except Exception:
                continue
        dialogue.sort(key=lambda x: x["seq"])
        return dialogue

    def get_session_active_context(self, project_uuid: str, session_id: str) -> Dict[str, Any]:
        """
        Retorna el contexto activo de la conversación para alimentar el prompt de los agentes a costo mínimo.
        Si hubo un CHECKPOINT_COMPACT previo, omite los turnos antiguos y retorna solo el último checkpoint
        más los mensajes emitidos posteriormente.
        """
        all_msgs = self.get_dialogue_session_messages(project_uuid, session_id)
        if not all_msgs:
            return {"session_id": session_id, "messages": [], "is_compacted": False}

        # Buscar el último CHECKPOINT_COMPACT
        last_compact_idx = -1
        for idx, m in enumerate(all_msgs):
            if m.get("message_type") == "CHECKPOINT_COMPACT":
                last_compact_idx = idx

        if last_compact_idx >= 0:
            active_slice = all_msgs[last_compact_idx:]
            return {
                "session_id": session_id,
                "messages": active_slice,
                "is_compacted": True,
                "last_checkpoint": all_msgs[last_compact_idx]["reasoning_text"],
                "total_history_count": len(all_msgs)
            }

        return {
            "session_id": session_id,
            "messages": all_msgs,
            "is_compacted": False,
            "total_history_count": len(all_msgs)
        }

    # ==========================================
    # Motor de Juicio de Honor Ponderado (EGM-01 / EGM-02 / EGM-03 / EGM-04 / EGM-05)
    # ==========================================

    def _get_project_voter_nodes(
        self,
        project_uuid: str,
        contenders: Optional[List[str]] = None
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        """Retorna (colaboradores_totales, nodos_base_para_universo_electoral)."""
        collabs = self.db.get_project_collaborators(project_uuid)
        all_nodes = [c["node_uuid"] for c in collabs if c.get("status") == "approved"]
        if not all_nodes:
            swarm = self.db.get_swarm_nodes()
            all_nodes = [n["node_uuid"] for n in swarm] if swarm else [c for c in (contenders or []) if c]
        return collabs, all_nodes

    def eligible_humans_count(
        self,
        project_uuid: str,
        contenders: Optional[List[str]] = None,
        arbiter_node_uuid: Optional[str] = None
    ) -> int:
        """
        Calcula el universo real de humanos elegibles para un proyecto y disputa (EGM-04 / EGM-36).
        Delega exclusivamente en resolve_voter_universe().
        """
        _, all_nodes = self._get_project_voter_nodes(project_uuid, contenders=contenders)
        contenders_clean = [c for c in (contenders or []) if c]
        universe = self.jury_engine.resolve_voter_universe(
            all_nodes,
            contenders_clean,
            arbiter_node_uuid=arbiter_node_uuid
        )
        return int(universe.get("eligible_humans_count", max(1, len(universe.get("eligible_human_nodes", [])))))

    def open_dispute_ticket(
        self,
        project_uuid: str,
        title: str,
        collision_reason: str,
        contender_a_node: str,
        contender_b_node: str,
        proposal_a_ref: str,
        proposal_b_ref: str,
        card_id: Optional[str] = None,
        ticket_id: Optional[str] = None,
        proposal_c_ref: str = "",
        num_evaluators: int = 3,
        domain: Optional[str] = None,
        escalation_recommendation: Optional[str] = None
    ) -> Dict[str, Any]:
        """Abre formalmente un ticket de juicio técnico en disputa e inicializa su Matriz Neutral Anti-Sycophancy."""
        t_id = ticket_id or f"TICKET-{str(uuid.uuid4())[:8].upper()}"

        # 1. EGM-01: Seleccionar árbitro externo pre-registrado más antiguo que no sea contendiente
        arbiters = self.db.list_project_arbiters(project_uuid)
        contenders = [c for c in [contender_a_node, contender_b_node] if c]
        arbiter_node_uuid = None
        for arb in arbiters:
            a_node = arb.get("arbiter_node_uuid") or arb.get("node_uuid")
            if a_node and a_node not in contenders:
                arbiter_node_uuid = a_node
                break

        # 2. EGM-04: Conteo unificado de humanos elegibles
        eligible_count = self.eligible_humans_count(
            project_uuid=project_uuid,
            contenders=contenders,
            arbiter_node_uuid=arbiter_node_uuid
        )

        # 3. Inferencia de dominio si no se pasa explícitamente (§2.2)
        inferred_domain = domain
        if not inferred_domain:
            text_context = f"{title} {collision_reason} {proposal_a_ref} {proposal_b_ref}".lower()
            if any(w in text_context for w in ("test", "qa", "cobertura", "mock")):
                inferred_domain = "testing"
            elif any(w in text_context for w in ("ui", "frontend", "hick", "nielsen", "css", "accesib")):
                inferred_domain = "frontend"
            elif any(w in text_context for w in ("sqlite", "wal", "db", "base de datos", "acid", "migrac")):
                inferred_domain = "storage"
            elif any(w in text_context for w in ("mcp", "protocol", "contrato", "schema", "rpc")):
                inferred_domain = "mcp"
            elif any(w in text_context for w in ("segur", "crypto", "ed25519", "auth", "token")):
                inferred_domain = "security"
            elif any(w in text_context for w in ("perf", "latenc", "concurren", "throughput", "cpu", "memoria")):
                inferred_domain = "performance"

        matrix_info = self.jury_engine.generate_neutral_jury_matrix(
            eligible_humans_count=eligible_count,
            ticket_id=t_id,
            num_evaluators=num_evaluators,
            seed=t_id,
            domain=inferred_domain
        )

        # 4. EGM-06: Marcado e inspección de prompt injection (señal para auditoría humana)
        injection_suspected = False
        injection_patterns = [
            r"ignore\s+(all\s+)?(previous|prior)\s+instructions",
            r"disregard\s+(all\s+)?(previous|prior)\s+instructions",
            r"system\s*instruction",
            r"system\s*prompt",
            r"<\|im_start\|>",
            r"<\|im_end\|>",
            r"\[INST\]",
            r"\[\/INST\]",
            r"system\s*:\s*",
            r"you\s+are\s+now\b",
            r"<!--",
            r"<\/untrusted",
        ]
        combined_text = f"{title}\n{collision_reason}\n{proposal_a_ref}\n{proposal_b_ref}"
        for pat in injection_patterns:
            if re.search(pat, combined_text, re.IGNORECASE):
                injection_suspected = True
                break

        # 5. NEW-04: Síntesis C no se fabrica con plantilla; queda vacía si no fue provista
        synthesis_text = (proposal_c_ref or "").strip()

        return self.db.create_dispute_ticket(
            ticket_id=t_id,
            project_uuid=project_uuid,
            title=title,
            collision_reason=collision_reason,
            contender_a_node=contender_a_node,
            contender_b_node=contender_b_node,
            proposal_a_ref=proposal_a_ref,
            proposal_b_ref=proposal_b_ref,
            card_id=card_id,
            proposal_c_ref=synthesis_text,
            jury_matrix_json=json.dumps(matrix_info.get("evaluators", []), ensure_ascii=False),
            escalation_recommendation=escalation_recommendation,
            arbiter_node_uuid=arbiter_node_uuid,
            injection_suspected=injection_suspected
        )

    def submit_agent_borda_evaluation(
        self,
        ticket_id: str,
        juror_agent_id: str,
        scores_per_option: Dict[str, Dict[str, float]],
        rationale: str = ""
    ) -> List[Dict[str, Any]]:
        """
        Registra la evaluación técnica de un agente jurado (0 a 100 en KISS, Perf, Sec, UX, Test).
        scores_per_option = {
            'OPTION_A': {'kiss': 80, 'performance': 90, 'security': 85, 'ux_hicks': 70, 'testability': 80},
            'OPTION_B': {'kiss': 60, ...}
        }
        """
        results = []
        for opt, scores in scores_per_option.items():
            vote_uuid = str(uuid.uuid4())
            res = self.db.add_jury_borda_score(
                vote_uuid=vote_uuid,
                ticket_id=ticket_id,
                juror_id=juror_agent_id,
                evaluated_option=opt,
                score_kiss=float(scores.get("kiss", 70)),
                score_performance=float(scores.get("performance", 70)),
                score_security=float(scores.get("security", 70)),
                score_ux_hicks=float(scores.get("ux_hicks", 70)),
                score_testability=float(scores.get("testability", 70)),
                rationale_summary=(scores.get("rationale") or scores.get("rationale_summary") or rationale)
            )
            results.append({"option": opt, **res})
        return results

    def submit_human_jury_vote(
        self,
        ticket_id: str,
        node_uuid: str,
        developer_name: str,
        voted_option: str,
        signature_ed25519: str,
        public_key: str = "",
        signed_at: str = "",
        content_sha256: str = "",
        project_uuid: str = ""
    ) -> Dict[str, Any]:
        """
        Registra el voto humano obligatorio con autenticación Ed25519 estricta (EGM-05-R / EGM-36).
        - Exige firma Ed25519 no vacía (code=401).
        - Delega exclusión de contendientes/árbitro en resolve_voter_universe (code=403).
        - Exige colaborador aprobado en project_collaborators con public_key registrada (code=403).
        - Rechaza si caller envía public_key distinta a la registrada en BD (code=403).
        - Verifica la firma exclusivamente contra collab['public_key'] registrada en BD (code=401).
        """
        if not isinstance(signature_ed25519, str) or not signature_ed25519.strip():
            return {"success": False, "error": "Firma Ed25519 requerida (signature_ed25519 vacía)", "code": 401}

        ticket = self.db.get_dispute_ticket(ticket_id) or {}
        p_uuid = project_uuid or ticket.get("project_uuid", "")

        contenders = [c for c in [ticket.get("contender_a_node"), ticket.get("contender_b_node")] if c]
        arbiter = (ticket.get("arbiter_node_uuid") or "").strip() or None

        collabs, all_nodes = self._get_project_voter_nodes(p_uuid, contenders=contenders)
        universe = self.jury_engine.resolve_voter_universe(
            all_nodes,
            contenders,
            arbiter_node_uuid=arbiter
        )

        # Exclusión unificada vía resolve_voter_universe (EGM-36)
        if node_uuid in universe.get("excluded_nodes", []):
            return {"success": False, "error": f"Nodo {node_uuid} es contendiente en disputa N>=3", "code": 403}

        if arbiter and node_uuid == arbiter:
            return {"success": False, "error": f"Nodo {node_uuid} es el árbitro designado", "code": 403}

        # Exigir colaborador aprobado con public_key registrada en BD (EGM-05-RESIDUAL)
        collab = next((c for c in collabs if c.get("node_uuid") == node_uuid), None)
        if not collab or collab.get("status") != "approved":
            return {
                "success": False,
                "error": f"Nodo {node_uuid} no es un colaborador aprobado del proyecto",
                "code": 403
            }

        registered_pubkey = (collab.get("public_key") or "").strip()
        if not registered_pubkey:
            return {
                "success": False,
                "error": f"Colaborador {node_uuid} no tiene public_key Ed25519 registrada en BD",
                "code": 403
            }

        if isinstance(public_key, str) and public_key.strip() and public_key.strip() != registered_pubkey:
            return {
                "success": False,
                "error": f"La clave pública enviada no coincide con la public_key registrada para {node_uuid}",
                "code": 401
            }

        from src.p2p.crypto import NodeCryptoManager
        import hashlib
        content_text = f"VOTE:{voted_option}:TICKET:{ticket_id}:PROJECT:{p_uuid}"
        sha = hashlib.sha256(content_text.encode("utf-8")).hexdigest()
        sig_block = {
            "artifact_id": ticket_id,
            "content_sha256": content_sha256 or sha,
            "node_uuid": node_uuid,
            "developer_name": developer_name,
            "decision": voted_option,
            "signed_at": signed_at or datetime.now(timezone.utc).isoformat(),
            "public_key": registered_pubkey,
            "signature_ed25519": signature_ed25519
        }
        if not NodeCryptoManager.verify_artifact_signature(content_text, sig_block):
            legacy_content = f"VOTE:{voted_option}:TICKET:{ticket_id}"
            if not NodeCryptoManager.verify_artifact_signature(legacy_content, sig_block):
                return {"success": False, "error": "Firma Ed25519 inválida o adulterada", "code": 401}

        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            self.db.record_vote_delivery(ticket_id, node_uuid, now_iso)
        except Exception:
            pass

        vote_uuid = str(uuid.uuid4())
        try:
            res = self.db.add_human_jury_vote(
                vote_uuid=vote_uuid,
                ticket_id=ticket_id,
                node_uuid=node_uuid,
                developer_name=developer_name,
                voted_option=voted_option,
                signature_ed25519=signature_ed25519,
                weight=2.0,
                public_key=registered_pubkey,
                signed_at=signed_at or now_iso,
                content_sha256=content_sha256 or sha,
                project_uuid=p_uuid
            )
        except sqlite3.IntegrityError:
            return {"success": False, "error": f"Nodo {node_uuid} ya registró un voto para {ticket_id}", "code": 409}
        return {"success": True, "vote_uuid": vote_uuid, **res}

    def _persist_and_commit_ruling(
        self,
        project_uuid: str,
        ticket: Dict[str, Any],
        consensus: Dict[str, Any]
    ) -> Optional[str]:
        """
        Publica el laudo ratificado delegando en `ruling_publisher` (EGM-56).

        Sin publicador inyectado (un `A2ABus(db)` suelto, fuera de `ApplicationFactory`) se
        conserva el comportamiento histórico construyendo el publicador del núcleo sobre
        `OKFAdapter`; el código de git/OKF ya no vive aquí.
        """
        publisher = self.ruling_publisher
        if publisher is None:
            try:
                from src.core.services.tribunal_ruling_publisher import OkfRulingPublisher
                from src.storage.okf_adapter import OKFAdapter

                publisher = OkfRulingPublisher(
                    okf_port=OKFAdapter(),
                    project_lookup=self.db.get_project_by_uuid,
                    jury_engine=self.jury_engine,
                ).publish
            except Exception as e:
                logger.warning("No se pudo persistir ruling YAML para %s: %s", ticket.get("ticket_id"), e)
                return None
        return publisher(project_uuid, ticket, consensus)

    def submit_external_arbiter_ruling(
        self,
        ticket_id: str,
        arbiter_node_uuid: str,
        winning_option: str,
        signature_ed25519: str,
        rationale: str = "",
        public_key: str = "",
        signed_at: str = "",
        content_sha256: str = "",
        project_uuid: str = "",
        developer_name: str = ""
    ) -> Dict[str, Any]:
        """
        Registra y ratifica el laudo vinculante de un árbitro externo pre-registrado (EGM-01-R).
        Verifica firma Ed25519 exclusivamente contra la public_key registrada en BD.
        """
        valid_options = {"OPTION_A", "OPTION_B", "THIRD_WAY_C", "ASYMMETRIC_HYBRID"}
        opt_norm = (winning_option or "").strip().upper()
        if opt_norm not in valid_options:
            return {"success": False, "error": f"winning_option inválida: {winning_option}", "code": 400}

        if not isinstance(signature_ed25519, str) or not signature_ed25519.strip():
            return {"success": False, "error": "Firma Ed25519 del árbitro requerida", "code": 401}

        ticket = self.db.get_dispute_ticket(ticket_id)
        if not ticket:
            return {"success": False, "error": f"Ticket de disputa {ticket_id} no encontrado", "code": 404}

        p_uuid = project_uuid or ticket.get("project_uuid", "")
        contenders = [c for c in [ticket.get("contender_a_node"), ticket.get("contender_b_node")] if c]
        if arbiter_node_uuid in contenders:
            return {"success": False, "error": f"Un contendiente ({arbiter_node_uuid}) no puede arbitrar su propia disputa", "code": 403}

        ticket_arbiter = (ticket.get("arbiter_node_uuid") or "").strip()
        if ticket_arbiter and ticket_arbiter != arbiter_node_uuid:
            return {"success": False, "error": f"Nodo {arbiter_node_uuid} no es el árbitro designado del ticket ({ticket_arbiter})", "code": 403}

        registered_arbiters = {
            (a.get("arbiter_node_uuid") or a.get("node_uuid"))
            for a in self.db.list_project_arbiters(p_uuid)
        }
        if arbiter_node_uuid not in registered_arbiters:
            return {"success": False, "error": f"Nodo {arbiter_node_uuid} no está registrado como árbitro en project_arbiters", "code": 403}

        collab = self.db.get_collaborator(p_uuid, arbiter_node_uuid)
        if collab and collab.get("status") != "approved":
            return {"success": False, "error": f"Árbitro {arbiter_node_uuid} no está aprobado en el proyecto", "code": 403}

        node_info = self.db.get_node_by_uuid(arbiter_node_uuid)
        registered_pubkey = (
            (collab.get("public_key") if collab else "")
            or (node_info.get("public_key") if node_info else "")
            or ""
        ).strip()
        if not registered_pubkey:
            return {"success": False, "error": f"Árbitro {arbiter_node_uuid} sin public_key Ed25519 registrada en BD", "code": 403}

        if isinstance(public_key, str) and public_key.strip() and public_key.strip() != registered_pubkey:
            return {"success": False, "error": "public_key del árbitro no coincide con la registrada en BD", "code": 401}

        from src.p2p.crypto import NodeCryptoManager
        import hashlib
        eff_dev_name = developer_name or (node_info.get("developer_name") if node_info else "") or arbiter_node_uuid
        content_candidates = [
            f"ARBITER_RULING:{opt_norm}:TICKET:{ticket_id}:PROJECT:{p_uuid}",
            f"ARBITER_RULING:{opt_norm}:TICKET:{ticket_id}",
            f"RULING:{opt_norm}:TICKET:{ticket_id}:PROJECT:{p_uuid}",
        ]
        verified = False
        for c_text in content_candidates:
            sha = hashlib.sha256(c_text.encode("utf-8")).hexdigest()
            sig_block = {
                "artifact_id": ticket_id,
                "content_sha256": content_sha256 or sha,
                "node_uuid": arbiter_node_uuid,
                "developer_name": eff_dev_name,
                "decision": opt_norm,
                "signed_at": signed_at or datetime.now(timezone.utc).isoformat(),
                "public_key": registered_pubkey,
                "signature_ed25519": signature_ed25519,
            }
            if NodeCryptoManager.verify_artifact_signature(c_text, sig_block):
                verified = True
                break
        if not verified:
            return {"success": False, "error": "Firma Ed25519 del árbitro inválida o adulterada", "code": 401}

        _, all_nodes = self._get_project_voter_nodes(p_uuid, contenders=contenders)
        voter_universe = self.jury_engine.resolve_voter_universe(
            all_nodes,
            contenders,
            arbiter_node_uuid=arbiter_node_uuid
        )
        verdict = self.jury_engine.build_arbiter_verdict(
            ticket_id=ticket_id,
            project_uuid=p_uuid,
            winning_option=opt_norm,
            arbiter_node_uuid=arbiter_node_uuid,
            voter_universe=voter_universe,
        )
        ruling_yaml_path = self._persist_and_commit_ruling(p_uuid, ticket, verdict)
        self.db.update_dispute_status(
            ticket_id=ticket_id,
            status="ratified",
            resolution_mode="external_arbiter_ruling",
            winning_option=opt_norm,
            final_adr_path=ruling_yaml_path,
        )
        self._dispatch_local({
            "message_uuid": str(uuid.uuid4()),
            "project_uuid": p_uuid,
            "sender_node_uuid": arbiter_node_uuid,
            "intent": A2AIntent.SUMMARY_SYNTHESIS.value,
            "card_id": ticket.get("card_id"),
            "payload": {
                "ticket_id": ticket_id,
                "status": "ratified",
                "resolution_mode": "external_arbiter_ruling",
                "winning_option": opt_norm,
                "rationale": rationale,
            },
        })
        return {
            "success": True,
            "ticket_id": ticket_id,
            "project_uuid": p_uuid,
            "status": "ratified",
            "resolution_mode": "external_arbiter_ruling",
            "winning_option": opt_norm,
            "arbiter_node_uuid": arbiter_node_uuid,
            "rationale": rationale,
            "ruling_yaml_path": ruling_yaml_path,
            "consensus_reached": True,
        }

    def compute_jury_verdict(self, project_uuid: str, ticket_id: str) -> Dict[str, Any]:
        """
        Calcula el veredicto ponderado continuo del Juicio de Honor Dialéctico (EGM-01/02/03):
        - R_efectivo = 0.70·h_O + 0.30·a_O con umbral de ratificación 0.80.
        - Persiste dictamen YAML y commitea OKF SOLO si status == 'ratified' (vía `ruling_publisher`).
        - Actualiza el estado del ticket en base de datos al status del veredicto.

        Ruta legacy: el núcleo usa `DialecticTribunalService.compute_verdict`, que orquesta el
        mismo efecto sobre `evaluate_jury_verdict` (EGM-56).
        """
        result, ticket, consensus = self.evaluate_jury_verdict(project_uuid, ticket_id)
        ruling_yaml_path = None
        if result["status"] == "ratified":
            ruling_yaml_path = self._persist_and_commit_ruling(project_uuid, ticket, consensus)
        self.db.update_dispute_status(
            ticket_id=ticket_id,
            status=result["status"],
            resolution_mode=result["resolution_mode"],
            winning_option=result["winning_option"],
            final_adr_path=ruling_yaml_path
        )
        result["ruling_yaml_path"] = ruling_yaml_path
        return result

    def evaluate_jury_verdict(
        self, project_uuid: str, ticket_id: str
    ) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
        """
        Cálculo PURO del veredicto (solo lecturas): devuelve (resultado, ticket, consenso) sin
        escribir el estado del ticket ni tocar OKF/git (EGM-56).
        """
        summary = self.db.get_jury_votes_summary(ticket_id)
        human_votes = summary.get("human_votes", [])
        agent_scores = summary.get("agent_scores", [])
        ticket = self.db.get_dispute_ticket(ticket_id) or {}

        contenders = [
            c for c in [ticket.get("contender_a_node"), ticket.get("contender_b_node")]
            if c
        ]
        _, all_nodes = self._get_project_voter_nodes(project_uuid, contenders=contenders)
        arbiter_node_uuid = ticket.get("arbiter_node_uuid")
        voter_universe = self.jury_engine.resolve_voter_universe(
            all_nodes,
            contenders,
            arbiter_node_uuid=arbiter_node_uuid
        )

        # Verificar si el quórum de votación está cerrado
        deliveries = self.db.get_vote_deliveries(ticket_id)
        quorum_closed = self.jury_engine.is_quorum_closed(
            ticket=ticket,
            human_votes=human_votes,
            deliveries=deliveries,
            eligible_human_nodes=voter_universe["eligible_human_nodes"]
        )

        # Ejecutar motor de consenso dialéctico
        consensus = self.jury_engine.compute_dialectic_consensus(
            ticket_id=ticket_id,
            project_uuid=project_uuid,
            human_votes=human_votes,
            agent_scores=agent_scores,
            all_project_nodes=all_nodes,
            contender_nodes=contenders,
            arbiter_node_uuid=arbiter_node_uuid,
            quorum_closed=quorum_closed
        )

        winning_option = consensus["winning_option"]
        breakdown = consensus["breakdown"]
        highest_score = breakdown[winning_option]["final_score"]
        status = consensus["status"]
        resolution_mode = consensus["resolution_mode"]
        tie_detected = consensus["tie_detected"]

        # El dictamen YAML (solo si 'ratified', NEW-02) y el estado del ticket los escribe quien
        # orquesta: `compute_jury_verdict` (legacy) o `DialecticTribunalService.compute_verdict`.
        result = {
            "ticket_id": ticket_id,
            "project_uuid": project_uuid,
            "winning_option": winning_option,
            "highest_score": highest_score,
            "tie_detected": tie_detected,
            "consensus_reached": consensus["consensus_reached"],
            "threshold_required": consensus["threshold_required"],
            "winning_ratio": consensus["consensus_ratio"],
            "status": status,
            "resolution_mode": resolution_mode,
            "effective_agent_weight": consensus.get("effective_agent_unit_weight", 1.5),
            "voter_universe": voter_universe,
            "ruling_yaml_path": None,
            "breakdown": breakdown,
            "dialectic_breakdown": breakdown,
            "human_votes_count": len(human_votes),
            "agent_evaluations_count": len(agent_scores)
        }
        return result, ticket, consensus
