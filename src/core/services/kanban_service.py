"""
Servicio de Gobernanza Kanban con aplicación estricta de invariantes HITL (EGM-35).

Es la ÚNICA implementación de "qué transiciones requieren firma humana" del sistema
(EGM-35 / EGM-49). Los adaptadores primarios
(`ApiBridge`/UI, `MCPToolsHandler`, `fast_cli`) construyen un `ActorContext` y delegan
aquí; ninguno debe reimplementar la lista de estados reservados.

Reglas:
- Transiciones a estados terminales (`HUMAN_ONLY_CARD_STATES`) y promoción de decisiones
  a `HUMAN_ONLY_DECISION_STATES` solo para `actor.kind == 'human_ui'`.
- Un actor no humano no puede mutar `status` por la vía de edición genérica
  (`update_task`/`upsert_task`): debe usar el flujo de *lease* (`claim_lease`/`release_lease`).
- Un actor no humano no puede "blanquear" un estado reservado hidratándolo desde YAML OKF
  (`reconcile_from_okf`, EGM-49).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.domain.context import ActorContext, DomainEvent
from src.core.events import DomainEventBus
from src.core.ports.connectors import OkfGovernancePort
from src.core.ports.repositories import KanbanRepositoryPort, ProjectRepositoryPort

HUMAN_ONLY_CARD_STATES = frozenset({"merged", "rejected", "discarded", "consolidated"})
HUMAN_ONLY_DECISION_STATES = frozenset({"approved", "rejected"})

# Estados a los que un actor no humano puede llevar una tarjeta mediante release_lease.
AGENT_RELEASE_STATES: tuple[str, ...] = ("backlog", "in_progress", "in_review", "disputed")
# Destinos reconocidos por release_lease (los terminales solo para human_ui).
RELEASE_TARGET_STATES: tuple[str, ...] = AGENT_RELEASE_STATES + ("merged", "rejected", "discarded")
# Destinos reconocidos para la promoción de decisiones OKF.
DECISION_TARGET_STATES: tuple[str, ...] = ("approved", "discarded", "superseded")


class _HumanOnlyStatusGuardDb:
    """
    Proxy de `DatabaseManager` usado por `reconcile_from_okf` para actores no humanos (EGM-49).

    Intercepta `upsert_card_from_okf`: si el YAML intenta llevar una tarjeta a un estado
    reservado al humano que SQLite no tiene ya, la escritura se omite y se registra.
    El resto de atributos se delegan al `DatabaseManager` real.
    """

    def __init__(self, db: Any, kanban_repo: KanbanRepositoryPort) -> None:
        self._db = db
        self._kanban_repo = kanban_repo
        self.blocked: List[Dict[str, Any]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._db, name)

    def upsert_card_from_okf(self, project_uuid: str, card_id: str, title: str, status: str, **kwargs: Any) -> Any:
        if status in HUMAN_ONLY_CARD_STATES:
            current = self._kanban_repo.get_card_by_id(project_uuid, card_id)
            current_status = current.get("status") if current else None
            if current_status != status:
                self.blocked.append(
                    {"card_id": card_id, "yaml_status": status, "current_status": current_status}
                )
                return current or {}
        return self._db.upsert_card_from_okf(
            project_uuid=project_uuid, card_id=card_id, title=title, status=status, **kwargs
        )


class KanbanGovernanceService:
    """Casos de uso Kanban y promoción de decisiones bajo reglas HITL."""

    def __init__(
        self,
        kanban_repo: KanbanRepositoryPort,
        project_repo: ProjectRepositoryPort,
        okf_port: Optional[OkfGovernancePort] = None,
        event_bus: Optional[DomainEventBus] = None,
        tribunal_service: Optional[Any] = None,
    ) -> None:
        self.kanban_repo = kanban_repo
        self.project_repo = project_repo
        self.okf_port = okf_port
        self.event_bus = event_bus
        # Opcional: permite que release_lease(disputed) abra el ticket del Tribunal
        # automáticamente (evita disputas huérfanas sin arbitraje) por cualquier adaptador.
        self.tribunal_service = tribunal_service

    def _emit(self, event_type: str, project_uuid: str, payload: Dict[str, Any]) -> None:
        if self.event_bus is not None:
            self.event_bus.publish(DomainEvent(event_type=event_type, project_uuid=project_uuid, payload=payload))

    @staticmethod
    def _human_required_error(action_desc: str, allowed: Optional[tuple[str, ...]] = None) -> Dict[str, Any]:
        msg = (
            f"EGM-35: Operación '{action_desc}' bloqueada: requiere actor humano autenticado "
            "(actor.kind == 'human_ui') con firma Ed25519 en la UI HITL."
        )
        if allowed:
            msg += f" Estados permitidos para agentes: {list(allowed)}."
        return {"success": False, "code": "HUMAN_SIGNATURE_REQUIRED", "error": msg}

    @staticmethod
    def _lease_workflow_error() -> Dict[str, Any]:
        return {
            "success": False,
            "code": "USE_LEASE_WORKFLOW",
            "error": (
                "EGM-35: No se permite cambiar 'status' directamente por la vía de edición "
                "para actores no humanos. Use el flujo de lease/review (claim/release) y firma HITL."
            ),
        }

    # ------------------------------------------------------------------
    # Lecturas
    # ------------------------------------------------------------------

    def get_board(self, project_uuid: str, status: Optional[str] = None) -> Dict[str, Any]:
        """Obtiene las tarjetas Kanban de un proyecto agrupadas y en lista."""
        cards = self.kanban_repo.get_cards(project_uuid, status=status)
        return {"success": True, "project_uuid": project_uuid, "cards": cards, "count": len(cards)}

    def get_card(self, project_uuid: str, card_id: str) -> Optional[Dict[str, Any]]:
        return self.kanban_repo.get_card_by_id(project_uuid, card_id)

    # ------------------------------------------------------------------
    # Escrituras gobernadas
    # ------------------------------------------------------------------

    def create_task(
        self,
        project_uuid: str,
        card_id: str,
        title: str,
        description: str = "",
        acceptance_criteria: str = "",
        assignee_node_uuid: Optional[str] = None,
        actor: Optional[ActorContext] = None,
    ) -> Dict[str, Any]:
        """Crea una tarjeta Kanban en backlog."""
        card = self.kanban_repo.create_card(
            project_uuid=project_uuid,
            card_id=card_id,
            title=title,
            description=description,
            acceptance_criteria=acceptance_criteria,
            assignee_node_uuid=assignee_node_uuid,
        )
        self._emit(
            "kanban.card_created",
            project_uuid,
            {
                "card_id": card_id,
                "actor_kind": actor.kind if actor else "system",
                "actor_node": actor.node_uuid if actor else "",
                "assignee_node_uuid": card.get("assignee_node_uuid") or "",
            },
        )
        return {"success": True, "card": card}

    def update_task(
        self,
        project_uuid: str,
        card_id: str,
        actor: ActorContext,
        title: Optional[str] = None,
        description: Optional[str] = None,
        acceptance_criteria: Optional[str] = None,
        status: Optional[str] = None,
        branch_ref: Optional[str] = None,
        assignee_node_uuid: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Actualiza metadatos de tarjeta.

        Un actor no humano no puede mutar `status` por esta vía (USE_LEASE_WORKFLOW);
        el humano puede, incluidos los estados terminales.
        """
        if status is not None and not actor.is_human_ui():
            return self._lease_workflow_error()
        if self.kanban_repo.get_card_by_id(project_uuid, card_id) is None:
            return {
                "success": False,
                "error": f"Tarjeta '{card_id}' no encontrada en el proyecto '{project_uuid}'.",
            }
        card = self.kanban_repo.update_card_details(
            project_uuid=project_uuid,
            card_id=card_id,
            title=title,
            description=description,
            acceptance_criteria=acceptance_criteria,
            status=status,
            branch_ref=branch_ref,
            assignee_node_uuid=assignee_node_uuid,
        )
        self._emit(
            "kanban.card_updated",
            project_uuid,
            {
                "card_id": card_id,
                "status": card.get("status"),
                "actor_node": actor.node_uuid,
                "assignee_node_uuid": card.get("assignee_node_uuid") or "",
            },
        )
        return {"success": True, "card": card}

    def upsert_task(
        self,
        project_uuid: str,
        card_id: Optional[str],
        actor: ActorContext,
        title: Optional[str] = None,
        description: Optional[str] = None,
        acceptance_criteria: Optional[str] = None,
        branch_ref: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Crea la tarjeta si no existe o actualiza sus metadatos si existe (micro-script OKF
        `update_card`). Misma regla de `status` que `update_task`.
        """
        if status is not None and not actor.is_human_ui():
            return self._lease_workflow_error()
        if not card_id:
            return {"success": False, "error": "card_data debe contener un identificador 'card_id' o 'id'."}
        existing = self.kanban_repo.get_card_by_id(project_uuid, card_id)
        if existing:
            return self.update_task(
                project_uuid=project_uuid,
                card_id=card_id,
                actor=actor,
                title=title,
                description=description,
                acceptance_criteria=acceptance_criteria,
                status=status,
                branch_ref=branch_ref,
            )
        res = self.create_task(
            project_uuid=project_uuid,
            card_id=card_id,
            title=title or "Sin título",
            description=description or "",
            acceptance_criteria=acceptance_criteria or "",
            actor=actor,
        )
        res["created"] = True
        return res

    def claim_lease(
        self,
        project_uuid: str,
        card_id: str,
        actor: ActorContext,
        duration_minutes: int = 30,
    ) -> Dict[str, Any]:
        """
        Adquiere atómicamente el lease de una tarjeta para el actor.

        Lanza `PermissionError` si otro nodo mantiene un lease vigente (los adaptadores
        lo traducen a `code: LEASE_COLLISION`).
        """
        card = self.kanban_repo.claim_card_lease(
            project_uuid=project_uuid,
            card_id=card_id,
            node_uuid=actor.node_uuid,
            duration_minutes=duration_minutes,
        )
        self._emit(
            "kanban.lease_claimed",
            project_uuid,
            {"card_id": card_id, "node_uuid": actor.node_uuid, "actor_node": actor.node_uuid},
        )
        return {"success": True, "card": card}

    def release_lease(
        self,
        project_uuid: str,
        card_id: str,
        actor: ActorContext,
        target_status: str = "in_review",
        branch_ref: Optional[str] = None,
        *,
        dispute_reason: Optional[str] = None,
        contender_node_uuid: Optional[str] = None,
        auto_open_ticket: bool = True,
    ) -> Dict[str, Any]:
        """
        Libera/transiciona una tarjeta con lease (atómico en `BEGIN IMMEDIATE`, EGM-38).

        Orden de validación (contrato MCP/CLI):
        1. `target_status` reconocido;
        2. la tarjeta existe;
        3. solo el titular puede transicionar, salvo para escalar a `disputed` (PERMISSION_DENIED);
        4. estados terminales solo para `human_ui` (HUMAN_SIGNATURE_REQUIRED, EGM-35).
        Si el destino es `disputed` y hay `tribunal_service`, abre (o reutiliza) el ticket.
        """
        if target_status not in RELEASE_TARGET_STATES:
            return {
                "success": False,
                "error": (
                    f"target_status inválido: '{target_status}'. "
                    f"Valores permitidos: {list(AGENT_RELEASE_STATES)}"
                ),
            }

        card = self.kanban_repo.get_card_by_id(project_uuid, card_id)
        if not card:
            return {"success": False, "error": f"Tarjeta {card_id} no encontrada"}

        assignee = card.get("assignee_node_uuid")
        if assignee and assignee != actor.node_uuid and target_status != "disputed":
            return {
                "success": False,
                "code": "PERMISSION_DENIED",
                "error": (
                    f"La tarea está asignada al nodo {assignee}, no a {actor.node_uuid}. "
                    f"Solo el titular puede transicionar a '{target_status}', salvo para escalar a 'disputed'."
                ),
            }

        if target_status in HUMAN_ONLY_CARD_STATES and not actor.is_human_ui():
            return self._human_required_error(
                f"release_lease(target_status={target_status})", allowed=AGENT_RELEASE_STATES
            )

        try:
            updated = self.kanban_repo.release_card_lease(
                project_uuid=project_uuid,
                card_id=card_id,
                node_uuid=actor.node_uuid,
                target_status=target_status,
                branch_ref=branch_ref,
            )
        except PermissionError as pe:
            # Carrera: otro nodo tomó el lease entre la pre-validación y la transacción.
            return {"success": False, "code": "PERMISSION_DENIED", "error": str(pe)}

        self._emit(
            "kanban.lease_released",
            project_uuid,
            {
                "card_id": card_id,
                "status": target_status,
                "node_uuid": actor.node_uuid,
                "actor_node": actor.node_uuid,
            },
        )

        result: Dict[str, Any] = {"success": True, "card": updated}
        if target_status == "disputed" and auto_open_ticket and self.tribunal_service is not None:
            result["dispute_ticket"] = self._ensure_dispute_ticket(
                project_uuid=project_uuid,
                card=card,
                card_id=card_id,
                reporter_node=actor.node_uuid,
                branch_ref=branch_ref,
                dispute_reason=dispute_reason,
                contender_node_uuid=contender_node_uuid,
            )
        return result

    def _ensure_dispute_ticket(
        self,
        *,
        project_uuid: str,
        card: Dict[str, Any],
        card_id: str,
        reporter_node: str,
        branch_ref: Optional[str],
        dispute_reason: Optional[str],
        contender_node_uuid: Optional[str],
    ) -> Dict[str, Any]:
        """Reutiliza el ticket abierto de la tarjeta o abre uno nuevo en el Tribunal."""
        existing = self.tribunal_service.get_dispute_ticket_by_card(project_uuid, card_id)
        if existing and existing.get("status") == "open":
            return existing
        reason = dispute_reason or f"Tarea {card_id} escalada a 'disputed' por el revisor {reporter_node}"
        return self.tribunal_service.open_dispute(
            project_uuid=project_uuid,
            title=f"Disputa en {card_id}: {card.get('title', 'Tarea')}",
            collision_reason=reason,
            contender_a_node=card.get("assignee_node_uuid") or "unassigned",
            contender_b_node=contender_node_uuid or reporter_node,
            proposal_a_ref=branch_ref or card.get("branch_ref") or f"branch_{card_id}",
            proposal_b_ref=f"review_dispute_{card_id}_{reporter_node}",
            card_id=card_id,
        )

    def transition_card_status(
        self,
        project_uuid: str,
        card_id: str,
        new_status: str,
        actor: ActorContext,
        branch_ref: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Transiciona el estado de una tarjeta aplicando el guardrail HITL (EGM-35)."""
        if new_status in HUMAN_ONLY_CARD_STATES and not actor.is_human_ui():
            return self._human_required_error(f"transition_card_status({new_status})")

        card = self.kanban_repo.update_card_status(
            project_uuid=project_uuid,
            card_id=card_id,
            new_status=new_status,
            branch_ref=branch_ref,
        )
        self._emit(
            "kanban.status_changed",
            project_uuid,
            {"card_id": card_id, "status": new_status, "actor_node": actor.node_uuid},
        )
        return {"success": True, "card": card}

    def promote_decision(
        self,
        project_uuid: str,
        decision_id: str,
        target_state: str,
        actor: ActorContext,
        signature_block: Optional[Dict[str, Any]] = None,
        reason: str = "",
    ) -> Dict[str, Any]:
        """
        Promueve una decisión arquitectónica OKF.

        Bloquea `HUMAN_ONLY_DECISION_STATES` si `actor.kind != 'human_ui'` (EGM-35) y descarta
        cualquier `signature_block` aportado por un actor no humano.
        Devuelve `{"success": True, "promoted_path": str}` o un error estructurado.
        """
        if not decision_id:
            return {"success": False, "error": "promote_decision requiere el campo obligatorio 'decision_id'."}
        normalized_state = (target_state or "").strip().lower()
        if normalized_state not in DECISION_TARGET_STATES:
            return {
                "success": False,
                "error": (
                    "target_state es requerido en promote_decision y debe ser uno de: "
                    f"{list(DECISION_TARGET_STATES)}. Recibido: '{target_state}'"
                ),
            }
        if normalized_state in HUMAN_ONLY_DECISION_STATES and not actor.is_human_ui():
            return self._human_required_error(f"promote_decision(target_state={normalized_state})")

        proj = self.project_repo.get_project_by_uuid(project_uuid)
        if not proj or not proj.get("gov_repo_path"):
            return {"success": False, "error": f"Proyecto {project_uuid} sin repositorio OKF configurado"}

        if self.okf_port is None:
            return {"success": False, "error": "OkfGovernancePort no conectado al núcleo"}

        effective_signature = signature_block if actor.is_human_ui() else None
        try:
            promoted = self.okf_port.move_decision_lifecycle(
                gov_base_path=Path(proj["gov_repo_path"]),
                dec_id=decision_id,
                target_state=normalized_state,
                reason_or_consequences=reason or "",
                signature_block=effective_signature,
            )
        except (FileNotFoundError, ValueError) as exc:
            return {"success": False, "error": str(exc)}

        self._emit(
            "okf.decision_promoted",
            project_uuid,
            {"decision_id": decision_id, "target_state": normalized_state, "actor_node": actor.node_uuid},
        )
        return {"success": True, "promoted_path": str(promoted)}

    def reconcile_from_okf(self, project_uuid: str, actor: ActorContext, db: Any) -> Dict[str, Any]:
        """
        Hidrata SQLite desde los YAML Kanban del repositorio OKF.

        Para actores no humanos (EGM-49) se bloquea cualquier escalada hacia un estado
        reservado al humano que SQLite no tenga ya: un agente no puede escribir
        `status: merged` en un YAML y "blanquearlo" con reconcile.
        """
        proj = self.project_repo.get_project_by_uuid(project_uuid)
        if not proj or not proj.get("gov_repo_path"):
            return {"success": False, "error": f"Proyecto {project_uuid} sin repositorio OKF configurado"}
        if self.okf_port is None:
            return {"success": False, "error": "OkfGovernancePort no conectado al núcleo"}

        target_db = db if actor.is_human_ui() else _HumanOnlyStatusGuardDb(db, self.kanban_repo)
        rec = self.okf_port.reconcile_kanban_from_okf(Path(proj["gov_repo_path"]), project_uuid, target_db)
        result: Dict[str, Any] = {"success": True, "reconciled": rec}
        if isinstance(target_db, _HumanOnlyStatusGuardDb) and target_db.blocked:
            rec = dict(rec) if isinstance(rec, dict) else {"result": rec}
            rec["blocked_human_only_transitions"] = target_db.blocked
            result["reconciled"] = rec
        self._emit(
            "kanban.reconciled_from_okf",
            project_uuid,
            {"actor_kind": actor.kind, "blocked": len(getattr(target_db, "blocked", []) or [])},
        )
        return result
