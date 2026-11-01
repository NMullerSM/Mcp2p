# Flujo HITL (Human-in-the-Loop), Doble Confirmación y Navegación UI

> **Componentes Frontend**: [`HumanApprovalsView.jsx`](../frontend/src/components/HumanApprovalsView.jsx), [`AppHeader.jsx`](../frontend/src/components/AppHeader.jsx), [`ProjectWorkspace.jsx`](../frontend/src/components/ProjectWorkspace.jsx), [`Modals.jsx`](../frontend/src/components/Modals.jsx), [`BranchConsensusView.jsx`](../frontend/src/components/BranchConsensusView.jsx)  
> **Puente Backend**: [`src/app/api_bridge.py`](../src/app/api_bridge.py) (`get_pending_human_approvals`, `get_hitl_item_detail`, `sign_and_approve_item`)

---

## 1. Rediseño de la Barra Superior (`AppHeader.jsx`)

1. **Eliminación de Ruido Visual (UUID Oculto)**:
   - Se eliminó el badge `proj_xxxxxxxxxxxx` de la barra superior para despejar el espacio horizontal. El identificador técnico sigue disponible en Configuración (`F1`).
2. **Altura Ampliada (`64px`) y Alineación Axial Exacta**:
   - La barra superior opera a `64px` de alto con centrado vertical estricto (`display: inline-flex; alignItems: center`).
   - Los botones de acción de la derecha (`Nueva Tarea`, `@usuario GitHub`, `Tema`, `Configuración`) comparten una altura uniforme de `36px`.
3. **Selector Central Limpio**:
   - Pestañas: `Tablero Kanban` (sin icono redundante), `Control de Cambios`, `HITL` (con icono `ShieldCheck` único), y `Colaboradores & Agentes`.

---

## 2. Bandeja HITL y Modal Frontal de Inspección Profunda (`HumanApprovalsView.jsx`)

### Limpieza de Iconografía
- Se eliminó el escudo circular redundante del banner izquierdo en `HumanApprovalsView.jsx`.
- Se eliminaron los iconos de las cabeceras de las 3 columnas (`Decisiones Arquitectónicas`, `Juicios de Honor Abiertos`, `Tareas en Revisión / PRs`), manteniendo el mismo estilo tipográfico limpio del Tablero Kanban.

### Inspector Frontal Desplegable (`inspectedItem`)
Al hacer clic sobre cualquier tarjeta o su botón de inspección en cualquiera de las 3 columnas, se despliega hacia el frente un modal de lectura detallada (`maxWidth: 820px`):

1. **Expediente de Juicio Dialéctico (`jury_vote`)**:
   - **Fase 1**: Tarjetas comparativas con el texto íntegro de **Tesis (`OPTION_A`)**, **Antítesis (`OPTION_B`)** y **Síntesis / Tercera Vía (`THIRD_WAY_C`)**.
   - **Fase 2**: Lista detallada de cada subagente neutral (`neutral_juror_01`, etc.), mostrando su temperatura ($T$), sus vectores de foco asignados y su argumento mayéutico completo, manteniendo ocultas las puntuaciones numéricas hasta que el humano vote.
   - **Fase 3**: Botones directos para votar por **Tesis (A)**, **Antítesis (B)** o **Síntesis (C)**.
2. **Inspección de Tarea en Revisión (`pr_merge`, ej. `TASK-102`)**:
   - Muestra el nodo/desarrollador asignado, la rama Git asociada (`branch_ref`), la **Descripción Técnica**, los **Criterios de Aceptación (`acceptance_criteria`)** y los **Commits recientes** asociados.
   - Permite **Aprobar y Fusionar Entrega** (fusiona en Git y mueve la tarjeta a `merged`) o **Devolver a En Proceso** (`in_progress`).
3. **Inspección de Decisión Arquitectónica (`decision`)**:
   - Muestra el documento Markdown completo (`full_content`) y permite **Aprobar y Promover a ADR** o **Descartar Decisión**.

---

## 3. Doble Factor de Confirmación Consciente (2FA Cognitivo HITL)

Ningún clic en los botones de acción ejecuta una firma de inmediato. Toda acción en la bandeja HITL abre primero el modal de **Doble Confirmación HITL**:

1. Muestra un resumen explícito del artefacto, opción votada o rama que se va a firmar.
2. Permite añadir una **nota de auditoría** opcional.
3. Exige marcar conscientemente la casilla:
   > *"Sí, acepto y confirmo conscientemente esta decisión bajo mi firma criptográfica `Ed25519`."*
4. Recién al habilitarse y pulsar **"Sí, Acepto y Firmar Ed25519"**, se invoca `api.sign_and_approve_item(...)`.

---

## 4. Navegación Bidireccional (`Kanban ↔ Control de Cambios ↔ HITL`)

- **Desde HITL → Kanban / Control de Cambios**:
  - Cada disputa o tarea en revisión incluye chips interactivos para abrir la tarjeta Kanban (`onOpenCard(card_id)`) o saltar directamente a su rama en el grafo de commits (`onNavigateToBranch(branch_ref)`).
- **Desde Kanban (`CardDetailModal`) → Control de Cambios / HITL**:
  - Incluye enlaces directos *"Ver en Grafo de Commits →"* y, cuando la tarea está en `in_review` o `disputed`, *"Inspeccionar en HITL →"*.
