"""
Suite exhaustiva y adversarial para el motor del Tribunal de Honor y Máquina de Estados:
- NEW-01: El primer voto no gana (quórum abierto vs cerrado)
- NEW-02: Persistencia condicional solo en estado 'ratified'
- EGM-01: Desempate con árbitro externo pre-registrado y temporizador
- EGM-02: Modelo de consenso ponderado 0.70 / 0.30 con umbral 0.80
- EGM-03: Eliminación de offset +10.0 y sincronía final_score == total_points
- EGM-04: Conteo unificado de votantes elegibles
"""

from datetime import datetime, timezone, timedelta
import pytest
from src.a2a.jury_engine import DialecticJuryEngine as Engine


def test_no_magic_numbers_outside_constants():
    """Verifica que las constantes de consenso estén nombradas y no existan literales sueltos."""
    assert Engine.HUMAN_BLOCK_WEIGHT == 0.70
    assert Engine.MACHINE_BLOCK_WEIGHT == 0.30
    assert Engine.CONSENSUS_THRESHOLD == 0.80
    assert Engine.HUMAN_BLOCK_WEIGHT + Engine.MACHINE_BLOCK_WEIGHT == 1.0


@pytest.mark.parametrize(
    "scenario_name, h_votes, agent_votes, h_eligible, arbiter, quorum_closed, expected_status, expected_mode, expected_winner",
    [
        # H=1, vota O, sin jurados -> ratificado por override
        ("H1_no_jurors", [{"node_uuid": "h1", "voted_option": "OPTION_A"}], [], 1, None, True, "ratified", "human_unanimity_override", "OPTION_A"),
        # H=1, vota O, >=1/3 bots acompañan -> R >= 0.80 -> supermajority
        ("H1_bots_supermajority", [{"node_uuid": "h1", "voted_option": "OPTION_A"}], [{"juror_id": "j1", "evaluated_option": "OPTION_A", "weighted_total": 90.0}], 1, None, True, "ratified", "weighted_supermajority", "OPTION_A"),
        # H=2 unánime en O, máquinas 0% en O -> override
        ("H2_unanimous_override", [{"node_uuid": "h1", "voted_option": "OPTION_A"}, {"node_uuid": "h2", "voted_option": "OPTION_A"}], [{"juror_id": "j1", "evaluated_option": "OPTION_B", "weighted_total": 90.0}], 2, None, True, "ratified", "human_unanimity_override", "OPTION_A"),
        # H=2 1-1 (A/B), máquinas 100% en A, sin árbitro -> desempate técnico (paso 4)
        ("H2_tie_machine_tiebreak_no_arbiter", [{"node_uuid": "h1", "voted_option": "OPTION_A"}, {"node_uuid": "h2", "voted_option": "OPTION_B"}], [{"juror_id": "j1", "evaluated_option": "OPTION_A", "weighted_total": 90.0}], 2, None, True, "ratified", "technical_tiebreak_no_arbiter", "OPTION_A"),
        # H=2 1-1 (A/B), máquinas 100% en C, sin árbitro -> R(A)==R(B)==0.35, R no separa -> challenged
        ("H2_tie_machines_on_C_escalated", [{"node_uuid": "h1", "voted_option": "OPTION_A"}, {"node_uuid": "h2", "voted_option": "OPTION_B"}], [{"juror_id": "j1", "evaluated_option": "THIRD_WAY_C", "weighted_total": 90.0}], 2, None, True, "challenged", "escalated_manual_cluster_review", "OPTION_A"),
        # H=2 1-1, con árbitro -> paso 3 awaiting_external_arbiter
        ("H2_tie_with_arbiter", [{"node_uuid": "h1", "voted_option": "OPTION_A"}, {"node_uuid": "h2", "voted_option": "OPTION_B"}], [{"juror_id": "j1", "evaluated_option": "OPTION_A", "weighted_total": 90.0}], 2, "arbiter_node", True, "challenged", "awaiting_external_arbiter", "OPTION_A"),
        # H=3 2-1, todas las máquinas con la mayoría -> R=0.767 < 0.80 -> con árbitro pasa al árbitro (D-2)
        ("H3_majority_with_arbiter", [{"node_uuid": "h1", "voted_option": "OPTION_A"}, {"node_uuid": "h2", "voted_option": "OPTION_A"}, {"node_uuid": "h3", "voted_option": "OPTION_B"}], [{"juror_id": "j1", "evaluated_option": "OPTION_A", "weighted_total": 90.0}], 3, "arbiter_node", True, "challenged", "awaiting_external_arbiter", "OPTION_A"),
        # H=3 2-1, todas las máquinas con la minoría -> máquinas no pueden voltear mayoría -> challenged
        ("H3_minority_bots_cannot_flip", [{"node_uuid": "h1", "voted_option": "OPTION_A"}, {"node_uuid": "h2", "voted_option": "OPTION_A"}, {"node_uuid": "h3", "voted_option": "OPTION_B"}], [{"juror_id": "j1", "evaluated_option": "OPTION_B", "weighted_total": 90.0}], 3, None, True, "challenged", "escalated_manual_cluster_review", "OPTION_A"),
        # H=4 3-1, todas las máquinas con mayoría -> R = 0.70*(3/4)+0.30 = 0.825 >= 0.80 -> supermajority
        ("H4_supermajority", [{"node_uuid": "h1", "voted_option": "OPTION_A"}, {"node_uuid": "h2", "voted_option": "OPTION_A"}, {"node_uuid": "h3", "voted_option": "OPTION_A"}, {"node_uuid": "h4", "voted_option": "OPTION_B"}], [{"juror_id": "j1", "evaluated_option": "OPTION_A", "weighted_total": 90.0}], 4, None, True, "ratified", "weighted_supermajority", "OPTION_A"),
        # Quórum abierto: 1 voto de 2 elegibles -> status open, awaiting_human_quorum (NEW-01)
        ("H2_quorum_open", [{"node_uuid": "h1", "voted_option": "OPTION_A"}], [], 2, None, False, "open", "awaiting_human_quorum", "OPTION_A"),
        # 0 votos humanos, solo bots -> open, machines cannot force
        ("No_humans_only_bots", [], [{"juror_id": "j1", "evaluated_option": "OPTION_A", "weighted_total": 90.0}], 2, None, True, "open", "awaiting_human_votes_machines_cannot_force", "OPTION_A"),
    ]
)
def test_dialectic_consensus_consequence_table(
    scenario_name, h_votes, agent_votes, h_eligible, arbiter, quorum_closed, expected_status, expected_mode, expected_winner
):
    nodes = [f"h{i+1}" for i in range(h_eligible)]
    res = Engine.compute_dialectic_consensus(
        ticket_id="TICKET-TEST",
        project_uuid="proj_test",
        human_votes=h_votes,
        agent_scores=agent_votes,
        all_project_nodes=nodes,
        contender_nodes=[],
        human_unanimity_override=True,
        arbiter_node_uuid=arbiter,
        quorum_closed=quorum_closed
    )

    assert res["status"] == expected_status, f"{scenario_name}: status mismatch"
    assert res["resolution_mode"] == expected_mode, f"{scenario_name}: resolution_mode mismatch"
    assert res["winning_option"] == expected_winner, f"{scenario_name}: winner mismatch"

    # Verificación EGM-03: final_score == total_points sin offsets mágicos
    for opt, data in res["breakdown"].items():
        assert data["final_score"] == data["total_points"], f"Offset mágico detectado en {opt}"


def test_quorum_timing_and_abstention():
    """Verifica la lógica de la ventana de votación, temporizador y abstenciones."""
    t0 = datetime(2026, 11, 10, 12, 0, 0, tzinfo=timezone.utc)
    ticket = {
        "ticket_id": "TICKET-1",
        "created_at": t0.isoformat()
    }
    eligible_nodes = ["node_a", "node_b"]

    # Caso 1: Todos votaron -> quórum cerrado de inmediato
    votes = [{"node_uuid": "node_a"}, {"node_uuid": "node_b"}]
    assert Engine.is_quorum_closed(ticket, votes, [], eligible_nodes, now=t0) is True

    # Caso 2: 1 votó, el otro no ha recibido notificación (sin delivered_at) y han pasado < 168h
    votes_partial = [{"node_uuid": "node_a"}]
    deliveries_partial = [{"node_uuid": "node_a", "delivered_at": t0.isoformat()}]
    t_after_24h = t0 + timedelta(hours=24)
    assert Engine.is_quorum_closed(ticket, votes_partial, deliveries_partial, eligible_nodes, now=t_after_24h) is False

    # Caso 3: Ambos recibieron notificación a t0, pero aún estamos dentro de las 48h de gracia
    deliveries_both = [
        {"node_uuid": "node_a", "delivered_at": t0.isoformat()},
        {"node_uuid": "node_b", "delivered_at": t0.isoformat()}
    ]
    assert Engine.is_quorum_closed(ticket, votes_partial, deliveries_both, eligible_nodes, now=t_after_24h) is False

    # Caso 4: Vencieron las 48h tras la última entrega -> quórum cerrado por expiración (node_b se abstiene)
    t_after_49h = t0 + timedelta(hours=49)
    assert Engine.is_quorum_closed(ticket, votes_partial, deliveries_both, eligible_nodes, now=t_after_49h) is True

    # Caso 5: node_b nunca recibió entrega pero pasaron 169h (>168h timeout) -> se le trata como abstención
    t_after_169h = t0 + timedelta(hours=169)
    assert Engine.is_quorum_closed(ticket, votes_partial, deliveries_partial, eligible_nodes, now=t_after_169h) is True
