---
id: ADR-002
title: Archivos YAML en OKF como Fuente de Verdad del Kanban
status: approved
related_ids: [US-02, TASK-102]
created_at: 2026-10-29T23:00:00Z
---
# ADR-002: Archivos YAML en OKF como Fuente de Verdad del Kanban

## Contexto
En un enjambre P2P desconectado, los cambios de estado en el tablero deben auditarse en Git sin depender de un servidor de base de datos compartido.

## Decisión
- Los archivos en `governance/kanban/cards/{card_id}.yaml` son la Fuente de Verdad Primaria.
- La tabla `kanban_cards` en SQLite WAL actúa como Caché de Lectura Rápida (<1ms).
- Toda mutación escribe el YAML y actualiza SQLite; todo `git pull` reconcilia los YAMLs hacia SQLite.

## Consecuencias
- Inmunidad a corrupción de base de datos binaria en Git.
- Trazabilidad línea a línea de leases y asignaciones vía `git diff`.
