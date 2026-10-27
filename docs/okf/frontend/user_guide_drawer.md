# OKF Feature: Guía de Usuario Integrada y Ergonomía
## Especificación del Panel de Ayuda Contextual (Heurística 10 de Nielsen)

> **Ubicación en Código**: [`src/ui/js/guide.js`](src/ui/js/guide.js), [`src/ui/css/styles.css`](src/ui/css/styles.css) y [`src/ui/index.html`](src/ui/index.html)  

---

## 1. Propósito y Experiencia de Usuario
Permitir que el desarrollador consulte instrucciones sobre leases, gobernanza A2A y atajos de teclado sin abandonar el tablero ni abrir un navegador externo.

### Mecánica de Interacción
* **Apertura**: Tecla `F1` o clic en el botón `? Guía (F1)` de la cabecera.
* **Cierre**: Tecla `ESC`, clic en el botón `✕` del panel o clic fuera del cajón.
* **Animación**: Panel lateral deslizable (*Drawer*) con curva de transición suave `cubic-bezier(0.16, 1, 0.3, 1)`.

---

## 2. Atajos de Teclado del Sistema

| Atajo | Acción | Contexto |
| :--- | :--- | :--- |
| <kbd>F1</kbd> | Abrir / Cerrar Guía de Usuario | Global |
| <kbd>Ctrl</kbd> + <kbd>N</kbd> | Abrir Modal de Nueva Tarea | Global |
| <kbd>Ctrl</kbd> + <kbd>D</kbd> | Alternar Tema Claro / Oscuro | Global |
| <kbd>Escape</kbd> | Cerrar Guía o Modal Abierto | Global |
