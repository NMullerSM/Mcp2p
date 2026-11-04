# MCP2P: Plataforma de Colaboración Agéntica y Gobernanza OKF

> **Plataforma de Escritorio Local-First** construida con **PyWebView**, **React + Vite**, **Model Context Protocol (MCP)** y persistencia determinista **SQLite (WAL)** para la orquestación, concurrencia y desarrollo colaborativo entre humanos y agentes de Inteligencia Artificial (Claude, Gemini, Antigravity, Cursor, etc.).

---

## 📌 Estado del Sistema y Capacidades Clave

* **Servidor MCP Local (23 Herramientas Deterministas a 0 Tokens)**: Expone herramientas deterministas para gestión de proyectos, leases de Kanban, worktrees aislados por tarea, micro-scripts de gobernanza OKF, canal dialéctico A2A, descubrimiento y arbitraje del Tribunal de Honor Borda e introspección de runtime.
* **Resiliencia Bidireccional de Esquema**: Soporte nativo para convenciones `camelCase` / `snake_case`, payloads estructurados (`dict`/`list`) y cadenas JSON, eliminando errores de validación entre agentes heterogéneos.
* **Control Estricto de Concurrencia**: Bloqueo atómico de tarjetas Kanban con TTL dinámico y detección de colisiones de alcance de archivos (`files_scope`) en Git Worktrees.
* **Tribunal de Honor Borda Anti-Sycophancy**: Resolución algorítmica de disputas técnicas y arquitectónicas entre agentes con ponderación imparcial (peso 1.5).
* **HITL Guardrail (Human-In-The-Loop)**: Los agentes inspeccionan código y recomiendan acciones (`RECOMMEND_ACCEPT`, `RECOMMEND_CHANGES`, `RECOMMEND_REJECT`, `RECOMMEND_JURY`), pero únicamente el humano puede firmar y fusionar mediante su clave Ed25519.
* **Compatibilidad Multiplataforma**:
  * 🐧 **Linux**: Ejecución nativa mediante PyWebView (WebKitGTK 4.1 / 4.0).
  * 🪟 **Windows**: Ejecución nativa mediante PyWebView (WebView2).
  * 🍎 **macOS**: Soporte nativo mediante PyWebView (WebKit Cocoa / pyobjc).

---

## 📂 Estructura del Proyecto

```text
Mcp2p/
├── docs/                                 # Documentación técnica, arquitectura y auditoría
│   ├── okf/                              # Gobernanza viva OKF (ADRs, decisiones, requirements, mcps)
│   ├── PLAN_MAESTRO_MCP2P.md             # Plan maestro ODD y roadmap
│   └── GUIA_DE_USUARIO_Y_UX.md           # Heurísticas de Nielsen y diseño UX
├── frontend/                             # Interfaz de usuario SPA en React + Vite + Tailwind/Lucide
│   ├── src/                              # Componentes visuales conectados de verdad al puente PyWebView
│   └── dist/                             # Bundle estático servido localmente a coste cero de red
├── src/                                  # Backend modular en Python 3.11+
│   ├── app/                              # Lógica de arranque (main.py) y puente bidireccional (api_bridge.py)
│   ├── mcp/                              # Servidor local Model Context Protocol (server.py y tools.py)
│   ├── a2a/                              # Canal dialéctico Agente-a-Agente y motor de juicio Borda
│   └── storage/                          # Base de datos SQLite (WAL) y adaptadores de memoria OKF
├── run.sh                                # Lanzador automático para Linux / macOS (con auto-build de frontend)
├── run.bat                               # Lanzador automático para Windows (con auto-build de frontend)
└── README.md                             # Este archivo
```

---

## 🚀 Inicio Rápido

### Linux / macOS
```bash
chmod +x run.sh
./run.sh
```
El script detectará el entorno virtual `.venv`, compilará el bundle de React si hay cambios en `frontend/src/` y lanzará la aplicación de escritorio.

### Windows
```cmd
run.bat
```

### Servidor MCP para Clientes Agénticos (`mcp.json`)
Para conectar Claude Desktop, Cursor o Antigravity IDE:
```json
{
  "mcpServers": {
    "mcp2p": {
      "command": "/ruta/absoluta/a/Mcp2p/.venv/bin/python",
      "args": ["-m", "src.mcp.server"],
      "env": {
        "PYTHONPATH": "/ruta/absoluta/a/Mcp2p",
        "MCP2P_DB_PATH": "/ruta/absoluta/a/Mcp2p/mcp2p_local.db"
      }
    }
  }
}
```

---

## 🧪 Pruebas Automatizadas

```bash
PYTHONPATH=. .venv/bin/pytest tests/ -v
```
Suite exhaustiva con 69+ pruebas unitarias y de integración cubriendo persistencia, criptografía Ed25519, colisiones de leases, worktrees aislados, bus dialéctico A2A, tribunal de honor y las 20 herramientas MCP.
