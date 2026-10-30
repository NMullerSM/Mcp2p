---
id: ADR-006
title: Pull Requests Cruzadas con OKF y Tiempo de Gracia Anti-Bloqueo
status: approved
related_ids: [DEC-003, US-03, TASK-203]
created_at: 2026-10-29T23:00:00Z
---
# ADR-006: Pull Requests Cruzadas con OKF y Tiempo de Gracia Anti-Bloqueo

## Contexto
El flujo de desarrollo no debe paralizarse si un colaborador se encuentra desconectado, pero las PRs deben validarse contra las directrices de arquitectura.

## Decisión
- El agente revisor contrasta la PR con la tarjeta YAML y los ADRs, emitiendo una recomendación.
- Cada revisor autodeclara su SLA/Tiempo de Gracia en horas (ej. 4h, 24h, máx 72h).
- Si vence el tiempo de gracia sin objeción, la PR se auto-fusiona (`AUTO_MERGED_BY_GRACE_TIMEOUT`).

## Consecuencias
- Cero bloqueos de flujo de trabajo en equipos asíncronos.
