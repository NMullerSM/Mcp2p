---
id: DIS-001
title: WebRTC DataChannels Nativo para Comunicación A2A
status: discarded
related_ids: [DEC-001, ADR-004]
created_at: 2026-10-29T23:00:00Z
---
# DIS-001: WebRTC DataChannels Nativo para Comunicación A2A

## Alternativa Evaluada
Implementar WebRTC nativo mediante la librería `aiortc` para establecer canales de datos P2P directos a través de Internet.

## Razones del Descarte
1. Requiere servidores STUN/TURN externos para cruzar NAT residencial.
2. Agrega dependencias binarias pesadas y fragilidad de compilación en Windows/Linux.
3. Se reemplazó por la arquitectura híbrida Webhooks LAN + Git Outbox WAN (ADR-004), mucho más robusta y de costo cero.
