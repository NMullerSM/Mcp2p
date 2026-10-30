---
id: DIS-002
title: Control de Versiones Directo del Archivo Binario SQLite en Git
status: discarded
related_ids: [ADR-002]
created_at: 2026-10-29T23:00:00Z
---
# DIS-002: Control de Versiones Directo del Archivo Binario SQLite en Git

## Alternativa Evaluada
Hacer push y pull del archivo `mcp2p_local.db` a una rama de Git para sincronizar el estado del tablero.

## Razones del Descarte
1. Los archivos binarios en Git no permiten merges automáticos; cualquier concurrencia causa corrupción o conflicto irresoluble.
2. Infla el repositorio con blobs binarios duplicados.
3. Se adoptó el modelo de tarjetas YAML individuales en `kanban/cards/` (ADR-002).
