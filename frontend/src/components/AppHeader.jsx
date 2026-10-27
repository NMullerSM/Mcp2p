import React from 'react';
import { Sun, Moon, Settings, Plus } from 'lucide-react';

export function AppHeader({ theme, onToggleTheme, onOpenSettings, activeProject, onNewTask }) {
  return (
    <header style={{
      height: '56px',
      backgroundColor: 'var(--bg-surface)',
      borderBottom: '1px solid var(--border-subtle)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      padding: '0 20px',
      flexShrink: 0,
      zIndex: 10
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
        <span style={{
          background: 'var(--accent-primary)',
          color: '#ffffff',
          fontWeight: 900,
          fontSize: '15px',
          padding: '4px 10px',
          borderRadius: 'var(--radius-sm)',
          fontFamily: 'var(--font-mono)',
          letterSpacing: '0.5px'
        }}>
          MCP2P
        </span>
      </div>

      {/* Máximo 3 acciones primarias en la cabecera (Ley de Hick) */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
        {activeProject && (
          <button
            onClick={onNewTask}
            title="Nueva Tarea (Ctrl+N)"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '6px',
              padding: '6px 12px',
              fontSize: '13px',
              fontWeight: 600,
              borderRadius: 'var(--radius-md)',
              border: 'none',
              backgroundColor: 'var(--accent-primary)',
              color: '#ffffff',
              cursor: 'pointer'
            }}
          >
            <Plus size={16} /> Nueva Tarea
          </button>
        )}

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
            cursor: 'pointer'
          }}
        >
          {theme === 'dark' ? <Sun size={17} /> : <Moon size={17} />}
        </button>

        <button
          onClick={onOpenSettings}
          title="Configuración, Enlace P2P y Agentes"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '6px',
            padding: '6px 12px',
            fontSize: '13px',
            fontWeight: 500,
            borderRadius: 'var(--radius-md)',
            border: '1px solid var(--border-subtle)',
            backgroundColor: 'var(--bg-surface)',
            color: 'var(--text-primary)',
            cursor: 'pointer'
          }}
        >
          <Settings size={16} /> Configuración
        </button>
      </div>
    </header>
  );
}
