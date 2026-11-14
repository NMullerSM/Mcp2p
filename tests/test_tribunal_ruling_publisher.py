"""
EGM-56: el efecto lateral OKF/git del veredicto ratificado (YAML de ruling + índice de
precedentes + commit `ratify(jury): ...` con push) se publica desde el núcleo
(`DialecticTribunalService` / `OkfRulingPublisher`) a través de `OkfGovernancePort`, no
desde `A2ABus`. Estas pruebas caracterizan los argumentos exactos del commit y fijan que el
Tribunal es testeable con un doble de puerto, sin git real.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest

from conftest import make_test_db
from src.a2a.bus import A2ABus
from src.p2p.crypto import NodeCryptoManager


class _SpyOkf:
    """Doble de OkfGovernancePort: registra commits sin tocar git."""

    def __init__(self) -> None:
        self.commits: List[Dict[str, Any]] = []
        self.exports: List[str] = []

    def export_precedents_index(self, gov_base_path):
        self.exports.append(str(gov_base_path))
        return None

    def commit_gov_file(self, gov_base_path, file_path, commit_message, push_now=False):
        self.commits.append({
            "gov": str(gov_base_path),
            "files": [str(f) for f in file_path],
            "message": commit_message,
            "push_now": push_now,
        })
        return True


def _ratifiable_ticket(db, bus, tmp_path):
    proj = db.create_project(name="Tribunal EGM56", base_path=str(tmp_path / "projs"), init_git=False)
    p_uuid = proj["project_uuid"]
    ticket = bus.open_dispute_ticket(
        project_uuid=p_uuid,
        title="Disputa EGM-56",
        collision_reason="Colision de precedentes",
        contender_a_node="node_a",
        contender_b_node="node_b",
        proposal_a_ref="prop_a",
        proposal_b_ref="prop_b",
    )
    t_id = ticket["ticket_id"]
    owner_node = db.get_project_collaborators(p_uuid)[0]["node_uuid"]
    crypto = NodeCryptoManager(key_path=tmp_path / "keys" / "id.key")
    db.add_or_request_collaborator(
        p_uuid, owner_node, public_key=crypto.public_key_base64, role="owner", status="approved"
    )
    sig = crypto.sign_human_approval(
        artifact_id=t_id,
        content_text=f"VOTE:OPTION_A:TICKET:{t_id}:PROJECT:{p_uuid}",
        node_uuid=owner_node,
        developer_name="Human Dev",
        decision="OPTION_A",
    )
    res = bus.submit_human_jury_vote(
        ticket_id=t_id, node_uuid=owner_node, developer_name="Human Dev", voted_option="OPTION_A",
        signature_ed25519=sig["signature_ed25519"], public_key=sig["public_key"],
        signed_at=sig["signed_at"], content_sha256=sig["content_sha256"], project_uuid=p_uuid,
    )
    assert res["success"], res
    return db.get_project_by_uuid(p_uuid), t_id


def _assert_ratify_commit(commits, gov_path: str, t_id: str):
    assert len(commits) == 1
    c = commits[0]
    assert c["gov"] == gov_path
    assert c["push_now"] is True
    assert c["message"] == f"ratify(jury): {t_id} [OPTION_A] Disputa EGM-56\n\nColision de precedentes"
    assert c["files"][0].endswith(f"RULING-{t_id}.yaml")


def test_legacy_bus_verdict_commits_ruling_with_exact_message(tmp_path, monkeypatch):
    """Caracterización: un `A2ABus(db)` suelto conserva el commit+push de siempre."""
    from src.storage import okf_adapter as okf_mod

    spy = _SpyOkf()
    monkeypatch.setattr(okf_mod.OKFAdapter, "commit_gov_file",
                        lambda self, *a, **k: spy.commit_gov_file(*a, **k))
    db = make_test_db()
    bus = A2ABus(db=db)
    proj, t_id = _ratifiable_ticket(db, bus, tmp_path)

    verdict = bus.compute_jury_verdict(proj["project_uuid"], t_id)

    assert verdict["status"] == "ratified" and verdict["winning_option"] == "OPTION_A"
    assert verdict["ruling_yaml_path"] and Path(verdict["ruling_yaml_path"]).exists()
    _assert_ratify_commit(spy.commits, proj["gov_repo_path"], t_id)
    assert db.get_dispute_ticket(t_id)["final_adr_path"] == verdict["ruling_yaml_path"]


def test_kernel_tribunal_service_publishes_through_injected_okf_port(tmp_path, monkeypatch):
    """El servicio del núcleo usa el `OkfGovernancePort` inyectado; A2ABus no instancia OKFAdapter."""
    from src.core.factory import ApplicationFactory
    from src.storage import okf_adapter as okf_mod

    def _forbidden(*_a, **_k):
        raise AssertionError("A2ABus no debe construir OKFAdapter cuando el núcleo está compuesto")

    spy = _SpyOkf()
    db = make_test_db()
    kernel = ApplicationFactory.create_test_kernel(db_manager=db, okf_adapter=spy)
    proj, t_id = _ratifiable_ticket(db, kernel.bus, tmp_path)
    monkeypatch.setattr(okf_mod, "OKFAdapter", _forbidden)

    verdict = kernel.tribunal_service.compute_verdict(proj["project_uuid"], t_id)

    assert verdict["status"] == "ratified"
    _assert_ratify_commit(spy.commits, proj["gov_repo_path"], t_id)
    assert spy.exports == [proj["gov_repo_path"]]
    assert db.get_dispute_ticket(t_id)["final_adr_path"] == verdict["ruling_yaml_path"]

    # La ruta legacy (UI/tests que llaman al bus directamente) pasa por el mismo publicador.
    monkeypatch.undo()  # create_project usa OKFAdapter para inicializar la gobernanza
    proj2, t2 = _ratifiable_ticket(db, kernel.bus, tmp_path / "second")
    monkeypatch.setattr(okf_mod, "OKFAdapter", _forbidden)
    kernel.bus.compute_jury_verdict(proj2["project_uuid"], t2)
    assert len(spy.commits) == 2


def test_non_ratified_verdict_has_no_side_effects(tmp_path):
    from src.core.factory import ApplicationFactory

    spy = _SpyOkf()
    db = make_test_db()
    kernel = ApplicationFactory.create_test_kernel(db_manager=db, okf_adapter=spy)
    proj = db.create_project(name="Sin votos", base_path=str(tmp_path / "p"), init_git=False)
    ticket = kernel.bus.open_dispute_ticket(
        project_uuid=proj["project_uuid"], title="T", collision_reason="R",
        contender_a_node="node_a", contender_b_node="node_b",
        proposal_a_ref="a", proposal_b_ref="b",
    )
    verdict = kernel.tribunal_service.compute_verdict(proj["project_uuid"], ticket["ticket_id"])
    assert verdict["status"] != "ratified"
    assert verdict["ruling_yaml_path"] is None
    assert spy.commits == []
