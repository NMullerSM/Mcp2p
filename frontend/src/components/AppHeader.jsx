import React, { useState, useEffect } from 'react';
import {
  Sun,
  Moon,
  Settings,
  Plus,
  GitBranch,
  Users,
  Github,
  ShieldCheck,
  BookOpen,
  LayoutGrid
} from 'lucide-react';
import BrandMark from './BrandMark';

const WORKSPACE_TABS = [
  { id: 'kanban', label: 'Tablero Kanban', icon: LayoutGrid },
  { id: 'changes', label: 'Control de Cambios', icon: GitBranch },
  { id: 'approvals', label: 'HITL', title: 'HITL — Human in the Loop', icon: ShieldCheck },
  { id: 'okf', label: 'Gobernanza OKF', title: 'Gobernanza OKF (ADRs, Freshness y Firmas)', icon: BookOpen },
  { id: 'collaborators', label: 'Colaboradores', title: 'Colaboradores & Agentes', icon: Users }
];

export function AppHeader({
  theme,
  onToggleTheme,
  onOpenSettings,
  activeProject,
  onBackToProjects,
  onSelectProjectByUuid,
  projects = [],
  activeTab,
  onTabChange,
  onNewTask,
  githubStatus,
  projectsCount = 0
}) {
  // Por debajo de 960px, la navegación y acciones pasan a modo compacto
  const COMPACT_BREAKPOINT = 960;
  const [isCompact, setIsCompact] = useState(() => window.innerWidth < COMPACT_BREAKPOINT);

  useEffect(() => {
    const handleResize = () => setIsCompact(window.innerWidth < COMPACT_BREAKPOINT);
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  const projectOptions = React.useMemo(() => {
    const list = Array.isArray(projects) ? [...projects] : [];
    if (activeProject && !list.some(p => p.project_uuid === activeProject.project_uuid)) {
      list.unshift(activeProject);
    }
    return list;
  }, [projects, activeProject]);

  const totalProjects = projectOptions.length || projectsCount;

  const handleProjectSelectChange = (e) => {
    const value = e.target.value;
    if (value === '__hub__') {
      if (onBackToProjects) onBackToProjects();
      return;
    }
    if (onSelectProjectByUuid) {
      onSelectProjectByUuid(value);
    }
  };

  return (
    <header className="mcp-header">
      {/* 1. SECCIÓN IZQUIERDA: BrandMark + Combobox Selector de Proyecto */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexShrink: 0, height: '100%' }}>
        <BrandMark size={24} />

        <span style={{ color: 'var(--border-subtle)', fontWeight: 300, display: 'inline-flex', alignItems: 'center' }}>|</span>

        <select
          data-testid="project-switcher-combobox"
          className="mcp-combobox"
          aria-label="Selector de proyecto"
          title={activeProject ? `Proyecto activo: ${activeProject.name}` : 'Seleccionar proyecto o permanecer en el Hub'}
          value={activeProject ? activeProject.project_uuid : '__hub__'}
          onChange={handleProjectSelectChange}
        >
          {activeProject ? (
            <option value="__hub__">&lt;- Volver al Hub de Proyectos</option>
          ) : (
            <option value="__hub__">Hub de Proyectos ({totalProjects})</option>
          )}
          {projectOptions.map(proj => (
            <option key={proj.project_uuid} value={proj.project_uuid}>
              {proj.name}{proj.join_status && proj.join_status !== 'active' ? ' (Pendiente)' : ''}
            </option>
          ))}
        </select>
      </div>

      {/* 2. SECCIÓN CENTRAL: Navegación Integrada (con Gobernanza OKF) o Selector Compacto */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', flex: 1, minWidth: 0, height: '100%' }}>
        {activeProject ? (
          isCompact ? (
            <select
              data-testid="nav-tab-selector"
              className="mcp-combobox"
              aria-label="Cambiar vista del proyecto"
              value={activeTab}
              onChange={(e) => onTabChange && onTabChange(e.target.value)}
              style={{ maxWidth: '185px' }}
            >
              {WORKSPACE_TABS.map(tab => (
                <option key={tab.id} value={tab.id}>
                  {tab.label}
                </option>
              ))}
            </select>
          ) : (
            <nav
              aria-label="Navegación del proyecto"
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                backgroundColor: 'var(--bg-subtle)',
                padding: '3px',
                height: '34px',
                borderRadius: 'var(--radius-md)',
                gap: '2px',
                border: '1px solid var(--border-subtle)',
                overflowX: 'auto',
                overflowY: 'hidden'
              }}
            >
              {WORKSPACE_TABS.map(tab => {
                const Icon = tab.icon;
                const isSelected = activeTab === tab.id;
                return (
                  <button
                    key={tab.id}
                    type="button"
                    data-testid={`tab-${tab.id}`}
                    aria-pressed={isSelected}
                    title={tab.title || tab.label}
                    onClick={() => onTabChange && onTabChange(tab.id)}
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      gap: '5px',
                      height: '26px',
                      padding: '0 clamp(8px, 1vw, 13px)',
                      fontSize: '12px',
                      fontWeight: isSelected ? 600 : 500,
                      borderRadius: 'var(--radius-sm)',
                      border: 'none',
                      backgroundColor: isSelected ? 'var(--bg-surface)' : 'transparent',
                      color: isSelected ? 'var(--text-primary)' : 'var(--text-secondary)',
                      cursor: 'pointer',
                      boxShadow: isSelected ? 'var(--shadow-sm)' : 'none',
                      transition: 'var(--transition)',
                      whiteSpace: 'nowrap'
                    }}
                  >
                    {Icon && <Icon size={13} color={isSelected ? 'var(--accent-primary)' : 'currentColor'} />}
                    <span>{tab.label}</span>
                  </button>
                );
              })}
            </nav>
          )
        ) : null}
      </div>

      {/* 3. SECCIÓN DERECHA: Acciones Compactas (32px) */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', justifyContent: 'flex-end', height: '100%', flexShrink: 0 }}>
        {activeProject && (
          <button
            type="button"
            onClick={onNewTask}
            title="Nueva Tarea (Ctrl+N)"
            aria-label="Nueva Tarea"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: '5px',
              height: '32px',
              padding: isCompact ? '0 9px' : '0 12px',
              fontSize: '12px',
              fontWeight: 600,
              borderRadius: 'var(--radius-md)',
              border: 'none',
              backgroundColor: 'var(--accent-primary)',
              color: '#ffffff',
              cursor: 'pointer',
              boxShadow: 'var(--shadow-sm)',
              transition: 'var(--transition)'
            }}
          >
            <Plus size={14} />
            {!isCompact && 'Nueva Tarea'}
          </button>
        )}

        {/* Chip de Identidad GitHub (PAT / gh CLI) */}
        <button
          type="button"
          onClick={() => onOpenSettings('github')}
          title={githubStatus?.authenticated
            ? `GitHub: @${githubStatus.username} (${githubStatus.provider === 'pat' ? 'PAT Token' : 'gh CLI'})\nClick para gestionar conexión`
            : "Conectar GitHub (PAT o gh CLI)"
          }
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: '6px',
            height: '32px',
            padding: isCompact ? '0 9px' : '0 10px',
            fontSize: '12px',
            fontWeight: 500,
            borderRadius: 'var(--radius-md)',
            border: '1px solid var(--border-subtle)',
            backgroundColor: 'var(--bg-surface)',
            color: 'var(--text-primary)',
            cursor: 'pointer',
            transition: 'var(--transition)'
          }}
        >
          <Github size={14} />
          <span style={{
            width: '7px',
            height: '7px',
            borderRadius: '50%',
            backgroundColor: githubStatus?.authenticated ? 'var(--accent-success)' : 'var(--text-muted)',
            display: 'inline-block'
          }} />
          {!isCompact && (
            <span style={{
              fontSize: '11.5px',
              fontFamily: 'var(--font-mono)',
              maxWidth: '100px',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap'
            }}>
              {githubStatus?.authenticated ? `@${githubStatus.username}` : 'GitHub'}
            </span>
          )}
        </button>

        {/* Alternar Tema */}
        <button
          type="button"
          onClick={onToggleTheme}
          title="Alternar Tema Claro / Oscuro (Ctrl+D)"
          aria-label="Alternar tema"
          style={{
            width: '32px',
            height: '32px',
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            borderRadius: 'var(--radius-md)',
            border: '1px solid var(--border-subtle)',
            backgroundColor: 'var(--bg-surface)',
            color: 'var(--text-primary)',
            cursor: 'pointer',
            transition: 'var(--transition)'
          }}
        >
          {theme === 'dark' ? <Sun size={15} /> : <Moon size={15} />}
        </button>

        {/* Botón de Configuración */}
        <button
          type="button"
          onClick={() => onOpenSettings()}
          title="Configuración del Nodo, P2P y Agentes (F1)"
          aria-label="Configuración"
          style={{
            width: '32px',
            height: '32px',
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            borderRadius: 'var(--radius-md)',
            border: '1px solid var(--border-subtle)',
            backgroundColor: 'var(--bg-surface)',
            color: 'var(--text-primary)',
            cursor: 'pointer',
            transition: 'var(--transition)'
          }}
        >
          <Settings size={16} />
        </button>
      </div>
    </header>
  );
}
