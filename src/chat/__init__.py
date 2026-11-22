"""Módulo de chat de MCP2P: conversación del humano con los agentes (y, más adelante, con otros nodos y el grupo).

Paquete autónomo y desacoplado: `domain`, `ports` y `service` no importan infraestructura (ni `src.adapters`, `src.app`,
`src.storage`, `src.mcp`, `src.p2p` ni `src.core`). Las fuentes de mensajes, los transportes de envío y los listados de
participantes se enchufan desde fuera mediante `ChatService.register_*`; el adaptador local vive en `adapters/` y recibe
funciones, no servicios concretos. Una prueba de arquitectura vigila estas dependencias.
"""

from src.chat.domain import (  # noqa: F401
    CHANNEL_KINDS,
    Channel,
    ChatEntry,
    MentionParse,
    Participant,
    SendResult,
    bubble_color,
    parse_mentions,
    tick_for,
)
from src.chat.service import ChatService  # noqa: F401
