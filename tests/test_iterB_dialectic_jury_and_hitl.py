"""
Suite de Pruebas de Integración — Iteración B & C:
1. Motor de Juicio Dialéctico en 3 Fases (Tesis A, Antítesis B, Síntesis Tercera Vía C).
2. Matriz de Neutralidad Anti-Sycophancy (0..4 focos estocásticos, temperaturas, Clean-Room prompt).
3. Exclusión de Contendientes (N >= 3 vs N = 2) y Tope Matemático de Subagentes (57.14% Humano vs 42.86% Máquina).
4. Algoritmo de Consenso del 80% y Supremacía Humana Unánime.
5. Persistencia de Dictámenes YAML OKF v2 y Exportador de Precedentes (precedents_index.yaml).
6. Inspector HITL Profundo (get_hitl_item_detail) con protección contra sesgo cognitivo antes del voto humano.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

from src.a2a.jury_engine import CLEAN_ROOM_ALLIANCE_PROMPT, FOCUS_CATALOG, DialecticJuryEngine
from src.app.api_bridge import ApiBridge
from src.storage.db import DatabaseManager


def test_voter_universe_contender_exclusion_and_small_cluster_fallback():
    """Verifica que para N >= 3 se excluyan contendientes y para N = 2 se habilite votación post-síntesis."""
    # Caso N = 4 nodos totales, 2 en conflicto -> 2 elegibles
    u_large = DialecticJuryEngine.resolve_voter_universe(
        all_project_nodes=["node_1", "node_2", "node_3", "node_4"],
        contender_nodes=["node_1", "node_2"]
    )
    assert u_large["exclusion_mode"] == "strict_contender_exclusion"
    assert u_large["excluded_nodes"] == ["node_1", "node_2"]
    assert u_large["eligible_human_nodes"] == ["node_3", "node_4"]
    assert u_large["eligible_humans_count"] == 2
    assert u_large["human_share_ratio"] == pytest.approx(2.0 / 3.5, rel=1e-3)
    assert u_large["agent_share_ratio"] == pytest.approx(1.5 / 3.5, rel=1e-3)

    # Caso N = 2 nodos totales en conflicto -> fallback post-síntesis sin bloqueo
    u_small = DialecticJuryEngine.resolve_voter_universe(
        all_project_nodes=["node_a", "node_b"],
        contender_nodes=["node_a", "node_b"]
    )
    assert u_small["exclusion_mode"] == "small_cluster_post_synthesis_voting"
    assert u_small["excluded_nodes"] == []
    assert set(u_small["eligible_human_nodes"]) == {"node_a", "node_b"}


def test_neutral_anti_sycophancy_matrix_generation():
    """Verifica el muestreo de 0 a 4 focos, temperaturas y ausencia de herencia del prompt anfitrión."""
    matrix = DialecticJuryEngine.generate_neutral_jury_matrix(
        eligible_humans_count=2,
        requested_jurors=5,
        temperature_mode="hybrid",
        seed=42
    )
    assert matrix["jurors_count"] == 5
    # Para H=2 (4.0 pts humanos), el pool total de 5 subagentes no debe superar 2 * 1.5 = 3.0 pts (0.6 c/u)
    assert matrix["effective_agent_unit_weight"] == pytest.approx(0.6, rel=1e-3)
    assert matrix["total_agent_pool_weight"] == pytest.approx(3.0, rel=1e-3)
    assert matrix["total_human_pool_weight"] == pytest.approx(4.0, rel=1e-3)

    synth = matrix["synthesizer_agent"]
    assert synth["inherits_host_prompt"] is False
    assert 0.60 <= synth["temperature"] <= 0.70

    for idx, j in enumerate(matrix["jurors"]):
        assert 0 <= j["focus_count"] <= 4
        assert len(j["focus_vectors"]) == j["focus_count"]
        for fk in j["focus_vectors"]:
            assert fk in FOCUS_CATALOG
        assert j["inherits_host_prompt"] is False
        assert CLEAN_ROOM_ALLIANCE_PROMPT in j["clean_room_prompt"]
        if idx == 0:
            assert 0.10 <= j["temperature"] <= 0.20
        else:
            assert 0.20 <= j["temperature"] <= 0.90


def test_dialectic_consensus_80_threshold_and_human_supremacy():
    """
    Verifica:
    1. Si todos los subagentes votan OPTION_A pero los humanos votan THIRD_WAY_C, ganan siempre los humanos.
    2. Si las máquinas votan solas sin humanos, jamás pueden ratificar (quedan en provisional_ruling).
    3. Cuando humanos + agentes superan el 80% en THIRD_WAY_C, se alcanza supermajority_80_percent.
    """
    # Escenario 1: Máquinas solas (3 subagentes votan OPTION_B, 0 humanos han votado)
    agent_scores_b = [
        {"juror_id": f"j_{i}", "evaluated_option": "OPTION_B", "weighted_total": 92.0, "rationale_summary": "Pro B"}
        for i in range(3)
    ]
    res_machines_only = DialecticJuryEngine.compute_dialectic_consensus(
        ticket_id="T-1",
        project_uuid="P-1",
        human_votes=[],
        agent_scores=agent_scores_b,
        all_project_nodes=["h1", "h2"],
        contender_nodes=["h1", "h2"]
    )
    assert res_machines_only["consensus_reached"] is False
    # En EGM-02 / NEW-01: máquinas solas no pueden forzar quórum ni cerrar el ticket; queda en status 'open'
    assert res_machines_only["status"] == "open"
    assert res_machines_only["resolution_mode"] == "awaiting_human_votes_machines_cannot_force"

    # Escenario 2: 3 subagentes votan OPTION_B, pero los humanos votan unánimemente THIRD_WAY_C
    human_votes_c = [
        {"node_uuid": "h1", "voted_option": "THIRD_WAY_C", "weight": 2.0},
        {"node_uuid": "h2", "voted_option": "THIRD_WAY_C", "weight": 2.0},
    ]
    res_human_override = DialecticJuryEngine.compute_dialectic_consensus(
        ticket_id="T-2",
        project_uuid="P-1",
        human_votes=human_votes_c,
        agent_scores=agent_scores_b,
        all_project_nodes=["h1", "h2"],
        contender_nodes=["h1", "h2"]
    )
    assert res_human_override["winning_option"] == "THIRD_WAY_C"
    assert res_human_override["consensus_reached"] is True
    assert res_human_override["status"] == "ratified"
    assert res_human_override["resolution_mode"] == "human_unanimity_override"

    # Escenario 3: Supermayoría >= 80% cuando humanos y al menos 2 de 3 subagentes coinciden en THIRD_WAY_C
    agent_scores_c = [
        {"juror_id": "j_0", "evaluated_option": "THIRD_WAY_C", "weighted_total": 95.0, "rationale_summary": "Pro C"},
        {"juror_id": "j_1", "evaluated_option": "THIRD_WAY_C", "weighted_total": 91.0, "rationale_summary": "Pro C"},
        {"juror_id": "j_2", "evaluated_option": "OPTION_A", "weighted_total": 88.0, "rationale_summary": "Pro A"},
    ]
    res_super = DialecticJuryEngine.compute_dialectic_consensus(
        ticket_id="T-3",
        project_uuid="P-1",
        human_votes=human_votes_c,
        agent_scores=agent_scores_c,
        all_project_nodes=["h1", "h2"],
        contender_nodes=["h1", "h2"]
    )
    assert res_super["winning_option"] == "THIRD_WAY_C"
    assert res_super["consensus_ratio"] >= 0.80
    # En EGM-02: la supermayoría ponderada (R >= 0.80) se identifica como weighted_supermajority
    assert res_super["resolution_mode"] == "weighted_supermajority"


def test_hitl_deep_inspector_and_okf_precedents_export(tmp_path: Path, monkeypatch):
    """
    Verifica de extremo a extremo en ApiBridge:
    - Apertura de disputa con Tercera Vía (THIRD_WAY_C) y matriz de subagentes neutrales.
    - get_hitl_item_detail ocultando puntajes numéricos antes del voto humano (protección de sesgo).
    - Emisión de voto humano por THIRD_WAY_C con firma Ed25519 -> persistencia de RULING YAML y precedents_index.yaml.
    - Revisión y fusión de tarea en revisión (TASK-102) desde HITL.
    """
    db_file = tmp_path / "test_iterB.db"
    db_mgr = DatabaseManager(str(db_file))
    api = ApiBridge(db_manager=db_mgr)

    proj_res = api.create_project("Test Dialectic Suite", str(tmp_path / "ws_dialectic"))
    assert proj_res["success"] is True
    puuid = proj_res["project"]["project_uuid"]
    gov_path = Path(proj_res["project"]["gov_repo_path"])

    # 1. Crear tarjeta en revisión TASK-102
    api.create_kanban_card("TASK-102", "Catálogo de Productos P2P", "Microservicio de catálogo", "1. Latencia < 5ms")
    api.update_task_status("TASK-102", "in_review", branch_ref="feature/task-102")

    # 2. Abrir ticket de Juicio de Honor con Síntesis (Tercera Vía C)
    ticket = api.bus.open_dispute_ticket(
        project_uuid=puuid,
        ticket_id="TICKET-500",
        card_id="TASK-102",
        title="Indexación Full-Text: SQLite FTS5 vs Tantivy Rust Binding",
        collision_reason="Consumo de RAM vs velocidad de indexación incremental.",
        contender_a_node="node_alpha",
        contender_b_node="node_beta",
        proposal_a_ref="Usar extensión nativa SQLite FTS5 en el mismo archivo WAL.",
        proposal_b_ref="Usar índice externo Tantivy en memoria mapeada.",
        proposal_c_ref="Síntesis C: Usar SQLite FTS5 por defecto con adaptador pluggable para colecciones >1M.",
        num_evaluators=3
    )
    assert ticket["proposal_c_ref"].startswith("Síntesis C:")

    # 3. Inspeccionar antes de votar: las discusiones mayéuticas están presentes pero los puntajes ocultos
    detail_before = api.get_hitl_item_detail(puuid, "jury_vote", "TICKET-500")
    assert detail_before["success"] is True
    item_before = detail_before["item"]
    assert len(item_before["mayeutic_discussions"]) == 3
    assert item_before["mayeutic_discussions"][0]["scores_hidden_until_human_vote"] is True
    assert "bias_protection" in item_before["votes_summary"]

    # 4. Votar por THIRD_WAY_C mediante sign_and_approve_item
    vote_res = api.sign_and_approve_item(
        project_uuid=puuid,
        item_type="jury_vote",
        item_id="TICKET-500",
        decision="THIRD_WAY_C",
        comment="Aprobada la Tercera Vía C tras revisar mayéutica."
    )
    assert vote_res["success"] is True
    assert vote_res["verdict"]["winning_option"] == "THIRD_WAY_C"

    # Verificar que se generó el archivo YAML del dictamen y el índice de precedentes OKF
    ruling_file = gov_path / "docs" / "okf" / "architecture" / "rulings" / "RULING-TICKET-500.yaml"
    assert ruling_file.exists()
    ruling_data = yaml.safe_load(ruling_file.read_text(encoding="utf-8"))
    assert ruling_data["phases"]["phase_3_voting"]["winning_option"] == "THIRD_WAY_C"

    precedents_file = gov_path / "docs" / "okf" / "architecture" / "precedents_index.yaml"
    assert precedents_file.exists()
    prec_data = yaml.safe_load(precedents_file.read_text(encoding="utf-8"))
    assert prec_data["total_precedents"] >= 1
    assert any(p["id"] == "RULING-TICKET-500" for p in prec_data["precedents"])

    # 5. Inspeccionar y aprobar TASK-102 en revisión
    pr_detail = api.get_hitl_item_detail(puuid, "pr_merge", "TASK-102")
    assert pr_detail["success"] is True
    assert pr_detail["item"]["acceptance_criteria"] == "1. Latencia < 5ms"

    # F1/F2: sign_and_approve_item ya no marca 'merged' si no hay un merge real que hacer,
    # así que para validar la ruta de éxito hay que fusionar una rama que exista de verdad.
    monkeypatch.setenv("GIT_AUTHOR_NAME", "MCP2P Test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@mcp2p.local")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "MCP2P Test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@mcp2p.local")
    app = Path(proj_res["project"]["code_repo_path"])
    subprocess.run(["git", "checkout", "-b", "feature/task-102"], cwd=app, capture_output=True)
    (app / "catalog.py").write_text("# catalogo de productos\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=app, capture_output=True)
    subprocess.run(["git", "commit", "-m", "feat: catalog"], cwd=app, capture_output=True)
    subprocess.run(["git", "checkout", "main"], cwd=app, capture_output=True)

    merge_res = api.sign_and_approve_item(
        project_uuid=puuid,
        item_type="pr_merge",
        item_id="TASK-102",
        decision="APPROVED",
        comment="Entrega revisada y aprobada."
    )
    assert merge_res["success"] is True
    assert merge_res["merged"] is True
    assert (app / "catalog.py").exists()
    updated_card = api.db.get_card_by_id(puuid, "TASK-102")
    assert updated_card["status"] == "merged"
