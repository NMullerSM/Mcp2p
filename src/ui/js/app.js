/**
 * MCP2P Application Core Controller
 * Hub Principal de Proyectos, Tablero Kanban Aislado y Ergonomía
 */

// Wrapper seguro de almacenamiento (inmune a restricciones de file:// en WebKitGTK)
const safeStorage = {
  get: function(key, fallback) {
    try {
      if (typeof window !== "undefined" && typeof window.localStorage !== "undefined") {
        return window.localStorage.getItem(key) || fallback;
      }
    } catch (e) {}
    return fallback;
  },
  set: function(key, value) {
    try {
      if (typeof window !== "undefined" && typeof window.localStorage !== "undefined") {
        window.localStorage.setItem(key, value);
      }
    } catch (e) {}
  }
};

class AppController {
  constructor() {
    this.currentTheme = safeStorage.get("mcp2p_theme", "dark");
    this.activeProject = null;
    this.projectsList = [];

    this.initElements();
    this.applyTheme(this.currentTheme);
    this.bindEvents();
    this.listenBridge();
  }

  initElements() {
    // Vistas principales
    this.viewProjects = document.getElementById("view-projects");
    this.viewKanban = document.getElementById("view-kanban");
    this.projectsGrid = document.getElementById("projects-grid");

    // Botones de Cabecera
    this.themeBtn = document.getElementById("theme-toggle-btn");
    this.guideToggleBtn = document.getElementById("toggle-guide-btn");
    this.newProjectHubBtn = document.getElementById("btn-new-project-hub");
    this.newTaskBtn = document.getElementById("btn-new-task");
    this.backToProjectsBtn = document.getElementById("btn-back-projects");

    // Elementos de estado
    this.activeProjectTitle = document.getElementById("active-project-title");
    this.activeProjectUuid = document.getElementById("active-project-uuid");
    this.statusProject = document.getElementById("status-project");
    this.statusNode = document.getElementById("status-node");
    this.statusWal = document.getElementById("status-wal");
    this.statusOs = document.getElementById("status-os");

    // Modales
    this.projectModal = document.getElementById("new-project-modal");
    this.taskModal = document.getElementById("new-task-modal");

    this.cancelProjectBtn = document.getElementById("cancel-project-modal-btn");
    this.saveProjectBtn = document.getElementById("save-project-btn");

    this.cancelTaskBtn = document.getElementById("cancel-task-modal-btn");
    this.saveTaskBtn = document.getElementById("save-task-btn");
  }

  applyTheme(theme) {
    this.currentTheme = theme;
    document.documentElement.setAttribute("data-theme", theme);
    safeStorage.set("mcp2p_theme", theme);
    if (this.themeBtn) {
      this.themeBtn.textContent = theme === "dark" ? "☀️" : "🌙";
    }
  }

  toggleTheme() {
    const next = this.currentTheme === "dark" ? "light" : "dark";
    this.applyTheme(next);
  }

  bindEvents() {
    // 1. Tema
    if (this.themeBtn) {
      this.themeBtn.onclick = () => this.toggleTheme();
    }

    // 2. Nuevo Proyecto
    if (this.newProjectHubBtn) {
      this.newProjectHubBtn.onclick = () => this.openNewProjectModal();
    }

    // 3. Volver al Hub de Proyectos
    if (this.backToProjectsBtn) {
      this.backToProjectsBtn.onclick = () => this.showProjectsHub();
    }

    // 4. Nueva Tarea en Proyecto
    if (this.newTaskBtn) {
      this.newTaskBtn.onclick = () => this.openNewTaskModal();
    }

    // 5. Botones Modales
    if (this.cancelProjectBtn) {
      this.cancelProjectBtn.onclick = () => this.closeNewProjectModal();
    }
    if (this.saveProjectBtn) {
      this.saveProjectBtn.onclick = () => this.submitNewProject();
    }

    if (this.cancelTaskBtn) {
      this.cancelTaskBtn.onclick = () => this.closeNewTaskModal();
    }
    if (this.saveTaskBtn) {
      this.saveTaskBtn.onclick = () => this.submitNewTask();
    }

    // 6. Atajos de Teclado Globales
    window.addEventListener("keydown", (e) => {
      // Ctrl+D o Cmd+D: Alternar Tema
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "d") {
        e.preventDefault();
        this.toggleTheme();
      }
      // Ctrl+N o Cmd+N: Nueva Tarea (o nuevo proyecto si estamos en el Hub)
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "n") {
        e.preventDefault();
        if (this.viewKanban && this.viewKanban.classList.contains("active")) {
          this.openNewTaskModal();
        } else {
          this.openNewProjectModal();
        }
      }
      // Ctrl+P: Nuevo Proyecto
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "p") {
        e.preventDefault();
        this.openNewProjectModal();
      }
      // ESC: Cerrar Modales o Drawer
      if (e.key === "Escape") {
        this.closeNewProjectModal();
        this.closeNewTaskModal();
      }
    });
  }

  listenBridge() {
    window.addEventListener("pywebviewready", () => {
      console.log("[MCP2P] PyWebView Bridge conectado con éxito.");
      this.onBridgeReady();
    });

    if (window.pywebview && window.pywebview.api) {
      this.onBridgeReady();
    }
  }

  async onBridgeReady() {
    await this.refreshSystemInfo();
    await this.loadProjectsHub();
  }

  async refreshSystemInfo() {
    if (!window.pywebview || !window.pywebview.api) return;
    try {
      const info = await window.pywebview.api.get_system_info();
      if (info.success) {
        if (this.statusNode) this.statusNode.textContent = `Nodo: ${info.active_node}`;
        if (this.statusWal) this.statusWal.textContent = `SQLite WAL: ACTIVO`;
        if (this.statusOs) this.statusOs.textContent = `${info.os} (${info.python_version})`;
      }
    } catch (e) {
      console.warn("[System Info Error]", e);
    }
  }

  // ==========================
  // Navegación de Vistas
  // ==========================

  showProjectsHub() {
    if (this.viewKanban) this.viewKanban.classList.remove("active");
    if (this.viewProjects) this.viewProjects.classList.add("active");
    if (this.newTaskBtn) this.newTaskBtn.style.display = "none";
    if (this.statusProject) this.statusProject.textContent = "Hub de Proyectos";
    this.activeProject = null;
    this.loadProjectsHub();
  }

  async openProjectWorkspace(project) {
    this.activeProject = project;
    if (!window.pywebview || !window.pywebview.api) return;

    try {
      await window.pywebview.api.select_project(project.project_uuid);

      if (this.activeProjectTitle) this.activeProjectTitle.textContent = project.name;
      if (this.activeProjectUuid) this.activeProjectUuid.textContent = project.project_uuid;
      if (this.statusProject) this.statusProject.textContent = `Proyecto: ${project.name} [${project.project_uuid}]`;

      if (this.viewProjects) this.viewProjects.classList.remove("active");
      if (this.viewKanban) this.viewKanban.classList.add("active");
      if (this.newTaskBtn) this.newTaskBtn.style.display = "inline-flex";

      if (window.kanbanController) {
        await window.kanbanController.loadBoard();
      }
    } catch (err) {
      alert(`Error al abrir proyecto: ${err}`);
    }
  }

  // ==========================
  // Renderizado del Hub de Proyectos
  // ==========================

  async loadProjectsHub() {
    if (!window.pywebview || !window.pywebview.api || !this.projectsGrid) return;

    try {
      const res = await window.pywebview.api.list_projects();
      if (res.success) {
        this.projectsList = res.projects;
        this.renderProjects(res.projects);
      }
    } catch (err) {
      console.error("[Load Projects Error]", err);
    }
  }

  renderProjects(projects) {
    if (!this.projectsGrid) return;
    this.projectsGrid.innerHTML = "";

    projects.forEach((proj) => {
      const card = document.createElement("div");
      card.className = "project-card";
      card.ondblclick = () => this.openProjectWorkspace(proj);

      card.innerHTML = `
        <div class="project-card-top">
          <div>
            <h3 class="project-name">${this.escape(proj.name)}</h3>
            <span style="font-size: 11px; color: var(--text-muted); font-family: var(--font-mono)">
              Creado: ${new Date(proj.created_at).toLocaleDateString()}
            </span>
          </div>
          <span class="project-uuid-badge">${this.escape(proj.project_uuid)}</span>
        </div>

        <div class="repo-align-box">
          <div class="repo-item" title="Repositorio de Código">
            <span class="repo-badge badge-app">APP</span>
            <span>${this.escape(proj.code_repo_path || "app/")}</span>
          </div>
          <div class="repo-item" title="Repositorio de Gobernanza OKF">
            <span class="repo-badge badge-gov">OKF</span>
            <span>${this.escape(proj.gov_repo_path || "governance/")}</span>
          </div>
        </div>

        <div class="project-card-footer">
          <span class="project-stats">orphan-conversa-memory-${this.escape(proj.project_uuid.slice(-6))}</span>
          <button class="btn btn-primary btn-sm btn-open">Abrir Proyecto →</button>
        </div>
      `;

      const openBtn = card.querySelector(".btn-open");
      if (openBtn) {
        openBtn.onclick = (e) => {
          e.stopPropagation();
          this.openProjectWorkspace(proj);
        };
      }

      this.projectsGrid.appendChild(card);
    });

    // Tarjeta adicional para crear proyecto con 1 clic
    const newCard = document.createElement("div");
    newCard.className = "project-card new-project-card";
    newCard.onclick = () => this.openNewProjectModal();
    newCard.innerHTML = `
      <span class="plus-icon">+</span>
      <span style="font-size: 14px; font-weight: 600;">Crear Nuevo Proyecto</span>
      <span style="font-size: 11px;">Aislamiento Doble Repositorio + OKF</span>
    `;
    this.projectsGrid.appendChild(newCard);
  }

  // ==========================
  // Modales
  // ==========================

  openNewProjectModal() {
    if (this.projectModal) {
      this.projectModal.classList.add("open");
      const nameInput = document.getElementById("input-project-name");
      if (nameInput) {
        nameInput.value = "";
        nameInput.focus();
      }
      const codeUrl = document.getElementById("input-code-url");
      if (codeUrl) codeUrl.value = "";
      const govUrl = document.getElementById("input-gov-url");
      if (govUrl) govUrl.value = "";
    }
  }

  closeNewProjectModal() {
    if (this.projectModal) this.projectModal.classList.remove("open");
  }

  async submitNewProject() {
    const nameInput = document.getElementById("input-project-name");
    const codeUrl = document.getElementById("input-code-url");
    const govUrl = document.getElementById("input-gov-url");

    const name = nameInput ? nameInput.value.trim() : "";
    if (!name) {
      alert("Por favor ingresa un nombre para el proyecto.");
      return;
    }

    try {
      const res = await window.pywebview.api.create_project(
        name,
        codeUrl ? codeUrl.value.trim() : "",
        govUrl ? govUrl.value.trim() : ""
      );

      if (res.success) {
        this.closeNewProjectModal();
        await this.loadProjectsHub();
        // Abrir inmediatamente el nuevo proyecto creado
        await this.openProjectWorkspace(res.project);
      } else {
        alert(`Error al crear proyecto: ${res.error}`);
      }
    } catch (err) {
      alert(`Error de comunicación: ${err}`);
    }
  }

  openNewTaskModal() {
    if (!this.activeProject) {
      alert("Selecciona primero un proyecto para crear tareas en su Kanban.");
      return;
    }
    if (this.taskModal) {
      this.taskModal.classList.add("open");
      const idInput = document.getElementById("input-task-id");
      if (idInput) {
        idInput.value = `CARD-${Math.floor(100 + Math.random() * 900)}`;
      }
      const titleInput = document.getElementById("input-task-title");
      if (titleInput) {
        titleInput.value = "";
        titleInput.focus();
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
      alert("El ID y el título de la tarea son obligatorios.");
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
      alert(`Error de comunicación: ${err}`);
    }
  }

  escape(str) {
    if (!str) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
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
