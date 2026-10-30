---
id: DEC-001
title: Sincronización Híbrida LAN vs WAN
status: in_discussion
related_ids: [ADR-004, RNF-01, TASK-201]
created_at: 2026-10-29T23:00:00Z
---
# DEC-001: Sincronización Híbrida LAN vs WAN

## Contexto
Los nodos del enjambre pueden encontrarse en la misma red local (LAN) o en distintas ubicaciones detrás de routers residenciales con NAT/CGNAT. Se requiere notificar cambios en milisegundos sin depender de un servidor central.

## Alternativas Evaluadas
1. **Transporte Dual**: Webhook HTTP directo firmado con Ed25519 en LAN (:8765) + Watcher ligero `git ls-remote` en WAN cada 20s.
2. **WebRTC DataChannels**: Conexión P2P directa, pero requiere servidores STUN/TURN y dependencias pesadas.
3. **Polling constante `git fetch`**: Descarga objetos innecesarios y satura la CPU/red.

## Decisión Preliminar
Adoptar Transporte Dual por simplicidad (KISS) y cero dependencias externas pesadas.
