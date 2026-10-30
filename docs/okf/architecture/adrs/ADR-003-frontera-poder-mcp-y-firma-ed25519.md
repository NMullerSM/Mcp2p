---
id: ADR-003
title: Frontera Estricta de Poder del Servidor MCP y Firma Ed25519 Humana
status: approved
related_ids: [RNF-03, US-05, TASK-202]
created_at: 2026-10-29T23:00:00Z
---
# ADR-003: Frontera Estricta de Poder del Servidor MCP y Firma Ed25519

## Contexto
Los agentes de IA no deben usurpar la identidad ni la autoridad legal/técnica del desarrollador humano.

## Decisión
- El servidor MCP expone "manos" operativas al agente para preparar tareas, leer PRs y debatir.
- El servidor MCP tiene terminantemente prohibido acceder a `~/.mcp2p/node_identity.key`.
- Solo la acción humana explícita en la UI (HITL) estampa la firma criptográfica `Ed25519` sobre decisiones y merges.

## Consecuencias
- Garantía matemática contra auto-aprobaciones o alucinaciones destructivas de agentes.
