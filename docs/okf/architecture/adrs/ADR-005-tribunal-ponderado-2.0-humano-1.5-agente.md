---
id: ADR-005
title: Tribunal Ponderado 2.0 Desarrollador y 1.5 Agente Borda
status: approved
related_ids: [DEC-002, US-06, TASK-303]
created_at: 2026-10-29T23:00:00Z
---
# ADR-005: Tribunal Ponderado 2.0 Desarrollador y 1.5 Agente Borda

## Contexto
En discrepancias sobre opciones A, B y Tercera Vía C, se requiere un mecanismo democrático que garantice la supremacía humana pero aproveche el análisis técnico de los agentes.

## Decisión
- Voto Humano: Peso 2.0 puntos por desarrollador.
- Voto Agente: Peso 1.5 * (Puntaje Borda / 100.0) basado en 5 criterios (KISS, Performance, Seguridad, UX/Hick, Testabilidad).
- La opción ganadora genera un ADR; las opciones descartadas se archivan en `architecture/discarded/`.

## Consecuencias
- Soberanía humana preservada; desempate técnico objetivo ante discrepancias humanas divididas.
