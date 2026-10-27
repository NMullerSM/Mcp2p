/**
 * MCP2P User Guide Component (Heurística 10 de Nielsen)
 * Gestiona el panel de ayuda contextual y atajos de teclado globales.
 */

class UserGuide {
  constructor() {
    this.drawer = document.getElementById("guide-drawer");
    this.closeBtn = document.getElementById("close-guide-btn");
    this.toggleBtn = document.getElementById("toggle-guide-btn");
    this.initListeners();
  }

  initListeners() {
    if (this.toggleBtn) {
      this.toggleBtn.addEventListener("click", () => this.toggle());
    }
    if (this.closeBtn) {
      this.closeBtn.addEventListener("click", () => this.close());
    }

    // Atajos de Teclado Globales
    window.addEventListener("keydown", (e) => {
      // F1: Abrir / Cerrar Guía
      if (e.key === "F1") {
        e.preventDefault();
        this.toggle();
      }
      // ESC: Cerrar Drawer o Modales
      if (e.key === "Escape") {
        this.close();
      }
    });
  }

  toggle() {
    if (this.drawer) {
      this.drawer.classList.toggle("open");
    }
  }

  open() {
    if (this.drawer) {
      this.drawer.classList.add("open");
    }
  }

  close() {
    if (this.drawer) {
      this.drawer.classList.remove("open");
    }
  }
}

window.userGuide = new UserGuide();
