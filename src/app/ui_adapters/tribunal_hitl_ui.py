"""
Sub-adaptador UI para Bandeja HITL, Tribunal Dialéctico (EGM-07 / EGM-37 / EGM-01-R)
y firmas Ed25519 de aprobaciones humanas.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from src.app.ui_adapters import _BaseUIAdapter

logger = logging.getLogger("mcp2p.api_bridge.tribunal")


class TribunalHitlUIAdapter(_BaseUIAdapter):
    """Orquesta la bandeja HITL, voto ciego determinista y firma criptográfica Ed25519."""

    @staticmethod
    def get_blind_options_mapping(ticket_id: str, viewer_node: str) -> Tuple[Dict[str, str], Dict[str, str]]:
        seed = hashlib.sha256(f"{ticket_id}:{viewer_node}".encode("utf-8")).digest()
        shuffled = random.Random(seed).sample(["OPTION_A", "OPTION_B", "THIRD_WAY_C"], 3)
        alias_names = ["ALFA", "BETA", "GAMMA"]
        alias_to_canonical = {alias_names[i]: shuffled[i] for i in range(3)}
        canonical_to_alias = {shuffled[i]: alias_names[i] for i in range(3)}
        return alias_to_canonical, canonical_to_alias

    @staticmethod
    def anonymize_text(text: str, canonical_to_alias: Dict[str, str]) -> str:
        if not text:
            return ""
        alias_a = canonical_to_alias.get("OPTION_A", "ALFA")
        alias_b = canonical_to_alias.get("OPTION_B", "BETA")
        alias_c = canonical_to_alias.get("THIRD_WAY_C", "GAMMA")
        patterns = [
            (r"\bOPTION_A\b", f"Opción {alias_a}"),
            (r"\bOPTION_B\b", f"Opción {alias_b}"),
            (r"\bTHIRD_WAY_C\b", f"Opción {alias_c}"),
            (r"\bOpci[oó]n\s+A\b", f"Opción {alias_a}"),
            (r"\bOpci[oó]n\s+B\b", f"Opción {alias_b}"),
            (r"\bOpci[oó]n\s+C\b", f"Opción {alias_c}"),
            (r"\bTesis\s+A\b", f"Propuesta {alias_a}"),
            (r"\bAnt[ií]tesis\s+B\b", f"Propuesta {alias_b}"),
            (r"\bS[ií]ntesis\s+C\b", f"Propuesta {alias_c}"),
            (r"\bTesis\b", f"Propuesta {alias_a}"),
            (r"\bAnt[ií]tesis\b", f"Propuesta {alias_b}"),
            (r"\bS[ií]ntesis\b", f"Propuesta {alias_c}"),
            (r"\bTercera\s+V[ií]a\b", f"Propuesta {alias_c}"),
        ]
        res = text
        for pat, repl in patterns:
            res = re.sub(pat, repl, res, flags=re.IGNORECASE)
        return res

    def get_pending_human_approvals(
        self, project_uuid: Optional[str] = None, viewer_node: Optional[str] = None
    ) -> Dict[str, Any]:
        try:
            target_uuid, proj, err = self._get_project_or_error(project_uuid)
            if err or proj is None or not target_uuid:
                return err or {"success": False, "error": "Proyecto no encontrado"}

            gov_path = Path(proj["gov_repo_path"])
            app_path = Path(proj["code_repo_path"])
            local_node = self._local_node(viewer_node)

            decisions = []
            docs_base = gov_path / "docs" / "okf" if (gov_path / "docs" / "okf").exists() else gov_path
            dec_dir = docs_base / "architecture" / "decisions"
            if dec_dir.is_dir():
                for f in sorted(dec_dir.glob("*.md")):
                    txt = f.read_text(encoding="utf-8", errors="ignore")
                    item_id = f.stem
                    id_match = re.search(r"^id:\s*([^\n\r]+)", txt, re.MULTILINE)
                    if id_match:
                        item_id = id_match.group(1).strip()
                    elif "-" in f.stem:
                        parts = f.stem.split("-")
                        if len(parts) >= 2 and parts[0].isalpha() and parts[1].isdigit():
                            item_id = f"{parts[0]}-{parts[1]}"
                    title_match = re.search(r"^#\s+(.+)$", txt, re.MULTILINE)
                    clean_title = title_match.group(1).strip() if title_match else f.stem
                    related_cards = re.findall(r"(TASK-\d+)", txt)
                    decisions.append({
                        "file_name": f.name,
                        "item_id": item_id,
                        "title": clean_title,
                        "excerpt": txt[:360],
                        "full_content": txt,
                        "related_cards": list(dict.fromkeys(related_cards)),
                        "status": "pending_approval",
                    })

            tribunal = self._tribunal_service()
            bundle = (
                tribunal.get_pending_hitl_bundle(target_uuid, local_node_uuid=local_node)
                if tribunal is not None
                else self.db.get_pending_hitl_bundle(target_uuid, local_node=local_node)
            )
            collabs = bundle.get("collaborators") or self.db.get_project_collaborators(target_uuid)
            all_nodes = [c["node_uuid"] for c in collabs if c.get("status") == "approved"]
            if not all_nodes:
                all_nodes = [n["node_uuid"] for n in (bundle.get("swarm_nodes") or self.db.get_swarm_nodes())]

            disputes = []
            for row in bundle.get("open_disputes", []):
                t = dict(row)
                t_id = t["ticket_id"]
                now_iso = datetime.now(timezone.utc).isoformat()
                try:
                    self.db.record_vote_delivery(t_id, local_node, now_iso)
                except Exception:
                    pass

                summary = self.db.get_jury_votes_summary(t_id)
                raw_human_votes = summary.get("human_votes", [])
                has_voted = any(hv.get("node_uuid") == local_node for hv in raw_human_votes)
                proposal_c = t.get("proposal_c_ref") or ""

                raw_matrix = t.get("jury_matrix_json") or "[]"
                try:
                    matrix_list = json.loads(raw_matrix) if raw_matrix else []
                except Exception:
                    matrix_list = []

                contenders = [c for c in [t.get("contender_a_node"), t.get("contender_b_node")] if c]
                arbiter_uuid = t.get("arbiter_node_uuid")
                h_eligible = self.bus.eligible_humans_count(target_uuid, contenders, arbiter_node_uuid=arbiter_uuid)

                if not matrix_list:
                    gen_matrix = self.bus.jury_engine.generate_neutral_jury_matrix(
                        eligible_humans_count=h_eligible,
                        ticket_id=t_id,
                        seed=t_id,
                    )
                    matrix_list = gen_matrix.get("evaluators", [])
                    self.db.update_dispute_synthesis_and_matrix(
                        ticket_id=t_id,
                        proposal_c_ref=proposal_c,
                        jury_matrix=matrix_list,
                    )

                alias_to_canonical, canonical_to_alias = self.get_blind_options_mapping(t_id, local_node)
                agent_scores = summary.get("agent_scores", [])
                mayeutic_discussions = []
                grouped_by_juror: Dict[str, List[Dict[str, Any]]] = {}
                for sc in agent_scores:
                    j_id = sc.get("juror_id", "subagent_neutral")
                    grouped_by_juror.setdefault(j_id, []).append(sc)

                for idx, m_spec in enumerate(matrix_list):
                    real_jid = m_spec.get("juror_id", f"subagent_neutral_{idx+1}")
                    j_evals = grouped_by_juror.get(real_jid, [])
                    rationale_text = ""
                    for ev in j_evals:
                        if ev.get("rationale_summary"):
                            rationale_text = ev["rationale_summary"]
                            break

                    is_pending = not bool(rationale_text)
                    if is_pending:
                        display_text = "⏳ Pendiente de evaluación por el subagente jurado."
                    else:
                        display_text = (
                            self.anonymize_text(rationale_text, canonical_to_alias)
                            if not has_voted
                            else rationale_text
                        )

                    juror_display_id = f"Jurado {idx + 1}" if not has_voted else real_jid
                    role_display = "EVALUADOR_NEUTRAL" if not has_voted else m_spec.get("role", "NEUTRAL_MATRIX_JUROR")

                    mayeutic_entry: Dict[str, Any] = {
                        "juror_id": juror_display_id,
                        "role": role_display,
                        "focus_vectors": m_spec.get("focus_vectors", []),
                        "focus_labels": m_spec.get("focus_labels", []),
                        "focus_count": m_spec.get("focus_count", 0),
                        "pending": is_pending,
                        "mayeutic_argument": display_text,
                        "scores_hidden_until_human_vote": not has_voted,
                    }
                    if has_voted:
                        mayeutic_entry["temperature"] = m_spec.get("temperature", 0.2)
                        mayeutic_entry["temperature_profile"] = m_spec.get("temperature_profile", "analytical_strict")
                        mayeutic_entry["revealed_scores"] = j_evals
                    mayeutic_discussions.append(mayeutic_entry)

                voter_universe = self.bus.jury_engine.resolve_voter_universe(
                    all_nodes, contenders, arbiter_node_uuid=arbiter_uuid
                )

                canonical_proposals = {
                    "OPTION_A": t.get("proposal_a_ref", ""),
                    "OPTION_B": t.get("proposal_b_ref", ""),
                    "THIRD_WAY_C": proposal_c,
                }
                options_view = []
                for alias in ["ALFA", "BETA", "GAMMA"]:
                    canon = alias_to_canonical[alias]
                    raw_prop = canonical_proposals.get(canon, "")
                    is_available = True if (canon != "THIRD_WAY_C" or bool(proposal_c)) else False
                    opt_entry: Dict[str, Any] = {
                        "alias_id": alias,
                        "alias_label": f"Opción {alias}",
                        "text": self.anonymize_text(raw_prop, canonical_to_alias) if not has_voted else raw_prop,
                        "is_available": is_available,
                    }
                    if has_voted or t.get("status") != "open":
                        opt_entry["canonical_option"] = canon
                        opt_entry["author"] = (
                            t.get("contender_a_node")
                            if canon == "OPTION_A"
                            else (t.get("contender_b_node") if canon == "OPTION_B" else "Subagente Sintetizador")
                        )
                    options_view.append(opt_entry)

                t["options_view"] = options_view
                t["has_voted"] = has_voted
                t["jury_matrix"] = matrix_list
                t["mayeutic_discussions"] = mayeutic_discussions
                t["voter_universe"] = voter_universe

                if not has_voted and t.get("status") == "open":
                    t["votes_summary"] = {
                        "human_votes": {
                            "human_votes_cast": len(raw_human_votes),
                            "human_votes_expected": h_eligible,
                        },
                        "agent_scores_count": len(agent_scores),
                        "bias_protection": (
                            "Voto ciego activado: opciones anonimizadas con alias estables. "
                            "Puntuaciones ocultas hasta emitir tu voto."
                        ),
                    }
                    t.pop("proposal_a_ref", None)
                    t.pop("proposal_b_ref", None)
                    t.pop("proposal_c_ref", None)
                    t.pop("contender_a_node", None)
                    t.pop("contender_b_node", None)
                    t.pop("jury_matrix_json", None)
                else:
                    t["votes_summary"] = summary
                    t["reveal_map"] = {
                        alias: {
                            "canonical_option": alias_to_canonical[alias],
                            "author": (
                                t.get("contender_a_node")
                                if alias_to_canonical[alias] == "OPTION_A"
                                else (
                                    t.get("contender_b_node")
                                    if alias_to_canonical[alias] == "OPTION_B"
                                    else "Subagente Sintetizador"
                                )
                            ),
                            "label": (
                                "Tesis (A)"
                                if alias_to_canonical[alias] == "OPTION_A"
                                else ("Antítesis (B)" if alias_to_canonical[alias] == "OPTION_B" else "Síntesis (C)")
                            ),
                        }
                        for alias in ["ALFA", "BETA", "GAMMA"]
                    }
                disputes.append(t)

            prs = []
            cards_in_review = bundle.get("cards_in_review") or self.db.get_cards(target_uuid, status="in_review")
            git_app_info = (
                self.github.get_git_branches_and_log(app_path)
                if app_path.exists()
                else {"commits": [], "branches": []}
            )
            all_commits = git_app_info.get("commits", [])

            for c in cards_in_review:
                c_id = c["card_id"]
                b_ref = c.get("branch_ref") or ""
                related_commits = [
                    cm
                    for cm in all_commits
                    if c_id.lower() in (cm.get("message", "") + " " + cm.get("decorations", "")).lower()
                    or (b_ref and b_ref.lower() in cm.get("decorations", "").lower())
                ][:6]
                if not related_commits and all_commits:
                    related_commits = all_commits[:3]

                prs.append({
                    "card_id": c_id,
                    "title": c["title"],
                    "description": c.get("description", ""),
                    "acceptance_criteria": c.get("acceptance_criteria", ""),
                    "branch_ref": b_ref,
                    "assignee_node_uuid": c.get("assignee_node_uuid"),
                    "status": c.get("status"),
                    "commits": related_commits,
                    "updated_at": c.get("updated_at"),
                })

            history_precedents = []
            docs_okf = gov_path / "docs" / "okf" if (gov_path / "docs" / "okf").exists() else gov_path
            precedents_file = docs_okf / "architecture" / "precedents_index.yaml"
            if precedents_file.exists():
                try:
                    p_index = yaml.safe_load(precedents_file.read_text(encoding="utf-8")) or {}
                    history_precedents = p_index.get("precedents", [])
                except Exception as prec_err:
                    logger.debug("Error leyendo precedents_index.yaml: %s", prec_err)

            for row in bundle.get("resolved_disputes", []):
                row_dict = dict(row)
                summary = self.db.get_jury_votes_summary(row_dict["ticket_id"])
                row_dict["votes_summary"] = summary
                if not any(
                    p.get("id") == f"RULING-{row_dict['ticket_id']}" or p.get("id") == row_dict["ticket_id"]
                    for p in history_precedents
                ):
                    history_precedents.append({
                        "id": f"RULING-{row_dict['ticket_id']}",
                        "type": "JURY_RULING",
                        "title": row_dict.get("title"),
                        "winning_option": row_dict.get("winning_quadrant"),
                        "card_id": row_dict.get("card_id"),
                        "status": row_dict.get("status"),
                        "final_adr_path": row_dict.get("final_adr_path"),
                    })

            alignment_status = proj.get("okf_alignment_status", "aligned_okf")
            dossier_md = proj.get("alignment_dossier_markdown", "")
            has_pending_bootstrap = alignment_status == "pending_bootstrap"

            return {
                "success": True,
                "project_uuid": target_uuid,
                "alignment_status": alignment_status,
                "has_pending_bootstrap": has_pending_bootstrap,
                "alignment_dossier_markdown": dossier_md,
                "pending_decisions": decisions,
                "open_disputes": disputes,
                "pending_prs": prs,
                "history_precedents": history_precedents,
                "total_count": len(decisions) + len(disputes) + len(prs) + (1 if has_pending_bootstrap else 0),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_hitl_item_detail(
        self, project_uuid: Optional[str] = None, item_type: str = "", item_id: str = ""
    ) -> Dict[str, Any]:
        try:
            target_uuid = self._resolve_project_uuid(project_uuid)
            proj = self.db.get_project_by_uuid(target_uuid) if target_uuid else None
            gov_path = Path(proj["gov_repo_path"]) if proj and proj.get("gov_repo_path") else None

            if item_type in ("bootstrap_dossier", "bootstrap"):
                dossier_res = self._bridge.get_alignment_dossier(target_uuid)
                return {
                    "success": True,
                    "item_type": "bootstrap_dossier",
                    "item": {
                        "item_id": "ALIGNMENT_DOSSIER",
                        "title": f"Dossier Unificado de Alineación Inicial OKF v2 — {proj.get('name') if proj else ''}",
                        "full_content": dossier_res.get("dossier_markdown", ""),
                        "status": proj.get("okf_alignment_status", "pending_bootstrap") if proj else "pending_bootstrap",
                    },
                }

            if item_type in ("ruling", "resolved_jury"):
                docs_okf = (
                    gov_path / "docs" / "okf" if (gov_path and (gov_path / "docs" / "okf").exists()) else gov_path
                )
                ruling_file = None
                if docs_okf and (docs_okf / "architecture" / "rulings").is_dir():
                    for f in (docs_okf / "architecture" / "rulings").glob("*.yaml"):
                        if item_id in f.stem:
                            ruling_file = f
                            break
                if ruling_file and ruling_file.exists():
                    ruling_data = yaml.safe_load(ruling_file.read_text(encoding="utf-8")) or {}
                    return {"success": True, "item_type": "ruling", "item": ruling_data}

            bundle = self.get_pending_human_approvals(target_uuid)
            if not bundle.get("success"):
                return bundle

            if item_type == "decision":
                for d in bundle.get("pending_decisions", []):
                    if d.get("item_id") == item_id or d.get("file_name") == item_id:
                        return {"success": True, "item_type": "decision", "item": d}
                if gov_path:
                    docs_okf = (
                        gov_path / "docs" / "okf" if (gov_path / "docs" / "okf").exists() else gov_path
                    )
                    if (docs_okf / "architecture" / "adrs").is_dir():
                        for f in (docs_okf / "architecture" / "adrs").glob("*.md"):
                            if item_id in f.stem:
                                txt = f.read_text(encoding="utf-8", errors="ignore")
                                return {
                                    "success": True,
                                    "item_type": "decision",
                                    "item": {
                                        "item_id": item_id,
                                        "title": f.stem,
                                        "full_content": txt,
                                        "status": "approved_adr",
                                    },
                                }
                return {"success": False, "error": f"Decisión {item_id} no encontrada"}

            elif item_type in ("jury_vote", "dispute", "arbiter_ruling"):
                tribunal = self._tribunal_service()
                for disp in bundle.get("open_disputes", []):
                    if disp.get("ticket_id") == item_id:
                        detail = (
                            tribunal.get_hitl_ticket_detail(target_uuid, item_id)
                            if tribunal is not None
                            else self.db.get_hitl_ticket_detail(target_uuid, item_id)
                        )
                        dialogue_turns = []
                        if detail:
                            for r in detail.get("a2a_messages", []):
                                try:
                                    p = json.loads(r["payload_rfc_yaml"])
                                except Exception:
                                    p = {"raw": r.get("payload_rfc_yaml")}
                                dialogue_turns.append({
                                    "message_uuid": r["message_uuid"],
                                    "sender_node_uuid": r["sender_node_uuid"],
                                    "intent": r["intent"],
                                    "payload": p,
                                    "created_at": r["created_at"],
                                })
                        disp["dialogue_turns"] = dialogue_turns
                        return {"success": True, "item_type": item_type, "item": disp}
                return {"success": False, "error": f"Ticket de disputa {item_id} no encontrado"}

            elif item_type in ("pr_merge", "task_review"):
                for pr in bundle.get("pending_prs", []):
                    if pr.get("card_id") == item_id:
                        return {"success": True, "item_type": "pr_merge", "item": pr}
                card = self.db.get_card_by_id(target_uuid, item_id)
                if card:
                    return {"success": True, "item_type": "pr_merge", "item": card}
                return {"success": False, "error": f"Tarea en revisión {item_id} no encontrada"}

            return {"success": False, "error": f"Tipo de ítem desconocido: {item_type}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def sign_and_approve_item(
        self,
        project_uuid: str,
        item_type: str,
        item_id: str,
        decision: str = "APPROVED",
        comment: str = "",
        voter_node: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            proj = self.db.get_project_by_uuid(project_uuid)
            if not proj:
                return {"success": False, "error": "Proyecto no encontrado"}

            gov_path = Path(proj["gov_repo_path"])
            app_path = Path(proj["code_repo_path"])
            local_node = self._local_node(voter_node)
            dev_name = self._dev_name()
            tribunal = self._tribunal_service()

            if item_type == "decision":
                docs_base = gov_path / "docs" / "okf" if (gov_path / "docs" / "okf").exists() else gov_path
                dec_dir = docs_base / "architecture" / "decisions"
                dec_file = None
                for f in dec_dir.glob("*.md"):
                    if item_id in f.name:
                        dec_file = f
                        break
                if not dec_file:
                    return {"success": False, "error": f"Decisión {item_id} no encontrada"}

                content = dec_file.read_text(encoding="utf-8")
                sig_block = self.crypto.sign_human_approval(
                    artifact_id=item_id,
                    content_text=content,
                    node_uuid=local_node,
                    developer_name=dev_name,
                    decision=decision,
                )
                adr_path = self.okf.move_decision_lifecycle(
                    gov_base_path=gov_path,
                    dec_id=item_id,
                    target_state="approved" if decision == "APPROVED" else "discarded",
                    reason_or_consequences=comment or "Aprobado formalmente mediante firma HITL.",
                    signature_block=sig_block,
                )
                try:
                    self.okf.export_precedents_index(gov_path)
                except Exception:
                    pass
                return {
                    "success": True,
                    "approved": decision == "APPROVED",
                    "item_id": item_id,
                    "adr_path": str(adr_path),
                    "signature": sig_block,
                }

            elif item_type == "jury_vote":
                dec_upper = str(decision).strip().upper()
                alias_to_canonical, canonical_to_alias = self.get_blind_options_mapping(item_id, local_node)

                if dec_upper in alias_to_canonical:
                    canonical_decision = alias_to_canonical[dec_upper]
                elif dec_upper in ("OPTION_A", "OPTION_B", "THIRD_WAY_C"):
                    canonical_decision = dec_upper
                else:
                    return {
                        "success": False,
                        "error": f"Opción de voto inválida '{decision}'. Debe ser ALFA, BETA o GAMMA.",
                    }

                sig_content = f"VOTE:{canonical_decision}:TICKET:{item_id}:PROJECT:{project_uuid}"
                sig_block = self.crypto.sign_human_approval(
                    artifact_id=item_id,
                    content_text=sig_content,
                    node_uuid=local_node,
                    developer_name=dev_name,
                    decision=canonical_decision,
                )
                if tribunal is not None:
                    vote_res = tribunal.submit_human_vote(
                        ticket_id=item_id,
                        node_uuid=local_node,
                        developer_name=dev_name,
                        voted_option=canonical_decision,
                        signature_ed25519=sig_block["signature_ed25519"],
                        public_key=sig_block.get("public_key", ""),
                        signed_at=sig_block.get("signed_at", ""),
                        content_sha256=sig_block.get("content_sha256", ""),
                        project_uuid=project_uuid,
                        actor=self._human_actor(local_node),
                    )
                else:
                    vote_res = self.bus.submit_human_jury_vote(
                        ticket_id=item_id,
                        node_uuid=local_node,
                        developer_name=dev_name,
                        voted_option=canonical_decision,
                        signature_ed25519=sig_block["signature_ed25519"],
                        public_key=sig_block.get("public_key", ""),
                        signed_at=sig_block.get("signed_at", ""),
                        content_sha256=sig_block.get("content_sha256", ""),
                        project_uuid=project_uuid,
                    )

                # EGM-37: Si falla el registro/validación del voto humano, abortar sin recalcular veredicto.
                if not vote_res.get("success"):
                    return {
                        "success": False,
                        "error": vote_res.get("error", "Fallo al registrar el voto humano"),
                        "code": vote_res.get("code"),
                        "vote": vote_res,
                    }

                verdict = (
                    tribunal.compute_verdict(project_uuid, item_id)
                    if tribunal is not None
                    else self.bus.compute_jury_verdict(project_uuid, item_id)
                )
                return {
                    "success": True,
                    "vote": vote_res,
                    "verdict": verdict,
                    "signature": sig_block,
                    "canonical_decision": canonical_decision,
                    "voted_alias": canonical_to_alias.get(canonical_decision, dec_upper),
                }

            elif item_type == "arbiter_ruling":
                # EGM-01-R: Laudo vinculante de árbitro externo designado
                dec_upper = str(decision).strip().upper()
                alias_to_canonical, _canonical_to_alias = self.get_blind_options_mapping(item_id, local_node)
                if dec_upper in alias_to_canonical:
                    canonical_option = alias_to_canonical[dec_upper]
                elif dec_upper in ("OPTION_A", "OPTION_B", "THIRD_WAY_C", "ASYMMETRIC_HYBRID"):
                    canonical_option = dec_upper
                else:
                    return {
                        "success": False,
                        "error": (
                            f"Opción de laudo inválida '{decision}'. "
                            "Debe ser OPTION_A, OPTION_B, THIRD_WAY_C, ASYMMETRIC_HYBRID o alias ALFA/BETA/GAMMA."
                        ),
                    }

                ruling_content = f"ARBITER_RULING:{canonical_option}:TICKET:{item_id}:PROJECT:{project_uuid}"
                sig_block = self.crypto.sign_human_approval(
                    artifact_id=item_id,
                    content_text=ruling_content,
                    node_uuid=local_node,
                    developer_name=dev_name,
                    decision=canonical_option,
                )
                if tribunal is not None:
                    ruling_res = tribunal.submit_arbiter_ruling(
                        ticket_id=item_id,
                        arbiter_node_uuid=local_node,
                        winning_option=canonical_option,
                        signature_ed25519=sig_block["signature_ed25519"],
                        rationale=comment,
                        public_key=sig_block.get("public_key", ""),
                        signed_at=sig_block.get("signed_at", ""),
                        content_sha256=sig_block.get("content_sha256", ""),
                        project_uuid=project_uuid,
                        developer_name=dev_name,
                        actor=self._human_actor(local_node),
                    )
                else:
                    ruling_res = self.bus.submit_external_arbiter_ruling(
                        ticket_id=item_id,
                        arbiter_node_uuid=local_node,
                        winning_option=canonical_option,
                        signature_ed25519=sig_block["signature_ed25519"],
                        rationale=comment,
                        public_key=sig_block.get("public_key", ""),
                        signed_at=sig_block.get("signed_at", ""),
                        content_sha256=sig_block.get("content_sha256", ""),
                        project_uuid=project_uuid,
                        developer_name=dev_name,
                    )
                if not ruling_res.get("success"):
                    return ruling_res
                return {**ruling_res, "signature": sig_block}

            elif item_type == "pr_merge":
                sig_block = self.crypto.sign_human_approval(
                    artifact_id=item_id,
                    content_text=f"PR_REVIEW:{decision}:ITEM:{item_id}",
                    node_uuid=local_node,
                    developer_name=dev_name,
                    decision=decision,
                )
                card = self.db.get_card_by_id(project_uuid, item_id)
                if decision in ("REJECTED", "REJECT"):
                    if card:
                        self.db.update_card_status(project_uuid, item_id, "rejected")
                    return {
                        "success": True,
                        "merged": False,
                        "rejected": True,
                        "item_id": item_id,
                        "signature": sig_block,
                    }
                elif decision in ("DISCARDED", "RETURN_TO_PROGRESS"):
                    if card:
                        self.db.update_card_status(project_uuid, item_id, "in_progress")
                    return {
                        "success": True,
                        "merged": False,
                        "returned_to_progress": True,
                        "item_id": item_id,
                        "signature": sig_block,
                    }

                if card:
                    branch_ref = (card.get("branch_ref") or "").strip()
                    if not branch_ref or branch_ref == "main":
                        merge_res: Dict[str, Any] = {
                            "success": True,
                            "merged": True,
                            "skipped_git_merge": True,
                            "reason": "La tarjeta no tiene una rama propia que fusionar.",
                        }
                    elif not app_path.exists():
                        merge_res = {"success": False, "error": f"No existe el repositorio de código local: {app_path}"}
                    else:
                        git_m = self.github.git_merge(app_path, source_branch=branch_ref, target_branch="main")
                        ok = bool(git_m.get("success"))
                        merge_res = {"success": ok, "merged": ok, "git_merge": git_m}
                        if not ok:
                            merge_res["error"] = (
                                f"Fallo al fusionar '{branch_ref}' en main: {git_m.get('error') or 'error desconocido'}"
                            )
                elif item_id.isdigit():
                    owner_repo = self._bridge._parse_github_owner_repo(proj.get("code_repo_url", ""))
                    if not owner_repo:
                        merge_res = {
                            "success": False,
                            "error": (
                                f"No se pudo resolver owner/repo de GitHub desde code_repo_url del proyecto; "
                                f"no se fusionó la PR #{item_id}."
                            ),
                        }
                    else:
                        merge_res = self.github.evaluate_and_merge_pull_request(
                            repo_owner=owner_repo[0],
                            repo_name=owner_repo[1],
                            pr_number=int(item_id),
                            local_repo_path=app_path,
                            force_auto_merge=True,
                        )
                else:
                    merge_res = {"success": False, "error": f"No existe la tarjeta '{item_id}' ni es un número de PR válido."}

                merge_ok = bool(merge_res.get("success"))
                if card and merge_ok:
                    self.db.update_card_status(project_uuid, item_id, "merged")

                result: Dict[str, Any] = {
                    "success": merge_ok,
                    "merged": merge_ok,
                    "item_id": item_id,
                    "details": merge_res,
                    "signature": sig_block,
                }
                if not merge_ok:
                    result["error"] = merge_res.get("error") or merge_res.get("reason") or "La fusión no se completó."
                return result

            elif item_type in ("bootstrap_dossier", "bootstrap"):
                return self._bridge.ratify_okf_bootstrap(project_uuid=project_uuid, comment=comment)

            else:
                return {"success": False, "error": f"item_type desconocido: {item_type}"}
        except Exception as e:
            return {"success": False, "error": str(e)}
