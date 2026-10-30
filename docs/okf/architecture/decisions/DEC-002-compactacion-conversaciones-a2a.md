---
id: DEC-002
title: Compactación de Conversaciones A2A por Checkpoints
status: in_discussion
related_ids: [ADR-005, RNF-02, TASK-301]
created_at: 2026-10-29T23:00:00Z
---
# DEC-002: Compactación de Conversaciones A2A por Checkpoints

## Contexto
Permitir debates técnicos profundos entre agentes sin que el consumo de tokens crezca exponencialmente con el número de turnos.

## Mecanismo Propuesto
- Rondas de 3 mensajes.
- Al tercer mensaje, emisión obligatoria de un `CHECKPOINT_COMPACT` (síntesis densa <=150 palabras).
- La memoria activa solo expone el último checkpoint más los mensajes de la ronda en curso.
