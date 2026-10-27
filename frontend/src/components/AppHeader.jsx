import React from 'react';
import { Sun, Moon, Settings, Plus, ArrowLeft, Kanban, GitBranch, Users, Github } from 'lucide-react';

export function AppHeader({
  theme,
  onToggleTheme,
  onOpenSettings,
  activeProject,
  onBackToProjects,
  activeTab,
  onTabChange,
  onNewTask,
  githubStatus,
  projectsCount = 0
}) {
  return (
    <header style={{
      height: '56px',
      backgroundColor: 'var(--bg-surface)',
      borderBottom: '1px solid var(--border-subtle)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      padding: '0 clamp(10px, 2vw, 20px)',
      flexShrink: 0,
      zIndex: 20,
      gap: '12px',
      overflowX: 'auto',
      whiteSpace: 'nowrap'
    }}>
      {/* 1. SECCIÓN IZQUIERDA: Marca, Breadcrumbs e Identificador de Proyecto */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexShrink: 0 }}>
        <span style={{
          background: 'var(--accent-primary)',
          color: '#ffffff',
          fontWeight: 900,
          fontSize: '14px',
          padding: '4px 10px',
          borderRadius: 'var(--radius-sm)',
          fontFamily: 'var(--font-mono)',
          letterSpacing: '0.6px',
          boxShadow: 'var(--shadow-sm)'
        }}>
          MCP2P
        </span>

        <span style={{ color: 'var(--border-subtle)', fontWeight: 300 }}>|</span>

        {activeProject ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <button
              onClick={onBackToProjects}
              title="Volver al Hub de Proyectos"
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '4px',
                padding: '4px 8px',
                fontSize: '12px',
                fontWeight: 600,
                borderRadius: 'var(--radius-sm)',
                border: '1px solid var(--border-subtle)',
                backgroundColor: 'var(--bg-subtle)',
                color: 'var(--text-secondary)',
                cursor: 'pointer',
                transition: 'var(--transition)'
              }}
            >
              <ArrowLeft size={13} /> Hub
            </button>
            <span style={{
              fontSize: '13px',
              fontWeight: 700,
              color: 'var(--text-primary)',
              maxWidth: 'clamp(100px, 15vw, 200px)',
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis'
            }}>
              {activeProject.name}
            </span>
            <span style={{
              fontFamily: 'var(--font-mono)',
              fontSize: '10px',
              backgroundColor: 'var(--bg-subtle)',
              color: 'var(--accent-primary)',
              padding: '2px 6px',
              borderRadius: 'var(--radius-sm)',
              fontWeight: 600
            }}>
              {activeProject.project_uuid}
            </span>
          </div>
        ) : (
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span style={{ fontSize: '14px', fontWeight: 700, color: 'var(--text-primary)' }}>
              Hub de Proyectos
            </span>
            <span style={{
              fontSize: '11px',
              fontWeight: 600,
              fontFamily: 'var(--font-mono)',
              padding: '1px 7px',
              borderRadius: '10px',
              backgroundColor: 'var(--bg-subtle)',
              color: 'var(--text-secondary)'
            }}>
              {projectsCount}
            </span>
          </div>
        )}
      </div>

      {/* 2. SECCIÓN CENTRAL: Segmented Switcher tipo Parma */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', flex: 1, minWidth: 0 }}>
        {activeProject ? (
          <div style={{
            display: 'inline-flex',
            backgroundColor: 'var(--bg-subtle)',
            padding: '3px',
            borderRadius: 'var(--radius-md)',
            gap: '3px',
            border: '1px solid var(--border-subtle)',
            overflowX: 'auto'
          }}>
            {[
              { id: 'kanban', label: 'Tablero Kanban', icon: Kanban },
              { id: 'branches', label: 'Ramas & Git', icon: GitBranch },
              { id: 'collaborators', label: 'Colaboradores P2P', icon: Users }
            ].map(tab => {
              const Icon = tab.icon;
              const isSelected = activeTab === tab.id;
              return (
                <button
                  key={tab.id}
                  onClick={() => onTabChange(tab.id)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px',
                    padding: '5px clamp(8px, 1.2vw, 14px)',
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
                  <Icon size={14} color={isSelected ? 'var(--accent-primary)' : 'currentColor'} />
                  <span>{tab.label}</span>
                </button>
              );
            })}
          </div>
        ) : (
          <div style={{ fontSize: '12px', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            Enjambre P2P Descentralizado • Zero-Tokens • Gobernanza OKF
          </div>
        )}
      </div>

      {/* 3. SECCIÓN DERECHA: Acciones, Estado GitHub, Tema y Tuerca de Configuración */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', justifyContent: 'flex-end' }}>
        {/* Botón Nueva Tarea si hay proyecto activo */}
        {activeProject && (
          <button
            onClick={onNewTask}
            title="Nueva Tarea (Ctrl+N)"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '6px',
              padding: '6px 14px',
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
            <Plus size={15} /> Nueva Tarea
          </button>
        )}

        {/* Chip de Identidad GitHub (PAT / gh CLI) */}
        <button
          onClick={() => onOpenSettings('github')}
          title={githubStatus?.authenticated
            ? `GitHub: @${githubStatus.username} (${githubStatus.provider === 'pat' ? 'PAT Token' : 'gh CLI'})\nClick para gestionar conexión`
            : "Conectar GitHub (PAT o gh CLI)"
          }
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '6px',
            padding: '5px 10px',
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
          <span style={{
            fontSize: '11px',
            fontFamily: 'var(--font-mono)',
            maxWidth: '110px',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap'
          }}>
            {githubStatus?.authenticated ? `@${githubStatus.username}` : 'GitHub'}
          </span>
        </button>

        {/* Alternar Tema */}
        <button
          onClick={onToggleTheme}
          title="Alternar Tema Claro / Oscuro (Ctrl+D)"
          style={{
            width: '36px',
            height: '36px',
            display: 'flex',
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
          {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
        </button>

        {/* Botón de Configuración: ESTRICTAMENTE SOLO LA TUERCA (Sin texto) */}
        <button
          onClick={() => onOpenSettings()}
          title="Configuración del Nodo, P2P y Agentes (F1)"
          style={{
            width: '36px',
            height: '36px',
            display: 'flex',
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
          <Settings size={17} />
        </button>
      </div>
    </header>
  );
}
