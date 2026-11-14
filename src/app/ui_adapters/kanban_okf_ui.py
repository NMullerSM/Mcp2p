"""
Sub-adaptador UI para Tablero Kanban, telemetría A2A y gestión/bootstrap OKF.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.app.identity import local_node_uuid
from src.app.ui_adapters import _BaseUIAdapter
from src.core.domain.context import ActorContext


class KanbanOkfUIAdapter(_BaseUIAdapter):
    """Gestiona tarjetas Kanban (con tags YAML OKF), feeds A2A y gobernanza OKF."""

    def _enrich_cards_with_okf_tags(
        self, target_uuid: Optional[str], cards: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        if not target_uuid or not cards:
            return cards
        proj = self.db.get_project_by_uuid(target_uuid)
        gov_path = proj.get("gov_repo_path") if proj else None
        tags_by_card: Dict[str, List[str]] = {}
        if gov_path and Path(gov_path).exists():
            try:
                idx = self.okf.get_tags_index(gov_path, rebuild=False)
                for item in idx.get("items", []):
                    p = str(item.get("path", "")).replace("\\", "/")
                    if "kanban/cards/" in p:
                        tags_by_card[Path(p).stem] = list(item.get("tags", []))
            except Exception:
                pass
        for card in cards:
            cid = card.get("card_id", "")
            if cid in tags_by_card:
                card["tags"] = tags_by_card[cid]
            else:
                card.setdefault("tags", [])
        return cards

    # ==========================
    # Tablero Kanban por Proyecto
    # ==========================

    def get_kanban_board(self, status: Optional[str] = None) -> Dict[str, Any]:
        try:
            target_uuid = self._resolve_project_uuid()
            svc = self._kanban_service()
            if svc is not None and target_uuid:
                res = svc.get_board(target_uuid, status=status)
                cards = res.get("cards", [])
            else:
                cards = self.db.get_cards(project_uuid=target_uuid, status=status)
            self._enrich_cards_with_okf_tags(target_uuid, cards)
            return {"success": True, "cards": cards, "project_uuid": target_uuid}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def create_kanban_card(
        self,
        card_id: str,
        title: str,
        description: str = "",
        acceptance_criteria: str = "",
        assignee_node_uuid: str = "",
    ) -> Dict[str, Any]:
        try:
            target_uuid = self._resolve_project_uuid()
            if not card_id.strip() or not title.strip():
                return {"success": False, "error": "El ID y el título son obligatorios"}
            svc = self._kanban_service()
            if svc is not None and target_uuid:
                return svc.create_task(
                    project_uuid=target_uuid,
                    card_id=card_id.strip(),
                    title=title.strip(),
                    description=description.strip(),
                    acceptance_criteria=acceptance_criteria.strip(),
                    assignee_node_uuid=assignee_node_uuid.strip() or None,
                    actor=self._human_actor(),
                )
            card = self.db.create_card(
                project_uuid=target_uuid,
                card_id=card_id.strip(),
                title=title.strip(),
                description=description.strip(),
                acceptance_criteria=acceptance_criteria.strip(),
                assignee_node_uuid=assignee_node_uuid.strip() or None,
            )
            return {"success": True, "card": card}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def update_kanban_card_details(
        self,
        card_id: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        acceptance_criteria: Optional[str] = None,
        status: Optional[str] = None,
        branch_ref: Optional[str] = None,
        assignee_node_uuid: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            target_uuid = self._resolve_project_uuid()
            svc = self._kanban_service()
            if svc is not None and target_uuid:
                return svc.update_task(
                    project_uuid=target_uuid,
                    card_id=card_id,
                    actor=self._human_actor(),
                    title=title,
                    description=description,
                    acceptance_criteria=acceptance_criteria,
                    status=status,
                    branch_ref=branch_ref,
                    assignee_node_uuid=assignee_node_uuid,
                )
            card = self.db.update_card_details(
                project_uuid=target_uuid,
                card_id=card_id,
                title=title,
                description=description,
                acceptance_criteria=acceptance_criteria,
                status=status,
                branch_ref=branch_ref,
                assignee_node_uuid=assignee_node_uuid,
            )
            return {"success": True, "card": card}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def claim_task_lease(
        self, card_id: str, node_uuid: str, duration_minutes: int = 30
    ) -> Dict[str, Any]:
        try:
            target_uuid = self._resolve_project_uuid()
            svc = self._kanban_service()
            if svc is not None and target_uuid:
                return svc.claim_lease(
                    project_uuid=target_uuid,
                    card_id=card_id,
                    actor=ActorContext(kind="human_ui", node_uuid=node_uuid, human_presence_verified=True),
                    duration_minutes=duration_minutes,
                )
            card = self.db.claim_card_lease(
                project_uuid=target_uuid,
                card_id=card_id,
                node_uuid=node_uuid,
                duration_minutes=duration_minutes,
            )
            return {"success": True, "card": card}
        except PermissionError as pe:
            return {"success": False, "error": str(pe), "code": "LEASE_COLLISION"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def update_task_status(
        self, card_id: str, new_status: str, branch_ref: Optional[str] = None
    ) -> Dict[str, Any]:
        try:
            target_uuid = self._resolve_project_uuid()
            svc = self._kanban_service()
            if svc is not None and target_uuid:
                return svc.transition_card_status(
                    project_uuid=target_uuid,
                    card_id=card_id,
                    new_status=new_status,
                    actor=self._human_actor(),
                    branch_ref=branch_ref,
                )
            card = self.db.update_card_status(
                project_uuid=target_uuid,
                card_id=card_id,
                new_status=new_status,
                branch_ref=branch_ref,
            )
            return {"success": True, "card": card}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ==========================
    # Enjambre y Telemetría A2A
    # ==========================

    def get_swarm_nodes(self) -> Dict[str, Any]:
        try:
            nodes = self.db.get_swarm_nodes()
            return {"success": True, "nodes": nodes}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_a2a_feed(self, limit: int = 30) -> Dict[str, Any]:
        try:
            target_uuid = self._resolve_project_uuid()
            messages = self.db.get_recent_a2a_messages(project_uuid=target_uuid, limit=limit)
            return {"success": True, "messages": messages}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def send_a2a_signal(
        self, signal_type: str, data: Dict[str, Any], card_id: Optional[str] = None
    ) -> Dict[str, Any]:
        try:
            target_uuid = self._resolve_project_uuid()
            local_node = local_node_uuid()
            packet = self.bus.send_informative(
                project_uuid=target_uuid,
                sender_node_uuid=local_node,
                signal_type=signal_type,
                data=data,
                card_id=card_id,
            )
            return {"success": True, "packet": packet}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_a2a_session_turns(self, session_id: str) -> Dict[str, Any]:
        try:
            target_uuid = self._resolve_project_uuid()
            turns = self.bus.get_session_turns(project_uuid=target_uuid, session_id=session_id)
            return {"success": True, "turns": turns}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ==========================
    # Gobernanza OKF, Memoria y Árbol
    # ==========================

    def get_okf_overview(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        try:
            target_uuid, proj, err = self._get_project_or_error(project_uuid)
            if err or proj is None or not target_uuid:
                return err or {"success": False, "error": "Proyecto no encontrado"}

            gov_path = proj.get("gov_repo_path")
            if gov_path:
                self.okf.ensure_okf_structure(gov_path, proj.get("name", "Proyecto"), target_uuid)

            adrs = self.okf.get_okf_adrs(gov_path)
            skills = self.okf.get_okf_skills(gov_path)
            validation = self.okf.validate_okf_integrity(gov_path)
            return {
                "success": True,
                "project_uuid": target_uuid,
                "adrs": adrs,
                "skills": skills,
                "validation": validation,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def repair_project_okf(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        try:
            target_uuid, proj, err = self._get_project_or_error(project_uuid)
            if err or proj is None or not target_uuid:
                return err or {"success": False, "error": "Proyecto no encontrado"}

            gov_path = proj.get("gov_repo_path")
            if not gov_path:
                return {"success": False, "error": "Ruta de gobernanza no configurada"}

            self.okf.ensure_okf_structure(gov_path, proj.get("name", "Proyecto"), target_uuid)
            report = self.okf.validate_okf_integrity(gov_path)
            return {"success": True, "report": report, "message": "Estructura OKF reparada y sincronizada"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def validate_project_okf(self) -> Dict[str, Any]:
        try:
            target_uuid = self._resolve_project_uuid()
            proj = self.db.get_project_by_uuid(target_uuid) if target_uuid else None
            if not proj:
                return {"success": False, "error": "Proyecto activo no encontrado"}
            gov_path = proj.get("gov_repo_path")
            if not gov_path:
                return {"success": False, "error": "Ruta de gobernanza no configurada"}
            self.okf.ensure_okf_structure(gov_path, proj.get("name", "Proyecto"), target_uuid)
            report = self.okf.validate_okf_integrity(gov_path)
            return {"success": True, "report": report}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def write_memory_snapshot(self, snapshot_data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            target_uuid = self._resolve_project_uuid()
            proj = self.db.get_project_by_uuid(target_uuid) if target_uuid else None
            if not proj:
                return {"success": False, "error": "Proyecto activo no encontrado"}
            gov_path = proj.get("gov_repo_path")
            res = self.okf.write_orphan_memory_snapshot(
                gov_base_path=gov_path,
                project_uuid=target_uuid,
                snapshot_data=snapshot_data,
            )
            return {"success": True, "result": res}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def read_memory_snapshot(self) -> Dict[str, Any]:
        try:
            target_uuid = self._resolve_project_uuid()
            proj = self.db.get_project_by_uuid(target_uuid) if target_uuid else None
            if not proj:
                return {"success": False, "error": "Proyecto activo no encontrado"}
            gov_path = proj.get("gov_repo_path")
            mem = self.okf.read_orphan_memory_snapshot(
                gov_base_path=gov_path,
                project_uuid=target_uuid,
            )
            return {"success": True, "snapshot": mem}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_okf_raw_tree(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        try:
            target_uuid, proj, err = self._get_project_or_error(project_uuid)
            if err or proj is None or not target_uuid:
                return err or {"success": False, "error": "Proyecto no encontrado"}

            gov_path = proj.get("gov_repo_path")
            if not gov_path:
                return {"success": False, "error": "Ruta de gobernanza no configurada"}

            self.okf.ensure_okf_structure(gov_path, proj.get("name", "Proyecto"), target_uuid)
            docs_okf = Path(gov_path) / "docs" / "okf"
            tree = []
            if docs_okf.is_dir():
                for root, _dirs, files in os.walk(docs_okf):
                    rel_root = os.path.relpath(root, docs_okf)
                    for f in sorted(files):
                        rel_path = f if rel_root == "." else os.path.join(rel_root, f)
                        full_p = Path(root) / f
                        tree.append({
                            "path": rel_path,
                            "name": f,
                            "folder": rel_root if rel_root != "." else "",
                            "size_bytes": full_p.stat().st_size if full_p.exists() else 0,
                        })
            return {"success": True, "tree": tree, "base_path": str(docs_okf)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def read_okf_file(self, project_uuid: Optional[str] = None, file_path: str = "") -> Dict[str, Any]:
        try:
            _target_uuid, proj, err = self._get_project_or_error(project_uuid)
            if err or proj is None:
                return err or {"success": False, "error": "Proyecto no encontrado"}

            gov_path = proj.get("gov_repo_path")
            docs_okf = Path(gov_path) / "docs" / "okf"
            target_file = (docs_okf / file_path).resolve()
            if not target_file.is_relative_to(docs_okf.resolve()):
                return {"success": False, "error": "Acceso denegado fuera de docs/okf"}
            if not target_file.is_file():
                return {"success": False, "error": f"Archivo no encontrado: {file_path}"}
            content = target_file.read_text(encoding="utf-8", errors="ignore")
            return {
                "success": True,
                "file_path": file_path,
                "name": target_file.name,
                "content": content,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ==========================
    # Ingesta Cognitiva (Bootstrap) y Auditoría de Deriva (Drift)
    # ==========================

    def scan_and_bootstrap_okf(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        try:
            target_uuid, proj, err = self._get_project_or_error(project_uuid)
            if err or proj is None or not target_uuid:
                return err or {"success": False, "error": "Proyecto no encontrado"}

            code_path = proj.get("code_repo_path")
            if not code_path or not Path(code_path).exists():
                return {"success": False, "error": f"Ruta de código inválida o inexistente: {code_path}"}

            from src.storage.code_scanner import CodebaseAnalyzer
            analyzer = CodebaseAnalyzer(code_path)
            analysis = analyzer.analyze_full_repository()
            dossier_md = analyzer.generate_alignment_dossier(proj.get("name", "Proyecto"), target_uuid)

            self.db.update_project_alignment(
                project_uuid=target_uuid,
                status="pending_bootstrap",
                dossier_markdown=dossier_md,
            )
            stack_data = analysis["stack"].__dict__ if hasattr(analysis["stack"], "__dict__") else analysis["stack"]
            return {
                "success": True,
                "project_uuid": target_uuid,
                "alignment_status": "pending_bootstrap",
                "stack": stack_data,
                "total_adrs_extracted": len(analysis["adrs"]),
                "total_requirements": len(analysis["requirements"]),
                "backlog_cards_suggested": len(analysis["backlog_suggestions"]),
                "dossier_markdown": dossier_md,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_alignment_dossier(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        try:
            target_uuid, proj, err = self._get_project_or_error(project_uuid)
            if err or proj is None or not target_uuid:
                return err or {"success": False, "error": "Proyecto no encontrado"}

            dossier = proj.get("alignment_dossier_markdown")
            status = proj.get("okf_alignment_status", "aligned_okf")
            if not dossier and proj.get("code_repo_path") and Path(proj["code_repo_path"]).exists():
                from src.storage.code_scanner import CodebaseAnalyzer
                analyzer = CodebaseAnalyzer(proj["code_repo_path"])
                dossier = analyzer.generate_alignment_dossier(proj.get("name", "Proyecto"), target_uuid)
                self.db.update_project_alignment(target_uuid, status, dossier)

            return {
                "success": True,
                "project_uuid": target_uuid,
                "alignment_status": status,
                "dossier_markdown": dossier or "No hay dossier disponible.",
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def ratify_okf_bootstrap(self, project_uuid: Optional[str] = None, comment: str = "") -> Dict[str, Any]:
        try:
            target_uuid, proj, err = self._get_project_or_error(project_uuid)
            if err or proj is None or not target_uuid:
                return err or {"success": False, "error": "Proyecto no encontrado"}

            code_path = proj.get("code_repo_path")
            gov_path = proj.get("gov_repo_path")
            if not code_path or not gov_path:
                return {"success": False, "error": "Rutas de repositorio no configuradas"}

            from src.storage.code_scanner import CodebaseAnalyzer
            analyzer = CodebaseAnalyzer(code_path)
            analysis = analyzer.analyze_full_repository()
            dossier_md = analyzer.generate_alignment_dossier(proj.get("name", "Proyecto"), target_uuid)
            analysis["dossier_markdown"] = dossier_md

            local_node = local_node_uuid()
            dev_name = self._dev_name()
            sig_block = self.crypto.sign_human_approval(
                artifact_id=f"BOOTSTRAP_OKF_{target_uuid}",
                content_text=dossier_md,
                node_uuid=local_node,
                developer_name=dev_name,
                decision="RATIFIED",
            )
            mat_res = self.okf.materialize_bootstrap_dossier(
                gov_base_path=gov_path,
                analysis_result=analysis,
                signature_block=sig_block,
                project_uuid=target_uuid,
            )

            suggested_cards = analysis.get("backlog_suggestions", [])
            existing_cards = {c["card_id"] for c in self.db.get_cards(target_uuid)}
            created_cards_count = 0
            for card in suggested_cards:
                cid = card["card_id"]
                if cid not in existing_cards:
                    self.db.create_card(
                        project_uuid=target_uuid,
                        card_id=cid,
                        title=card["title"],
                        description=card.get("description", ""),
                    )
                    created_cards_count += 1

            self.db.update_project_alignment(
                project_uuid=target_uuid,
                status="aligned_okf",
                dossier_markdown=dossier_md,
            )
            return {
                "success": True,
                "project_uuid": target_uuid,
                "new_status": "aligned_okf",
                "materialization": mat_res,
                "backlog_cards_created": created_cards_count,
                "signature": sig_block,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def audit_okf_drift(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        try:
            target_uuid, proj, err = self._get_project_or_error(project_uuid)
            if err or proj is None or not target_uuid:
                return err or {"success": False, "error": "Proyecto no encontrado"}

            status = proj.get("okf_alignment_status", "aligned_okf")
            if status == "pending_bootstrap":
                return {
                    "success": True,
                    "is_aligned": False,
                    "status": "pending_bootstrap",
                    "message": "El proyecto está en estado pending_bootstrap. Requiere ratificación HITL antes de auditar derivas.",
                }

            drift = self.okf.audit_okf_drift(
                code_repo_path=proj.get("code_repo_path", ""),
                gov_base_path=proj.get("gov_repo_path", ""),
            )
            return {"success": True, "project_uuid": target_uuid, "drift": drift}
        except Exception as e:
            return {"success": False, "error": str(e)}
