---
id: RNF-04
title: Compatibilidad Multiplataforma Total (Linux y Windows)
status: approved
related_ids: [DES-001]
created_at: 2026-10-29T23:00:00Z
---
# RNF-04: Compatibilidad Multiplataforma

- El sistema debe funcionar de forma idéntica en Linux (WebKitGTK) y Windows (WebView2).
- Todas las rutas deben manejarse con `pathlib.Path` y los comandos Git en Windows no deben abrir consolas CMD emergentes.
