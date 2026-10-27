# OKF Feature: Servidor Local Model Context Protocol (MCP)
## Especificación de Herramientas Agénticas y Control de Concurrencia (Fase 2)

> **Ubicación en Código**: [`src/mcp/server.py`](src/mcp/server.py) y [`src/mcp/tools.py`](src/mcp/tools.py)  
> **Pruebas Automatizadas**: [`tests/test_mcp_server.py`](tests/test_mcp_server.py)  
> **Configuración para IDEs**: [`mcp.json`](mcp.json)  

---

## 1. Propósito y Filosofía
El servidor MCP local expone las capacidades del enjambre a cualquier cliente agéntico estándar (Cursor, Claude Code, Gemini CLI, Antigravity IDE, etc.) a través del transporte estándar `stdio`.

Todas las operaciones se ejecutan de manera determinista contra SQLite en modo WAL, consumiendo exactamente **0 tokens de inferencia en el servidor**.

---

## 2. Herramientas Expuestas a los Agentes

| Herramienta | Parámetros | Descripción |
| :--- | :--- | :--- |
| `list_projects` | Ninguno | Lista todos los proyectos disponibles con sus `project_uuid` y rutas físicas de doble repositorio. |
| `get_kanban_board` | `project_uuid`, `status` (opcional) | Consulta las tareas del Kanban filtradas por estado (`all`, `backlog`, `in_progress`, `in_review`, `merged`, etc.). |
| `create_kanban_task` | `project_uuid`, `card_id`, `title`, `description` | Inserta una nueva tarea en el `backlog` del proyecto indicado a coste de 0 tokens. |
| `claim_kanban_task` | `project_uuid`, `card_id`, `node_uuid`, `duration_minutes` | Adquiere un **lease temporal exclusivo** (candado) sobre una tarjeta. Rechaza con `LEASE_COLLISION` si otro agente la tiene activa. |
| `release_kanban_task` | `project_uuid`, `card_id`, `node_uuid`, `target_status`, `branch_ref` | Transiciona la tarea a `in_review` o `merged`, vinculando la rama de código (`app/`). |
| `get_project_context` | `project_uuid` | Devuelve las rutas de `app/`, `governance/`, nombre de la rama huérfana y total de leases activos. |
| `register_agent_node` | `node_uuid`, `developer_name`, `agent_id`, `reviewer_sla_hours` | Registra el agente en el enjambre o renueva su latido (*heartbeat*) con su SLA autodeclarado de revisión. |

---

## 3. Ejemplo de Configuración en IDE (`mcp.json`)

```json
{
  "mcpServers": {
    "mcp2p": {
      "command": "./.venv/bin/python",
      "args": ["-m", "src.mcp.server"],
      "env": {
        "PYTHONPATH": "."
      }
    }
  }
}
```

---

## 4. Prevención de Condiciones de Carrera (Race Conditions)
Cuando dos agentes intentan reclamar la misma tarea:
* El primer agente recibe: `{"success": true, "lease_expires_at": "..."}`.
* El segundo agente recibe: `{"success": false, "code": "LEASE_COLLISION", "error": "La tarjeta está bloqueada por el nodo X hasta Y"}`.
* Al expirar el tiempo de lease (TTL), la tarjeta se libera automáticamente si el agente titular no emitió un latido de renovación.
