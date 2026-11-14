"""
Sub-adaptador UI para ramas Git, consenso dialéctico A2A y sincronización de 3 niveles.
"""

from __future__ import annotations

import json
import logging
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from src.app.identity import local_node_uuid
from src.app.ui_adapters import _BaseUIAdapter

logger = logging.getLogger("mcp2p.api_bridge.git")


class GitBranchesUIAdapter(_BaseUIAdapter):
    """Gestiona ramas Git, worktree huérfano, publicación en GitHub y sync_three_tiers."""

    def get_branches_and_consensus(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        try:
            target_uuid, proj, err = self._get_project_or_error(project_uuid)
            if err or proj is None or not target_uuid:
                return err or {"success": False, "error": "Proyecto no encontrado"}

            code_repo = proj.get("code_repo_path")
            gov_repo = proj.get("gov_repo_path")
            git_app = self.github.get_git_branches_and_log(code_repo) if code_repo else {"branches": [], "commits": []}
            git_gov = self.github.get_git_branches_and_log(gov_repo) if gov_repo else {"branches": [], "commits": []}

            summary = self.db.get_project_cards_and_disputes_summary(target_uuid)
            cards = summary.get("cards", [])
            msgs = summary.get("a2a_messages", [])
            tickets = summary.get("dispute_tickets", [])

            sessions_map: Dict[str, Any] = {}
            for row in msgs:
                try:
                    payload = json.loads(row["payload_rfc_yaml"])
                except Exception:
                    payload = {"raw": row.get("payload_rfc_yaml")}

                session_id = payload.get("session_id") or row["message_uuid"]
                if session_id not in sessions_map:
                    sessions_map[session_id] = {
                        "session_id": session_id,
                        "card_id": row.get("card_id"),
                        "component": payload.get("component", "General"),
                        "intent": payload.get("intent", ""),
                        "status": "IN_DISCUSSION",
                        "verdict_signal": payload.get("verdict_signal"),
                        "agreed_branch": None,
                        "turns": [],
                    }

                turn_data = {
                    "turn": payload.get("turn", 1),
                    "stage": payload.get("stage", "PROPOSAL"),
                    "sender_node": row.get("sender_node_uuid"),
                    "verdict_signal": payload.get("verdict_signal"),
                    "critique": payload.get("critique"),
                    "alternative": payload.get("alternative"),
                    "rationale": payload.get("rationale"),
                    "diff_ref": payload.get("diff_ref"),
                    "final_decision": payload.get("final_decision"),
                    "synthesis_notes": payload.get("synthesis_notes"),
                    "timestamp": payload.get("timestamp") or row.get("created_at"),
                }
                sessions_map[session_id]["turns"].append(turn_data)

                if payload.get("verdict_signal"):
                    sessions_map[session_id]["verdict_signal"] = payload.get("verdict_signal")

                if payload.get("final_decision") == "AGREED":
                    sessions_map[session_id]["status"] = "AGREED"
                    sessions_map[session_id]["verdict_signal"] = sessions_map[session_id]["verdict_signal"] or "ACCEPT"
                    sessions_map[session_id]["agreed_branch"] = payload.get("agreed_branch") or payload.get("diff_ref")
                elif payload.get("final_decision") == "REJECTED":
                    sessions_map[session_id]["status"] = "REJECTED"
                    sessions_map[session_id]["verdict_signal"] = "REJECT"

            sessions_list = list(sessions_map.values())
            return {
                "success": True,
                "project_uuid": target_uuid,
                "git": git_app,
                "git_app": git_app,
                "git_gov": git_gov,
                "cards": cards,
                "sessions": sessions_list,
                "consensus_sessions": sessions_list,
                "disputes": [dict(t) for t in tickets],
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _resolve_repo_path(
        self, project_uuid: Optional[str], repo_target: str
    ) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        target_uuid = project_uuid or self._bridge.active_project_uuid
        proj = self.db.get_project_by_uuid(target_uuid) if target_uuid else None
        if not proj:
            return None, {"success": False, "error": f"Repositorio ({repo_target}) no configurado"}
        repo_path = proj.get("gov_repo_path") if repo_target == "gov" else proj.get("code_repo_path")
        if not repo_path:
            return None, {"success": False, "error": f"Repositorio ({repo_target}) no configurado"}
        return repo_path, None

    def git_fetch_action(self, project_uuid: Optional[str] = None, repo_target: str = "app") -> Dict[str, Any]:
        repo_path, err = self._resolve_repo_path(project_uuid, repo_target)
        if err or not repo_path:
            return err or {"success": False, "error": f"Repositorio ({repo_target}) no configurado"}
        return self.github.git_fetch(repo_path)

    def git_pull_action(
        self, project_uuid: Optional[str] = None, branch: Optional[str] = None, repo_target: str = "app"
    ) -> Dict[str, Any]:
        repo_path, err = self._resolve_repo_path(project_uuid, repo_target)
        if err or not repo_path:
            return err or {"success": False, "error": f"Repositorio ({repo_target}) no configurado"}
        return self.github.git_pull(repo_path, branch=branch)

    def git_push_action(
        self, project_uuid: Optional[str] = None, branch: Optional[str] = None, repo_target: str = "app"
    ) -> Dict[str, Any]:
        repo_path, err = self._resolve_repo_path(project_uuid, repo_target)
        if err or not repo_path:
            return err or {"success": False, "error": f"Repositorio ({repo_target}) no configurado"}
        if not branch:
            b_info = self.github.get_git_branches_and_log(repo_path)
            branch = b_info.get("current_branch", "main")
        return self.github.git_push_branch(repo_path, branch=branch)

    def git_merge_action(
        self,
        project_uuid: Optional[str] = None,
        source_branch: str = "",
        target_branch: str = "main",
        repo_target: str = "app",
    ) -> Dict[str, Any]:
        repo_path, err = self._resolve_repo_path(project_uuid, repo_target)
        if err or not repo_path:
            return err or {"success": False, "error": f"Repositorio ({repo_target}) no configurado"}
        if not source_branch:
            return {"success": False, "error": "Debe especificar la rama origen a fusionar"}
        return self.github.git_merge(repo_path, source_branch=source_branch, target_branch=target_branch)

    def git_create_branch_action(
        self, project_uuid: Optional[str] = None, branch_name: str = "", repo_target: str = "app"
    ) -> Dict[str, Any]:
        repo_path, err = self._resolve_repo_path(project_uuid, repo_target)
        if err or not repo_path or not branch_name.strip():
            return {"success": False, "error": "Nombre de rama inválido o repositorio inexistente"}
        return self.github.git_create_branch(repo_path, branch_name.strip())

    def set_project_remote_url(
        self,
        project_uuid: Optional[str] = None,
        remote_url: str = "",
        remote_name: str = "origin",
        repo_target: str = "app",
    ) -> Dict[str, Any]:
        target_uuid = project_uuid or self._bridge.active_project_uuid
        proj = self.db.get_project_by_uuid(target_uuid) if target_uuid else None
        if not proj:
            return {"success": False, "error": "Proyecto no encontrado"}
        repo_path = proj.get("gov_repo_path") if repo_target == "gov" else proj.get("code_repo_path")
        if not repo_path:
            return {"success": False, "error": f"Repositorio {repo_target} no configurado"}
        return self.github.set_remote_url(repo_path, url=remote_url, remote=remote_name)

    def ensure_orphan_worktree(self, gov_repo_path: str | Path, project_uuid: str) -> Path:
        gov_path = Path(gov_repo_path).resolve()
        wt_path = gov_path / ".git" / "orphan_worktree"
        orphan_branch = f"orphan-conversa-memory-{project_uuid}"

        if wt_path.exists() and (wt_path / ".git").exists():
            return wt_path

        if not (gov_path / ".git").exists():
            raise RuntimeError(
                f"{gov_path} no es la raíz de un repositorio Git; se omite el worktree huérfano "
                "para no operar sobre un repositorio contenedor"
            )

        creationflags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
        ref_check = self._subprocess_run(
            ["git", "show-ref", "--verify", f"refs/heads/{orphan_branch}"],
            cwd=str(gov_path),
            capture_output=True,
            creationflags=creationflags,
        )

        if ref_check.returncode != 0 and self.github.has_remote(gov_path, "origin"):
            self.github.git_fetch(gov_path, "origin")
            fetch_orphan = self._subprocess_run(
                ["git", "fetch", "origin", f"{orphan_branch}:{orphan_branch}"],
                cwd=str(gov_path),
                capture_output=True,
                creationflags=creationflags,
            )
            if fetch_orphan.returncode == 0:
                ref_check.returncode = 0

        if ref_check.returncode == 0:
            wt_res = self._subprocess_run(
                ["git", "worktree", "add", str(wt_path), orphan_branch],
                cwd=str(gov_path),
                capture_output=True,
                creationflags=creationflags,
            )
            if wt_res.returncode != 0 and not (wt_path / ".git").exists():
                err = (wt_res.stderr or b"").decode("utf-8", errors="replace").strip()
                raise RuntimeError(f"No se pudo montar el worktree huérfano en {wt_path}: {err[:300]}")
        else:
            wt_res = self._subprocess_run(
                ["git", "worktree", "add", "--orphan", "-b", orphan_branch, str(wt_path)],
                cwd=str(gov_path),
                capture_output=True,
                creationflags=creationflags,
            )
            if wt_res.returncode != 0 and not (wt_path / ".git").exists():
                err = (wt_res.stderr or b"").decode("utf-8", errors="replace").strip()
                raise RuntimeError(f"No se pudo montar el worktree huérfano en {wt_path}: {err[:300]}")
            if wt_path.exists():
                readme = wt_path / "README_CONVERSA.md"
                if not readme.exists():
                    readme.write_text(
                        f"# Memoria Conversacional Desacoplada\n\nUUID: `{project_uuid}`\nRama: `{orphan_branch}`\n",
                        encoding="utf-8",
                    )
                self._subprocess_run(["git", "add", "."], cwd=str(wt_path), capture_output=True, creationflags=creationflags)
                commit_res = self._subprocess_run(
                    ["git", "commit", "-m", f"init(orphan): initialize decoupled conversational memory for {project_uuid}"],
                    cwd=str(wt_path),
                    capture_output=True,
                    creationflags=creationflags,
                )
                if commit_res.returncode != 0:
                    logger.warning(
                        "ensure_orphan_worktree: commit inicial del README falló en %s: %s",
                        wt_path,
                        (commit_res.stderr or b"").decode("utf-8", errors="replace").strip()[:300],
                    )
        return wt_path

    def publish_project_to_github(
        self,
        project_uuid: Optional[str] = None,
        repo_target: str = "both",
        repo_name: Optional[str] = None,
        is_private: bool = True,
    ) -> Dict[str, Any]:
        target_uuid = self._resolve_project_uuid(project_uuid)
        proj = self.db.get_project_by_uuid(target_uuid) if target_uuid else None
        if not proj:
            return {"success": False, "error": "Proyecto no encontrado"}
        if self._bridge._is_pending_join_stub(proj):
            return self._bridge._pending_join_refusal(proj, "publicar el proyecto en GitHub")

        clean_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in proj.get("name", "proyecto").lower())

        if repo_target == "both":
            app_path = proj.get("code_repo_path")
            if app_path:
                app_res = self.github.auto_create_and_publish_repo(
                    repo_path=app_path,
                    repo_name=f"mcp2p-{clean_name}",
                    description=f"Repositorio de código local-first para {proj.get('name')}",
                    private=is_private,
                )
            else:
                app_res = {"success": False, "error": "Ruta del repositorio (app) no configurada"}

            gov_path = proj.get("gov_repo_path")
            if gov_path:
                gov_res = self.github.auto_create_and_publish_repo(
                    repo_path=gov_path,
                    repo_name=f"mcp2p-{clean_name}-okf",
                    description=f"Repositorio de gobernanza OKF para {proj.get('name')}",
                    private=is_private,
                )
            else:
                gov_res = {"success": False, "error": "Ruta del repositorio (gov) no configurada"}

            orphan_res = {"success": False, "error": "No ejecutado"}
            if gov_path and Path(gov_path).exists():
                try:
                    self._bridge.ensure_orphan_worktree(gov_path, target_uuid)
                    orphan_branch = f"orphan-conversa-memory-{target_uuid}"
                    orphan_res = self.github.git_push_branch(gov_path, branch=orphan_branch, remote="origin")
                except Exception as oe:
                    orphan_res = {"success": False, "error": str(oe)}

            self.db.update_project_repo_urls(
                target_uuid,
                code_repo_url=app_res.get("repo_url") if app_res.get("success") else None,
                gov_repo_url=gov_res.get("repo_url") if gov_res.get("success") else None,
            )

            overall_success = app_res.get("success", False) and gov_res.get("success", False)
            return {
                "success": overall_success,
                "app": app_res,
                "gov": gov_res,
                "orphan": orphan_res,
                "repo_url": app_res.get("repo_url") or gov_res.get("repo_url"),
                "branch": app_res.get("branch", "main"),
                "message": "Repositorios de código, gobernanza y memoria conversacional publicados con éxito"
                if overall_success
                else "Publicación parcial completada",
            }

        repo_path = proj.get("gov_repo_path") if repo_target == "gov" else proj.get("code_repo_path")
        if not repo_path:
            return {"success": False, "error": f"Ruta del repositorio ({repo_target}) no configurada"}

        if not repo_name:
            target_repo_name = f"mcp2p-{clean_name}-okf" if repo_target == "gov" else f"mcp2p-{clean_name}"
        else:
            target_repo_name = repo_name.strip()

        desc = f"Repositorio autónomo local-first para {proj.get('name')} ({repo_target.upper()})"
        res = self.github.auto_create_and_publish_repo(
            repo_path=repo_path,
            repo_name=target_repo_name,
            description=desc,
            private=is_private,
        )

        if res.get("success") and res.get("repo_url"):
            if repo_target == "gov":
                self.db.update_project_repo_urls(target_uuid, gov_repo_url=res["repo_url"])
                try:
                    self._bridge.ensure_orphan_worktree(repo_path, target_uuid)
                    orphan_branch = f"orphan-conversa-memory-{target_uuid}"
                    self.github.git_push_branch(repo_path, branch=orphan_branch, remote="origin")
                except Exception:
                    pass
            else:
                self.db.update_project_repo_urls(target_uuid, code_repo_url=res["repo_url"])
        return res

    def sync_three_tiers(self, project_uuid: Optional[str] = None) -> Dict[str, Any]:
        target_uuid = self._resolve_project_uuid(project_uuid)
        proj = self.db.get_project_by_uuid(target_uuid) if target_uuid else None
        if not proj:
            return {"success": False, "error": "Proyecto no encontrado para sincronización"}
        if self._bridge._is_pending_join_stub(proj):
            return self._bridge._pending_join_refusal(proj, "sincronizar el proyecto")

        gov_raw = (proj.get("gov_repo_path") or "").strip()
        code_raw = (proj.get("code_repo_path") or "").strip()
        if not gov_raw:
            return {"success": False, "error": "Ruta del repositorio de gobernanza no configurada; sincronización omitida"}

        creationflags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
        local_node = local_node_uuid()
        gov_path = Path(gov_raw).resolve()
        app_path = Path(code_raw).resolve() if code_raw else None

        summary = self.db.get_project_cards_and_disputes_summary(target_uuid)
        a2a_by_uuid: Dict[str, Dict[str, Any]] = {
            m["message_uuid"]: m for m in summary.get("a2a_messages", []) if m.get("message_uuid")
        }

        tier1_res: Dict[str, Any] = {"flushed": 0, "imported": 0, "status": "ok"}
        events_committed = False
        try:
            wt_path = self._bridge.ensure_orphan_worktree(gov_path, target_uuid)
            orphan_branch = f"orphan-conversa-memory-{target_uuid}"

            pending_events = self.db.get_pending_outbox_events(target_uuid, limit=500)
            if pending_events:
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                events_dir = wt_path / "nodes" / local_node / "events"
                events_dir.mkdir(parents=True, exist_ok=True)
                events_file = events_dir / f"{today}.jsonl"
                pre_size = events_file.stat().st_size if events_file.exists() else 0

                with events_file.open("a", encoding="utf-8") as f:
                    for ev in pending_events:
                        evt_uuid = ev["event_uuid"]
                        msg_row = a2a_by_uuid.get(evt_uuid)
                        intent = msg_row.get("intent", "INFORMATIVE") if msg_row else "INFORMATIVE"
                        card_id = msg_row.get("card_id") if msg_row else None
                        line_data = {
                            "event_uuid": evt_uuid,
                            "project_uuid": ev["project_uuid"],
                            "node_uuid": ev["node_uuid"],
                            "event_type": ev["event_type"],
                            "payload_raw": ev["payload_raw"],
                            "created_at": ev["created_at"],
                            "intent": intent,
                            "card_id": card_id,
                        }
                        f.write(json.dumps(line_data, ensure_ascii=False) + "\n")

                self._subprocess_run(["git", "add", "."], cwd=str(wt_path), capture_output=True, creationflags=creationflags)
                commit_res = self._subprocess_run(
                    ["git", "commit", "-m", f"sync(orphan): flush {len(pending_events)} events from node {local_node}"],
                    cwd=str(wt_path),
                    capture_output=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=creationflags,
                )
                if commit_res.returncode == 0:
                    events_committed = True
                    tier1_res["flushed"] = len(pending_events)
                else:
                    err = (commit_res.stderr or commit_res.stdout or "").strip()
                    tier1_res["status"] = f"commit_failed: {err[:300]}"
                    logger.warning(f"sync_three_tiers: commit de outbox falló, eventos quedan pendientes: {err[:300]}")
                    with events_file.open("r+b") as tf:
                        tf.truncate(pre_size)

            if self.github.has_remote(gov_path, "origin"):
                self.github.git_pull(wt_path, remote="origin", branch=orphan_branch, rebase=True)
                self.github.git_push_branch(wt_path, branch=orphan_branch, remote="origin")

            imported_count = 0
            nodes_base = wt_path / "nodes"
            if nodes_base.is_dir():
                for node_folder in nodes_base.iterdir():
                    if not node_folder.is_dir():
                        continue
                    ev_folder = node_folder / "events"
                    if not ev_folder.is_dir():
                        continue
                    for jf in ev_folder.glob("*.jsonl"):
                        try:
                            with jf.open("r", encoding="utf-8") as f:
                                for line in f:
                                    line_str = line.strip()
                                    if not line_str:
                                        continue
                                    evt = json.loads(line_str)
                                    evt_uuid = evt.get("event_uuid")
                                    if not evt_uuid or evt_uuid in a2a_by_uuid:
                                        continue
                                    self.db.log_a2a_message(
                                        project_uuid=target_uuid,
                                        message_uuid=evt_uuid,
                                        sender_node_uuid=evt.get("node_uuid", "unknown"),
                                        intent=evt.get("intent", "INFORMATIVE"),
                                        payload_rfc_yaml=evt.get("payload_raw", "{}"),
                                        card_id=evt.get("card_id"),
                                    )
                                    self.db.enqueue_outbox_event(
                                        project_uuid=target_uuid,
                                        node_uuid=evt.get("node_uuid", "unknown"),
                                        event_type=evt.get("event_type", "A2A_REPLICATED"),
                                        payload_raw=evt.get("payload_raw", "{}"),
                                        event_uuid=evt_uuid,
                                    )
                                    self.db.update_outbox_sync_status(evt_uuid, "acked")
                                    a2a_by_uuid[evt_uuid] = evt
                                    imported_count += 1
                                    try:
                                        self.bus._dispatch_local(evt)
                                    except Exception:
                                        pass
                        except Exception as jf_err:
                            logger.debug(f"Error procesando {jf}: {jf_err}")

            if events_committed:
                for ev in pending_events:
                    self.db.update_outbox_sync_status(ev["event_uuid"], "acked")

            tier1_res["imported"] = imported_count
        except Exception as t1_err:
            tier1_res["status"] = f"error: {t1_err}"

        tier2_res: Dict[str, Any] = {"status": "ok"}
        try:
            if (gov_path / ".git").exists() and self.github.has_remote(gov_path, "origin"):
                pull_gov = self.github.git_pull(gov_path, remote="origin", branch="main", rebase=True)
                tier2_res["pull"] = pull_gov.get("success", False)
            if gov_path.exists():
                reconcile_res = self.okf.reconcile_kanban_from_okf(gov_path, target_uuid, self.db)
                tier2_res["reconciled"] = reconcile_res
        except Exception as t2_err:
            tier2_res["status"] = f"error: {t2_err}"

        tier3_res: Dict[str, Any] = {"status": "ok"}
        try:
            if app_path is not None and (app_path / ".git").exists() and self.github.has_remote(app_path, "origin"):
                fetch_app = self.github.git_fetch(app_path, remote="origin")
                tier3_res["fetch"] = fetch_app.get("success", False)
        except Exception as t3_err:
            tier3_res["status"] = f"error: {t3_err}"

        return {
            "success": True,
            "project_uuid": target_uuid,
            "tier1_orphan": tier1_res,
            "tier2_okf": tier2_res,
            "tier3_app": tier3_res,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
