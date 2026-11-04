---
id: ADR-007
title: Orquestación Externa de Jurados del Tribunal de Honor, Sin Deliberación, Tercera Vía por Agente Único
status: approved
related_ids: [ADR-005, DEC-002]
created_at: 2026-11-04T00:00:00Z
---
# ADR-007: Orquestación Externa de Jurados del Tribunal de Honor, Sin Deliberación, Tercera Vía por Agente Único

## Contexto

`docs/PROMPT_REPARACION_JURADO_DINAMICO.md` (sesión de auditoría del 2026-11-04) dejó 4 decisiones de diseño abiertas (§3.1-§3.4) sobre el motor de jurado dinámico implementado en `docs/okf/auditoria_2026-11/07_PLAN_REPARACION_JURADO_DINAMICO.md`. Esta ADR resuelve 3 de las 4, en una sesión de diseño entre el usuario y Claude, usando el código ya implementado y verificado (69→73 tests en verde a lo largo de los planes v1-v5) como base — no se trata de construir algo nuevo, sino de confirmar y documentar cómo se usa lo que ya existe.

## Decisión

### 1. El motor de generación de la matriz NO vive fuera de MCP2P — ya está completo, dentro de MCP2P (resuelve §3.1)

`DialecticJuryEngine.generate_neutral_jury_matrix()` (`src/a2a/jury_engine.py`) ya genera, por cada ticket, una tabla efímera (no hardcodeada) con:
- **Cantidad de jurados**: `adaptive_odd_juror_cap(h)` — escala con los humanos elegibles reales del proyecto (vía `get_project_collaborators`/`get_swarm_nodes`), techo de 9, siempre impar. Se evaluó escalar multiplicativamente "4 o 5 subagentes por nodo" (propuesta original del usuario) y se descartó por costo de tokens — se mantiene el techo aditivo-con-tope.
- **Características asignadas**: 0-4 focos elegidos de `FOCUS_CATALOG` (10 vectores técnicos), con sesgo opcional por dominio.
- **Temperatura**: por rol (`SYNTHESIS_SEEKER` 0.6-0.7, `HARD_METRICS_AUDITOR` 0.1-0.2, `STOCHASTIC_JUROR` 0.2-0.9).

No hace falta ningún motor adicional — este ya cumple lo pedido.

### 2. El orquestador que despliega los subagentes vive FUERA de MCP2P, en el agente conectado (resuelve §3.2)

MCP2P **no ejecuta LLMs ni tiene credenciales de API propias** (por diseño, consistente con `ADR-003`: el servidor no toca identidad/claves). El flujo operativo real:

1. Un humano le pregunta a su agente conectado (Claude, Antigravity) algo equivalente a *"¿hay novedades?"* — no hay activación automática/proactiva del modo juicio.
2. El agente chequea el estado real (`get_kanban_board`, `list_dispute_tickets`) y detecta un ticket abierto con matriz generada.
3. El agente llama `get_jury_evaluation_prompt(project_uuid, ticket_id, juror_id)` por cada jurado de la matriz — esta tool (añadida en el plan de jurado dinámico, Fase 4) ya devuelve el paquete completo: `temperature`, `focus_vectors`, el prompt de cámara limpia, la disputa anonimizada, y las instrucciones exactas de envío (`submit_agent_jury_evaluation`, 5 dimensiones, rango 0-100).
4. El agente despliega un subagente por paquete, usando su propia herramienta de generación de subagentes, traduciendo la `temperature` numérica a instrucción de prompt (no hay forma de setear el parámetro de muestreo literal de un subagente hoy).
5. Cada subagente llama `submit_agent_jury_evaluation` directamente — ya tiene acceso a MCP2P porque corre bajo la misma conexión del agente que lo desplegó.

### 3. Los jurados NO deliberan entre sí — es el diseño correcto, no una limitación a resolver (resuelve §3.3)

El modelo Borda ponderado es, por construcción, de **votación independiente, no deliberativa**: cada jurado puntúa sin ver los puntajes de los demás (`scores_hidden_until_human_vote`, ya implementado). No se necesita ni se diseña ningún protocolo de conversación entre jurados de distintos contendores — sería complejidad añadida sin beneficio, y rompería la garantía anti-sycophancy que ya existe.

### 4. La Tercera Vía la propone un único agente sintetizador, nunca una convergencia multi-agente

Se genera siempre exactamente un `subagent_synthesizer_third_way` (rol `SYNTHESIS_SEEKER`) por ticket, que propone `THIRD_WAY_C`. **Todos** los jurados (el sintetizador incluido) puntúan las 3 opciones (`OPTION_A`/`OPTION_B`/`THIRD_WAY_C`) de forma independiente — la Tercera Vía gana si puntúa mejor en la votación ponderada, igual que cualquier otra opción. No requiere que los jurados conversen ni converjan.

## Consecuencias

- Cero cambios de código necesarios para esto — es documentación de un flujo ya soportado por `get_jury_evaluation_prompt` + `submit_agent_jury_evaluation`.
- MCP2P sigue sin necesitar credenciales de LLM propias.
- Queda pendiente, **sin resolver todavía** (§3.4 del prompt original): criterio de selección de modelo barato vs. caro por complejidad de la disputa — eso es responsabilidad de quien orquesta (el agente externo), no de MCP2P.
- Validación en vivo de punta a punta (abrir un ticket real, desplegar subagentes de verdad, confirmar que `submit_agent_jury_evaluation` resuelve el ticket) **todavía no se hizo** — el usuario decidió posponerla, se hará al retomar las pruebas en Parma.
