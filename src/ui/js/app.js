/**
 * MCP2P Application Core Controller
 * Inicialización de la interfaz, temas, subsistema de proyectos y atajos globales.
 */

class AppController {
  constructor() {
    this.theme = localStorage.getItem("mcp2p_theme") || "dark";
    this.initElements();
    this.initTheme();
    this.initEventListeners();
    this.checkBridge();
  }

  initElements() {
    this.themeBtn = document.getElementById("theme-toggle-btn");
    this.newTaskBtn = document.getElementById("btn-new-task");
    this.newProjectBtn = document.getElementById("btn-new-project");
    this.projectSelect = document.getElementById("project-selector");

    this.taskModal = document.getElementById("new-task-modal");
    this.projectModal = document.getElementById("new-project-modal");

    this.cancelTaskBtn = document.getElementById("cancel-modal-btn");
    this.saveTaskBtn = document.getElementById("save-task-btn");

    this.cancelProjectBtn = document.getElementById("cancel-project-modal-btn");
    this.saveProjectBtn = document.getElementById("save-project-btn");
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
    if (this.themeBtn) {
      this.themeBtn.textContent = this.theme === "dark" ? "☀️" : "🌙";
    }
  }

  initEventListeners() {
    // 1. Botón de Tema
    if (this.themeBtn) {
      this.themeBtn.onclick = (e) => {
        e.preventDefault();
        this.toggleTheme();
      };
    }

    // 2. Botón Nueva Tarea
    if (this.newTaskBtn) {
      this.newTaskBtn.onclick = (e) => {
        e.preventDefault();
        this.openNewTaskModal();
      };
    }

    // 3. Botón Nuevo Proyecto
    if (this.newProjectBtn) {
      this.newProjectBtn.onclick = (e) => {
        e.preventDefault();
        this.openNewProjectModal();
      };
    }

    // 4. Selector de Proyecto
    if (this.projectSelect) {
      this.projectSelect.onchange = (e) => {
        const uuid = e.target.value;
        if (uuid === "__NEW__") {
          this.openNewProjectModal();
        } else {
          this.switchProject(uuid);
        }
      };
    }

    // 5. Botones Modales
    if (this.cancelTaskBtn) {
      this.cancelTaskBtn.onclick = () => this.closeNewTaskModal();
    }
    if (this.saveTaskBtn) {
      this.saveTaskBtn.onclick = () => this.submitNewTask();
    }

    if (this.cancelProjectBtn) {
      this.cancelProjectBtn.onclick = () => this.closeNewProjectModal();
    }
    if (this.saveProjectBtn) {
      this.saveProjectBtn.onclick = () => this.submitNewProject();
    }

    // 6. Atajos de Teclado Globales
    window.addEventListener("keydown", (e) => {
      // Ctrl+D o Cmd+D: Alternar Tema
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "d") {
        e.preventDefault();
        this.toggleTheme();
      }
      // Ctrl+N o Cmd+N: Nueva Tarea
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "n") {
        e.preventDefault();
        this.openNewTaskModal();
      }
      // ESC: Cerrar Modales
      if (e.key === "Escape") {
        this.closeNewTaskModal();
        this.closeNewProjectModal();
      }
    });
  }

  checkBridge() {
    window.addEventListener("pywebviewready", () => {
      console.log("[MCP2P] PyWebView Bridge conectado.");
      this.onBridgeReady();
    });

    if (window.pywebview && window.pywebview.api) {
      this.onBridgeReady();
    }
  }

  async onBridgeReady() {
    await this.loadProjects();
    await this.updateSystemStatus();
    if (window.kanbanController) {
      await window.kanbanController.loadBoard();
    }
  }

  async loadProjects() {
    if (!window.pywebview || !window.pywebview.api) return;
    try {
      const res = await window.pywebview.api.list_projects();
      if (res.success && this.projectSelect) {
        this.projectSelect.innerHTML = "";
        res.projects.forEach((proj) => {
          const opt = document.createElement("option");
          opt.value = proj.project_uuid;
          opt.textContent = `${proj.name} (${proj.project_uuid})`;
          if (proj.project_uuid === res.active_uuid) {
            opt.selected = true;
          }
          this.projectSelect.appendChild(opt);
        });

        // Opción rápida para crear nuevo
        const newOpt = document.createElement("option");
        newOpt.value = "__NEW__";
        newOpt.textContent = "+ Crear Nuevo Proyecto...";
        this.projectSelect.appendChild(newOpt);
      }
    } catch (err) {
      console.error("[Projects Error]", err);
    }
  }

  async switchProject(projectUuid) {
    if (!window.pywebview || !window.pywebview.api) return;
    try {
      const res = await window.pywebview.api.select_project(projectUuid);
      if (res.success) {
        await this.updateSystemStatus();
        if (window.kanbanController) {
          await window.kanbanController.loadBoard();
        }
      }
    } catch (err) {
      console.error("[Switch Project Error]", err);
    }
  }

  async updateSystemStatus() {
    if (!window.pywebview || !window.pywebview.api) return;
    try {
      const info = await window.pywebview.api.get_system_info();
      if (info.success) {
        const nodeEl = document.getElementById("status-node");
        if (nodeEl) nodeEl.textContent = `Nodo: ${info.active_node}`;

        const walEl = document.getElementById("status-wal");
        if (walEl) walEl.textContent = `SQLite WAL: ACTIVO`;

        const osEl = document.getElementById("status-os");
        if (osEl) osEl.textContent = `${info.os} (${info.python_version})`;

        const projEl = document.getElementById("status-project");
        if (projEl && info.active_project) {
          projEl.textContent = `Proyecto: ${info.active_project.name} [${info.active_project.project_uuid}]`;
        }
      }
    } catch (err) {
      console.warn("[App Status Error]", err);
    }
  }

  // Modales
  openNewTaskModal() {
    if (this.taskModal) {
      this.taskModal.classList.add("open");
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
    if (this.taskModal) this.taskModal.classList.remove("open");
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

  openNewProjectModal() {
    if (this.projectModal) {
      this.projectModal.classList.add("open");
      const nameInput = document.getElementById("input-project-name");
      if (nameInput) {
        nameInput.value = "";
        nameInput.focus();
      }
      const codeUrlInput = document.getElementById("input-code-url");
      if (codeUrlInput) codeUrlInput.value = "";
      const govUrlInput = document.getElementById("input-gov-url");
      if (govUrlInput) govUrlInput.value = "";
    }
  }

  closeNewProjectModal() {
    if (this.projectModal) this.projectModal.classList.remove("open");
  }

  async submitNewProject() {
    const nameInput = document.getElementById("input-project-name");
    const codeUrlInput = document.getElementById("input-code-url");
    const govUrlInput = document.getElementById("input-gov-url");

    const name = nameInput ? nameInput.value.trim() : "";
    const codeUrl = codeUrlInput ? codeUrlInput.value.trim() : "";
    const govUrl = govUrlInput ? govUrlInput.value.trim() : "";

    if (!name) {
      alert("Por favor ingresa un nombre para el proyecto.");
      return;
    }

    try {
      const res = await window.pywebview.api.create_project(name, codeUrl, govUrl);
      if (res.success) {
        this.closeNewProjectModal();
        await this.loadProjects();
        await this.updateSystemStatus();
        if (window.kanbanController) {
          await window.kanbanController.loadBoard();
        }
      } else {
        alert(`Error al crear proyecto: ${res.error}`);
      }
    } catch (err) {
      alert(`Error al crear proyecto: ${err}`);
    }
  }
}

function initApp() {
  window.appController = new AppController();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initApp);
} else {
  initApp();
}
