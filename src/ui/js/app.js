/**
 * MCP2P Application Core Controller
 * Inicialización de la interfaz, temas, atajos globales y puente PyWebView.
 */

class AppController {
  constructor() {
    this.theme = localStorage.getItem("mcp2p_theme") || "dark";
    this.modal = document.getElementById("new-task-modal");
    this.initTheme();
    this.initEventListeners();
  }

  initTheme() {
    document.documentElement.setAttribute("data-theme", this.theme);
    this.updateThemeButtonIcon();
  }

  toggleTheme() {
    this.theme = this.theme === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", this.theme);
    localStorage.setItem("mcp2p_theme", this.theme);
    this.updateThemeButtonIcon();
  }

  updateThemeButtonIcon() {
    const btn = document.getElementById("theme-toggle-btn");
    if (btn) {
      btn.textContent = this.theme === "dark" ? "☀️" : "🌙";
    }
  }

  initEventListeners() {
    // Botón de Tema
    const themeBtn = document.getElementById("theme-toggle-btn");
    if (themeBtn) {
      themeBtn.addEventListener("click", () => this.toggleTheme());
    }

    // Botón Nueva Tarea
    const newTaskBtn = document.getElementById("btn-new-task");
    if (newTaskBtn) {
      newTaskBtn.addEventListener("click", () => this.openNewTaskModal());
    }

    // Botones del Modal
    const cancelModalBtn = document.getElementById("cancel-modal-btn");
    if (cancelModalBtn) {
      cancelModalBtn.addEventListener("click", () => this.closeNewTaskModal());
    }

    const saveTaskBtn = document.getElementById("save-task-btn");
    if (saveTaskBtn) {
      saveTaskBtn.addEventListener("click", () => this.submitNewTask());
    }

    // Atajos de Teclado Globales
    window.addEventListener("keydown", (e) => {
      // Ctrl+D: Alternar Tema
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "d") {
        e.preventDefault();
        this.toggleTheme();
      }
      // Ctrl+N: Nueva Tarea
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "n") {
        e.preventDefault();
        this.openNewTaskModal();
      }
    });

    // Escuchar el evento de disponibilidad de PyWebView
    window.addEventListener("pywebviewready", () => {
      console.log("[MCP2P] PyWebView Bridge conectado con éxito.");
      this.onBridgeReady();
    });

    // Fallback por si pywebviewready ya ocurrió antes de montar listeners
    if (window.pywebview && window.pywebview.api) {
      this.onBridgeReady();
    }
  }

  async onBridgeReady() {
    await this.updateSystemStatus();
    if (window.kanbanController) {
      await window.kanbanController.loadBoard();
    }
  }

  async updateSystemStatus() {
    try {
      const info = await window.pywebview.api.get_system_info();
      if (info.success) {
        const nodeEl = document.getElementById("status-node");
        if (nodeEl) nodeEl.textContent = `Nodo: ${info.active_node}`;

        const walEl = document.getElementById("status-wal");
        if (walEl) walEl.textContent = `SQLite WAL: ACTIVO`;

        const osEl = document.getElementById("status-os");
        if (osEl) osEl.textContent = `${info.os} (${info.python_version})`;
      }
    } catch (err) {
      console.warn("[App Status] No se pudo leer info del sistema:", err);
    }
  }

  openNewTaskModal() {
    if (this.modal) {
      this.modal.classList.add("open");
      const titleInput = document.getElementById("input-task-title");
      if (titleInput) {
        titleInput.value = "";
        titleInput.focus();
      }
      const idInput = document.getElementById("input-task-id");
      if (idInput) {
        idInput.value = `CARD-${Math.floor(100 + Math.random() * 900)}`;
      }
      const descInput = document.getElementById("input-task-desc");
      if (descInput) descInput.value = "";
    }
  }

  closeNewTaskModal() {
    if (this.modal) {
      this.modal.classList.remove("open");
    }
  }

  async submitNewTask() {
    const idInput = document.getElementById("input-task-id");
    const titleInput = document.getElementById("input-task-title");
    const descInput = document.getElementById("input-task-desc");

    const cardId = idInput ? idInput.value.trim() : "";
    const title = titleInput ? titleInput.value.trim() : "";
    const desc = descInput ? descInput.value.trim() : "";

    if (!cardId || !title) {
      alert("Por favor ingresa un ID y título para la tarea.");
      return;
    }

    try {
      const res = await window.pywebview.api.create_kanban_card(cardId, title, desc);
      if (res.success) {
        this.closeNewTaskModal();
        if (window.kanbanController) {
          await window.kanbanController.loadBoard();
        }
      } else {
        alert(`Error al crear tarea: ${res.error}`);
      }
    } catch (err) {
      alert(`Error de comunicación con el puente: ${err}`);
    }
  }
}

document.addEventListener("DOMContentLoaded", () => {
  window.appController = new AppController();
});
