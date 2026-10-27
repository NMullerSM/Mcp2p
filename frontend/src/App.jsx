import React, { useState, useEffect, useCallback } from 'react';
import { usePyWebView } from './hooks/usePyWebView';
import { AppHeader } from './components/AppHeader';
import { Footer } from './components/Footer';
import { ProjectsHub } from './components/ProjectsHub';
import { ProjectWorkspace } from './components/ProjectWorkspace';
import { SettingsDrawer } from './components/SettingsDrawer';
import {
  NewProjectModal,
  NewTaskModal,
  JoinP2PModal,
  DeleteProjectModal
} from './components/Modals';

export default function App() {
  const { isReady, api } = usePyWebView();
  const [theme, setTheme] = useState(() => {
    try {
      return localStorage.getItem('mcp2p_theme') || 'dark';
    } catch {
      return 'dark';
    }
  });

  const [projects, setProjects] = useState([]);
  const [activeProject, setActiveProject] = useState(null);
  const [activeTab, setActiveTab] = useState('kanban');
  const [githubStatus, setGithubStatus] = useState(null);

  // Modals & Drawers state
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [settingsTab, setSettingsTab] = useState('p2p');
  const [isNewProjectOpen, setIsNewProjectOpen] = useState(false);
  const [isNewTaskOpen, setIsNewTaskOpen] = useState(false);
  const [isJoinP2POpen, setIsJoinP2POpen] = useState(false);
  const [projectToDelete, setProjectToDelete] = useState(null);
  const [taskTrigger, setTaskTrigger] = useState(0);

  // Aplicar tema
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    try {
      localStorage.setItem('mcp2p_theme', theme);
    } catch {}
  }, [theme]);

  const toggleTheme = useCallback(() => {
    setTheme(t => (t === 'dark' ? 'light' : 'dark'));
  }, []);

  // Cargar proyectos
  const loadProjects = useCallback(async () => {
    if (!api) return;
    try {
      const res = await api.list_projects();
      if (res.success) {
        setProjects(res.projects || []);
      }
    } catch (e) {
      console.error("Error al cargar proyectos:", e);
    }
  }, [api]);

  // Cargar estado de GitHub
  const loadGithubStatus = useCallback(async () => {
    if (!api) return;
    try {
      const res = await api.get_github_status();
      if (res.success) {
        setGithubStatus(res.github);
      }
    } catch (e) {
      console.error("Error al cargar estado de GitHub:", e);
    }
  }, [api]);

  useEffect(() => {
    if (isReady && api) {
      loadProjects();
      loadGithubStatus();
    }
  }, [isReady, api, loadProjects, loadGithubStatus]);

  // Manejo de atajos de teclado globales
  useEffect(() => {
    const handleKeyDown = (e) => {
      // Ctrl+D: Alternar tema
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'd') {
        e.preventDefault();
        toggleTheme();
      }
      // Ctrl+P: Nuevo Proyecto
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'p') {
        e.preventDefault();
        setIsNewProjectOpen(true);
      }
      // Ctrl+N: Nueva Tarea / Nuevo Proyecto
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'n') {
        e.preventDefault();
        if (activeProject) {
          setIsNewTaskOpen(true);
        } else {
          setIsNewProjectOpen(true);
        }
      }
      // F1: Abrir / Cerrar Drawer de Configuración y Manual
      if (e.key === 'F1') {
        e.preventDefault();
        setIsSettingsOpen(prev => !prev);
      }
      // ESC: Cerrar Modales o Drawer
      if (e.key === 'Escape') {
        setIsSettingsOpen(false);
        setIsNewProjectOpen(false);
        setIsNewTaskOpen(false);
        setIsJoinP2POpen(false);
        setProjectToDelete(null);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [toggleTheme, activeProject]);

  // Acciones de Proyecto
  const handleSelectProject = async (proj) => {
    if (api) {
      await api.select_project(proj.project_uuid);
    }
    setActiveProject(proj);
    setActiveTab('kanban');
  };

  const handleCreateProject = async (name, codeUrl, govUrl) => {
    if (!api) return;
    try {
      const res = await api.create_project(name, codeUrl, govUrl);
      if (res.success) {
        setIsNewProjectOpen(false);
        await loadProjects();
        setActiveProject(res.project);
        setActiveTab('kanban');
      } else {
        alert("Error al crear proyecto: " + res.error);
      }
    } catch (e) {
      alert("Error de comunicación: " + e);
    }
  };

  const handleJoinP2P = async (inviteStr) => {
    if (!api) return;
    try {
      const res = await api.join_p2p_project(inviteStr);
      if (res.success) {
        setIsJoinP2POpen(false);
        await loadProjects();
        setActiveProject(res.project);
        setActiveTab('kanban');
        alert(res.message || "¡Conectado exitosamente!");
      } else {
        alert("Error al unirse: " + res.error);
      }
    } catch (e) {
      alert("Error de comunicación: " + e);
    }
  };

  const handleDeleteProject = async () => {
    if (!api || !projectToDelete) return;
    try {
      const res = await api.delete_project(projectToDelete.project_uuid);
      if (res.success) {
        setProjectToDelete(null);
        await loadProjects();
      } else {
        alert("Error al eliminar proyecto: " + res.error);
      }
    } catch (e) {
      alert("Error de comunicación: " + e);
    }
  };

  const handleCreateTask = async (cardId, title, desc, criteria = '') => {
    if (!api) return;
    try {
      const res = await api.create_kanban_card(cardId, title, desc, criteria);
      if (res.success) {
        setIsNewTaskOpen(false);
        setTaskTrigger(prev => prev + 1);
      } else {
        alert("Error al crear tarea: " + res.error);
      }
    } catch (e) {
      alert("Error de comunicación: " + e);
    }
  };

  const handleOpenSettings = (tab = 'p2p') => {
    setSettingsTab(tab);
    setIsSettingsOpen(true);
  };

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      height: '100vh',
      backgroundColor: 'var(--bg-app)',
      color: 'var(--text-primary)',
      overflow: 'hidden'
    }}>
      {/* Parma-Style Distributed Header */}
      <AppHeader
        theme={theme}
        onToggleTheme={toggleTheme}
        onOpenSettings={handleOpenSettings}
        activeProject={activeProject}
        onBackToProjects={() => {
          setActiveProject(null);
          loadProjects();
        }}
        activeTab={activeTab}
        onTabChange={setActiveTab}
        onNewTask={() => setIsNewTaskOpen(true)}
        githubStatus={githubStatus}
        projectsCount={projects.length}
      />

      {/* Main View Area */}
      <main style={{ flex: 1, overflow: 'hidden', position: 'relative', display: 'flex' }}>
        {activeProject ? (
          <ProjectWorkspace
            project={activeProject}
            activeTab={activeTab}
            onBack={() => {
              setActiveProject(null);
              loadProjects();
            }}
            api={api}
            taskTrigger={taskTrigger}
          />
        ) : (
          <ProjectsHub
            projects={projects}
            onSelectProject={handleSelectProject}
            onNewProject={() => setIsNewProjectOpen(true)}
            onJoinP2P={() => setIsJoinP2POpen(true)}
            onDeleteProject={(proj) => setProjectToDelete(proj)}
          />
        )}
      </main>

      {/* Footer Minimalista: Solo SQLite WAL Activo */}
      <Footer />

      {/* Drawer de Configuración Desplegable desde la Derecha */}
      <SettingsDrawer
        isOpen={isSettingsOpen}
        onClose={() => setIsSettingsOpen(false)}
        api={api}
        activeProject={activeProject}
        projectsList={projects}
        initialTab={settingsTab}
        onGithubStatusChange={setGithubStatus}
      />

      {/* Modales */}
      <NewProjectModal
        isOpen={isNewProjectOpen}
        onClose={() => setIsNewProjectOpen(false)}
        onSubmit={handleCreateProject}
      />

      <NewTaskModal
        isOpen={isNewTaskOpen}
        onClose={() => setIsNewTaskOpen(false)}
        onSubmit={handleCreateTask}
      />

      <JoinP2PModal
        isOpen={isJoinP2POpen}
        onClose={() => setIsJoinP2POpen(false)}
        onSubmit={handleJoinP2P}
      />

      <DeleteProjectModal
        isOpen={Boolean(projectToDelete)}
        project={projectToDelete}
        onClose={() => setProjectToDelete(null)}
        onConfirm={handleDeleteProject}
      />
    </div>
  );
}
