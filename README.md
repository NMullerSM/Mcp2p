# MCP2P: Plataforma de Colaboración Agéntica y Gobernanza

> **Plataforma de Escritorio Local-First** construida con **PyWebView**, **Model Context Protocol (MCP)** y persistencia **SQLite (WAL)** para la orquestación y desarrollo concurrente entre humanos y agentes de Inteligencia Artificial.

---

## 📌 Estado del Repositorio y Metodología
* **Metodología de Desarrollo**: **Organic Driven Development (ODD)**. Se avanza mediante prototipos ejecutables incrementales y verificación continua de comportamiento en lugar de especificaciones rígidas pre-congeladas.
* **Control de Versiones (Git)**: El repositorio local se encuentra preparado estructuralmente. La inicialización y enlace remoto de Git queda reservada para gestión manual por el desarrollador.
* **Compatibilidad Multiplataforma**:
  * 🐧 **Linux**: Ejecución nativa mediante PyWebView (WebKitGTK/Qt).
  * 🪟 **Windows**: Ejecución nativa mediante PyWebView (WebView2).
  * 🍎 **macOS**: Soporte dual (Nativo o en contenedor Dockerizado aislado vía `docker/`).

---

## 📂 Estructura del Proyecto

```text
Mcp2p/
├── docs/                                 # Documentación técnica y arquitectura
│   ├── PLAN_MAESTRO_MCP2P.md             # Plan maestro ODD, puntos críticos y roadmap
│   └── GUIA_DE_USUARIO_Y_UX.md           # Heurísticas de Nielsen, Ley de Hick y UI tokens
├── src/                                  # Código fuente modular
│   ├── app/                              # Lógica principal y puente PyWebView (js_api)
│   ├── ui/                               # Frontend (HTML5, CSS3 temas claro/oscuro, JS)
│   ├── mcp/                              # Servidor local Model Context Protocol (MCP)
│   ├── a2a/                              # Canal de comunicación directa Agente-a-Agente
│   └── storage/                          # Persistencia SQLite WAL y adaptadores OKF
├── docker/                               # Entorno contenedor para macOS / Headless
│   ├── Dockerfile
│   └── docker-compose.yml
└── README.md                             # Este archivo
```

---

## 🎯 Pilares del Sistema
1. **Interfaz de Usuario Ergonómica (PyWebView)**:
   * Diseñada bajo las **10 Heurísticas de Nielsen** y la **Ley de Hick** (máximo 3 acciones primarias visibles para minimizar fatiga cognitiva).
   * Modo visual **Claro / Oscuro** nativo y tipografía técnica de alta legibilidad (**Inter** y **JetBrains Mono** / **Roboto Mono**).
   * **Guía de Usuario Integrada** en la propia aplicación mediante panel contextual interactivo.
2. **Tablero Kanban con Control de Concurrencia (MCP)**:
   * Herramientas MCP expuestas para agentes (`get_kanban_state`, `claim_kanban_task`, `release_kanban_task`).
   * Adquisición atómica con **Lease de tiempo** y candados temporales para evitar que dos agentes trabajen sobre la misma tarea.
3. **Canal Directo Agente a Agente (A2A)**:
   * Vía de comunicación prioritaria directa para coordinar bloqueos, dependencias y contratos de interfaz sin alterar código productivo.
4. **Gobernanza OKF Abierta**:
   * Desacoplada mediante adaptadores modulares para que en fases posteriores se establezca la taxonomía final de carpetas y formatos compartidos.

---

## 📖 Documentación Detallada
* Para revisar el plan técnico completo y análisis de riesgos, consulta:  
  👉 [docs/PLAN_MAESTRO_MCP2P.md](docs/PLAN_MAESTRO_MCP2P.md)
* Para revisar la especificación de diseño visual, temas y heurísticas UX, consulta:  
  👉 [docs/GUIA_DE_USUARIO_Y_UX.md](docs/GUIA_DE_USUARIO_Y_UX.md)
