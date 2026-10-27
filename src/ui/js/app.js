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
    this.projectToDelete = null;

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
    this.settingsBtn = document.getElementById("btn-settings");
    this.newProjectHubBtn = document.getElementById("btn-new-project-hub");
    this.joinP2PBtn = document.getElementById("btn-join-p2p");
    this.newTaskBtn = document.getElementById("btn-new-task");
    this.backToProjectsBtn = document.getElementById("btn-back-projects");

    // Elementos de estado
    this.activeProjectTitle = document.getElementById("active-project-title");
    this.activeProjectUuid = document.getElementById("active-project-uuid");
    this.statusWal = document.getElementById("status-wal");

    // Modales
    this.projectModal = document.getElementById("new-project-modal");
    this.taskModal = document.getElementById("new-task-modal");
    this.settingsModal = document.getElementById("settings-modal");
    this.joinP2PModal = document.getElementById("join-p2p-modal");
    this.deleteProjectModal = document.getElementById("delete-project-modal");

    // Elementos dentro de Modales
    this.cancelProjectBtn = document.getElementById("cancel-project-modal-btn");
    this.saveProjectBtn = document.getElementById("save-project-btn");

    this.cancelTaskBtn = document.getElementById("cancel-task-modal-btn");
    this.saveTaskBtn = document.getElementById("save-task-btn");

    this.closeSettingsBtn = document.getElementById("close-settings-modal-btn");
    this.doneSettingsBtn = document.getElementById("btn-done-settings");
    this.copyP2PInviteBtn = document.getElementById("btn-copy-p2p-invite");
    this.openGuideFromSettingsBtn = document.getElementById("btn-open-guide-from-settings");
    this.inputP2PInviteUrl = document.getElementById("input-p2p-invite-url");
    this.p2pQrCodeBox = document.getElementById("p2p-qr-code-box");
    this.settingsNodeUuid = document.getElementById("settings-node-uuid");
    this.settingsActiveProj = document.getElementById("settings-active-proj");

    this.cancelJoinP2PBtn = document.getElementById("cancel-join-p2p-btn");
    this.submitJoinP2PBtn = document.getElementById("submit-join-p2p-btn");
    this.inputJoinP2PToken = document.getElementById("input-join-p2p-token");

    this.deleteProjectNameEl = document.getElementById("delete-project-name");
    this.cancelDeleteProjectBtn = document.getElementById("cancel-delete-project-btn");
    this.confirmDeleteProjectBtn = document.getElementById("confirm-delete-project-btn");
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

    // 2. Configuración & Enlace P2P
    if (this.settingsBtn) {
      this.settingsBtn.onclick = () => this.openSettingsModal();
    }
    if (this.closeSettingsBtn) {
      this.closeSettingsBtn.onclick = () => this.closeSettingsModal();
    }
    if (this.doneSettingsBtn) {
      this.doneSettingsBtn.onclick = () => this.closeSettingsModal();
    }
    if (this.copyP2PInviteBtn) {
      this.copyP2PInviteBtn.onclick = () => this.copyP2PInvite();
    }
    if (this.openGuideFromSettingsBtn) {
      this.openGuideFromSettingsBtn.onclick = () => {
        this.closeSettingsModal();
        if (window.guideDrawer) window.guideDrawer.open();
      };
    }

    // 3. Nuevo Proyecto Hub
    if (this.newProjectHubBtn) {
      this.newProjectHubBtn.onclick = () => this.openNewProjectModal();
    }
    if (this.cancelProjectBtn) {
      this.cancelProjectBtn.onclick = () => this.closeNewProjectModal();
    }
    if (this.saveProjectBtn) {
      this.saveProjectBtn.onclick = () => this.submitNewProject();
    }

    // 4. Unirse vía P2P
    if (this.joinP2PBtn) {
      this.joinP2PBtn.onclick = () => this.openJoinP2PModal();
    }
    if (this.cancelJoinP2PBtn) {
      this.cancelJoinP2PBtn.onclick = () => this.closeJoinP2PModal();
    }
    if (this.submitJoinP2PBtn) {
      this.submitJoinP2PBtn.onclick = () => this.submitJoinP2P();
    }

    // 5. Eliminar Proyecto
    if (this.cancelDeleteProjectBtn) {
      this.cancelDeleteProjectBtn.onclick = () => this.closeDeleteProjectModal();
    }
    if (this.confirmDeleteProjectBtn) {
      this.confirmDeleteProjectBtn.onclick = () => this.submitDeleteProject();
    }

    // 6. Volver al Hub de Proyectos
    if (this.backToProjectsBtn) {
      this.backToProjectsBtn.onclick = () => this.showProjectsHub();
    }

    // 7. Nueva Tarea en Proyecto
    if (this.newTaskBtn) {
      this.newTaskBtn.onclick = () => this.openNewTaskModal();
    }
    if (this.cancelTaskBtn) {
      this.cancelTaskBtn.onclick = () => this.closeNewTaskModal();
    }
    if (this.saveTaskBtn) {
      this.saveTaskBtn.onclick = () => this.submitNewTask();
    }

    // 8. Atajos de Teclado Globales
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
      // ESC: Cerrar Modales
      if (e.key === "Escape") {
        this.closeNewProjectModal();
        this.closeNewTaskModal();
        this.closeSettingsModal();
        this.closeJoinP2PModal();
        this.closeDeleteProjectModal();
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
    await this.loadProjectsHub();
  }

  // ==========================
  // Navegación de Vistas
  // ==========================

  showProjectsHub() {
    if (this.viewKanban) this.viewKanban.classList.remove("active");
    if (this.viewProjects) this.viewProjects.classList.add("active");
    if (this.newTaskBtn) this.newTaskBtn.style.display = "none";
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
          <div style="display: flex; align-items: center; gap: 8px;">
            <span class="project-uuid-badge">${this.escape(proj.project_uuid)}</span>
            <button class="btn-delete-proj" title="Eliminar Proyecto">🗑️</button>
          </div>
        </div>

        <div class="repo-align-box">
          <div class="repo-item" title="Repositorio de Código">
            <span class="repo-badge badge-app">APP</span>
            <span>${this.escape(proj.code_repo_path || "app/")}</span>
          </div>
          <div class="repo-item" title="Repositorio de Gobernanza OKF">
            <span class="repo-badge badge-gov">OKF</span>
            <span>${this.escape(proj.gov_repo_path || "okf/")}</span>
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

      const delBtn = card.querySelector(".btn-delete-proj");
      if (delBtn) {
        delBtn.onclick = (e) => {
          e.stopPropagation();
          this.confirmDeleteProject(proj);
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
      <span style="font-size: 11px;">Doble Repositorio + OKF Aislado</span>
    `;
    this.projectsGrid.appendChild(newCard);
  }

  // ==========================
  // Modal de Configuración y Enlace P2P
  // ==========================

  async openSettingsModal() {
    if (!this.settingsModal) return;
    this.settingsModal.classList.add("open");

    if (window.pywebview && window.pywebview.api) {
      try {
        const sys = await window.pywebview.api.get_system_info();
        if (this.settingsNodeUuid) {
          this.settingsNodeUuid.textContent = sys.active_node || "node_local";
        }
        if (this.settingsActiveProj) {
          this.settingsActiveProj.textContent = this.activeProject 
            ? `${this.activeProject.name} (${this.activeProject.project_uuid})`
            : (this.projectsList[0] ? `${this.projectsList[0].name} (${this.projectsList[0].project_uuid})` : "Ningún proyecto activo");
        }

        // Generar invitación P2P
        const targetUuid = this.activeProject?.project_uuid || this.projectsList[0]?.project_uuid;
        if (targetUuid) {
          const inv = await window.pywebview.api.generate_p2p_invite(targetUuid);
          if (inv.success) {
            if (this.inputP2PInviteUrl) this.inputP2PInviteUrl.value = inv.invite_url;
            if (this.p2pQrCodeBox && window.QRCodeGenerator) {
              this.p2pQrCodeBox.innerHTML = window.QRCodeGenerator.generateSVG(inv.invite_url, 150);
            }
          }
        }
      } catch (err) {
        console.error("[Settings Open Error]", err);
      }
    }
  }

  closeSettingsModal() {
    if (this.settingsModal) this.settingsModal.classList.remove("open");
  }

  copyP2PInvite() {
    if (this.inputP2PInviteUrl) {
      this.inputP2PInviteUrl.select();
      navigator.clipboard?.writeText(this.inputP2PInviteUrl.value).then(() => {
        if (this.copyP2PInviteBtn) {
          const prev = this.copyP2PInviteBtn.textContent;
          this.copyP2PInviteBtn.textContent = "¡Copiado!";
          setTimeout(() => { this.copyP2PInviteBtn.textContent = prev; }, 1800);
        }
      }).catch(() => {
        document.execCommand("copy");
        alert("Enlace P2P copiado al portapapeles.");
      });
    }
  }

  // ==========================
  // Modal Unirse a Proyecto P2P
  // ==========================

  openJoinP2PModal() {
    if (this.joinP2PModal) {
      this.joinP2PModal.classList.add("open");
      if (this.inputJoinP2PToken) {
        this.inputJoinP2PToken.value = "";
        this.inputJoinP2PToken.focus();
      }
    }
  }

  closeJoinP2PModal() {
    if (this.joinP2PModal) this.joinP2PModal.classList.remove("open");
  }

  async submitJoinP2P() {
    const raw = this.inputJoinP2PToken ? this.inputJoinP2PToken.value.trim() : "";
    if (!raw) {
      alert("Por favor ingresa un token o enlace de invitación P2P.");
      return;
    }

    try {
      const res = await window.pywebview.api.join_p2p_project(raw);
      if (res.success) {
        this.closeJoinP2PModal();
        await this.loadProjectsHub();
        await this.openProjectWorkspace(res.project);
        alert(res.message || "¡Conectado al proyecto P2P con éxito!");
      } else {
        alert("Error al conectar con proyecto P2P: " + res.error);
      }
    } catch (err) {
      alert("Error de comunicación: " + err);
    }
  }

  // ==========================
  // Modal Eliminar Proyecto
  // ==========================

  confirmDeleteProject(project) {
    this.projectToDelete = project;
    if (this.deleteProjectNameEl) {
      this.deleteProjectNameEl.textContent = `${project.name} (${project.project_uuid})`;
    }
    if (this.deleteProjectModal) {
      this.deleteProjectModal.classList.add("open");
    }
  }

  closeDeleteProjectModal() {
    this.projectToDelete = null;
    if (this.deleteProjectModal) {
      this.deleteProjectModal.classList.remove("open");
    }
  }

  async submitDeleteProject() {
    if (!this.projectToDelete || !window.pywebview || !window.pywebview.api) return;
    try {
      const res = await window.pywebview.api.delete_project(this.projectToDelete.project_uuid);
      if (res.success) {
        this.closeDeleteProjectModal();
        await this.loadProjectsHub();
      } else {
        alert("Error al eliminar proyecto: " + (res.error || "Operación fallida"));
      }
    } catch (err) {
      alert("Error de comunicación: " + err);
    }
  }

  // ==========================
  // Modales Creación
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
