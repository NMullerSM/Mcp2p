# Arquitectura del Juicio Dialéctico en 3 Fases y Matriz Neutral Anti-Sycophancy

> **Módulo Principal**: [`src/a2a/jury_engine.py`](../src/a2a/jury_engine.py) · Integrado en [`src/a2a/bus.py`](../src/a2a/bus.py) y [`src/app/api_bridge.py`](../src/app/api_bridge.py)  
> **Estándar de Gobernanza**: OKF v2 (`docs/okf/architecture/rulings/` y `docs/okf/architecture/precedents_index.yaml`)

---

## 1. Flujo Dialéctico de 3 Fases (Tesis, Antítesis, Síntesis)

Cuando dos nodos o agentes colisionan en una decisión arquitectónica o de implementación, el sistema evita votaciones binarias a ciegas ejecutando tres fases deterministas:

1. **Fase 1 — Exposición de Posturas (`OPTION_A` y `OPTION_B`)**:
   - **Tesis (`OPTION_A`)**: Propuesta técnica del primer contendiente (`contender_a_node`) con referencia de código/diff y justificación.
   - **Antítesis (`OPTION_B`)**: Contrapropuesta técnica del segundo contendiente (`contender_b_node`).
2. **Fase 2 — Mayéutica de Subagentes y Síntesis (`THIRD_WAY_C`)**:
   - Un subagente sintetizador (`subagent_synthesizer_third_way`, temperatura $T \in [0.60, 0.70]$) analiza ambas posturas sin heredar el prompt del usuario anfitrión y formula una **Tercera Vía / Síntesis (`THIRD_WAY_C`)**.
   - Una **Matriz de Subagentes Neutrales** evalúa las 3 opciones (`OPTION_A`, `OPTION_B`, `THIRD_WAY_C`) emitiendo argumentos mayéuticos basados en métricas técnicas medibles.
   - **Protección contra Sesgo Cognitivo**: En la interfaz HITL, el desarrollador humano puede leer íntegramente los argumentos mayéuticos y trade-offs expuestos por cada subagente, pero **las puntuaciones numéricas permanecen ocultas hasta que el humano emite su voto**.
3. **Fase 3 — Votación Ponderada con Umbral del 80% y Supremacía Humana**:
   - Los humanos elegibles y el pool de subagentes neutrales votan sobre las 3 vías (`OPTION_A`, `OPTION_B`, `THIRD_WAY_C`).
   - El dictamen se persiste de forma inmutable en `docs/okf/architecture/rulings/RULING-{ticket_id}.yaml` y actualiza automáticamente `docs/okf/architecture/precedents_index.yaml`.

---

## 2. Regla de Exclusión de Contendientes y Universo de Votantes

Implementada en `DialecticJuryEngine.resolve_voter_universe(all_project_nodes, contender_nodes)`:

- **Clústeres de $N \ge 3$ participantes (`strict_contender_exclusion`)**:
  $$N_{\text{elegibles}} = N - K$$
  donde $K$ son los nodos directamente involucrados en el conflicto. Los contendientes exponen sus posturas en Fase 1 pero quedan excluidos de emitir voto en Fase 3 para garantizar imparcialidad.
- **Clústeres pequeños de $N \le 2$ participantes (`small_cluster_post_synthesis_voting`)**:
  Cuando solo existen 1 o 2 colaboradores en el proyecto y ambos son parte de la disputa, excluirlos dejaría $H = 0$ humanos elegibles. En este caso se activa automáticamente el modo de **votación post-síntesis**: tras generarse la Síntesis (`THIRD_WAY_C`) y la evaluación mayéutica independiente en Fase 2, ambos humanos quedan habilitados para votar en Fase 3.

---

## 3. Modelo Matemático de Pesos y Supremacía Humana Absoluta

Para evitar que una proliferación de subagentes ($M$) diluya el poder de decisión humano ($H$), el motor aplica un **Tope al Pool de Máquinas**:

- **Peso de cada humano elegible**:
  $$W_H = 2.0 \quad \implies \quad P_{H,\text{total}} = 2.0 \times H$$
- **Tope del pool total de agentes/subagentes**:
  $$P_{A,\text{max}} = 1.5 \times H$$
- **Peso unitario efectivo de cada subagente ($w_a$)**:
  $$w_a = \begin{cases} 1.5 & \text{si } M \le H \\ \dfrac{1.5 \times H}{M} & \text{si } M > H \end{cases}$$

### Propiedades Invariantes
1. **Proporción Constante ($57.14\%$ Humano vs $42.86\%$ Máquina)**:
   $$\frac{P_{H,\text{total}}}{P_{H,\text{total}} + P_{A,\text{max}}} = \frac{2.0 H}{3.5 H} = 57.14\% \quad \text{vs} \quad \frac{1.5 H}{3.5 H} = 42.86\%$$
2. **Imposibilidad de Golpe de Estado de Bots (`awaiting_human_votes_machines_cannot_force`)**:
   Si $0$ humanos han votado, aunque el $100\%$ de los subagentes coincidan en una opción, su peso máximo alcanza el $42.86\%$ ($< 80\%$), por lo que el ticket permanece en `provisional_ruling` esperando firma humana.
3. **Umbral de Supermayoría ($\ge 80\%$ — `supermajority_80_percent`)**:
   $$R(O) = \frac{P_O}{P_{\text{total}}} \ge 0.80$$
4. **Supremacía por Unanimidad Humana (`human_unanimity_override`)**:
   Si todos los humanos votantes eligen la misma opción $O_H$ y todos los subagentes eligen una opción contraria $O_A$, prevalece siempre $O_H$.

---

## 4. Matriz de Neutralidad Anti-Sycophancy (Clean-Room)

Implementada en `DialecticJuryEngine.generate_neutral_jury_matrix`:

1. **Aislamiento del Prompt Anfitrión (`inherits_host_prompt = False`)**:
   Ningún subagente jurado recibe las preferencias conversacionales ni el prompt del usuario local. Todos parten exclusivamente de `CLEAN_ROOM_ALLIANCE_PROMPT`.
2. **Muestreo Estocástico de $0$ a $4$ Dimensiones Técnicas (`FOCUS_CATALOG`)**:
   Cada subagente recibe aleatoriamente $k \in \{0, 1, 2, 3, 4\}$ vectores de foco:
   - `ESCALABILIDAD_CONCURRENCIA`
   - `MANTENIBILIDAD_CLEAN_ARCH`
   - `SEGURIDAD_RESILIENCIA`
   - `EFICIENCIA_COMPUTACIONAL`
   *(Si $k = 0$, opera en modo de Evaluación Holística General).*
3. **Temperaturas Diferenciadas**:
   - **Sintetizador (`SYNTHESIS_SEEKER`)**: $T \in [0.60, 0.70]$ para creatividad técnica controlada en la Tercera Vía.
   - **Auditor de Métricas Duras (`HARD_METRICS_AUDITOR`)**: $T \in [0.10, 0.20]$ para rigor determinista.
   - **Jurados de Matriz Estocástica (`NEUTRAL_MATRIX_JUROR`)**: $T \in [0.20, 0.90]$ para diversidad de exploración.
