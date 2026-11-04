# OKF-02: Mapa de Código, Relación entre Componentes y Features
## Guía de Navegación Técnica para Agentes y Desarrolladores

> **Identificador**: `OKF-02`  
> **Propósito**: Mapa mental y grafo de dependencias para entender el código fuente sin leer archivos a ciegas.  
> **Versión Actual**: 0.1.0 (Iteración 1)  

---

## 1. Grafo de Dependencias entre Componentes

```mermaid
graph TD
    AppMain["src/app/main.py<br/>(Punto de Entrada PyWebView)"] --> Bridge["src/app/api_bridge.py<br/>(Puente JS API)"]
    MCPServer["src/mcp/server.py<br/>(Servidor Local MCP)"] --> MCPTools["src/mcp/tools.py<br/>(Herramientas MCP)"]
    
    Bridge --> DB["src/storage/db.py<br/>(Motor SQLite WAL)"]
    Bridge --> Bus["src/a2a/bus.py<br/>(Bus A2A Híbrido)"]
    Bridge --> OKF["src/storage/okf_adapter.py<br/>(Adaptador OKF)"]
    
    MCPTools --> DB
    MCPTools --> Bus
    MCPTools --> OKF
    
    Bus --> DB
    
    AppMain --> UI_HTML["src/ui/index.html<br/>(Vista Principal)"]
    UI_HTML --> UI_App["src/ui/js/app.js<br/>(Controlador UI & Atajos)"]
    UI_HTML --> UI_Kanban["src/ui/js/kanban.js<br/>(Lógica Kanban)"]
    UI_HTML --> UI_Guide["src/ui/js/guide.js<br/>(Drawer de Ayuda F1)"]
    
    UI_App <-->|"window.pywebview.api"| Bridge
    UI_Kanban <-->|"window.pywebview.api"| Bridge
    
    DB --> SQLiteFile[(mcp2p_local.db)]
```

---

## 2. Mapa Detallado de Features y Referencias OKF

| Feature | Archivos Clave | Responsabilidad | Ficha Técnica Detallada |
| :--- | :--- | :--- | :--- |
| **Persistencia SQLite WAL** | [`src/storage/db.py`](src/storage/db.py) | Inicialización DDL, modo WAL, transacciones ACID, leases de tareas y registro de nodos. | 📄 [Ver Documentación Backend: `storage_sqlite.md`](docs/okf/backend/storage_sqlite.md) |
| **Bus A2A Híbrido (Fase 3)** | [`src/a2a/bus.py`](src/a2a/bus.py) | Canal binario MessagePack (0 tokens) para señales y RFC dialéctico de 3 turnos para consenso. | 📄 [Ver Documentación Backend: `a2a_bus.md`](docs/okf/backend/a2a_bus.md) |
| **Adaptador OKF & Memoria (Fase 3)** | [`src/storage/okf_adapter.py`](src/storage/okf_adapter.py) | Validación de gobernanza viva, registro de ADRs y sincronización en rama huérfana Git. | 📄 [Ver Documentación Backend: `okf_adapter.md`](docs/okf/backend/okf_adapter.md) |
| **Puente JS API (PyWebView)** | [`src/app/api_bridge.py`](src/app/api_bridge.py) | Métodos asíncronos expuestos a JavaScript, control de excepciones y nodo local por defecto. | 📄 [Ver Documentación Backend: `api_bridge.md`](docs/okf/backend/api_bridge.md) |
| **Punto de Entrada de Escritorio** | [`src/app/main.py`](src/app/main.py) | Configuración de ventana de 1280x840, soporte `--headless`, argumentos CLI y cierre limpio. | 📄 [Ver Documentación Backend: `api_bridge.md`](docs/okf/backend/api_bridge.md) |
| **Servidor Local MCP (Fase 2)** | [`src/mcp/server.py`](src/mcp/server.py), [`src/mcp/tools.py`](src/mcp/tools.py) | Servidor MCP 2.x sobre stdio con 23 herramientas para proyectos, Kanban, A2A, disputas y gobernanza. | 📄 [Ver Documentación Backend: `mcp_server.md`](docs/okf/backend/mcp_server.md) |
| **Criptografía Ed25519 & P2P** | [`src/p2p/crypto.py`](src/p2p/crypto.py), [`src/storage/github_sync.py`](src/storage/github_sync.py) | Firma digital Ed25519 de tokens P2P, verificación de identidad y control HITL de colaboradores. | 📄 [Ver Documentación Backend: `crypto_p2p.md`](docs/okf/backend/crypto_p2p.md) |
| **Frontend React + Vite** | [`frontend/src/App.jsx`](frontend/src/App.jsx), [`frontend/src/components/`](frontend/src/components/) | Aplicación reactiva de escritorio: Hub, Kanban Dnd, Drawer de Configuración lateral, visor OKF y HITL. | 📄 [Ver Documentación Frontend: `react_ui.md`](docs/okf/frontend/react_ui.md) |
| **Sistema Visual y Temas** | [`src/ui/css/themes.css`](src/ui/css/themes.css), [`app.js`](src/ui/js/app.js) | Paleta dual Claro/Oscuro, tokens CSS, contraste WCAG AAA, atajo `Ctrl+D`. | 📄 [Ver Documentación Frontend: `theme_system.md`](docs/okf/frontend/theme_system.md) |
| **Guía de Usuario Integrada** | [`src/ui/js/guide.js`](src/ui/js/guide.js), [`index.html`](src/ui/index.html) | Panel lateral deslizante (Heurística 10 de Nielsen), atajo `F1`, tabla de atajos. | 📄 [Ver Documentación Frontend: `user_guide_drawer.md`](docs/okf/frontend/user_guide_drawer.md) |
| **Tablero Kanban & Leases** | [`src/ui/js/kanban.js`](src/ui/js/kanban.js) | Renderizado en 4 columnas, insignias de lease activo, asignaciones y contadores dinámicos. | 📄 [Ver Documentación Frontend: `user_guide_drawer.md`](docs/okf/frontend/user_guide_drawer.md) |
| **Arrastre Manual Kanban (Drag & Drop)** | [`src/ui/js/kanban.js`](src/ui/js/kanban.js), [`styles.css`](src/ui/css/styles.css) | Mover tarjetas entre columnas a mano con HTML5 Drag and Drop, disparando leases y cambios de estado. | 📄 [Ver Documentación Frontend: `theme_system.md`](docs/okf/frontend/theme_system.md) |
| **Enlace P2P & Generador QR** | [`src/ui/js/qr.js`](src/ui/js/qr.js), [`app.js`](src/ui/js/app.js) | Generación de enlaces de invitación `mcp2p://join?...`, código QR SVG interactivo y unión de proyectos. | 📄 [Ver Documentación Backend: `a2a_bus.md`](docs/okf/backend/a2a_bus.md) |

---

## 3. Instrucciones de Carga para un Agente de IA

Cuando un agente autónomo comience una tarea de desarrollo en este repositorio:
1. **Paso 1**: Leer [`01_ARQUITECTURA_Y_GOBERNANZA.md`](docs/okf/01_ARQUITECTURA_Y_GOBERNANZA.md) para internalizar los axiomas del proyecto.
2. **Paso 2**: Consultar este archivo (`02_MAPA_DE_CODIGO_Y_FEATURES.md`) para identificar los módulos involucrados.
3. **Paso 3**: Abrir la ficha técnica específica en `backend/` o `frontend/` correspondiente a la feature que va a modificar.
4. **Paso 4**: Ejecutar la suite de pruebas con `PYTHONPATH=. .venv/bin/pytest tests/` antes y después de cualquier cambio.
