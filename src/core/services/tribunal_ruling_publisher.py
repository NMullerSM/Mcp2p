"""
Publicación del laudo ratificado del Tribunal en la gobernanza OKF (EGM-56).

Antes vivía dentro de `A2ABus._persist_and_commit_ruling` (el bus construía `OKFAdapter()`
y hacía commit + push al calcular un veredicto). Ahora es un componente del núcleo que solo
habla con `OkfGovernancePort`; `DialecticTribunalService` lo posee y `A2ABus` lo recibe
inyectado desde la raíz de composición.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("mcp2p.core.tribunal_ruling")


class OkfRulingPublisher:
    """Escribe el YAML de ruling, refresca el índice de precedentes y comitea (con push)."""

    def __init__(
        self,
        okf_port: Any,
        project_lookup: Callable[[str], Optional[Dict[str, Any]]],
        jury_engine: Any,
    ) -> None:
        self.okf_port = okf_port
        self.project_lookup = project_lookup
        self.jury_engine = jury_engine

    def publish(self, project_uuid: str, ticket: Dict[str, Any], verdict: Dict[str, Any]) -> Optional[str]:
        """Devuelve la ruta del ruling YAML o None (proyecto sin gov_repo_path o fallo no fatal)."""
        proj = self.project_lookup(project_uuid)
        if not proj or not proj.get("gov_repo_path"):
            return None
        try:
            matrix_list = []
            if ticket.get("jury_matrix_json"):
                try:
                    matrix_list = json.loads(ticket["jury_matrix_json"])
                except Exception:
                    matrix_list = []
            synth_data = {
                "option_id": "THIRD_WAY_C",
                "generated_by": "agent" if ticket.get("proposal_c_ref") else "pending",
                "summary": ticket.get("proposal_c_ref", ""),
            }
            ruling_path = self.jury_engine.persist_ruling_yaml(
                gov_base_path=proj["gov_repo_path"],
                ticket=ticket,
                verdict=verdict,
                synthesis_data=synth_data,
                neutral_matrix=matrix_list,
            )
            ruling_yaml_path = str(ruling_path)

            files_to_commit = [ruling_path]
            try:
                prec_path = self.okf_port.export_precedents_index(proj["gov_repo_path"])
                if prec_path and Path(prec_path).exists():
                    files_to_commit.append(prec_path)
            except Exception as prec_err:
                logger.debug("Omitida exportación de precedentes en ruling: %s", prec_err)

            ticket_id = ticket.get("ticket_id", "")
            winning_option = verdict.get("winning_option", "")
            title = (ticket.get("title") or "").strip()
            collision_reason = (ticket.get("collision_reason") or "").strip()
            commit_message = f"ratify(jury): {ticket_id} [{winning_option}] {title}"
            if collision_reason:
                commit_message += f"\n\n{collision_reason}"
            self.okf_port.commit_gov_file(proj["gov_repo_path"], files_to_commit, commit_message, push_now=True)
            return ruling_yaml_path
        except Exception as e:
            logger.warning("No se pudo persistir ruling YAML para %s: %s", ticket.get("ticket_id"), e)
            return None
