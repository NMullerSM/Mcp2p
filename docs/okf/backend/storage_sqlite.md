# OKF Feature: Motor de Almacenamiento SQLite WAL
## Especificación Técnica de Persistencia Local-First

> **Ubicación en Código**: [`src/storage/db.py`](src/storage/db.py)  
> **Pruebas Automatizadas**: [`tests/test_storage.py`](tests/test_storage.py)  

---

## 1. Propósito y Responsabilidades
El módulo `DatabaseManager` es el motor central de estado de cada nodo local de MCP2P. Provee persistencia ACID inmediata sin llamadas de red ni consumo de tokens.

### Tablas Gestionadas
* `swarm_nodes`: Registro de nodos P2P colaboradores y su SLA autodeclarado de revisión (`reviewer_sla_hours`).
* `kanban_cards`: Tarjetas Kanban con soporte para leases temporales (`lease_expires_at`).
* `a2a_messages`: Registro de mensajes y eventos semánticos entre agentes.
* `dispute_tickets`: Tickets de arbitraje y disputas técnicas.
* `jury_borda_scores`: Puntuaciones de 0 a 100 en 5 dimensiones del jurado.
* `outbox_events`: Cola append-only para sincronización asíncrona hacia Git.

---

## 2. Métodos Públicos Principales

```python
# Obtener instancia Singleton
db = DatabaseManager.get_instance(db_path="mcp2p_local.db")

# Crear tarjeta en Backlog (0 tokens)
card = db.create_card(card_id="CARD-101", title="Título", description="Detalle")

# Reclamar lease atómico con expiración
claimed_card = db.claim_card_lease(card_id="CARD-101", node_uuid="node_alpha", duration_minutes=30)

# Actualizar estado de tarjeta
updated = db.update_card_status(card_id="CARD-101", new_status="in_review", branch_ref="feature/auth")

# Registrar o actualizar nodo del enjambre
node = db.register_or_update_node(node_uuid="node_1", developer_name="Alice", agent_id="agent_1", reviewer_sla_hours=48)
```

---

## 3. Manejo de Errores y Concurrencia
* **`PermissionError`**: Se lanza si un nodo intenta adquirir una tarjeta que mantiene un lease activo no expirado por otro nodo.
* **`IntegrityError`**: SQLite exige que el `assignee_node_uuid` exista previamente en `swarm_nodes` (integridad referencial estricta).
