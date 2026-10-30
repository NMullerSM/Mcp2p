---
id: ADR-001
title: Aislamiento Estricto de Doble Repositorio (app y governance)
status: approved
related_ids: [US-01, RNF-01]
created_at: 2026-10-27T12:00:00Z
---
# ADR-001: Aislamiento Estricto de Doble Repositorio

## Contexto
Se requiere aislar el código ejecutable de la aplicación respecto a la gobernanza y memoria agéntica para prevenir contaminación cruzada de tokens y colisiones en Git.

## Decisión
Se implementa una estructura desacoplada con dos repositorios físicos independientes:
1. `app/`: Repositorio exclusivo de código fuente ejecutable.
2. `governance/`: Repositorio exclusivo de gobernanza OKF (`main`) y memoria en rama huérfana (`orphan-conversa-memory`).

## Consecuencias
- Mantenimiento limpio sin mezcla de artefactos cognitivos en el historial de código.
- Trazabilidad determinista mediante enlaces en metadatos.
