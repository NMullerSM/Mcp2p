---
id: DEC-004
title: Canal de Conversación en Vivo Agente↔Humano y Chat de Juicio (Propuesta)
status: in_discussion
related_ids: [DEC-002, ADR-004, ADR-005, DEC-003]
created_at: 2026-11-03T23:00:00Z
---
# DEC-004: Canal de Conversación en Vivo Agente↔Humano y Chat de Juicio

> **Estado**: propuesta para discusión, **no implementada, no ratificada**. Nace de una conversación entre el usuario y Claude sobre cómo extender la coordinación A2A a algo conversacional, sin romper lo que ya existe (ADR-002, ADR-004, DEC-002). No se tocó código al escribir esto.

## Contexto

Hoy la coordinación entre agentes es turno a turno vía `send_coordination_rfc` (3 etapas: `PROPOSAL`/`COUNTER_PROPOSAL`/`RESOLUTION`) y async vía Kanban. Eso funciona bien para decisiones formales, pero no sirve para el caso de **live coding**: dos personas, cada una con su propio agente, en computadoras distintas, que necesitan resolver algo chico rápido — quién propone qué, ponerse de acuerdo en un detalle — sin pasar por todo el ritual de un RFC formal.

Se evaluó si esto requiere infraestructura nueva (WebRTC, relay propio) y se concluyó que no: ya existe la base (bus A2A, presencia por heartbeat en `swarm_nodes`, el híbrido webhook-LAN/poll-WAN de `ADR-004`). Lo que falta es una capa conversacional encima, y un criterio claro de **qué sí y qué no** pasa por ahí.

## Decisión propuesta

### 1. Alcance: para qué SÍ es este canal y para qué NO

- **SÍ**: coordinación informal y rápida entre pares (humano+agente ↔ humano+agente) durante trabajo simultáneo — "quién toma cuál card", "ojo que toqué este archivo", acuerdos chicos que no necesitan quedar como ADR.
- **NO**: decisiones de aceptación/rechazo de código, cambios de gobernanza, o cualquier cosa que hoy pasa por `inspect_pr_and_recommend` o `send_coordination_rfc`. Esas vías formales **no se tocan ni se reemplazan** — el chat es un canal adicional para lo informal, no un atajo para lo que debe quedar auditado formalmente.
- Todo lo que pase por este canal sigue siendo **MCP2P formal**: nuevas herramientas MCP (no un chat externo/paralelo), para que se pueda leer con el tiempo (no solo en vivo) y para que un agente pueda "conversar con el agente" dentro del propio flujo de la herramienta, no en un sistema aparte.

### 2. El dato vive en git, el trigger es solo una campanita

Mismo patrón que `ADR-002` (YAML como fuente de verdad, SQLite como caché): la conversación se persiste en la rama huérfana de gobernanza (p. ej. `governance/history/a2a_chat/{session_id}.md`), no en memoria de ningún proceso. Los mecanismos de aviso en tiempo real (cron de presencia, webhook LAN, o un trigger externo tipo rutina/webhook en el lado de cada agente) **nunca transportan el mensaje**, solo avisan "hay algo nuevo" — si el aviso no llega a tiempo, no se pierde nada, queda como un commit más que se reconcilia al próximo `pull`, igual que las cards.

### 3. Presencia, extendida a la sesión interactiva humana

`swarm_nodes.last_heartbeat` hoy es para nodos-agente que reclaman tareas. Se propone extender el mismo concepto a la sesión interactiva de cada humano (un heartbeat liviano mientras su chat con su agente está abierto), para que cualquier nodo pueda saber "¿está conectado ahora, o le dejo el mensaje para cuando vuelva?" — exactamente el caso que planteó el usuario: si Claude está cerrado, MCP2P lo sabe y no espera respuesta inmediata.

### 4. Compactación — revisar `DEC-002` antes de ratificar, no copiarla tal cual

`DEC-002` propone rondas fijas de 3 mensajes con `CHECKPOINT_COMPACT` obligatorio. **Objeción planteada en esta conversación**: 3 mensajes es muy poco para una discusión real — corta la conversación antes de que llegue a algo. Se propone que el criterio de corte **no sea un número fijo de mensajes**, sino algo más flexible (p. ej. un presupuesto de tokens, o una ronda más larga configurable) — a decidir junto con quien retome `DEC-002`, no copiarlo sin revisión.

### 5. Rondas que quedan a medio camino: no se fuerza nada

Si una conversación se corta (alguien cierra su sesión) antes de llegar a un checkpoint natural, **no se fuerza un `CHECKPOINT_COMPACT` por timeout**. Decisión explícita del usuario: *"si se corta es mejor no hacer nada que hardcodear"*. La ronda queda simplemente incompleta/sin cerrar — nadie la toca, no hay lógica especial de recuperación. Si hace falta retomarla, se retoma como una conversación nueva.

### 6. Chat de Juicio: reusar la superficie del Tribunal de Honor, no crear una nueva

En vez de un chat genérico para disputas, la propuesta es que cuando se abre un ticket (`open_dispute_ticket`), la sección de "discusión" de ese ticket se **expanda** a una vista de chat en vivo donde los jurados pueden conversar *y* votar (en vez de solo mandar `submit_agent_jury_evaluation` a ciegas, sin contexto de lo que piensan los otros jurados). Es el mismo canal del punto 1-5, aplicado a la superficie que ya existe del Tribunal Borda (`ADR-005`), no un sistema aparte.

## Qué falta decidir antes de que esto sea `approved`

- Tamaño/criterio de ronda para compactación (punto 4) — depende de retomar `DEC-002`.
- Qué pasa cuando solo una parte del jurado está presente en el chat de juicio en un momento dado (¿se vota igual, se espera?).
- Si la presencia de la sesión humana (punto 3) necesita un nuevo tool MCP o se resuelve en el cliente (Claude Code / host de cada agente).

## Consecuencias si se aprueba

- No reemplaza ningún flujo formal existente (RFC, PR review, Kanban) — los complementa.
- Cero riesgo de pérdida de datos por desconexión: todo lo que importa ya está committeado antes de que el trigger intente avisar a nadie.
- Depende de que cada agente/host tenga *algún* mecanismo de "despertar" (cron, webhook, rutina) — MCP2P no puede garantizar eso para todos los agentes por igual (es infraestructura de cada lado, no de este repo).
