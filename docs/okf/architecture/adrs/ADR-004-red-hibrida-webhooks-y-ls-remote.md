---
id: ADR-004
title: Red Híbrida P2P con Webhooks LAN y Watcher ls-remote en WAN
status: approved
related_ids: [DEC-001, TASK-204]
created_at: 2026-10-29T23:00:00Z
---
# ADR-004: Red Híbrida P2P con Webhooks LAN y Watcher ls-remote

## Contexto
Sincronizar eventos entre múltiples nodos sin servidor central y sin exigir apertura de puertos router (NAT traversal).

## Decisión
- **LAN**: Micro-servidor HTTP en puerto :8765 que recibe Webhooks firmados con Ed25519 (<20ms de latencia).
- **WAN**: Daemon de sondeo ligero que ejecuta `git ls-remote origin` cada 20 segundos (<200 bytes de carga).

## Consecuencias
- Funcionamiento inmediato tanto en la misma red local como entre desarrolladores remotos.
