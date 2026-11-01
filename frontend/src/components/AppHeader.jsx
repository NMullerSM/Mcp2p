import React from 'react';
import { Sun, Moon, Settings, Plus, ArrowLeft, GitBranch, Users, Github, ShieldCheck } from 'lucide-react';

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
      height: '64px',
      backgroundColor: 'var(--bg-surface)',
      borderBottom: '1px solid var(--border-subtle)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      padding: '0 clamp(14px, 2vw, 24px)',
      flexShrink: 0,
      zIndex: 20,
      gap: '16px',
      overflowX: 'auto',
      whiteSpace: 'nowrap'
    }}>
      {/* 1. SECCIÓN IZQUIERDA: Marca, Botón Hub y Nombre de Proyecto Limpio (Sin UUID) */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexShrink: 0, height: '100%' }}>
        <span style={{
          background: 'var(--accent-primary)',
          color: '#ffffff',
          fontWeight: 900,
          fontSize: '13px',
          height: '32px',
          padding: '0 11px',
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          lineHeight: 1,
          borderRadius: 'var(--radius-sm)',
          fontFamily: 'var(--font-mono)',
          letterSpacing: '0.6px',
          boxShadow: 'var(--shadow-sm)'
        }}>
          MCP2P
        </span>

        <span style={{ color: 'var(--border-subtle)', fontWeight: 300, display: 'inline-flex', alignItems: 'center' }}>|</span>

        {activeProject ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', height: '100%' }}>
            <button
              onClick={onBackToProjects}
              title="Volver al Hub de Proyectos"
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '5px',
                height: '32px',
                padding: '0 10px',
                lineHeight: 1,
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
            <span
              title={activeProject.name}
              style={{
                fontSize: '14px',
                fontWeight: 700,
                color: 'var(--text-primary)',
                maxWidth: 'clamp(140px, 20vw, 300px)',
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                display: 'inline-flex',
                alignItems: 'center',
                lineHeight: 1,
                height: '32px'
              }}
            >
              {activeProject.name}
            </span>
          </div>
        ) : (
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', height: '100%' }}>
            <span style={{ fontSize: '14px', fontWeight: 700, color: 'var(--text-primary)', display: 'inline-flex', alignItems: 'center', height: '32px' }}>
              Proyectos
            </span>
            <span style={{
              fontSize: '11px',
              fontWeight: 600,
              fontFamily: 'var(--font-mono)',
              padding: '2px 8px',
              borderRadius: '10px',
              backgroundColor: 'var(--bg-subtle)',
              color: 'var(--text-secondary)'
            }}>
              {projectsCount}
            </span>
          </div>
        )}
      </div>

      {/* 2. SECCIÓN CENTRAL: Segmented Switcher Centrado y Equilibrado */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', flex: 1, minWidth: 0, height: '100%' }}>
        {activeProject ? (
          <div style={{
            display: 'inline-flex',
            alignItems: 'center',
            backgroundColor: 'var(--bg-subtle)',
            padding: '4px',
            height: '38px',
            borderRadius: 'var(--radius-md)',
            gap: '4px',
            border: '1px solid var(--border-subtle)',
            overflowX: 'auto'
          }}>
            {[
              { id: 'kanban', label: 'Tablero Kanban', icon: null },
              { id: 'changes', label: 'Control de Cambios', icon: GitBranch },
              { id: 'approvals', label: 'HITL', title: 'HITL — Human in the Loop', icon: ShieldCheck },
              { id: 'collaborators', label: 'Colaboradores & Agentes', icon: Users }
            ].map(tab => {
              const Icon = tab.icon;
              const isSelected = activeTab === tab.id;
              return (
                <button
                  key={tab.id}
                  title={tab.title || tab.label}
                  onClick={() => onTabChange(tab.id)}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    gap: '6px',
                    height: '30px',
                    padding: '0 clamp(10px, 1.3vw, 16px)',
                    fontSize: '12.5px',
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
                  {Icon && <Icon size={14} color={isSelected ? 'var(--accent-primary)' : 'currentColor'} />}
                  <span>{tab.label}</span>
                </button>
              );
            })}
          </div>
        ) : null}
      </div>

      {/* 3. SECCIÓN DERECHA: Botones de Altura Unificada (36px) y Alineación Vertical Exacta */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', justifyContent: 'flex-end', height: '100%', flexShrink: 0 }}>
        {activeProject && (
          <button
            onClick={onNewTask}
            title="Nueva Tarea (Ctrl+N)"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: '6px',
              height: '36px',
              padding: '0 14px',
              fontSize: '12.5px',
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
            justifyContent: 'center',
            gap: '6px',
            height: '36px',
            padding: '0 12px',
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
            fontSize: '11.5px',
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
          {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
        </button>

        {/* Botón de Configuración: Solo la tuerca */}
        <button
          onClick={() => onOpenSettings()}
          title="Configuración del Nodo, P2P y Agentes (F1)"
          style={{
            width: '36px',
            height: '36px',
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
          <Settings size={17} />
        </button>
      </div>
    </header>
  );
}
