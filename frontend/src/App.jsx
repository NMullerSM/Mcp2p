import React, { useState, useEffect, useCallback } from 'react';
import { usePyWebView } from './hooks/usePyWebView';
import { AppHeader } from './components/AppHeader';
import { Footer } from './components/Footer';
import { ProjectsHub, isPendingJoinStub } from './components/ProjectsHub';
import { ProjectWorkspace } from './components/ProjectWorkspace';
import { SettingsDrawer } from './components/SettingsDrawer';
import {
  NewProjectModal,
  NewTaskModal,
  JoinP2PModal,
  DeleteProjectModal
} from './components/Modals';
import { JoinRequestPending } from './components/JoinRequestPending';
import { APP_VERSION } from './version';
import { UpdateNotificationPill } from './components/UpdateNotificationPill';
import { UpdateModal } from './components/UpdateModal';

function parseSemVer(v) {
  const parts = v.replace(/^v/, '').trim().split('.').map((n) => parseInt(n, 10) || 0);
  return [parts[0] || 0, parts[1] || 0, parts[2] || 0];
}

function isNewerVersion(remote, local) {
  const [rMaj, rMin, rPat] = parseSemVer(remote);
  const [lMaj, lMin, lPat] = parseSemVer(local);
  if (rMaj !== lMaj) return rMaj > lMaj;
  if (rMin !== lMin) return rMin > lMin;
  return rPat > lPat;
}

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
  const [pendingJoinRequest, setPendingJoinRequest] = useState(null); // { project, requestCode }
  const [projectToDelete, setProjectToDelete] = useState(null);
  const [taskTrigger, setTaskTrigger] = useState(0);

  // Auto-updater state
  const [availableRelease, setAvailableRelease] = useState(null);
  const [isUpdateModalOpen, setIsUpdateModalOpen] = useState(false);
  const [isUpdateDismissed, setIsUpdateDismissed] = useState(false);
  const [updateProgress, setUpdateProgress] = useState({ status: 'idle', percent: 0, downloaded_mb: 0, total_mb: 0 });
  const [installMode, setInstallMode] = useState(null);

  useEffect(() => {
    if (!isReady || !api) return;
    let isMounted = true;
    (async () => {
      try {
        const res = await api.get_install_mode();
        if (isMounted && res?.success) setInstallMode(res);
      } catch { /* noop */ }
    })();
    return () => { isMounted = false; };
  }, [isReady, api]);

  useEffect(() => {
    let isMounted = true;
    async function checkForUpdate() {
      try {
        const res = await fetch('https://api.github.com/repos/NMullerSM/Mcp2p/releases/latest', {
          headers: { Accept: 'application/vnd.github.v3+json' },
        });
        if (res.ok) {
          const release = await res.json();
          if (isMounted && release?.tag_name) {
            const ignored = localStorage.getItem('mcp2p_ignored_update_version');
            if (isNewerVersion(release.tag_name, APP_VERSION) && ignored !== release.tag_name) {
              setAvailableRelease(release);
            }
          }
        }
      } catch {
        // Sin red: no interrumpe el uso local-first.
      }
    }
    checkForUpdate();
    return () => { isMounted = false; };
  }, []);

  useEffect(() => {
    if (updateProgress.status !== 'downloading' || !isReady || !api) return;
    const interval = setInterval(async () => {
      try {
        setUpdateProgress(await api.get_update_progress());
      } catch { /* noop */ }
    }, 1500);
    return () => clearInterval(interval);
  }, [updateProgress.status, isReady, api]);

  // Aplicar tema
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    try {
      localStorage.setItem('mcp2p_theme', theme);
    } catch {}
    if (api && typeof api.set_ui_theme === 'function') {
      api.set_ui_theme(theme).catch(() => {});
    }
  }, [theme, api]);

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
        if (pendingJoinRequest) {
          // Igual que "Volver a Proyectos": refrescar la lista para que el stub recién
          // creado aparezca en el hub (antes Escape dejaba la lista obsoleta).
          setPendingJoinRequest(null);
          loadProjects();
        }
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [toggleTheme, activeProject, pendingJoinRequest, loadProjects]);

  // Acciones de Proyecto
  const handleSelectProject = useCallback(async (proj) => {
    // Hallazgo C2 de la revisión final: un stub de unión todavía pendiente no tiene
    // repositorios reales, así que nunca debe abrirse como un espacio de trabajo normal
    // (allí el botón Sync, las invitaciones, etc. lo tratarían como un proyecto real; el
    // backend además lo rechaza en select_project). Se muestra la pantalla de espera, con
    // el código de solicitud regenerado (el original de join_p2p_project no se persiste).
    if (isPendingJoinStub(proj)) {
      let requestCode = '';
      if (api && typeof api.get_pending_join_request_code === 'function') {
        try {
          const res = await api.get_pending_join_request_code(proj.project_uuid);
          if (res?.success) requestCode = res.request_code || '';
        } catch { /* la pantalla sigue siendo útil para reintentar sin el código */ }
      }
      setPendingJoinRequest({ project: proj, requestCode });
      return;
    }
    if (api) {
      const res = await api.select_project(proj.project_uuid);
      if (res && res.success === false) {
        alert("No se pudo abrir el proyecto: " + (res.error || "Error desconocido"));
        return;
      }
    }
    setPendingJoinRequest(null);
    setActiveProject(proj);
    setActiveTab('kanban');
  }, [api]);

  const handleSelectProjectByUuid = useCallback((projectUuid) => {
    if (!projectUuid || projectUuid === '__hub__') {
      setActiveProject(null);
      loadProjects();
      return;
    }
    const found = projects.find(p => p.project_uuid === projectUuid);
    if (found) {
      handleSelectProject(found);
    }
  }, [projects, handleSelectProject, loadProjects]);

  const handleCreateProject = async (name, codeUrl, govUrl, autoPublish = false) => {
    if (!api) return;
    try {
      const res = await api.create_project(name, codeUrl, govUrl, autoPublish);
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
      if (res.success && res.status === 'pending_approval') {
        // Nuevo flujo HITL (Tarea 5): el stub del proyecto ya existe localmente, pero
        // nada se clonó todavía -- queda a la espera de que el dueño apruebe al
        // colaborador. Mostramos la pantalla de espera en vez del alert() de éxito.
        setIsJoinP2POpen(false);
        setPendingJoinRequest({ project: res.project, requestCode: res.request_code });
      } else if (res.success) {
        // El proyecto ya estaba registrado localmente (p. ej. una unión anterior ya
        // aprobada y clonada): reactivar el contexto normalmente.
        setIsJoinP2POpen(false);
        await loadProjects();
        setActiveProject(res.project);
        setActiveTab('kanban');
        alert(res.message || "¡Conectado exitosamente!");
      } else if (res.status === 'locked') {
        const until = res.locked_until ? ` (hasta ${res.locked_until})` : '';
        alert("Solicitud de unión bloqueada temporalmente por rechazos previos" + until + ".");
      } else {
        alert("Error al unirse: " + res.error);
      }
    } catch (e) {
      alert("Error de comunicación: " + e);
    }
  };

  const handleRetryJoinRequest = async () => {
    if (!api || !pendingJoinRequest?.project?.project_uuid) return;
    try {
      const res = await api.check_join_request_status(pendingJoinRequest.project.project_uuid);
      if (res.success && (res.status === 'active' || res.status === 'already_active')) {
        // La unión se completó (el dueño aprobó y el clonado terminó, o ya estaba
        // activa de un reintento anterior): proceder exactamente como el flujo de
        // éxito existente.
        setPendingJoinRequest(null);
        await loadProjects();
        setActiveProject(res.project);
        setActiveTab('kanban');
        return undefined;
      }
      if (res.status === 'still_pending') {
        // Hallazgo I2: un rechazo nunca se comunica al solicitante, así que este mismo
        // estado cubre también una solicitud ya rechazada -- el mensaje debe decirlo.
        return "Todavía sin aprobación del dueño del proyecto. Si la rechazó, la aplicación no puede saberlo: si esto se prolonga, confírmalo directamente con el dueño. Inténtalo de nuevo en un momento.";
      }
      if (res.status === 'no_remote_repos') {
        // Estado terminal (Tarea 7): el dueño no ha publicado repositorios remotos
        // todavía. Reintentar no lo resuelve, así que el mensaje no debe sugerirlo.
        return "El dueño del proyecto todavía no ha publicado sus repositorios remotos. No hay nada que clonar todavía; espera a que el dueño comparta los repositorios.";
      }
      if (res.status === 'locked') {
        const until = res.locked_until ? ` (hasta ${res.locked_until})` : '';
        return "Solicitud de unión bloqueada temporalmente por rechazos previos" + until + ".";
      }
      return res.error || "No se pudo completar la unión. Inténtalo de nuevo más tarde.";
    } catch (e) {
      return "Error de comunicación: " + e;
    }
  };

  const handleCancelPendingJoin = () => {
    // El stub del proyecto (join_status='pending_approval', o 'no_remote_repos' en la
    // práctica si el dueño nunca publicó repositorios) ya quedó persistido en
    // join_p2p_project independientemente de que esta pantalla esté visible o no -- es
    // la misma fila que sobreviviría a un reinicio de la app. Salir de esta pantalla no
    // es una operación destructiva ni deja nada "a medio registrar": solo deja de
    // mostrar la pantalla de espera y refresca la lista para que el stub aparezca en
    // el hub de proyectos (desde donde, si hiciera falta, se puede reintentar la unión
    // más adelante vía CollaboratorsView/retry_p2p_join).
    setPendingJoinRequest(null);
    loadProjects();
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

  const handleCreateTask = async (cardId, title, desc, criteria = '', assigneeNodeUuid = '') => {
    if (!api) return;
    try {
      const res = await api.create_kanban_card(cardId, title, desc, criteria, assigneeNodeUuid);
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
        projects={projects}
        onSelectProjectByUuid={handleSelectProjectByUuid}
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
      <main style={{ flex: 1, overflow: 'hidden', position: 'relative', display: 'flex', minHeight: 0, minWidth: 0 }}>
        {pendingJoinRequest ? (
          <JoinRequestPending
            project={pendingJoinRequest.project}
            requestCode={pendingJoinRequest.requestCode}
            onRetry={handleRetryJoinRequest}
            onBack={handleCancelPendingJoin}
          />
        ) : activeProject ? (
          <ProjectWorkspace
            project={activeProject}
            activeTab={activeTab}
            onTabChange={setActiveTab}
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
        api={api}
        project={activeProject}
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

      {/* Notificación y Modal de Auto-Actualización */}
      {availableRelease && !isUpdateDismissed && !isUpdateModalOpen && (
        <UpdateNotificationPill
          release={availableRelease}
          progress={updateProgress}
          onOpenModal={() => setIsUpdateModalOpen(true)}
          onDismiss={() => setIsUpdateDismissed(true)}
        />
      )}
      {isUpdateModalOpen && availableRelease && (
        <UpdateModal
          release={availableRelease}
          currentVersion={APP_VERSION}
          api={api}
          installMode={installMode}
          progress={updateProgress}
          onIgnoreVersion={(v) => {
            localStorage.setItem('mcp2p_ignored_update_version', v);
            setAvailableRelease(null);
            setIsUpdateModalOpen(false);
          }}
          onClose={() => setIsUpdateModalOpen(false)}
        />
      )}
    </div>
  );
}
