---
id: RNF-01
title: Latencia Mínima y Operaciones a Cero Tokens
status: approved
related_ids: [ADR-002, ADR-004]
created_at: 2026-10-29T23:00:00Z
---
# RNF-01: Latencia Mínima y Operaciones a Cero Tokens

- Las lecturas y verificaciones de leases en SQLite deben responder en menos de 5 ms.
- La gestión de tarjetas, cambio de estados y empaquetado binario no debe consumir tokens de inferencia LLM.
