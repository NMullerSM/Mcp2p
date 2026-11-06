# Ficha Técnica: Frontend React + Vite con PyWebView
> **Área**: Frontend  
> **Identificador**: `react_ui`  
> **Código Fuente**: [`frontend/src/App.jsx`](../../../frontend/src/App.jsx), [`frontend/src/components/`](../../../frontend/src/components/)  
> **Documento OKF de Referencia**: [`02_MAPA_DE_CODIGO_Y_FEATURES.md`](../02_MAPA_DE_CODIGO_Y_FEATURES.md)

---

## 1. Resumen y Propósito
El frontend moderno de MCP2P está construido sobre **React + Vite** y empaquetado como aplicación de escritorio nativa mediante **PyWebView**:
1. **Ergonomía Cognitiva y Ley de Hick**:
   - Cabecera con máximo 3 acciones primarias (`+ Nueva Tarea`, `☀️/🌙`, `⚙ Configuración`).
   - Título sobrio y limpio: **`MCP2P`**.
   - Pie de pantalla minimalista: únicamente `● SQLite WAL Activo`.
2. **Drawer Lateral de Configuración**:
   - Despliegue animado desde la derecha hacia la izquierda (`transform: translateX`).
   - Enlace P2P de copiado con 1 clic sin elementos innecesarios.
   - Pestaña **Agente MCP** con `mcp.json` listo para Cursor/Claude y Prompt Semilla guiado.
   - Pestaña **GitHub** y **Manual de Usuario (F1)**.
3. **Tablero Kanban con Drag & Drop**:
   - Arrastre manual fluido entre las 4 columnas (*Por Hacer*, *En Proceso*, *En Revisión*, *Consolidado*).
   - Candado e insignia de *Lease Activo* en tareas en proceso.
4. **Visor de Gobernanza OKF**:
   - Pestaña para inspeccionar y leer decisiones de arquitectura (`ADRs`) vivas.
   - Catálogo de habilidades y fichas técnicas (`skills`).
   - Reporte de integridad semántica de la especificación OKF.
5. **Gestor de Colaboradores HITL**:
   - Botón de aprobación explícita humana (`Aceptar`) para solicitudes de unión P2P.

---

## 2. Componentes Clave

| Componente | Responsabilidad |
| :--- | :--- |
| `App.jsx` | Controlador de estado global, atajos de teclado y modales. |
| `AppHeader.jsx` | Cabecera ergonómica con título MCP2P y 3 botones primarios. |
| `ProjectsHub.jsx` | Grilla interactiva de proyectos con doble repositorio y eliminación segura. |
| `ProjectWorkspace.jsx` | Navegación de proyecto con selector de vistas: Kanban, OKF y Colaboradores. |
| `KanbanBoard.jsx` | Tablero de 4 columnas con HTML5 Drag and Drop nativo. |
| `SettingsDrawer.jsx` | Drawer animado con P2P token, snippets MCP para Claude/Cursor y manual. |
| `OKFGovernanceView.jsx` | Explorador interactivo de ADRs, User Stories y Fichas Técnicas. |
| `CollaboratorsView.jsx` | Panel de enjambre con aprobación explícita de colaboradores. |
