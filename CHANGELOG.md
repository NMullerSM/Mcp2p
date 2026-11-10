# Changelog

Todas las versiones siguen [SemVer](https://semver.org/lang/es/). Fuente única de la versión: `src/app/version.py`
(sincronizar con `python scripts/sync_version.py`).

## [1.0.3] - 2026-11-09

### Seguridad
- Webhook P2P LAN (`/webhook/p2p`, `/notify`): ahora exige firma Ed25519 **y** que la clave pública pertenezca a un
  colaborador aprobado (401 sin firma o firma inválida, 403 clave no confiable). Antes un payload sin `sig`/`payload`
  se aceptaba sin verificación, y la verificación usaba la clave enviada por el propio emisor.
- El listener P2P escucha por defecto solo en `127.0.0.1`. Para exponerlo en la LAN: `MCP2P_LAN_BIND_HOST=0.0.0.0`
  (o la IP de la interfaz).
- Límite de 1 MiB al cuerpo del webhook (413) y rechazo de `Content-Length` inválido (400).
- Path traversal: `card_id`, `project_uuid` (incluido el de tokens de invitación firmados) e `item_id` se validan
  (`[A-Za-z0-9][A-Za-z0-9_-]{0,63}`, sin nombres reservados de Windows) antes de construir rutas o ramas git; los
  puntos de escritura/lectura de YAML y worktrees verifican además contención de ruta.

### Corregido
- Leases de Kanban: dos agentes en procesos distintos podían adquirir la misma tarjeta (TOCTOU). Ahora la adquisición
  usa `BEGIN IMMEDIATE` de SQLite y es atómica entre procesos.
- Aprobación HITL de PR (`pr_merge`): ya no reporta `merged` ni marca la tarjeta como fusionada si el merge real falla
  (rama inexistente, conflicto, API de GitHub); los conflictos se abortan (`git merge --abort`) para no dejar el repo
  a medio fusionar. La UI muestra el error en lugar de forzar el estado "merged".
- Aprobación por número de PR: owner/repo se resuelven desde `code_repo_url` (antes se enviaban vacíos).
- `delete_project` devolvía `{}` (interpretado como éxito en JS) cuando el proyecto no existía.
- `create_kanban_task` sin `card_id` dejaba una tarjeta fantasma con id vacío y bloqueaba permanentemente la creación
  de tarjetas del proyecto (`UNIQUE constraint failed`); ahora se rechaza antes de escribir, con un error claro.
- Imports faltantes que rompían en silencio el historial de precedentes HITL, el detalle de dictámenes del jurado,
  el registro de errores de sincronización Kanban↔OKF y la creación de RFC/repartos sin `session_id`.
- `sync_three_tiers`: si el commit del outbox falla, los eventos ya no se marcan como `acked` (antes se perdían).
- Fallos de `git commit` / `git worktree add` en creación de proyectos, worktree huérfano, worktrees de tarea y
  aprobación de colaboradores ahora se registran y propagan en lugar de ignorarse.
- La versión embebida del frontend estaba desincronizada (1.0.2 se identificaba como 1.0.1 y mostraba siempre el
  aviso de actualización); `frontend/package.json` y `package-lock.json` también habían quedado desfasados.

### Mejorado
- Polling de sincronización de la UI: el intervalo ya no se re-arma en cada render y no solapa llamadas al puente
  PyWebView cuando este es lento (mitigación del parpadeo de paneles reportado en Windows; pendiente de verificación
  manual en Windows).

### Interno
- `scripts/sync_version.py` (+ verificación en CI y test) para mantener sincronizadas las copias de la versión;
  `get_version.py` ya no oculta errores con un valor por defecto.
- CI: `concurrency`, `timeout-minutes`, cache de npm, tests de frontend con vitest.
- 48 pruebas automatizadas nuevas (92 → 140), incluyendo pruebas multi-proceso de leases, autenticación del webhook
  P2P y path traversal.
- Eliminado código muerto del frontend (iconos y variables CSS sin uso).

## [1.0.2] - 2026-11-06
### Corregido
- Resolución de la UI en el build empaquetado de Windows (`get_ui_target`) y rutas de `mcp.json`/docs.

## [1.0.1] - 2026-11-06
### Añadido
- Auto-actualizador directo en caliente para Linux, sin descarga/reinstalo manual.

## [1.0.0] - 2026-11-05
### Añadido
- Primera versión publicada: pipeline CI/CD, instalador Windows, paquete Linux y auto-actualizador.
