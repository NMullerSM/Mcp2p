/**
 * MCP2P User Guide Component (Heurística 10 de Nielsen)
 * Gestiona el panel de ayuda contextual y atajos de teclado globales.
 */

class UserGuide {
  constructor() {
    this.initElements();
    this.initListeners();
  }

  initElements() {
    this.drawer = document.getElementById("guide-drawer");
    this.closeBtn = document.getElementById("close-guide-btn");
    this.toggleBtn = document.getElementById("toggle-guide-btn");
  }

  initListeners() {
    if (this.toggleBtn) {
      this.toggleBtn.onclick = (e) => {
        e.preventDefault();
        this.toggle();
      };
    }
    if (this.closeBtn) {
      this.closeBtn.onclick = (e) => {
        e.preventDefault();
        this.close();
      };
    }

    // Atajos de Teclado Globales (F1 / ESC)
    window.addEventListener("keydown", (e) => {
      if (e.key === "F1") {
        e.preventDefault();
        this.toggle();
      } else if (e.key === "Escape") {
        this.close();
      }
    });
  }

  toggle() {
    if (!this.drawer) this.initElements();
    if (this.drawer) {
      this.drawer.classList.toggle("open");
    }
  }

  open() {
    if (!this.drawer) this.initElements();
    if (this.drawer) {
      this.drawer.classList.add("open");
    }
  }

  close() {
    if (!this.drawer) this.initElements();
    if (this.drawer) {
      this.drawer.classList.remove("open");
    }
  }
}

// Inicialización segura sin importar el momento de carga
function initGuide() {
  window.userGuide = new UserGuide();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initGuide);
} else {
  initGuide();
}
