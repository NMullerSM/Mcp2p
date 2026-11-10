# Changelog

Todas las versiones siguen [SemVer](https://semver.org/lang/es/). Fuente única de la versión: `src/app/version.py`
(sincronizar con `python scripts/sync_version.py`).

## [Unreleased]

### Seguridad
- **Cierre de Quórum Humano y Prevención de Primer Voto (NEW-01, EGM-01)**: `is_quorum_closed` exige unanimidad de votos o el transcurso de una ventana de gracia (48h) tras recepción confirmada (`dispute_vote_delivery`), o 168h de timeout por estancamiento. Se elimina la vulnerabilidad donde un único voto humano prematuro cerraba la disputa.
- **Autenticación Ed25519 y Anti-Sybil en Votación (EGM-05)**: Los votos humanos requieren firma criptográfica válida sobre el contenido canónico (`VOTE:{option}:TICKET:{id}:PROJECT:{uuid}`) cruzada con la clave pública del colaborador aprobado. Nodos no registrados o atacantes Sybil se aíslan en `rejected_unknown_votes` sin diluir el denominador de elegibles. Exclusión estricta de contendientes en $N \ge 3$ y exclusión de árbitros en rondas regulares.
- **Aislamiento de Contexto y Anti-Prompt Injection (EGM-06, EGM-08)**: Paquetes Clean-Room delimitados por `<untrusted_proposal_context>` con nonces únicos impredecibles. Caracteres `<` y `>` escapados a entidades HTML. Truncado defensivo a 8,000 caracteres con verificación SHA-256 para prevenir ataques de denegación de servicio por desbordamiento de contexto.
- **Votación a Ciegas y Redacción Anti-Sesgo (EGM-07, NEW-05)**: Opciones proyectadas en alias neutrales (`ALFA`, `BETA`, `GAMMA`) mediante permutación determinista pseudoaleatoria por evaluador. Ocultamiento de puntuaciones numéricas de agentes y votos humanos individuales hasta que el evaluador firma su voto.
- **Validación de Identidad de Jurados MCP (NEW-03)**: Verificación estricta de asignación de `agent_id` en la matriz neutral del ticket, rechazo de evaluaciones duplicadas y bloqueo en tickets cerrados.

### Corregido
- **Persistencia Condicional de Dictámenes (NEW-02)**: Los archivos YAML `RULING-{ticket_id}.yaml` y sus commits correspondientes en Git solo se crean si el estado del veredicto es estrictamente `ratified`. Dictámenes en `open` o `challenged` no dejan artefactos huérfanos.
- **Eliminación de Sesgos Numéricos Mágicos (EGM-03)**: Removido el offset artificial `+10.0` y el multiplicador obsoleto `500.0`. Sincronía estricta `final_score == total_points`.
- **Eliminación de Síntesis Fabricadas (NEW-04)**: Erradicada la plantilla autogenerada para la Tercera Vía C. La síntesis solo se formula si un sintetizador genuino la genera o si es provista con fundamento técnico real.
- **Conteo Unificado de Votantes Elegibles (EGM-04)**: Centralizado en `bus.eligible_humans_count()` para evitar discrepancias entre bus, pasarela API y herramientas MCP.

### Mejorado
- **Modelo Continuo de Consenso Dialéctico Ponderado (EGM-02)**: Consenso ponderado $R = 0.70 \cdot h + 0.30 \cdot a$ con umbral estricto del $80\%$ y escalera jerárquica de resolución en 5 pasos (supermayoría, unanimidad humana, árbitro externo, desempate técnico sin árbitro, y escalamiento manual).
- **Mecanismo de Desempate con Árbitro Externo (EGM-01)**: Soporte para árbitro neutral pre-registrado (`project_arbiters`) congelado al momento de la apertura del ticket.
- **Escritura Atómica en Disco e Índice de Precedentes OKF (EGM-10)**: Nueva utilidad `atomic_write_text` con `fsync` y `replace` seguro con limpieza garantizada. Índice de precedentes determinista con suma SHA-256 e indexación exclusiva de fallos ratificados.
- **Identidad Visual de Marca MCP²P (Frente A)**: Generación de favicon e icono multi-resolución real para Windows (`mcp2p.ico` con capas de 16, 32, 48, 64 y 256 px), logos PNG optimizados, componente SVG inline `BrandMark.jsx`, tokens CSS de marca (`--brand-emerald`, `--brand-mint`) y títulos de ventana consistentes.
- **Mitigación de Parpadeo de Consola en Windows (Frente C)**: Creación del helper `run_hidden` / `no_window_kwargs` (`CREATE_NO_WINDOW`) para todos los subprocesos (`git`, `gh`, `scripts`), caché TTL de 60s para estado de autenticación GitHub, sincronización nativa de fondo en PyWebView (`set_ui_theme`) y eliminación de render commits redundantes en drawers.

### Interno
- **Sincronización Documental y Catálogo Técnico (EGM-09)**: Catálogo exhaustivo de 10 vectores técnicos (`FOCUS_CATALOG`) con directivas y submétricas sincronizado en `docs/ARQUITECTURA_JUICIO_DIALECTICO_Y_MATRIZ.md`.
- **Suite de Pruebas Adversariales**: 11 nuevas pruebas exhaustivas en `tests/test_adversarial_jury_and_hitl.py` cubriendo ataques Sybil, adulteración criptográfica, inyección de prompt, truncado masivo, aliasing ciego, resiliencia a caídas de disco y validación de jurados MCP.

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
