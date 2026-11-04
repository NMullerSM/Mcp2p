# OKF Feature: Servidor Local Model Context Protocol (MCP)
## Especificación de Herramientas Agénticas y Control de Concurrencia (Fase 2-4)

> **Ubicación en Código**: [`src/mcp/server.py`](src/mcp/server.py) y [`src/mcp/tools.py`](src/mcp/tools.py)  
> **Pruebas Automatizadas**: [`tests/test_mcp_server.py`](tests/test_mcp_server.py)  
> **Configuración para IDEs**: [`mcp.json`](mcp.json)  

---

## 1. Propósito y Filosofía
El servidor MCP local expone las capacidades del enjambre a cualquier cliente agéntico estándar (Cursor, Claude Code, Gemini CLI, Antigravity IDE, Claude Desktop, etc.) a través del transporte estándar `stdio`.

Todas las operaciones se ejecutan de manera determinista contra SQLite en modo WAL, consumiendo exactamente **0 tokens de inferencia en el servidor**. Además, cuenta con una capa de resiliencia bidireccional que acepta sin fricción tanto `camelCase` como `snake_case`, payloads estructurados en `dict`/`list` y strings JSON.

---

## 2. Herramientas Expuestas a los Agentes (20 Herramientas)

### Grupo A: Proyectos, Tablero Kanban y Leases Atómicos
| Herramienta | Parámetros Clave | Descripción |
| :--- | :--- | :--- |
| `list_projects` | Ninguno | Lista proyectos registrados con `project_uuid` y rutas de doble repositorio (`app/` y `governance/`). |
| `get_kanban_board` | `project_uuid`, `status` | Consulta tarjetas Kanban filtradas por estado (`all`, `backlog`, `in_progress`, `in_review`, `disputed`, `merged`). |
| `create_kanban_task` | `project_uuid`, `card_id`, `title`, `description` | Inserta nueva tarea en el `backlog` del proyecto. Acepta aliases `task_id`/`taskId`. |
| `claim_kanban_task` | `project_uuid`, `card_id`, `node_uuid`, `duration_minutes` | Adquiere **lease atómico exclusivo** temporal para prevenir colisiones de agentes. |
| `release_kanban_task` | `project_uuid`, `card_id`, `node_uuid`, `target_status`, `branch_ref`, `dispute_reason` | Transiciona a `in_review`, `merged`, `backlog` o `disputed`. Si se escala a `disputed`, dispara automáticamente la apertura del ticket en el Tribunal de Honor. |
| `get_project_context` | `project_uuid` | Devuelve rutas de `app/`, `governance/`, rama huérfana y total de leases activos. |
| `register_agent_node` | `node_uuid`, `developer_name`, `agent_id`, `reviewer_sla_hours` | Registra el agente en el enjambre o renueva su latido con su SLA autodeclarado. |

### Grupo B: Protocolo Dialéctico A2A y Gobernanza OKF
| Herramienta | Parámetros Clave | Descripción |
| :--- | :--- | :--- |
| `send_coordination_rfc` | `project_uuid`, `sender_node_uuid`, `stage`, `session_id`, `payload_json`, `card_id` | Envía turno dialéctico (`PROPOSAL`, `COUNTER_PROPOSAL`, `RESOLUTION`) con validación estricta de campos obligatorios. |
| `get_coordination_session` | `project_uuid`, `session_id` | Consulta la secuencia cronológica de turnos dialécticos intercambiados en una sesión. |
| `inspect_okf_governance` | `project_uuid` | Audita la integridad de la gobernanza OKF y la memoria huérfana. |

### Grupo C: Worktrees Aislados, Micro-scripts y Distribución
| Herramienta | Parámetros Clave | Descripción |
| :--- | :--- | :--- |
| `get_task_full_context` | `project_uuid`, `card_id` | Protocolo de lectura previa: tarjeta YAML, ADRs enlazados, reglas de diseño y rama. |
| `manage_task_worktree` | `project_uuid`, `card_id`, `node_uuid`, `action`, `files_scope_json` | Crea o remueve worktrees aislados en `app/.worktrees/{card_id}` verificando colisiones de alcance de archivos (`files_scope`). |
| `run_okf_micro_script` | `project_uuid`, `script_action`, `params_json` | Micro-scripts seguros sobre OKF (`scaffold_decision`, `promote_decision`, `update_card`, `reconcile_kanban`) con validación de estados (`approved`, `discarded`, `superseded`). |
| `propose_or_apply_task_distribution` | `project_uuid`, `sender_node_uuid`, `session_id`, `distribution_plan_json`, `apply_immediately` | Reparto coordinado de tareas del backlog entre nodos con adquisición atómica opcional. |

### Grupo D: HITL Guardrail y Tribunal de Honor Borda
| Herramienta | Parámetros Clave | Descripción |
| :--- | :--- | :--- |
| `inspect_pr_and_recommend` | `project_uuid`, `pr_id_or_branch`, `reviewer_node_uuid`, `recommendation`, `analysis_summary`, `suggested_reply` | Cruza PR contra OKF y deja recomendación humana (`RECOMMEND_ACCEPT`, `RECOMMEND_CHANGES`, `RECOMMEND_REJECT`, `RECOMMEND_JURY`). |
| `open_dispute_ticket` | `project_uuid`, `title`, `collision_reason`, `contender_a_node`, `contender_b_node`, `proposal_a_ref`, `proposal_b_ref` | Abre formalmente un ticket de juicio técnico en disputa e inicializa la Matriz Neutral Anti-Sycophancy. |
| `get_dispute_ticket` | `project_uuid`, `ticket_id`, `card_id` | Consulta los detalles de una disputa, su matriz neutral y los votos emitidos por ticket_id o card_id. |
| `list_dispute_tickets` | `project_uuid`, `status`, `card_id` | Lista todos los tickets de disputa registrados en el proyecto con filtros opcionales. |
| `submit_agent_jury_evaluation` | `project_uuid`, `ticket_id`, `agent_id`, `evaluations_json`, `rationale` | Emite evaluación técnica ponderada (peso 1.5) en 5 dimensiones (KISS, Performance, Seguridad, Hicks, Testabilidad). |

### Grupo E: Onboarding, Auditoría de Deriva y Runtime
| Herramienta | Parámetros Clave | Descripción |
| :--- | :--- | :--- |
| `scan_and_bootstrap_okf` | `project_uuid` | Escanea exhaustivamente código y docs existentes generando el Dossier Unificado de Alineación OKF. |
| `get_alignment_dossier` | `project_uuid` | Recupera el Dossier de Alineación para revisión del humano o agentes inspectores. |
| `audit_okf_drift` | `project_uuid` | Compara el código real en `app/` contra el OKF ratificado y reporta derivas arquitectónicas. |
| `get_server_version` | Ninguno | Retorna versión del servidor (`0.3.0`), commit hash, recuento de herramientas (23) y estado operativo. |

---

## 3. Configuración para IDEs y Clientes (`mcp.json`)

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
