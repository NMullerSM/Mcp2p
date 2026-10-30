---
id: RNF-03
title: Seguridad de Sandbox y Aislamiento de Claves Privadas
status: approved
related_ids: [ADR-003]
created_at: 2026-10-29T23:00:00Z
---
# RNF-03: Seguridad de Sandbox y Aislamiento de Claves

- Toda operación ejecutada por agentes vía MCP debe estar confinada al árbol de directorios del proyecto.
- La clave privada Ed25519 reside exclusivamente en el almacenamiento local seguro del usuario y no es accesible por la API del MCP.
