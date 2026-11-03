---
id: DEC-003
title: Aislamiento de Código mediante Git Worktrees por Tarea
status: approved
approved_at: 2026-11-03T19:48:00Z
related_ids: [ADR-006, US-04, TASK-103]
created_at: 2026-10-29T23:00:00Z
---
# DEC-003: Aislamiento de Código mediante Git Worktrees por Tarea

## Contexto
Dos o más agentes programando concurrentemente en el repositorio `app/` no deben pisarse archivos ni ensuciar la rama `main`.

## Mecanismo Ratificado
- Cada tarea reclamada instancia un worktree aislado en `app/.worktrees/{card_id}` apuntando a `wt/{card_id}`.
- La rama huérfana mantiene el archivo `worktrees_registry.json` con los módulos bloqueados por nodo (`files_scope`).
- Normalización obligatoria de `files_scope` a listas de cadenas en el servidor MCP y almacenamiento para prevenir falsos positivos de colisión por doble-serialización JSON.
- Al aprobarse o descartarse la PR, el worktree y la rama efímera se destruyen limpiamente.
