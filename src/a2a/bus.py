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
        message_type: 'PROPOSAL', 'REASONING_REPLY', 'TASK_DISTRIBUTION', 'CHECKPOINT_COMPACT', 'RESOLUTION', 'ESCALATE_TO_JURY'.
        Calcula si la sesión requiere compactación (cada 3 turnos).
        """
        valid_types = {t.value for t in DialogueMessageType}
        if message_type.upper() not in valid_types:
            raise ValueError(f"message_type inválido: {message_type}. Use: {sorted(list(valid_types))}")

        message_uuid = str(uuid.uuid4())
        now_str = datetime.now(timezone.utc).isoformat()

        # Calcular número de mensaje en esta sesión
        existing_msgs = self.get_dialogue_session_messages(project_uuid, session_id)
        msg_seq = len(existing_msgs) + 1

        payload_dict = {
            "session_id": session_id,
            "seq": msg_seq,
            "message_type": message_type.upper(),
            "reasoning_text": reasoning_text,
            "structured_proposal": structured_proposal or {},
            "card_id": card_id,
            "timestamp": now_str
        }

        # Guardar en SQLite
        payload_str = json.dumps(payload_dict, ensure_ascii=False)
        self.db.log_a2a_message(
            project_uuid=project_uuid,
            message_uuid=message_uuid,
            sender_node_uuid=sender_node_uuid,
            intent=A2AIntent.DIALOGUE.value,
            payload_rfc_yaml=payload_str,
            card_id=card_id
        )

        # Encolar en Outbox para difusión P2P
        self.db.enqueue_outbox_event(
            project_uuid=project_uuid,
            node_uuid=sender_node_uuid,
            event_type=f"A2A_DIALOGUE_{message_type.upper()}",
            payload_raw=payload_str,
            event_uuid=message_uuid
        )

        packet = {
            "message_uuid": message_uuid,
            "project_uuid": project_uuid,
            "sender_node_uuid": sender_node_uuid,
            "intent": A2AIntent.DIALOGUE.value,
            "card_id": card_id,
            "payload": payload_dict,
            "requires_compaction": (msg_seq % 3 == 0) and (message_type.upper() != "CHECKPOINT_COMPACT")
        }
        self._dispatch_local(packet)
        return packet

    def get_dialogue_session_messages(self, project_uuid: str, session_id: str) -> List[Dict[str, Any]]:
        """Recupera la lista completa de mensajes de diálogo de una sesión."""
        messages = self.db.get_recent_a2a_messages(project_uuid=project_uuid, limit=300)
        dialogue = []
        for msg in reversed(messages):
            try:
                payload = json.loads(msg["payload_rfc_yaml"])
                if isinstance(payload, dict) and payload.get("session_id") == session_id:
                    dialogue.append({
                        "message_uuid": msg["message_uuid"],
                        "sender_node_uuid": msg["sender_node_uuid"],
                        "developer_name": msg.get("developer_name"),
                        "seq": payload.get("seq", 0),
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
    # Motor de Juicio de Honor Ponderado (2.0 Humano / 1.5 Agente)
    # ==========================================

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
        domain: Optional[str] = None
    ) -> Dict[str, Any]:
        """Abre formalmente un ticket de juicio técnico en disputa e inicializa su Matriz Neutral Anti-Sycophancy."""
        t_id = ticket_id or f"TICKET-{str(uuid.uuid4())[:8].upper()}"

        # 1. Calcular universo real de nodos y humanos elegibles (§2.1 / §3.1)
        collabs = self.db.get_project_collaborators(project_uuid)
        all_nodes = [c["node_uuid"] for c in collabs if c.get("status") == "approved"]
        if not all_nodes:
            swarm = self.db.get_swarm_nodes()
            all_nodes = [n["node_uuid"] for n in swarm] if swarm else [contender_a_node, contender_b_node]

        contenders = [c for c in [contender_a_node, contender_b_node] if c]
        universe = self.jury_engine.resolve_voter_universe(all_nodes, contenders)
        eligible_voters = universe.get("eligible_voters", [])
        eligible_count = len(eligible_voters) if eligible_voters else max(1, len(all_nodes))

        # 2. Inferencia de dominio si no se pasa explícitamente (§2.2)
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
        synthesis_text = proposal_c_ref or (
            f"Síntesis (Tercera Vía C): Integrar los puntos de mayor resiliencia de Tesis ({proposal_a_ref[:60]}) "
            f"con la simplicidad operativa de Antítesis ({proposal_b_ref[:60]}), aislando efectos secundarios mediante interfaz intermedia."
        )
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
            jury_matrix_json=json.dumps(matrix_info.get("evaluators", []), ensure_ascii=False)
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
                rationale_summary=rationale
            )
            results.append({"option": opt, **res})
        return results

    def submit_human_jury_vote(
        self,
        ticket_id: str,
        node_uuid: str,
        developer_name: str,
        voted_option: str,
        signature_ed25519: str
    ) -> Dict[str, Any]:
        """Registra el voto humano obligatorio con firma Ed25519 y peso 2.0."""
        vote_uuid = str(uuid.uuid4())
        return self.db.add_human_jury_vote(
            vote_uuid=vote_uuid,
            ticket_id=ticket_id,
            node_uuid=node_uuid,
            developer_name=developer_name,
            voted_option=voted_option,
            signature_ed25519=signature_ed25519
        )

    def compute_jury_verdict(self, project_uuid: str, ticket_id: str) -> Dict[str, Any]:
        """
        Calcula el veredicto ponderado del Juicio de Honor Dialéctico (3 Fases):
        - Gobernanza Human-Dominant: Peso humano = 2.0 vs Pool de subagentes capado a 1.5 * H (57.14% vs 42.86%).
        - Umbral de consenso >= 80% o unanimidad humana decisoria.
        - Si todos los humanos votan por una opción y todos los bots por otra, los humanos ganan siempre.
        - Persiste el dictamen en docs/okf/architecture/rulings/{ticket_id}.yaml.
        """
        summary = self.db.get_jury_votes_summary(ticket_id)
        human_votes = summary.get("human_votes", [])
        agent_scores = summary.get("agent_scores", [])
        ticket = self.db.get_dispute_ticket(ticket_id) or {}

        # Universo de votantes del proyecto
        collabs = self.db.get_project_collaborators(project_uuid)
        all_nodes = [c["node_uuid"] for c in collabs if c.get("status") == "approved"]
        if not all_nodes:
            all_nodes = [n["node_uuid"] for n in self.db.get_swarm_nodes()]
        contenders = [
            c for c in [ticket.get("contender_a_node"), ticket.get("contender_b_node")]
            if c
        ]
        voter_universe = self.jury_engine.resolve_voter_universe(all_nodes, contenders)

        # Ejecutar motor de consenso dialéctico
        consensus = self.jury_engine.compute_dialectic_consensus(
            ticket_id=ticket_id,
            project_uuid=project_uuid,
            human_votes=human_votes,
            agent_scores=agent_scores,
            all_project_nodes=all_nodes,
            contender_nodes=contenders
        )

        # Compatibilidad hacia atrás con breakdown legacy (Score(k) = Votos Humanos * 1000 + Borda * 1.5)
        options = ["OPTION_A", "OPTION_B", "THIRD_WAY_C"]
        scores_by_option: Dict[str, Dict[str, float]] = {
            opt: {"human_weight": 0.0, "agent_score_sum": 0.0, "agent_count": 0.0, "final_score": 0.0}
            for opt in options
        }
        for hv in human_votes:
            voted = hv.get("voted_option")
            if voted in scores_by_option:
                raw_w = float(hv.get("weight", 2.0))
                scores_by_option[voted]["human_weight"] += (raw_w * 500.0)
        for ag in agent_scores:
            opt = ag.get("evaluated_option")
            if opt in scores_by_option:
                scores_by_option[opt]["agent_score_sum"] += float(ag.get("weighted_total", 0.0))
                scores_by_option[opt]["agent_count"] += 1.0

        for opt, val in scores_by_option.items():
            avg_agent = (val["agent_score_sum"] / val["agent_count"]) if val["agent_count"] > 0 else 0.0
            agent_points = (avg_agent / 100.0) * 1.5
            total = val["human_weight"] + agent_points
            val["final_score"] = round(total, 4)
            c_bd = consensus["breakdown"].get(opt, {})
            val["dialectic_ratio"] = c_bd.get("consensus_ratio", 0.0)
            val["dialectic_points"] = c_bd.get("total_points", 0.0)

        winning_option = consensus["winning_option"]
        highest_score = scores_by_option.get(winning_option, {}).get("final_score", 0.0)
        tie_detected = consensus["tie_detected"]

        # Persistir dictamen YAML en OKF si el proyecto tiene gov_repo_path
        ruling_yaml_path = None
        proj = self.db.get_project_by_uuid(project_uuid)
        if proj and proj.get("gov_repo_path"):
            try:
                matrix_list = []
                if ticket.get("jury_matrix_json"):
                    matrix_list = json.loads(ticket["jury_matrix_json"])
                ruling_path = self.jury_engine.persist_ruling_yaml(
                    gov_base_path=proj["gov_repo_path"],
                    ticket=ticket,
                    verdict=consensus,
                    synthesis_data={
                        "option_id": "THIRD_WAY_C",
                        "synthesizer_agent": "subagent_synthesizer_third_way",
                        "temperature": 0.65,
                        "summary": ticket.get("proposal_c_ref", "")
                    },
                    neutral_matrix=matrix_list
                )
                ruling_yaml_path = str(ruling_path)

                from src.storage.okf_adapter import OKFAdapter
                title = (ticket.get("title") or "").strip()
                collision_reason = (ticket.get("collision_reason") or "").strip()
                commit_message = f"ratify(jury): {ticket_id} [{winning_option}] {title}"
                if collision_reason:
                    commit_message += f"\n\n{collision_reason}"
                OKFAdapter().commit_gov_file(proj["gov_repo_path"], ruling_path, commit_message)
            except Exception as e:
                logger.warning("No se pudo persistir ruling YAML para %s: %s", ticket_id, e)

        # Actualizar estado del ticket en base de datos
        self.db.resolve_dispute_ticket(
            ticket_id=ticket_id,
            winning_option=winning_option,
            final_adr_path=ruling_yaml_path
        )

        return {
            "ticket_id": ticket_id,
            "project_uuid": project_uuid,
            "winning_option": winning_option,
            "highest_score": highest_score,
            "tie_detected": tie_detected,
            "consensus_reached": consensus["consensus_reached"],
            "threshold_required": consensus["threshold_required"],
            "winning_ratio": consensus["consensus_ratio"],
            "resolution_mode": consensus["resolution_mode"],
            "effective_agent_weight": consensus["effective_agent_unit_weight"],
            "voter_universe": voter_universe,
            "ruling_yaml_path": ruling_yaml_path,
            "breakdown": scores_by_option,
            "dialectic_breakdown": consensus["breakdown"],
            "human_votes_count": len(human_votes),
            "agent_evaluations_count": len(agent_scores)
        }
