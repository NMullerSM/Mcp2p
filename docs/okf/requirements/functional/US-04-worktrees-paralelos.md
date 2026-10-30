---
id: US-04
title: Trabajo Paralelo Aislado mediante Git Worktrees
status: approved
related_ids: [ADR-006, TASK-103]
created_at: 2026-10-29T23:00:00Z
---
# US-04: Trabajo Paralelo Aislado mediante Git Worktrees

Como agente asignado a una tarea, quiero que se instancie un worktree dedicado en `app/.worktrees/{card_id}` para codificar y probar sin ensuciar la rama `main` ni interferir con otros agentes.
