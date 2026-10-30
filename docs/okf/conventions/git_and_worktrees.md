# Convenciones de Git, Ramas y Worktrees

1. **Ramas Principales**:
   - `main`: Rama de código y gobernanza consensuada. Ningún agente puede hacer push directo a `main`.
2. **Ramas de Trabajo por Tarea**:
   - `wt/{card_id}`: Rama asociada al worktree `.worktrees/{card_id}` en `app/`.
3. **Rama de Memoria Huérfana**:
   - `orphan-conversa-memory-{project_uuid}`: Rama desconectada alojada en `.git/orphan_worktree` para almacenar transcripciones y telemetría.
4. **Orden de Sincronización Escalonada**:
   - 1º Fetch/Pull de Rama Huérfana.
   - 2º Fetch/Pull de Rama OKF (`main`).
   - 3º Validación cruzada contra el repositorio de código `app/`.
