"""
MCP2P Fast CLI Connector (`mcp2p fast <acción>`)
Subcomando ligero despachado tempranamente en src/app/main.py antes de importar
ApiBridge, PyWebView o WebRTC/aiortc (EGM-44 / RF-STM-04).

Adaptador primario fino sobre el CoreKernel (Tarea 2.3/2.4 de `23_`, ver `24_`):
construye un `ActorContext(kind="fast_cli")` y delega TODA la política de gobernanza
(lease, titularidad, estados reservados al humano EGM-35, ticket automático en
'disputed', promoción de decisiones) en `KanbanGovernanceService`. Este módulo no
define ninguna lista propia de estados reservados.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional


def _default_node_uuid() -> str:
    from src.app.identity import local_node_uuid
    return local_node_uuid()


def _cli_actor(args: argparse.Namespace) -> Any:
    from src.core.domain.context import ACTOR_FAST_CLI, ActorContext
    return ActorContext(kind=ACTOR_FAST_CLI, node_uuid=args.node or _default_node_uuid())


def _open_db_and_kernel(db_path: Optional[str]):
    """Abre el CoreKernel ligero (SQLite + repositorios + servicios) sin cargar UI, crypto ni P2P."""
    if db_path:
        os.environ["MCP2P_DB_PATH"] = str(db_path)
    from src.core.factory import ApplicationFactory
    kernel = ApplicationFactory.create_fast_cli_kernel(db_path=db_path)
    return kernel.db, kernel


def _ensure_okf_port(kernel: Any) -> None:
    """Enchufa el OKFAdapter de forma perezosa solo para los subcomandos que lo necesitan."""
    from src.core.kernel import CoreKernel
    if kernel.okf_adapter is None:
        from src.storage.okf_adapter import OKFAdapter
        kernel.register_connector(CoreKernel.PORT_OKF, OKFAdapter())


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
    board = kernel.kanban.get_board(p_uuid, status=args.status)
    return {
        "success": True,
        "project_uuid": p_uuid,
        "status_filter": args.status or "all",
        "count": board["count"],
        "cards": board["cards"],
    }


def _handle_kanban_claim(db: Any, kernel: Any, args: argparse.Namespace) -> Dict[str, Any]:
    p_uuid = _resolve_project_uuid(db, args.project)
    if not p_uuid:
        return {"success": False, "error": "No hay proyectos registrados."}
    try:
        res = kernel.kanban.claim_lease(
            project_uuid=p_uuid,
            card_id=args.card_id,
            actor=_cli_actor(args),
            duration_minutes=args.minutes,
        )
    except PermissionError as pe:
        return {"success": False, "code": "LEASE_COLLISION", "error": str(pe)}
    except Exception as exc:
        return {"success": False, "code": "CLAIM_ERROR", "error": str(exc)}
    res["lease_expires_at"] = (res.get("card") or {}).get("lease_expires_at")
    return res


def _handle_kanban_release(db: Any, kernel: Any, args: argparse.Namespace) -> Dict[str, Any]:
    p_uuid = _resolve_project_uuid(db, args.project)
    if not p_uuid:
        return {"success": False, "error": "No hay proyectos registrados."}
    try:
        return kernel.kanban.release_lease(
            project_uuid=p_uuid,
            card_id=args.card_id,
            actor=_cli_actor(args),
            target_status=args.status,
            branch_ref=args.branch,
        )
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _handle_decision_promote(db: Any, kernel: Any, args: argparse.Namespace) -> Dict[str, Any]:
    p_uuid = _resolve_project_uuid(db, args.project)
    if not p_uuid:
        return {"success": False, "error": "No hay proyectos registrados."}
    _ensure_okf_port(kernel)
    return kernel.kanban.promote_decision(
        project_uuid=p_uuid,
        decision_id=args.decision_id,
        target_state=args.state,
        actor=_cli_actor(args),
        signature_block=None,
        reason=args.notes or "",
    )


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
    res = kernel.bus.send_signal(
        project_uuid=p_uuid,
        sender_node_uuid=node_uuid,
        intent=args.intent,
        payload=payload,
        card_id=args.card_id,
    )
    return {"success": True, "result": res}


def _portability_db_path(args: argparse.Namespace) -> str:
    from src.storage.db import resolve_db_path
    return str(resolve_db_path(args.db_path))


def _default_identity_key_paths() -> List[str]:
    base = os.path.join(os.path.expanduser("~"), ".mcp2p")
    return [os.path.join(base, "node_identity.key"), os.path.join(base, "node_identity.key.enc")]


def _handle_projects_export(args: argparse.Namespace) -> Dict[str, Any]:
    """`mcp2p fast projects export PAQUETE.zip`: copia consistente de la base + repos elegidos."""
    from src.app.version import __version__
    from src.core.services.project_portability_service import PortabilityError, ProjectPortabilityService
    try:
        result = ProjectPortabilityService().export_projects(
            _portability_db_path(args),
            args.archive,
            workspace_root=args.workspace_root,
            include_repos=args.include_repos,
            project_uuids=[args.project] if args.project else None,
            include_secrets=args.include_secrets,
            identity_key_paths=_default_identity_key_paths() if args.include_secrets else None,
            overwrite=args.overwrite,
            app_version=__version__,
        )
    except PortabilityError as exc:
        return exc.to_dict()
    if result.get("secrets_warning"):
        sys.stderr.write(str(result["secrets_warning"]) + "\n")
    return result


def _handle_projects_import(args: argparse.Namespace) -> Dict[str, Any]:
    """`mcp2p fast projects import PAQUETE.zip --workspace-root NUEVA_RAIZ`: reescribe rutas absolutas."""
    from src.core.services.project_portability_service import PortabilityError, ProjectPortabilityService
    try:
        return ProjectPortabilityService().import_projects(
            args.archive,
            _portability_db_path(args),
            args.workspace_root,
            merge=args.merge,
            force=args.force,
            include_secrets=args.include_secrets,
        )
    except PortabilityError as exc:
        return exc.to_dict()


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

    p_proj = sub.add_parser("projects", help="Listar, exportar o importar proyectos")
    p_proj.set_defaults(_handler=lambda db, k, a: {"success": True, "projects": db.list_projects()})
    pj_sub = p_proj.add_subparsers(dest="projects_action", required=False)

    pj_export = pj_sub.add_parser("export", help="Exportar proyectos a un paquete .zip portable")
    pj_export.add_argument("archive", type=str, help="Ruta del .zip a crear")
    pj_export.add_argument("--workspace-root", type=str, default=None,
                           help="Raiz de workspace de origen (por defecto <carpeta de la base>/workspace)")
    pj_export.add_argument("--include-repos", action="store_true",
                           help="Incluir los repositorios de gobernanza/codigo que viven bajo la raiz de workspace")
    pj_export.add_argument("--include-secrets", action="store_true",
                           help="Incluir PAT y clave de identidad (EXCLUIDOS por defecto)")
    pj_export.add_argument("--overwrite", action="store_true", help="Sobrescribir el .zip si ya existe")

    pj_import = pj_sub.add_parser("import", help="Importar un paquete .zip reescribiendo rutas de workspace")
    pj_import.add_argument("archive", type=str, help="Ruta del .zip a importar")
    pj_import.add_argument("--workspace-root", type=str, required=True,
                           help="Nueva raiz de workspace (ruta absoluta) en esta maquina")
    pj_import.add_argument("--merge", action="store_true",
                           help="Agregar a una base existente (omite proyectos con el mismo project_uuid)")
    pj_import.add_argument("--force", action="store_true",
                           help="Reemplazar la base existente (con copia .bak) o, con --merge, pisar proyectos repetidos")
    pj_import.add_argument("--include-secrets", action="store_true",
                           help="Importar tambien PAT y clave de identidad si el paquete los trae")

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

    # Exportar/importar trabajan sobre el archivo de la base (sqlite3 directo): no se abre el kernel,
    # que crearia la base de destino antes de poder decidir si ya existia.
    if args.domain == "projects" and getattr(args, "projects_action", None) in ("export", "import"):
        handler = _handle_projects_export if args.projects_action == "export" else _handle_projects_import
        try:
            return _print_json(handler(args))
        except Exception as exc:
            return _print_json({"success": False, "code": "PORTABILITY_ERROR", "error": str(exc)})

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
