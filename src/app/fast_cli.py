"""
MCP2P Fast CLI Connector (`mcp2p fast <acción>`)
Subcomando ligero despachado tempranamente en src/app/main.py antes de importar
ApiBridge, PyWebView o WebRTC/aiortc (EGM-44 / RF-STM-04).

Aplica estrictamente las mismas reglas de gobernanza que MCPToolsHandler y el
CoreKernel con ActorContext(kind="fast_cli"), impidiendo transiciones reservadas
al humano Ed25519 (EGM-35).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

AGENT_ALLOWED_STATUSES = ("backlog", "in_progress", "in_review", "disputed")
HUMAN_ONLY_STATUSES = ("merged", "rejected", "discarded")


def _default_node_uuid() -> str:
    try:
        from src.app.identity import local_node_uuid
        return local_node_uuid()
    except ImportError:
        return os.environ.get("MCP2P_NODE_UUID", f"node_{platform.node().lower()[:12]}")


def _open_db_and_kernel(db_path: Optional[str]):
    """Abre DatabaseManager (y CoreKernel/ApplicationFactory si está disponible) sin cargar UI ni P2P."""
    if db_path:
        os.environ["MCP2P_DB_PATH"] = str(db_path)
    try:
        from src.core.factory import ApplicationFactory
        kernel = ApplicationFactory.create_fast_cli_kernel(db_path=db_path)
        db = getattr(kernel, "db", None)
        if db is None:
            from src.storage.db import DatabaseManager
            db = DatabaseManager.get_instance(db_path) if db_path else DatabaseManager.get_instance()
        return db, kernel
    except (ImportError, AttributeError):
        from src.storage.db import DatabaseManager
        db = DatabaseManager.get_instance(db_path) if db_path else DatabaseManager.get_instance()
        return db, None


def _resolve_project_uuid(db: Any, project_uuid: Optional[str]) -> Optional[str]:
    if project_uuid:
        return project_uuid
    projects = db.list_projects()
    if projects:
        return projects[0]["project_uuid"]
    return None


def _print_json(payload: Dict[str, Any]) -> int:
    sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return 0 if payload.get("success") else 1


def _handle_kanban_list(db: Any, kernel: Any, args: argparse.Namespace) -> Dict[str, Any]:
    p_uuid = _resolve_project_uuid(db, args.project)
    if not p_uuid:
        return {"success": False, "error": "No hay proyectos registrados."}
    cards = db.get_cards(project_uuid=p_uuid, status=args.status)
    return {
        "success": True,
        "project_uuid": p_uuid,
        "status_filter": args.status or "all",
        "count": len(cards),
        "cards": cards,
    }


def _handle_kanban_claim(db: Any, kernel: Any, args: argparse.Namespace) -> Dict[str, Any]:
    p_uuid = _resolve_project_uuid(db, args.project)
    if not p_uuid:
        return {"success": False, "error": "No hay proyectos registrados."}
    node_uuid = args.node or _default_node_uuid()
    if kernel is not None and hasattr(kernel, "kanban"):
        try:
            from src.core.domain.context import ActorContext
            actor = ActorContext(kind="fast_cli", node_uuid=node_uuid)
            return kernel.kanban.claim_task(
                actor=actor,
                project_uuid=p_uuid,
                card_id=args.card_id,
                duration_minutes=args.minutes,
            )
        except Exception as exc:
            code = "LEASE_COLLISION" if isinstance(exc, PermissionError) else "CLAIM_ERROR"
            return {"success": False, "code": code, "error": str(exc)}
    try:
        card = db.claim_card_lease(
            project_uuid=p_uuid,
            card_id=args.card_id,
            node_uuid=node_uuid,
            duration_minutes=args.minutes,
        )
        return {"success": True, "card": card, "lease_expires_at": card.get("lease_expires_at")}
    except PermissionError as pe:
        return {"success": False, "code": "LEASE_COLLISION", "error": str(pe)}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _handle_kanban_release(db: Any, kernel: Any, args: argparse.Namespace) -> Dict[str, Any]:
    p_uuid = _resolve_project_uuid(db, args.project)
    if not p_uuid:
        return {"success": False, "error": "No hay proyectos registrados."}
    node_uuid = args.node or _default_node_uuid()
    target_status = args.status

    if target_status in HUMAN_ONLY_STATUSES:
        return {
            "success": False,
            "code": "HUMAN_SIGNATURE_REQUIRED",
            "error": (
                f"EGM-35: La transición a '{target_status}' está prohibida por fast CLI "
                "y requiere firma humana Ed25519 en la UI HITL."
            ),
        }
    if target_status not in AGENT_ALLOWED_STATUSES:
        return {
            "success": False,
            "error": f"Estado inválido '{target_status}'. Permitidos: {list(AGENT_ALLOWED_STATUSES)}",
        }

    if kernel is not None and hasattr(kernel, "kanban"):
        try:
            from src.core.domain.context import ActorContext
            actor = ActorContext(kind="fast_cli", node_uuid=node_uuid)
            return kernel.kanban.release_or_transition(
                actor=actor,
                project_uuid=p_uuid,
                card_id=args.card_id,
                target_status=target_status,
                branch_ref=args.branch,
            )
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    card = db.get_card_by_id(p_uuid, args.card_id)
    if not card:
        return {"success": False, "error": f"Tarjeta {args.card_id} no encontrada."}
    if card.get("assignee_node_uuid") and card.get("assignee_node_uuid") != node_uuid and target_status != "disputed":
        return {
            "success": False,
            "code": "PERMISSION_DENIED",
            "error": f"La tarea está asignada a {card.get('assignee_node_uuid')}, no a {node_uuid}.",
        }
    updated = db.update_card_status(
        project_uuid=p_uuid,
        card_id=args.card_id,
        new_status=target_status,
        branch_ref=args.branch,
    )
    return {"success": True, "card": updated}


def _handle_decision_promote(db: Any, kernel: Any, args: argparse.Namespace) -> Dict[str, Any]:
    if args.state == "approved":
        return {
            "success": False,
            "code": "HUMAN_SIGNATURE_REQUIRED",
            "error": "EGM-35: Promover una decisión a 'approved' requiere firma humana Ed25519 en HITL.",
        }
    if args.state not in ("discarded", "superseded"):
        return {"success": False, "error": f"Estado inválido '{args.state}'. Permitidos: ['discarded', 'superseded']"}
    p_uuid = _resolve_project_uuid(db, args.project)
    if not p_uuid:
        return {"success": False, "error": "No hay proyectos registrados."}
    proj = db.get_project_by_uuid(p_uuid)
    if not proj:
        return {"success": False, "error": f"Proyecto '{p_uuid}' no encontrado."}
    from src.storage.okf_adapter import OKFAdapter
    okf = OKFAdapter()
    promoted = okf.move_decision_lifecycle(
        gov_base_path=Path(proj["gov_repo_path"]),
        dec_id=args.decision_id,
        target_state=args.state,
        reason_or_consequences=args.notes or "",
        signature_block=None,
    )
    return {"success": True, "promoted_path": str(promoted)}


def _handle_a2a_poll(db: Any, kernel: Any, args: argparse.Namespace) -> Dict[str, Any]:
    p_uuid = _resolve_project_uuid(db, args.project)
    if not p_uuid:
        return {"success": False, "error": "No hay proyectos registrados."}
    msgs = db.get_recent_a2a_messages(p_uuid, limit=args.limit)
    return {"success": True, "project_uuid": p_uuid, "count": len(msgs), "messages": msgs}


def _handle_a2a_send(db: Any, kernel: Any, args: argparse.Namespace) -> Dict[str, Any]:
    p_uuid = _resolve_project_uuid(db, args.project)
    if not p_uuid:
        return {"success": False, "error": "No hay proyectos registrados."}
    node_uuid = args.node or _default_node_uuid()
    try:
        payload = json.loads(args.payload_json) if args.payload_json else {}
    except json.JSONDecodeError as exc:
        return {"success": False, "error": f"JSON inválido en --payload-json: {exc}"}
    from src.a2a.bus import A2ABus
    bus = A2ABus(db=db)
    res = bus.send_signal(
        project_uuid=p_uuid,
        sender_node_uuid=node_uuid,
        intent=args.intent,
        payload=payload,
        card_id=args.card_id,
    )
    return {"success": True, "result": res}


def _handle_status(db: Any, kernel: Any, args: argparse.Namespace) -> Dict[str, Any]:
    projects = db.list_projects()
    return {
        "success": True,
        "mode": "fast_cli",
        "db_path": str(db.db_path),
        "node_uuid": _default_node_uuid(),
        "projects_count": len(projects),
        "kernel_attached": kernel is not None,
    }


def _normalize_shorthand_argv(argv: List[str]) -> List[str]:
    """Convierte alias compactos (card-list, card-claim, card-release, card-move, a2a-poll, status)
    al formato jerárquico canónico."""
    if not argv:
        return ["status"]

    cmd = argv[0]
    tail = argv[1:]
    mapping = {
        "card-list": ["kanban", "list"],
        "card-claim": ["kanban", "claim"],
        "card-release": ["kanban", "release"],
        "card-move": ["kanban", "release"],
        "a2a-poll": ["a2a", "poll"],
        "project-list": ["projects"],
    }
    if cmd in mapping:
        # Soportar `card-move CARD-1 merged` posicional opcional
        if cmd == "card-move" and len(tail) >= 2 and not tail[0].startswith("-") and not tail[1].startswith("-"):
            tail = [tail[0], "--status", tail[1], *tail[2:]]
        return mapping[cmd] + tail
    if cmd == "hub" and tail and tail[0] == "status":
        return ["status"] + tail[1:]
    return list(argv)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mcp2p fast")
    sub = parser.add_subparsers(dest="domain", required=True)

    p_status = sub.add_parser("status", help="Estado rápido del nodo/BD")
    p_status.set_defaults(_handler=_handle_status)

    p_proj = sub.add_parser("projects", help="Listar proyectos")
    p_proj.set_defaults(_handler=lambda db, k, a: {"success": True, "projects": db.list_projects()})

    # kanban
    p_kanban = sub.add_parser("kanban", help="Operaciones Kanban")
    k_sub = p_kanban.add_subparsers(dest="action", required=True)

    pk_list = k_sub.add_parser("list")
    pk_list.add_argument("--status", type=str, default=None)
    pk_list.set_defaults(_handler=_handle_kanban_list)

    pk_claim = k_sub.add_parser("claim")
    pk_claim.add_argument("card_id", type=str)
    pk_claim.add_argument("--minutes", type=int, default=30)
    pk_claim.set_defaults(_handler=_handle_kanban_claim)

    pk_rel = k_sub.add_parser("release")
    pk_rel.add_argument("card_id", type=str)
    pk_rel.add_argument("--status", type=str, default="in_review")
    pk_rel.add_argument("--branch", type=str, default=None)
    pk_rel.set_defaults(_handler=_handle_kanban_release)

    # decision
    p_dec = sub.add_parser("decision", help="Operaciones de decisiones OKF")
    d_sub = p_dec.add_subparsers(dest="action", required=True)
    pd_prom = d_sub.add_parser("promote")
    pd_prom.add_argument("decision_id", type=str)
    pd_prom.add_argument("--state", type=str, required=True)
    pd_prom.add_argument("--notes", type=str, default="")
    pd_prom.set_defaults(_handler=_handle_decision_promote)

    # a2a
    p_a2a = sub.add_parser("a2a", help="Bus A2A rápido")
    a_sub = p_a2a.add_subparsers(dest="action", required=True)

    pa_poll = a_sub.add_parser("poll")
    pa_poll.add_argument("--limit", type=int, default=10)
    pa_poll.set_defaults(_handler=_handle_a2a_poll)

    pa_send = a_sub.add_parser("send")
    pa_send.add_argument("--intent", type=str, default="INFORMATIVE")
    pa_send.add_argument("--payload-json", type=str, default="{}")
    pa_send.add_argument("--card-id", type=str, default=None)
    pa_send.set_defaults(_handler=_handle_a2a_send)

    return parser


def run_fast_cli(argv: Optional[List[str]] = None) -> int:
    raw_argv = list(sys.argv[2:] if argv is None else argv)

    # Extraer flags globales desde cualquier posición (--db-path, --project, --node)
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--db-path", type=str, default=None)
    pre.add_argument("--project", type=str, default=None)
    pre.add_argument("--node", type=str, default=None)
    g_args, remaining = pre.parse_known_args(raw_argv)

    normalized = _normalize_shorthand_argv(remaining)
    parser = build_parser()
    try:
        args = parser.parse_args(normalized)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2

    args.db_path = g_args.db_path
    args.project = g_args.project
    args.node = g_args.node

    db, kernel = _open_db_and_kernel(args.db_path)
    try:
        res = args._handler(db, kernel, args)
        return _print_json(res)
    except Exception as exc:
        return _print_json({"success": False, "error": str(exc)})
    finally:
        if kernel is not None and hasattr(kernel, "close"):
            try:
                kernel.close()
            except Exception:
                pass
