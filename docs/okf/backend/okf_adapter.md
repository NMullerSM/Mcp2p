# Ficha Técnica: Adaptador OKF y Gestor de Memoria Huérfana
> **Área**: Backend  
> **Identificador**: `okf_adapter`  
> **Código Fuente**: [`src/storage/okf_adapter.py`](../../../src/storage/okf_adapter.py)  
> **Documento OKF de Referencia**: [`02_MAPA_DE_CODIGO_Y_FEATURES.md`](../02_MAPA_DE_CODIGO_Y_FEATURES.md)

---

## 1. Resumen y Propósito
El módulo **OKF Adapter** (`OKFAdapter`) proporciona la capa desacoplada de gobernanza viva para MCP2P basada en la metodología **Organic Driven Development (ODD)**:
1. **Inicialización y Estandarización**: Genera la jerarquía estándar de carpetas (`docs/okf/`, `docs/okf/backend/`, `docs/okf/frontend/`), el esquema JSON y las plantillas iniciales para cualquier proyecto nuevo.
2. **Auditoría e Integridad**: Valida la existencia y conformidad de encabezados obligatorios, formato de ADRs y catálogo de características.
3. **Registro Determinista**: Métodos automáticos para añadir nuevos Architecture Decision Records (`append_adr`) y fichas técnicas (`create_feature_doc`) sin alteración manual propensa a errores.
4. **Gestión de Memoria en Rama Huérfana**: Escribe y lee snapshots de contexto cognitivo en la rama huérfana de Git `orphan-conversa-memory-{project_uuid}` a costo cero de tokens y sin modificar el árbol de trabajo de la rama activa.

---

## 2. Jerarquía de Carpetas OKF Estandarizada

```
governance/
└── docs/
    └── okf/
        ├── okf_schema.json                <-- Esquema formal de validación
        ├── 01_ARQUITECTURA_Y_GOBERNANZA.md <-- Axiomas, ADRs e Historias de Usuario
        ├── 02_MAPA_DE_CODIGO_Y_FEATURES.md <-- Grafo de dependencias y catálogo
        ├── backend/                       <-- Fichas técnicas de arquitectura backend
        └── frontend/                      <-- Fichas técnicas de componentes UI
```

---

## 3. Métodos Clave de la API en Python

| Método | Argumentos | Retorno | Descripción |
| :--- | :--- | :--- | :--- |
| `initialize_project_okf` | `gov_base_path, project_name, project_uuid` | `Path` | Crea la estructura de directorios y archivos base OKF. |
| `validate_okf_integrity` | `gov_base_path` | `dict` | Audita la integridad física y semántica del repositorio OKF. |
| `append_adr` | `gov_base_path, adr_number, title, context, decision, consequences` | `Path` | Inserta un nuevo ADR en `01_ARQUITECTURA_Y_GOBERNANZA.md`. |
| `create_feature_doc` | `gov_base_path, area, feature_slug, title, content` | `Path` | Crea una ficha técnica en `backend/` o `frontend/`. |
| `write_orphan_memory_snapshot` | `gov_base_path, project_uuid, snapshot_data` | `dict` | Registra el snapshot en la rama huérfana y cache local. |
| `read_orphan_memory_snapshot` | `gov_base_path, project_uuid` | `Optional[dict]` | Lee el snapshot de memoria huérfana del proyecto. |
