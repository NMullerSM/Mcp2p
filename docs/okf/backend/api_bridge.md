# OKF Feature: Puente JS API y Aplicación PyWebView
## Especificación Técnica del Enlace Frontend <-> Backend

> **Ubicación en Código**: [`src/app/api_bridge.py`](src/app/api_bridge.py) y [`src/app/main.py`](src/app/main.py)  
> **Pruebas Automatizadas**: [`tests/test_api_bridge.py`](tests/test_api_bridge.py)  

---

## 1. Propósito y Responsabilidades
`ApiBridge` expone de forma segura y tipada los métodos de negocio a la ventana web de PyWebView a través del objeto `window.pywebview.api`.

### Métodos Expuestos en JavaScript

```javascript
// Obtener tablero completo o filtrado
const res = await window.pywebview.api.get_kanban_board();

// Crear tarjeta
const res = await window.pywebview.api.create_kanban_card("CARD-102", "Título", "Descripción");

// Adquirir lease
const res = await window.pywebview.api.claim_task_lease("CARD-102", "node_pop-os", 30);

// Actualizar estado
const res = await window.pywebview.api.update_task_status("CARD-102", "in_review", "feature/my-branch");

// Info del sistema para status bar
const info = await window.pywebview.api.get_system_info();
```

---

## 2. Argumentos de Línea de Comandos (`main.py`)
* `--headless`: Ejecuta la inicialización de la base de datos y la verificación del puente sin abrir una ventana gráfica (ideal para testing y CI/CD).
* `--debug`: Abre el inspector de elementos web de Chromium/WebKit dentro de la ventana de PyWebView.
* `--db-path <ruta>`: Sobrescribe la ubicación del archivo SQLite local.
