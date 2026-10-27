# OKF Feature: Sistema Visual y Motor de Temas
## Especificación de Diseño Claro/Oscuro y Tokens CSS

> **Ubicación en Código**: [`src/ui/css/themes.css`](src/ui/css/themes.css) y [`src/ui/js/app.js`](src/ui/js/app.js)  

---

## 1. Propósito y Ergonomía
Proveer un entorno visual cómodo y descansado para jornadas prolongadas de desarrollo, cumpliendo con la **Heurística 8 de Nielsen (Diseño estético y minimalista)**.

### Variables CSS Clave

| Token | Tema Oscuro (`data-theme="dark"`) | Tema Claro (`data-theme="light"`) | Uso |
| :--- | :--- | :--- | :--- |
| `--bg-app` | `#121316` | `#f6f8fa` | Fondo general de la ventana |
| `--bg-surface` | `#1a1b20` | `#ffffff` | Cabecera, columnas y paneles |
| `--bg-card` | `#22242b` | `#ffffff` | Tarjetas Kanban individuales |
| `--accent-primary` | `#4f75ff` | `#2563eb` | Botones y acciones de alta prioridad |
| `--badge-lease` | `rgba(245, 158, 11, 0.15)` | `rgba(217, 119, 6, 0.12)` | Distintivo visual de tareas con candado |

---

## 2. Atajos y Persistencia
* **Alternar Tema**: Clic en el botón de sol/luna en la cabecera o mediante el atajo de teclado global `Ctrl + D` (o `Cmd + D` en macOS).
* **Persistencia**: La preferencia seleccionada se guarda automáticamente en `localStorage.getItem("mcp2p_theme")`.
