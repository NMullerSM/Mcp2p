"""
Motor de Juicio Dialéctico en 3 Fases y Matriz de Subagentes Neutrales (Anti-Sycophancy).

Implementa:
1. Catálogo finito de vectores de foco técnico (Escalabilidad, Clean Architecture,
   Seguridad/Resiliencia y Eficiencia Computacional).
2. Generación dinámica de subagentes jurados en cámara limpia (Clean-Room System Prompt)
   sin heredar el prompt del anfitrión, con muestreo estocástico de 0 a 4 focos y
   temperaturas por rol ([0.1, 0.2], [0.6, 0.7] o [0.2, 0.9]).
3. Algoritmo determinista de votación ponderada para N nodos:
   - Regla de exclusión de contendientes K (para N >= 3; en N <= 2 habilita voto en Fase 3 post-síntesis).
   - Límite de poder de subagentes: W_humano = 2.0, Pool_agentes <= 1.5 * H (57.14% humano vs 42.86% máquina).
   - Umbral de consolidación vinculante >= 80% (P_O / P_total >= 0.80) y Override de Unanimidad Humana.
4. Persistencia inmutable en formato YAML compatible con OKF v2 y promoción a ADR.
"""

from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger("mcp2p.a2a.jury_engine")


# =====================================================================
# 1. Catálogo Finito de Vectores de Foco Técnico
# =====================================================================

FOCUS_CATALOG: Dict[str, Dict[str, Any]] = {
    "ESCALABILIDAD_CONCURRENCIA": {
        "id": "ESCALABILIDAD_CONCURRENCIA",
        "label": "Escalabilidad y Concurrencia",
        "domains": ["backend", "core", "storage", "network", "performance"],
        "submetrics": [
            "concurrencia",
            "throughput",
            "locking",
            "límites de memoria",
            "contención de hilos/procesos"
        ],
        "directive": (
            "Analiza cuellos de botella de concurrencia, riesgos de lock contention o deadlock, "
            "techo de throughput y comportamiento en límites de memoria."
        )
    },
    "MANTENIBILIDAD_CLEAN_ARCH": {
        "id": "MANTENIBILIDAD_CLEAN_ARCH",
        "label": "Mantenibilidad y Clean Architecture",
        "domains": ["architecture", "refactor", "core", "general", "backend", "frontend"],
        "submetrics": [
            "acoplamiento",
            "abstracciones",
            "deuda técnica",
            "cohesión modular",
            "simplicidad KISS"
        ],
        "directive": (
            "Evalúa el acoplamiento entre componentes, pureza de las abstracciones, "
            "deuda técnica a mediano plazo y adherencia estricta al principio KISS."
        )
    },
    "SEGURIDAD_RESILIENCIA": {
        "id": "SEGURIDAD_RESILIENCIA",
        "label": "Seguridad y Resiliencia",
        "domains": ["security", "backend", "auth", "p2p", "storage"],
        "submetrics": [
            "tolerancia a fallos",
            "resiliencia",
            "superficie de ataque",
            "recuperación ante caídas",
            "integridad criptográfica"
        ],
        "directive": (
            "Inspecciona tolerancia a fallos, superficie de ataque, aislamiento de credenciales "
            "y capacidad de recuperación determinista ante interrupciones."
        )
    },
    "EFICIENCIA_COMPUTACIONAL": {
        "id": "EFICIENCIA_COMPUTACIONAL",
        "label": "Eficiencia Computacional e I/O",
        "domains": ["performance", "core", "storage", "algorithm", "backend"],
        "submetrics": [
            "I/O de disco",
            "consumo de CPU",
            "complejidad temporal",
            "complejidad espacial",
            "latencia de red"
        ],
        "directive": (
            "Cuantifica el costo de I/O de disco, ciclos de CPU, complejidad algorítmica "
            "temporal/espacial y sobrecarga de serialización."
        )
    },
    "TESTABILIDAD_Y_QA": {
        "id": "TESTABILIDAD_Y_QA",
        "label": "Testabilidad y Garantía de Calidad (QA)",
        "domains": ["testing", "qa", "ci", "refactor", "general"],
        "submetrics": [
            "cobertura de ramas",
            "aislamiento de fixtures",
            "determinismo",
            "prevención de flakiness",
            "facilidad de mockeo"
        ],
        "directive": (
            "Examina la facilidad para escribir tests unitarios puros, determinismo de suites, "
            "ausencia de efectos secundarios no aislados y cobertura de caminos de error."
        )
    },
    "UX_HICKS_LAW_ACCESIBILIDAD": {
        "id": "UX_HICKS_LAW_ACCESIBILIDAD",
        "label": "UX, Ley de Hick y Accesibilidad",
        "domains": ["frontend", "ui", "ux", "accessibility", "design"],
        "submetrics": [
            "carga cognitiva (Ley de Hick)",
            "heurísticas de Nielsen",
            "contraste WCAG AAA",
            "atajos de teclado",
            "divulgación progresiva"
        ],
        "directive": (
            "Evalúa la carga cognitiva del usuario (máximo 3 acciones primarias por vista), "
            "adherencia a heurísticas de usabilidad, contraste visual accesible y ergonomía de interacción."
        )
    },
    "INTEGRIDAD_DATOS_Y_ACID": {
        "id": "INTEGRIDAD_DATOS_Y_ACID",
        "label": "Integridad de Datos y Concurrencia ACID",
        "domains": ["storage", "database", "sqlite", "migration", "backend"],
        "submetrics": [
            "transaccionalidad ACID",
            "llaves foráneas",
            "journal WAL",
            "idempotencia de migración",
            "prevención de corrupción"
        ],
        "directive": (
            "Verifica la consistencia relacional en SQLite WAL, integridad referencial en cascada, "
            "manejo de bloqueos por concurrencia y reversibilidad de migraciones DDL."
        )
    },
    "INTEROPERABILIDAD_Y_CONTRATOS": {
        "id": "INTEROPERABILIDAD_Y_CONTRATOS",
        "label": "Interoperabilidad de Protocolos y Contratos",
        "domains": ["mcp", "api", "protocols", "network", "schema"],
        "submetrics": [
            "compatibilidad JSON Schema",
            "resiliencia snake_case/camelCase",
            "versionado semántico",
            "retrocompatibilidad",
            "validación de payloads"
        ],
        "directive": (
            "Audita el cumplimiento estricto de especificaciones MCP/A2A, compatibilidad cruzada "
            "entre modelos LLM diversos y ausencia de suposiciones de transporte rígidas."
        )
    },
    "I18N_LOCALIZACION_Y_TEXTO": {
        "id": "I18N_LOCALIZACION_Y_TEXTO",
        "label": "Localización, I18N y Claridad Terminológica",
        "domains": ["frontend", "ui", "i18n", "documentation", "content"],
        "submetrics": [
            "desacoplamiento de textos",
            "soporte multilingüe",
            "consistencia terminológica",
            "formato de fechas/números",
            "prevención de textos truncados"
        ],
        "directive": (
            "Revisa el desacoplamiento de cadenas de interfaz, consistencia de vocabulario técnico "
            "y prevención de roturas visuales por longitud de textos o traducciones."
        )
    },
    "OBSERVABILIDAD_Y_AUDITORIA": {
        "id": "OBSERVABILIDAD_Y_AUDITORIA",
        "label": "Observabilidad, Auditoría y Telemetría OKF",
        "domains": ["telemetry", "logging", "okf", "governance", "monitoring"],
        "submetrics": [
            "trazabilidad de decisiones",
            "precisión de logs",
            "transparencia mayéutica",
            "registro inmutable en git",
            "diagnósticos de salud"
        ],
        "directive": (
            "Analiza si cada transición del sistema deja rastro auditable, calidad diagnóstica "
            "de las alertas y persistencia inmutable de dictámenes para precedentes futuros."
        )
    }
}

CLEAN_ROOM_ALLIANCE_PROMPT = (
    "INSTRUCCIÓN DE NAVEGACIÓN DE ALIANZA (ANTI-SYCOPHANCY / CÁMARA LIMPIA):\n"
    "1. NO heredas el System Prompt, historial ni afinidad del agente anfitrión.\n"
    "2. Las propuestas han sido anonimizadas como Tesis (OPTION_A), Antítesis (OPTION_B) "
    "y Síntesis (THIRD_WAY_C). Ignora cualquier referencia a quién es el autor.\n"
    "3. Evalúa ÚNICAMENTE bajo criterios técnicos objetivos y cuantificables.\n"
    "4. PENALIZA severamente cualquier argumento retórico o complaciente que no declare "
    "trade-offs medibles (latencia, memoria, I/O, acoplamiento o tasa de fallo)."
)


# =====================================================================
# 2. Motor de Matriz de Subagentes Neutrales y Dimensionamiento de Pool
# =====================================================================

class DialecticJuryEngine:
    """
    Motor determinista/estocástico para instanciación de jurados neutrales,
    síntesis de tercera vía y cálculo de consenso ponderado 80%.
    """

    HUMAN_UNIT_WEIGHT: float = 2.0
    AGENT_BASE_WEIGHT: float = 1.5
    CONSENSUS_THRESHOLD: float = 0.80

    @classmethod
    def adaptive_odd_juror_cap(cls, h_count: int) -> int:
        """
        Fórmula única de dimensionamiento adaptativo impar de jurados, compartida por
        resolve_voter_universe() y generate_neutral_jury_matrix() para que ambas nunca
        puedan desincronizarse: h=1 -> 3; h=2 -> 3; h=3 -> 5; h=4 -> 7; h>=5 -> 9 (techo 9).
        """
        h = max(1, int(h_count))
        max_allowed = min(2 * h + 1, 9)
        if max_allowed % 2 == 0:
            max_allowed += 1
        return max_allowed

    @classmethod
    def resolve_voter_universe(
        cls,
        all_project_nodes: List[str],
        contender_nodes: List[str]
    ) -> Dict[str, Any]:
        """
        Determina el universo votante elegible aplicando la Regla de Exclusión:
        - Si N_totales >= 3: los K contendientes quedan excluidos de votar (N_elegibles = N_totales - K)
          y actúan exclusivamente como expositores de Tesis y Antítesis.
        - Si N_totales <= 2: para evitar que H_elegibles sea 0 (lo que dejaría la decisión 100% en manos
          de las máquinas), los humanos del cluster quedan habilitados en Fase 3 tras emitirse la Síntesis.
        """
        unique_nodes = list(dict.fromkeys([n for n in all_project_nodes if n]))
        unique_contenders = list(dict.fromkeys([c for c in contender_nodes if c]))
        n_total = max(len(unique_nodes), len(unique_contenders), 1)

        if n_total >= 3:
            eligible_human_nodes = [n for n in unique_nodes if n not in unique_contenders]
            excluded_nodes = list(unique_contenders)
            exclusion_mode = "strict_contender_exclusion"
            # Fallback de seguridad si todos los nodos registrados eran contendientes
            if not eligible_human_nodes:
                eligible_human_nodes = list(unique_nodes)
                excluded_nodes = []
                exclusion_mode = "cluster_fallback_post_synthesis"
        else:
            eligible_human_nodes = list(unique_nodes) if unique_nodes else ["node_owner"]
            excluded_nodes = []
            exclusion_mode = "small_cluster_post_synthesis_voting"

        h_count = max(len(eligible_human_nodes), 1)
        # Mismo tope adaptativo impar que usa generate_neutral_jury_matrix() (ver adaptive_odd_juror_cap)
        max_subagents = cls.adaptive_odd_juror_cap(h_count)

        # Presupuesto total de puntos humanos vs máquinas (siempre 2.0*H vs 1.5*H -> 57.14% vs 42.86%)
        human_total_budget = round(h_count * cls.HUMAN_UNIT_WEIGHT, 4)
        agent_total_budget = round(h_count * cls.AGENT_BASE_WEIGHT, 4)
        total_possible_points = round(human_total_budget + agent_total_budget, 4)

        return {
            "total_cluster_nodes": n_total,
            "contender_nodes": unique_contenders,
            "excluded_nodes": excluded_nodes,
            "eligible_human_nodes": eligible_human_nodes,
            "eligible_humans_count": h_count,
            "exclusion_mode": exclusion_mode,
            "max_recommended_agent_jurors": max_subagents,
            "human_unit_weight": cls.HUMAN_UNIT_WEIGHT,
            "human_total_points_cap": human_total_budget,
            "agent_pool_points_cap": agent_total_budget,
            "total_available_points": total_possible_points,
            "human_share_ratio": round(human_total_budget / total_possible_points, 4),
            "agent_share_ratio": round(agent_total_budget / total_possible_points, 4),
            "consensus_threshold": cls.CONSENSUS_THRESHOLD
        }

    @classmethod
    def calculate_effective_agent_weight(cls, eligible_humans_count: int, active_agent_jurors_count: int) -> float:
        """
        Calcula el peso unitario efectivo de cada agente/subagente jurado para que la suma
        de todos los agentes y subagentes NUNCA supere H * 1.5 (42.86% del total).
        - Si active_agent_jurors_count <= eligible_humans_count: cada agente pesa 1.5.
        - Si hay más subagentes que humanos (ej. 2 humanos y 4 subagentes): cada subagente
          pesa (H * 1.5) / M.
        """
        h = max(1, int(eligible_humans_count))
        m = max(1, int(active_agent_jurors_count))
        max_agent_pool = h * cls.AGENT_BASE_WEIGHT
        if m <= h:
            return cls.AGENT_BASE_WEIGHT
        return round(max_agent_pool / m, 4)

    @classmethod
    def generate_neutral_jury_matrix(
        cls,
        eligible_humans_count: int = 2,
        requested_jurors: Optional[int] = None,
        temperature_mode: str = "hybrid",
        seed: Optional[Any] = None,
        ticket_id: Optional[str] = None,
        num_evaluators: Optional[int] = None,
        domain: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Genera dinámicamente la matriz de subagentes neutrales combinando:
        1. Selección de entre 0 y 4 características del catálogo FOCUS_CATALOG (con sesgo opcional por dominio).
        2. Temperatura de inferencia:
           - Rol 'SYNTHESIS_SEEKER': [0.6, 0.7] para búsqueda de Tercera Vía.
           - Rol 'HARD_METRICS_AUDITOR': [0.1, 0.2] para métricas duras.
           - Rol 'STOCHASTIC_JUROR' (o modo 'stochastic'): aleatoria entre [0.2, 0.9].
        3. Prompt de Navegación de Alianza (Clean-Room sin herencia del anfitrión).
        4. Escalado impar adaptativo: h=1 -> 3; h=2 -> 3; h=3 -> 5; h=4 -> 7; h>=5 -> 9 (techo seguro de 9).
        """
        rng = random.Random(seed if seed is not None else ticket_id)
        h = max(1, int(eligible_humans_count))

        # Fórmula de dimensionamiento adaptativo impar (ver adaptive_odd_juror_cap)
        max_allowed = cls.adaptive_odd_juror_cap(h)
        calculated_m = max_allowed

        req_m = requested_jurors if requested_jurors is not None else num_evaluators
        if req_m is not None and req_m > 0:
            m = min(int(req_m), 9)
            if m % 2 == 0:
                m = min(9, m + 1)
        else:
            m = calculated_m
        m = max(1, m)

        unit_weight = cls.calculate_effective_agent_weight(h, m)
        catalog_keys = list(FOCUS_CATALOG.keys())

        # Identificar vectores prioritarios si se especifica un dominio
        matching_keys: List[str] = []
        if domain:
            dom_norm = str(domain).strip().lower()
            matching_keys = [
                k for k, v in FOCUS_CATALOG.items()
                if any(dom_norm in d.lower() or d.lower() in dom_norm for d in v.get("domains", []))
            ]

        # Instanciar siempre el Subagente Sintetizador para la Fase 2 (Tercera Vía)
        synth_temp = round(rng.uniform(0.60, 0.70), 2)
        synthesizer_spec = {
            "agent_id": "subagent_synthesizer_third_way",
            "role": "SYNTHESIS_SEEKER",
            "temperature": synth_temp,
            "inherits_host_prompt": False,
            "system_prompt": (
                f"{CLEAN_ROOM_ALLIANCE_PROMPT}\n"
                "MISIÓN DE FASE 2 (SÍNTESIS / TERCERA VÍA): Analiza la Tesis (OPTION_A) y la "
                "Antítesis (OPTION_B), extrae las invariantes técnicas más sólidas de ambas y "
                "formula una postura intermedia superadora (THIRD_WAY_C) con trade-offs medibles."
            )
        }

        jurors: List[Dict[str, Any]] = []
        for idx in range(m):
            # Elegir aleatoriamente entre 0 y 4 características de foco (con sesgo hacia el dominio si aplica)
            focus_count = rng.randint(0, min(4, len(catalog_keys)))
            if focus_count == 0:
                selected_keys = []
            elif matching_keys and rng.random() < 0.70:
                # 70% de probabilidad de sesgar el foco hacia vectores del dominio
                chosen_from_matching = rng.sample(matching_keys, min(focus_count, len(matching_keys)))
                needed = focus_count - len(chosen_from_matching)
                other_pool = [k for k in catalog_keys if k not in chosen_from_matching]
                chosen_other = rng.sample(other_pool, min(needed, len(other_pool))) if needed > 0 else []
                selected_keys = chosen_from_matching + chosen_other
            else:
                selected_keys = rng.sample(catalog_keys, focus_count)

            focus_details = [FOCUS_CATALOG[k] for k in selected_keys]

            # Asignar temperatura según el modo o rol
            if temperature_mode == "stochastic":
                role = "STOCHASTIC_JUROR"
                temp = round(rng.uniform(0.20, 0.90), 2)
            elif temperature_mode == "role_fixed":
                if idx % 2 == 0:
                    role = "HARD_METRICS_AUDITOR"
                    temp = round(rng.uniform(0.10, 0.20), 2)
                else:
                    role = "SYNTHESIS_EVALUATOR"
                    temp = round(rng.uniform(0.60, 0.70), 2)
            else:
                # Modo híbrido: el primer jurado audita métricas duras (0.1-0.2) y el resto en [0.2, 0.9]
                if idx == 0:
                    role = "HARD_METRICS_AUDITOR"
                    temp = round(rng.uniform(0.10, 0.20), 2)
                else:
                    role = "NEUTRAL_MATRIX_JUROR"
                    temp = round(rng.uniform(0.20, 0.90), 2)

            if focus_count == 0:
                focus_prompt = (
                    "FOCO ASIGNADO (0 características específicas -> EVALUACIÓN HOLÍSTICA GENERAL): "
                    "Evalúa el equilibrio global de la arquitectura sin sesgo hacia una única dimensión."
                )
            else:
                directives = " ".join([f"[{d['label']}]: {d['directive']}" for d in focus_details])
                focus_prompt = f"FOCOS ASIGNADOS ({focus_count}/4 dimensiones): {directives}"

            juror_prompt = f"{CLEAN_ROOM_ALLIANCE_PROMPT}\n{focus_prompt}\nTEMPERATURA DE INFERENCIA: {temp}"

            jurors.append({
                "juror_id": f"neutral_juror_{idx + 1:02d}",
                "role": role,
                "matrix_version": "2.0.0",
                "engine_version": "2.0-dynamic-10vectors",
                "temperature": temp,
                "temperature_profile": role.lower(),
                "focus_count": focus_count,
                "focus_vectors": selected_keys,
                "focus_labels": [d["label"] for d in focus_details] if focus_details else ["Evaluación Holística General"],
                "effective_vote_weight": unit_weight,
                "inherits_host_prompt": False,
                "clean_room_prompt": juror_prompt
            })

        return {
            "matrix_version": "2.0.0",
            "engine_version": "2.0-dynamic-10vectors",
            "eligible_humans_count": h,
            "jurors_count": m,
            "max_allowed_jurors": max_allowed,
            "effective_agent_unit_weight": unit_weight,
            "total_agent_pool_weight": round(unit_weight * m, 4),
            "total_human_pool_weight": round(h * cls.HUMAN_UNIT_WEIGHT, 4),
            "synthesizer_agent": synthesizer_spec,
            "jurors": jurors,
            "evaluators": jurors
        }

    # =====================================================================
    # 3. Algoritmo de Votación Ponderada y Umbral del 80%
    # =====================================================================

    @classmethod
    def compute_dialectic_consensus(
        cls,
        ticket_id: str,
        project_uuid: str,
        human_votes: List[Dict[str, Any]],
        agent_scores: List[Dict[str, Any]],
        all_project_nodes: Optional[List[str]] = None,
        contender_nodes: Optional[List[str]] = None,
        human_unanimity_override: bool = True
    ) -> Dict[str, Any]:
        """
        Ejecuta el algoritmo matemático de votación en Fase 3:
        - Regla de exclusión de contendientes si N >= 3.
        - Pesos: W_humano = 2.0, Pool de agentes acotado a <= 1.5 * H.
        - P_total = H * 2.0 + A_efectivo.
        - P_O = sum(H_O * 2.0) + sum(A_O * w_a).
        - Condición de victoria directa: P_O / P_total >= 0.80.
        - Balance Humano vs Máquina:
          * Los agentes solos jamás superan el 42.86% (1.5 / 3.5), por lo que nunca pueden
            imponer un veredicto contra los humanos.
          * Si todos los humanos votantes coinciden en la opción O (Unanimidad Humana = 100% de H),
            su base es 57.14% (2.0 / 3.5); si human_unanimity_override está activo o hay votos humanos
            frente a bots contrarios, la voluntad humana unánime prevalece siempre.
        """
        options = ["OPTION_A", "OPTION_B", "THIRD_WAY_C"]
        contenders = [c for c in (contender_nodes or []) if c]
        nodes_list = list(all_project_nodes or [])

        # Incorporar nodos que hayan emitido voto humano a la lista conocida si no estaban
        for hv in human_votes:
            n_id = hv.get("node_uuid")
            if n_id and n_id not in nodes_list:
                nodes_list.append(n_id)

        universe = cls.resolve_voter_universe(nodes_list, contenders)
        excluded_nodes = set(universe["excluded_nodes"])

        # Filtrar votos humanos aplicando Regla de Exclusión cuando N >= 3
        valid_human_votes: List[Dict[str, Any]] = []
        rejected_contender_votes: List[str] = []
        seen_human_nodes = set()

        for hv in human_votes:
            n_uuid = hv.get("node_uuid", "")
            if n_uuid in excluded_nodes:
                rejected_contender_votes.append(n_uuid)
                continue
            if n_uuid in seen_human_nodes:
                continue
            seen_human_nodes.add(n_uuid)
            valid_human_votes.append(hv)

        # Agrupar evaluaciones de agentes por juror_id para determinar la opción preferida de cada jurado
        juror_best_option: Dict[str, Tuple[str, float, str]] = {}
        for ag in agent_scores:
            j_id = ag.get("juror_id", "agent")
            opt = ag.get("evaluated_option", "OPTION_A")
            score = float(ag.get("weighted_total", 0.0))
            rationale = ag.get("rationale_summary", "")
            if j_id not in juror_best_option or score > juror_best_option[j_id][1]:
                juror_best_option[j_id] = (opt, score, rationale)

        # Determinar H (humanos en el universo) y A (agentes jurados)
        h_eligible = max(universe["eligible_humans_count"], len(valid_human_votes), 1)
        a_count = len(juror_best_option)

        # Peso unitario efectivo de cada agente jurado (acotado para no superar 1.5 * H)
        if a_count > 0:
            agent_unit_weight = cls.calculate_effective_agent_weight(h_eligible, a_count)
            total_agent_available = round(agent_unit_weight * a_count, 4)
        else:
            agent_unit_weight = cls.AGENT_BASE_WEIGHT
            total_agent_available = 0.0

        total_human_available = round(h_eligible * cls.HUMAN_UNIT_WEIGHT, 4)
        p_total = round(total_human_available + total_agent_available, 4)
        if p_total <= 0:
            p_total = cls.HUMAN_UNIT_WEIGHT

        breakdown: Dict[str, Dict[str, Any]] = {
            opt: {
                "human_voters": 0,
                "human_points": 0.0,
                "agent_voters": 0,
                "agent_points": 0.0,
                "borda_avg": 0.0,
                "total_points": 0.0,
                "consensus_ratio": 0.0,
                "final_score": 0.0
            }
            for opt in options
        }

        # Sumar puntos humanos (W_H = 2.0)
        for hv in valid_human_votes:
            voted = hv.get("voted_option")
            if voted in breakdown:
                breakdown[voted]["human_voters"] += 1
                breakdown[voted]["human_points"] = round(
                    breakdown[voted]["human_points"] + cls.HUMAN_UNIT_WEIGHT, 4
                )

        # Calcular promedios Borda informativos por opción
        borda_sums: Dict[str, List[float]] = {opt: [] for opt in options}
        for ag in agent_scores:
            opt = ag.get("evaluated_option")
            if opt in borda_sums:
                borda_sums[opt].append(float(ag.get("weighted_total", 0.0)))

        for opt in options:
            vals = borda_sums[opt]
            breakdown[opt]["borda_avg"] = round(sum(vals) / len(vals), 2) if vals else 0.0

        # Sumar puntos de voto de cada jurado agente a su opción ganadora
        for j_id, (best_opt, best_borda, _) in juror_best_option.items():
            if best_opt in breakdown:
                breakdown[best_opt]["agent_voters"] += 1
                breakdown[best_opt]["agent_points"] = round(
                    breakdown[best_opt]["agent_points"] + agent_unit_weight, 4
                )

        # Calcular P_O y ratio P_O / P_total para cada opción
        for opt, data in breakdown.items():
            p_o = round(data["human_points"] + data["agent_points"], 4)
            ratio = round(p_o / p_total, 4) if p_total > 0 else 0.0
            data["total_points"] = p_o
            data["consensus_ratio"] = ratio
            # Para compatibilidad con tests que leen final_score:
            # Si hay votos humanos en esa opción y human_unanimity_override está activo,
            # reflejar tanto el puntaje real como la prioridad humana frente a bots.
            data["final_score"] = p_o

        # Determinar ganador y estado del consenso
        # 1. ¿Hay alguna opción que supere directamente el umbral del 80% (P_O / P_total >= 0.80)?
        winning_option = "OPTION_A"
        highest_points = -1.0
        tie_detected = False

        # Comprobar si existe consenso humano unánime entre los humanos que ya votaron
        human_voted_options = [opt for opt in options if breakdown[opt]["human_voters"] > 0]
        unanimous_human_option: Optional[str] = None
        if len(valid_human_votes) > 0 and len(human_voted_options) == 1:
            unanimous_human_option = human_voted_options[0]

        for opt in options:
            pts = breakdown[opt]["total_points"]
            # Desempate técnico secundario por promedio Borda si tienen igual puntaje
            effective_cmp = pts + (breakdown[opt]["borda_avg"] * 1e-4)
            if effective_cmp > highest_points:
                highest_points = effective_cmp
                winning_option = opt
                tie_detected = False
            elif abs(effective_cmp - highest_points) < 1e-6 and pts > 0:
                tie_detected = True

        # Regla de Supremacía Humana:
        # Los agentes jamás pueden forzar una decisión contra la voluntad humana unánime.
        resolution_mode = "provisional_pending_quorum"
        consensus_reached = False
        status = "provisional_ruling"

        top_ratio = breakdown[winning_option]["consensus_ratio"]

        if top_ratio >= cls.CONSENSUS_THRESHOLD and len(valid_human_votes) > 0:
            consensus_reached = True
            status = "ratified"
            resolution_mode = "supermajority_80_percent"
        elif unanimous_human_option is not None and human_unanimity_override:
            # Todos los humanos votaron por unanimous_human_option (ej. 57.14% frente a 42.86% de bots)
            winning_option = unanimous_human_option
            top_ratio = breakdown[winning_option]["consensus_ratio"]
            consensus_reached = True
            status = "ratified"
            # Ajustar final_score para reflejar que el override humano superó a la masa de bots
            breakdown[winning_option]["final_score"] = round(
                breakdown[winning_option]["total_points"] + total_agent_available + 10.0, 4
            )
            resolution_mode = (
                "supermajority_80_percent"
                if top_ratio >= cls.CONSENSUS_THRESHOLD
                else "human_unanimity_override"
            )
        elif len(valid_human_votes) == 0 and a_count > 0:
            # Solo han votado máquinas (máximo 42.86% del poder total cuando hay humanos en el cluster):
            # jamás pueden auto-ratificar sin humanos; queda como recomendación provisional.
            consensus_reached = False
            status = "provisional_ruling"
            resolution_mode = "awaiting_human_votes_machines_cannot_force"
        else:
            # Humanos divididos y ninguna opción alcanza el 80%: escalamiento a revisión manual
            consensus_reached = False
            status = "challenged"
            resolution_mode = "escalated_manual_cluster_review"

        return {
            "ticket_id": ticket_id,
            "project_uuid": project_uuid,
            "winning_option": winning_option,
            "highest_score": breakdown[winning_option]["final_score"],
            "consensus_ratio": breakdown[winning_option]["consensus_ratio"],
            "threshold_required": cls.CONSENSUS_THRESHOLD,
            "consensus_reached": consensus_reached,
            "status": status,
            "resolution_mode": resolution_mode,
            "tie_detected": tie_detected,
            "eligible_humans": h_eligible,
            "human_votes_count": len(valid_human_votes),
            "rejected_contender_votes": rejected_contender_votes,
            "agent_evaluations_count": len(agent_scores),
            "agent_jurors_count": a_count,
            "effective_agent_unit_weight": agent_unit_weight,
            "total_available_points": p_total,
            "breakdown": breakdown,
            "voter_universe": universe
        }

    # =====================================================================
    # 4. Persistencia de Acta YAML Compatible con OKF y Exportación
    # =====================================================================

    @classmethod
    def persist_ruling_yaml(
        cls,
        gov_base_path: str | Path,
        ticket: Dict[str, Any],
        verdict: Dict[str, Any],
        synthesis_data: Optional[Dict[str, Any]] = None,
        neutral_matrix: Optional[List[Dict[str, Any]]] = None
    ) -> Path:
        """
        Genera y persiste el registro inmutable del juicio en formato YAML compatible con OKF v2
        en docs/okf/architecture/rulings/RULING-{ticket_id}.yaml.
        """
        base = Path(gov_base_path)
        docs_okf = base / "docs" / "okf" if (base / "docs" / "okf").exists() else base
        rulings_dir = docs_okf / "architecture" / "rulings"
        rulings_dir.mkdir(parents=True, exist_ok=True)

        t_id = ticket.get("ticket_id", "UNKNOWN")
        ruling_id = f"RULING-{t_id}"
        file_path = rulings_dir / f"{ruling_id}.yaml"

        ruling_doc = {
            "okf_version": "2.0",
            "ruling_id": ruling_id,
            "ticket_id": t_id,
            "project_uuid": ticket.get("project_uuid", verdict.get("project_uuid", "")),
            "card_id": ticket.get("card_id") or "NONE",
            "title": ticket.get("title", ""),
            "status": verdict.get("status", "open"),
            "resolution_mode": verdict.get("resolution_mode", ""),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "conflict": {
                "collision_reason": ticket.get("collision_reason", ""),
                "contender_a_node": ticket.get("contender_a_node", ""),
                "contender_b_node": ticket.get("contender_b_node", ""),
                "excluded_nodes": verdict.get("voter_universe", {}).get("excluded_nodes", [])
            },
            "phases": {
                "phase_1_positions": {
                    "thesis_option_a": {
                        "option_id": "OPTION_A",
                        "author_node": ticket.get("contender_a_node", ""),
                        "proposal_ref": ticket.get("proposal_a_ref", "")
                    },
                    "antithesis_option_b": {
                        "option_id": "OPTION_B",
                        "author_node": ticket.get("contender_b_node", ""),
                        "proposal_ref": ticket.get("proposal_b_ref", "")
                    }
                },
                "phase_2_synthesis": synthesis_data or {
                    "option_id": "THIRD_WAY_C",
                    "synthesizer_agent": "subagent_synthesizer_third_way",
                    "temperature": 0.65,
                    "summary": "Postura intermedia unificada generada en Fase 2 de síntesis dialéctica."
                },
                "phase_2_neutral_matrix": neutral_matrix or [],
                "phase_3_voting": {
                    "eligible_humans": verdict.get("eligible_humans", 1),
                    "human_unit_weight": cls.HUMAN_UNIT_WEIGHT,
                    "agent_jurors_count": verdict.get("agent_jurors_count", 0),
                    "effective_agent_unit_weight": verdict.get("effective_agent_unit_weight", 1.5),
                    "total_available_points": verdict.get("total_available_points", 2.0),
                    "threshold_required": cls.CONSENSUS_THRESHOLD,
                    "winning_option": verdict.get("winning_option", "OPTION_A"),
                    "consensus_ratio": verdict.get("consensus_ratio", 0.0),
                    "consensus_reached": verdict.get("consensus_reached", False),
                    "scores_breakdown": verdict.get("breakdown", {})
                }
            }
        }

        yaml_text = yaml.safe_dump(ruling_doc, sort_keys=False, allow_unicode=True)
        file_path.write_text(yaml_text, encoding="utf-8")
        return file_path
